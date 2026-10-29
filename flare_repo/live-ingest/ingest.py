"""
ingest.py - the live loop: IMD -> observations -> nowcast -> risk lookup.

    python ingest.py --once     # one cycle, then exit (good for testing / cron)
    python ingest.py            # loop forever, every POLL_MINUTES

Each cycle:
  1. pulls aws_data (Karnataka) + aws_data_mapping + stationnowcast from IMD,
  2. keeps the stations within STATION_RADIUS_KM of Koramangala and appends
     their new readings to data/live/observations.csv (rolling HISTORY_HOURS),
  3. turns IMD's cumulative-since-08:30 rainfall into mm/h per station,
  4. interpolates the stations onto a grid (inverse-distance weighting) for
     the last NOWCAST_FRAMES frames and runs the same PySteps extrapolation as
     ml-nowcasting/ for +1h/+2h/+3h,
  5. writes data/live/risk_lookup_koramangala.json, gauges_koramangala.json
     and status.json - atomically, so the APIs never read a half-written file.

If IMD can't be reached, the previous good files are left in place and
status.json records the error; the dashboard then shows the data as stale.
"""

import argparse
import json
import math
import os
import sys
import tempfile
import time
import traceback
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
import imd_client as imd
import risk_engine

sys.path.insert(0, str(config.REPO_ROOT / "ml-nowcasting"))
try:
    from nowcasting_api import build_nowcast, PYSTEPS_AVAILABLE  # same PySteps code as the demo
except Exception:  # pragma: no cover - pysteps/fastapi missing
    PYSTEPS_AVAILABLE = False
    build_nowcast = None

IST = ZoneInfo("Asia/Kolkata")
ATTRIBUTION = "Rainfall data source: India Meteorological Department (IMD), Ministry of Earth Sciences, Government of India."
OBS_COLUMNS = ["station_id", "name", "district", "lat", "lon", "distance_km", "obs_time", "rain_raw_mm"]
GRID_SIZE = 64
MIN_STATIONS_FOR_NOWCAST = 3
RAIN_EPS_MM_HR = 0.1


# ---------------------------------------------------------------- file helpers

def write_json_atomic(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def iso_ist(dt: datetime) -> str:
    return dt.astimezone(IST).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- step 1-2: fetch + store

def load_mapping():
    """Station metadata changes rarely: refresh at most once a day, else use the cache."""
    cached = read_json(config.STATIONS_FILE, {})
    fetched_at = cached.get("fetched_at")
    if fetched_at and datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at) < timedelta(hours=24):
        return cached.get("stations", {})
    try:
        records, _ = imd.fetch_mapping()
        stations = imd.parse_station_mapping(records)
        write_json_atomic(config.STATIONS_FILE, {
            "fetched_at": datetime.now(timezone.utc).isoformat(), "stations": stations})
        return stations
    except imd.IMDError as e:
        print(f"  aws_data_mapping unavailable ({e}); using cached station list")
        return cached.get("stations", {})


def fetch_observations():
    mapping = load_mapping()
    records, raw = imd.fetch_aws()
    config.RAW_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (config.RAW_SNAPSHOT_DIR / "aws_data_latest.json").write_text(raw, encoding="utf-8")
    obs, rain_field = imd.parse_aws_records(records, mapping)
    near = imd.stations_near_ward(obs)
    if not near:
        raise imd.IMDError(
            f"aws_data returned {len(records)} records but none within "
            f"{config.STATION_RADIUS_KM} km of Koramangala")
    if rain_field is None:
        raise imd.IMDError(
            "No rainfall field found in aws_data. Run probe_imd.py and set IMD_RAIN_FIELD.")
    return near, rain_field, len(records)


