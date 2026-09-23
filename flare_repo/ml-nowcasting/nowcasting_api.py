"""
FastAPI wrapper around the PySteps nowcasting logic.

Run with: uvicorn nowcasting_api:app --reload
Then visit http://localhost:8000/docs to test it interactively.
"""

import os
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from scipy.interpolate import griddata

try:
    from pysteps import motion, nowcasts
    from pysteps.utils import transformation
    PYSTEPS_AVAILABLE = True
except ImportError:
    PYSTEPS_AVAILABLE = False

app = FastAPI(title="FLARE Nowcasting API")

CSV_PATH = os.path.join(os.path.dirname(__file__), "historical_rainfall.csv")


# ---------- core logic (same as ml_nowcasting.py) ----------

def load_point_rainfall(csv_path):
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    required = {"timestamp", "station_id", "lat", "lon", "rainfall_mm"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    return df.sort_values("timestamp")


def interpolate_to_grid(df, grid_size=64, method="linear"):
    timestamps = sorted(df["timestamp"].unique())
    lat_min, lat_max = df["lat"].min(), df["lat"].max()
    lon_min, lon_max = df["lon"].min(), df["lon"].max()

    grid_lat, grid_lon = np.meshgrid(
        np.linspace(lat_min, lat_max, grid_size),
        np.linspace(lon_min, lon_max, grid_size),
    )

    frames = []
    for ts in timestamps:
        snapshot = df[df["timestamp"] == ts]
        points = snapshot[["lat", "lon"]].values
        values = snapshot["rainfall_mm"].values

        if len(points) < 3:
            grid = np.full((grid_size, grid_size), values.mean() if len(values) else 0.0)
        else:
            grid = griddata(points, values, (grid_lat, grid_lon), method=method, fill_value=0.0)

        frames.append(grid)

    return np.stack(frames), timestamps, (lat_min, lat_max, lon_min, lon_max)


def build_nowcast(rainfall_stack, n_leadtimes=18):
    if not PYSTEPS_AVAILABLE:
        raise RuntimeError("pysteps not installed on server")

    rainfall_db, _ = transformation.dB_transform(rainfall_stack, threshold=0.1, zerovalue=-15.0)
    oflow_method = motion.get_method("LK")
    motion_field = oflow_method(rainfall_db)

    extrapolate = nowcasts.get_method("extrapolation")
    forecast_db = extrapolate(rainfall_db[-1], motion_field, n_leadtimes)

    forecast_mm = transformation.dB_transform(forecast_db, inverse=True, threshold=-10.0, zerovalue=0.0)[0]
    return forecast_mm


# ---------- cached state (computed once, reused across requests) ----------

_cache = {"forecast": None, "bounds": None, "timestep_minutes": 15}


def _ensure_forecast_computed(n_leadtimes=18):
    if _cache["forecast"] is not None:
        return
    if not os.path.exists(CSV_PATH):
        raise HTTPException(status_code=500, detail=f"Rainfall CSV not found at {CSV_PATH}")

    df = load_point_rainfall(CSV_PATH)
    rainfall_stack, timestamps, bounds = interpolate_to_grid(df)
    forecast = build_nowcast(rainfall_stack, n_leadtimes=n_leadtimes)

    _cache["forecast"] = forecast
    _cache["bounds"] = bounds


# ---------- endpoints ----------

@app.get("/")
def root():
    return {"status": "ok", "pysteps_available": PYSTEPS_AVAILABLE}


@app.get("/nowcast/summary")
def nowcast_summary(n_leadtimes: int = 18):
    """
    Returns lightweight per-leadtime stats (mean/max rainfall) instead of
    the full grid -- cheap to call repeatedly, good for a dashboard sparkline.
    """
    _ensure_forecast_computed(n_leadtimes)
    forecast = _cache["forecast"]

    summary = []
    for i, grid in enumerate(forecast):
        summary.append({
            "leadtime_step": i,
            "minutes_ahead": (i + 1) * _cache["timestep_minutes"],
            "mean_rainfall_mm": float(np.nanmean(grid)),
            "max_rainfall_mm": float(np.nanmax(grid)),
        })
    return {"leadtimes": summary}


@app.get("/nowcast/grid/{leadtime_step}")
def nowcast_grid(leadtime_step: int, n_leadtimes: int = 18):
    """
    Returns the full 2D rainfall grid for one specific leadtime step,
    plus the lat/lon bounds it covers -- use this for actually drawing
    the forecast on the dashboard map.
    """
    _ensure_forecast_computed(n_leadtimes)
    forecast = _cache["forecast"]

    if leadtime_step < 0 or leadtime_step >= len(forecast):
        raise HTTPException(status_code=404, detail=f"leadtime_step must be between 0 and {len(forecast) - 1}")

    lat_min, lat_max, lon_min, lon_max = _cache["bounds"]
    return {
        "leadtime_step": leadtime_step,
        "minutes_ahead": (leadtime_step + 1) * _cache["timestep_minutes"],
        "bounds": {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max},
        "grid": forecast[leadtime_step].tolist(),
    }


@app.post("/nowcast/refresh")
def refresh_nowcast(n_leadtimes: int = 18):
    """Forces a recompute instead of serving the cached forecast."""
    _cache["forecast"] = None
    _ensure_forecast_computed(n_leadtimes)
    return {"status": "refreshed", "n_leadtimes": n_leadtimes}
