"""
Turns live rainfall into the same risk_lookup JSON that scripts/05 produces,
without re-running the heavy GIS pipeline.

The GIS part of the risk score (susceptibility + drain proximity = base_risk)
doesn't change with the weather, so it is computed once by scripts/05 and
saved to data/processed/segments_base_koramangala.json. Every ingest cycle
only redoes the cheap part, exactly as scripts/05 does it:

    dynamic_risk(seg, t) = clamp(base_risk(seg) x rain_factor(t), 0, 1)
    rain_factor          = dry 0.30 / light 0.55 / moderate 0.80 / heavy 1.00
    predicted_depth_cm   = dynamic_risk x 30     (illustrative, not calibrated)
"""

import json

import pandas as pd

import config

MAX_DEPTH_CM = 30.0   # same constants as scripts/05_build_lookup.py
CONFIDENCE = 0.60


# ---------------------------------------------------------------- multiplier

# Same bands and factors as scripts/04_rainfall_multiplier.py (a test checks it).
DRY_MAX_MM_HR = 0.5
LIGHT_MAX_MM_HR = 5.0
MODERATE_MAX_MM_HR = 15.0
RAIN_FACTORS = {"dry": 0.30, "light": 0.55, "moderate": 0.80, "heavy": 1.00}


def rain_band(rate_mm_hr):
    if rate_mm_hr < DRY_MAX_MM_HR:
        return "dry"
    if rate_mm_hr < LIGHT_MAX_MM_HR:
        return "light"
    if rate_mm_hr < MODERATE_MAX_MM_HR:
        return "moderate"
    return "heavy"


def rain_multiplier(rate_mm_hr):
    """
    Rain rate over the ward (mm/h) -> rain factor.

    rain factor(t) from the rain RATE over the ward, using IMD's own intensity bands
    (team decision, 31 Oct 2026 - rain decides HOW MUCH risk, susceptibility decides WHERE):
      dry      (< 0.5 mm/h)  -> 0.30   calm baseline: a dry day shows low risk
      light    (0.5-5 mm/h)  -> 0.55
      moderate (5-15 mm/h)   -> 0.80
      heavy    (>= 15 mm/h)  -> 1.00   full static susceptibility
    Previously 1.00/1.25/1.60/2.00 on 5/15/30 mm per 15 min, which put ~97% of the
    ward's streets in High/Severe even on a dry day once real drains were used.
    """
    return RAIN_FACTORS[rain_band(rate_mm_hr)]


# ---------------------------------------------------------------- base risk

def load_base_segments():
    """
    Per-segment static data: segment_id, geometry, base_risk, drain_proximity_m.

    Preferred source is segments_base_koramangala.json (written by scripts/05).
    If it's missing, fall back to recovering base_risk from the demo lookup's
    T+0 step (risk = base x multiplier, so base = risk / multiplier). That
    copy is only rounded to 2 decimals, so re-run scripts/05 when you can.
    """
    if config.BASE_SEGMENTS_FILE.exists():
        data = json.loads(config.BASE_SEGMENTS_FILE.read_text(encoding="utf-8"))
        return data["segments"]

    lookup = json.loads(config.DEMO_LOOKUP_FILE.read_text(encoding="utf-8"))
    first_ts = next(iter(lookup["timesteps"]))
    mult = 1.0
    rain_csv = config.PROCESSED_DIR / "rainfall_multiplier.csv"
    if rain_csv.exists():
        df = pd.read_csv(rain_csv)
        row = df[df["timestamp"] == first_ts]
        if not row.empty:
            mult = float(row["rainfall_multiplier"].iloc[0])
    return [
        {
            "segment_id": s["segment_id"],
            "road_name": s.get("road_name"),
            "geometry": s["geometry"],
            "base_risk": round(min(1.0, s["risk_score"] / mult), 4),
            "drain_proximity_m": s.get("drain_proximity_m"),
        }
        for s in lookup["timesteps"][first_ts]
    ]


# ---------------------------------------------------------------- lookup

def build_lookup(base_segments, steps, meta):
    """
    steps: [{timestamp, rain_rate_mm_hr, horizon_label, is_forecast}, ...]
    Returns the risk_lookup payload (same shape as scripts/05 + live metadata).
    """
    timesteps = {}
    for step in steps:
        rate = float(step["rain_rate_mm_hr"])
        mult = rain_multiplier(rate)
        segs = []
        for seg in base_segments:
            risk = max(0.0, min(1.0, seg["base_risk"] * mult))
            segs.append({
                "segment_id": seg["segment_id"],
                "road_name": seg.get("road_name"),
                "geometry": seg["geometry"],
                "risk_score": round(risk, 2),
                "predicted_depth_cm": round(risk * MAX_DEPTH_CM, 1),
                "confidence": CONFIDENCE,
                "drain_proximity_m": seg.get("drain_proximity_m"),
                "mean_rainfall_mm": round(rate, 2),     # mm over the hour = mm/h
                "rain_rate_mm_hr": round(rate, 2),
                "rain_band": rain_band(rate),
                "rainfall_multiplier": mult,
                "horizon_label": step["horizon_label"],
                "is_forecast": step["is_forecast"],
            })
        timesteps[step["timestamp"]] = segs

    return {
        "ward_id": config.WARD_ID,
        "generated_at": meta["generated_at"],
        "source": "imd_live",
        "live": meta,
        "formula": {
            "description": (
                "dynamic_risk = clamp(base_risk x rain_factor, 0, 1); base_risk from "
                "scripts/05 (0.70 x static susceptibility + 0.30 x drain proximity). "
                "rain_factor = dry 0.30 / light 0.55 / moderate 0.80 / heavy 1.00 on IMD "
                "rain-rate bands (<0.5, 0.5-5, 5-15, >=15 mm/h), from live IMD rainfall. "
                "predicted_depth_cm = dynamic_risk x 30 (illustrative, NOT calibrated). "
                "confidence = 0.60 (GIS-only model)."
            ),
            "rain_factors": RAIN_FACTORS,
        },
        "timesteps": timesteps,
    }