def update_store(new_obs):
    """Append unseen (station, obs_time) readings; drop anything older than HISTORY_HOURS."""
    new_df = pd.DataFrame([{**o, "obs_time": o["obs_time"].isoformat()} for o in new_obs],
                          columns=OBS_COLUMNS)
    if config.OBSERVATIONS_FILE.exists():
        old = pd.read_csv(config.OBSERVATIONS_FILE, dtype={"station_id": str})
        df = pd.concat([old, new_df], ignore_index=True)
    else:
        df = new_df
    df["obs_time"] = pd.to_datetime(df["obs_time"], utc=True, format="ISO8601")
    df = df.drop_duplicates(["station_id", "obs_time"], keep="last")
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=config.HISTORY_HOURS)
    df = df[df["obs_time"] >= cutoff].sort_values(["station_id", "obs_time"])

    config.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["obs_time"] = out["obs_time"].map(lambda t: t.isoformat())
    tmp = config.OBSERVATIONS_FILE.with_suffix(".csv.tmp")
    out.to_csv(tmp, index=False)
    os.replace(tmp, config.OBSERVATIONS_FILE)
    return df


# ---------------------------------------------------------------- step 3: rates

def add_rain_rates(df: pd.DataFrame) -> pd.DataFrame:
    """
    rain_rate_mm_hr per reading.

    cumulative mode: IMD totals rain since 08:30 IST and resets daily, so
      interval = this reading - previous reading; a drop means the counter
      reset, so the interval is just this reading. Gaps longer than 3 frames
      aren't differenced (we can't tell when that rain fell).
    interval mode: the reading already is the rain since the last report.
    """
    df = df.sort_values(["station_id", "obs_time"]).copy()
    prev_raw = df.groupby("station_id")["rain_raw_mm"].shift(1)
    prev_time = df.groupby("station_id")["obs_time"].shift(1)
    gap_h = (df["obs_time"] - prev_time).dt.total_seconds() / 3600.0
    max_gap_h = 3 * config.FRAME_MINUTES / 60.0

    if config.IMD_RAIN_MODE == "interval":
        gap_h = gap_h.fillna(config.FRAME_MINUTES / 60.0)
        interval = df["rain_raw_mm"]
    else:
        diff = df["rain_raw_mm"] - prev_raw
        interval = diff.where(diff >= 0, df["rain_raw_mm"])     # counter reset
        interval = interval.where(prev_raw.notna())             # first reading: unknown

    valid = gap_h.gt(0) & gap_h.le(max_gap_h) & interval.notna()
    df["rain_rate_mm_hr"] = (interval / gap_h).where(valid)
    return df


# ---------------------------------------------------------------- step 4: grid + nowcast

def grid_bounds(lats, lons):
    lon_min, lat_min, lon_max, lat_max = config.WARD_BBOX
    lat_lo, lat_hi = min(min(lats), lat_min) - 0.01, max(max(lats), lat_max) + 0.01
    lon_lo, lon_hi = min(min(lons), lon_min) - 0.01, max(max(lons), lon_max) + 0.01
    return lat_lo, lat_hi, lon_lo, lon_hi


def idw_grid(lats, lons, values, bounds, power=2.0):
    """
    Inverse-distance weighting onto a GRID_SIZE x GRID_SIZE grid. Used instead
    of the demo's linear griddata because IMD stations are sparse: linear
    interpolation is zero outside the stations' convex hull, which could
    silently blank out Koramangala.
    """
    lat_lo, lat_hi, lon_lo, lon_hi = bounds
    glat, glon = np.meshgrid(np.linspace(lat_lo, lat_hi, GRID_SIZE),
                             np.linspace(lon_lo, lon_hi, GRID_SIZE), indexing="ij")
    if len(values) == 1:
        return np.full(glat.shape, float(values[0]))
    coslat = math.cos(math.radians(np.mean(lats)))
    d2 = ((glat[..., None] - np.asarray(lats)) ** 2
          + ((glon[..., None] - np.asarray(lons)) * coslat) ** 2)
    w = 1.0 / np.maximum(d2, 1e-12) ** (power / 2)
    return (w * np.asarray(values)).sum(-1) / w.sum(-1)


def ward_mask(bounds):
    lat_lo, lat_hi, lon_lo, lon_hi = bounds
    lon_min, lat_min, lon_max, lat_max = config.WARD_BBOX
    glat, glon = np.meshgrid(np.linspace(lat_lo, lat_hi, GRID_SIZE),
                             np.linspace(lon_lo, lon_hi, GRID_SIZE), indexing="ij")
    mask = (glat >= lat_min) & (glat <= lat_max) & (glon >= lon_min) & (glon <= lon_max)
    if not mask.any():  # grid coarser than the ward: use the nearest cell
        i = np.abs(glat[:, 0] - config.WARD_CENTER_LAT).argmin()
        j = np.abs(glon[0, :] - config.WARD_CENTER_LON).argmin()
        mask[i, j] = True
    return mask


def build_frames(df):
    """Bin readings into FRAME_MINUTES frames; returns (frame_times, per-frame station rates)."""
    rated = df.dropna(subset=["rain_rate_mm_hr"]).copy()
    if rated.empty:
        return [], []
    rated["frame"] = rated["obs_time"].dt.floor(f"{config.FRAME_MINUTES}min")
    frame_times = sorted(rated["frame"].unique())[-config.NOWCAST_FRAMES:]
    frames = []
    for ft in frame_times:
        snap = (rated[rated["frame"] == ft]
                .groupby("station_id")
                .agg(lat=("lat", "first"), lon=("lon", "first"), rate=("rain_rate_mm_hr", "mean"))
                .reset_index())
        frames.append(snap)
    return frame_times, frames


def nowcast_ward_rates(df):
    """
    Returns (t0_rate, [rate +1h, +2h, +3h], method, frame_times, bounds).
    method: "pysteps" | "persistence" | "dry"
    """
    if 60 % config.FRAME_MINUTES:
        raise ValueError("FRAME_MINUTES must divide 60 (15, 20, 30 or 60)")
    frame_times, frames = build_frames(df)
    if not frames:
        raise imd.IMDError("Not enough consecutive readings yet to compute rain rates "
                           "(need two readings per station; wait one more cycle)")

    lats = df["lat"].tolist()
    lons = df["lon"].tolist()
    bounds = grid_bounds(lats, lons)
    mask = ward_mask(bounds)
    grids = np.stack([idw_grid(f["lat"].tolist(), f["lon"].tolist(), f["rate"].tolist(), bounds)
                      for f in frames])
    t0_rate = float(grids[-1][mask].mean())

    steps_per_hour = 60 // config.FRAME_MINUTES
    n_lead = 3 * steps_per_hour
    enough = len(frames) >= 2 and len(frames[-1]) >= MIN_STATIONS_FOR_NOWCAST

    if grids.max() < RAIN_EPS_MM_HR:
        return t0_rate, [0.0, 0.0, 0.0], "dry", frame_times, bounds

    if enough and PYSTEPS_AVAILABLE:
        try:
            with warnings.catch_warnings():  # PySteps is chatty on smooth, sparse-station fields
                warnings.simplefilter("ignore")
                forecast = build_nowcast(grids, n_leadtimes=n_lead)
            rates = [float(np.nanmean(forecast[h * steps_per_hour - 1][mask])) for h in (1, 2, 3)]
            rates = [0.0 if math.isnan(r) else max(0.0, r) for r in rates]
            return t0_rate, rates, "pysteps", frame_times, bounds
        except Exception as e:  # optical flow can fail on near-uniform fields
            print(f"  PySteps failed ({e}); falling back to persistence")

    return t0_rate, [t0_rate] * 3, "persistence", frame_times, bounds


# ---------------------------------------------------------------- one cycle

def run_cycle():
    started = datetime.now(timezone.utc)
    status = read_json(config.STATUS_FILE, {}) or {}
    status.update({"last_attempt": started.isoformat(), "attribution": ATTRIBUTION})

    try:
        near, rain_field, n_records = fetch_observations()
        df = add_rain_rates(update_store(near))
        t0_rate, fc_rates, method, frame_times, _ = nowcast_ward_rates(df)

        newest = df["obs_time"].max().to_pydatetime()
        t0 = newest.replace(second=0, microsecond=0)
        steps = [{"timestamp": iso_ist(t0), "rain_rate_mm_hr": t0_rate,
                  "horizon_label": "T+0", "is_forecast": False}]
        for h, rate in zip((1, 2, 3), fc_rates):
            steps.append({"timestamp": iso_ist(t0 + timedelta(hours=h)), "rain_rate_mm_hr": rate,
                          "horizon_label": f"T+{h}h", "is_forecast": True})

        latest = df.sort_values("obs_time").groupby("station_id").tail(1)
        gauges = [{
            "station_id": r.station_id, "name": r.name, "district": r.district,
            "lat": r.lat, "lon": r.lon, "distance_km": r.distance_km,
            "observed_at": iso_ist(r.obs_time.to_pydatetime()),
            "rain_rate_mm_hr": None if pd.isna(r.rain_rate_mm_hr) else round(float(r.rain_rate_mm_hr), 2),
            "rain_since_0830_mm": None if pd.isna(r.rain_raw_mm) or config.IMD_RAIN_MODE != "cumulative"
                                  else round(float(r.rain_raw_mm), 1),
        } for r in latest.itertuples()]

        try:
            nc_records, _ = imd.fetch_station_nowcast()
            imd_nowcast = imd.parse_station_nowcast(nc_records)
        except imd.IMDError as e:
            print(f"  stationnowcast unavailable ({e})")
            imd_nowcast = status.get("imd_nowcast")

        meta = {
            "generated_at": iso_ist(started),
            "newest_observation": iso_ist(newest),
            "stations_used": len(gauges),
            "stations_in_state_feed": n_records,
            "rain_field": rain_field,
            "rain_mode": config.IMD_RAIN_MODE,
            "nowcast_method": method,
            "frames_used": [iso_ist(t.to_pydatetime()) for t in frame_times],
            "ward_rain_mm_hr": [round(s["rain_rate_mm_hr"], 2) for s in steps],
            "attribution": ATTRIBUTION,
        }
        lookup = risk_engine.build_lookup(risk_engine.load_base_segments(), steps, meta)

        write_json_atomic(config.GAUGES_FILE, {
            "ward_id": config.WARD_ID, "source": "imd_live", "attribution": ATTRIBUTION,
            "frames": [{"timestamp": steps[0]["timestamp"], "gauges": gauges}]})
        write_json_atomic(config.LIVE_LOOKUP_FILE, lookup)

        status.update({"ok": True, "last_success": started.isoformat(), "last_error": None,
                       "imd_nowcast": imd_nowcast, **meta})
        print(f"[{iso_ist(started)}] OK  stations={len(gauges)}  method={method}  "
              f"ward rain (mm/h) T+0..3h = {meta['ward_rain_mm_hr']}")
    except Exception as e:
        status.update({"ok": False, "last_error": f"{type(e).__name__}: {e}"})
        print(f"[{iso_ist(started)}] FAILED  {e}")
        if not isinstance(e, imd.IMDError):
            traceback.print_exc()
    finally:
        write_json_atomic(config.STATUS_FILE, status)
    return status.get("ok", False)


def main():
    parser = argparse.ArgumentParser(description="FLARE live IMD ingest")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()

    if not PYSTEPS_AVAILABLE:
        print("NOTE: pysteps not installed - forecasts will use persistence only.")
    if args.once:
        return 0 if run_cycle() else 1

    print(f"Polling IMD every {config.POLL_MINUTES} min. Ctrl+C to stop.")
    while True:
        run_cycle()
        time.sleep(config.POLL_MINUTES * 60)


if __name__ == "__main__":
    sys.exit(main())
