"""
04_rainfall_multiplier.py
=========================
Derives four forecast steps (T+0 -> T+3h) for the risk pipeline.

T+0 uses the most recent OBSERVED rainfall (the actual last reading -
"now" doesn't need forecasting, it's already known).

T+1h, T+2h, T+3h use REAL PySteps nowcasts from ml-nowcasting/nowcasting_api.py,
built from the same historical gauge data ml-nowcasting already uses -
not a replay of past observations relabeled as a forecast.

Outputs
-------
data/processed/rainfall_multiplier.csv
  Columns: timestamp, mean_rainfall_mm, rainfall_multiplier, horizon_label,
           is_forecast

Formula
-------
  multiplier(t) driven by rain intensity bucket:
    dry   (< 5 mm)  -> 1.00  (baseline susceptibility only)
    light (5-15 mm) -> 1.25
    mod  (15-30 mm) -> 1.60  (conservative peak; multiplier kept <= 2 since
                              we do not model pipe capacity explicitly)
    heavy (>= 30 mm) -> 2.00
"""

import os
import sys
from datetime import timedelta

import pandas as pd
import numpy as np

# ml-nowcasting/nowcasting_api.py has the actual PySteps logic. Import its
# functions directly rather than duplicating them, so there is exactly one
# place that owns "how do we turn gauge readings into a forecast."
NOWCASTING_DIR = os.path.join(os.path.dirname(__file__), "..", "ml-nowcasting")
sys.path.insert(0, os.path.abspath(NOWCASTING_DIR))

from nowcasting_api import (  # noqa: E402
    load_point_rainfall,
    interpolate_to_grid,
    build_nowcast,
    PYSTEPS_AVAILABLE,
)


# ============================================================
# PATHS
# ============================================================

INPUT = "data/raw/rainfall.csv"
OUTPUT_DIR = "data/processed"
OUTPUT = os.path.join(OUTPUT_DIR, "rainfall_multiplier.csv")

NOWCAST_TIMESTEP_MINUTES = 15


# ============================================================
# THRESHOLDS (mm per 15-min accumulation)
# ============================================================

DRY_THRESH = 5.0        # < 5 mm   -> multiplier 1.00
LIGHT_THRESH = 15.0     # 5-15 mm  -> multiplier 1.25
MODERATE_THRESH = 30.0  # 15-30 mm -> multiplier 1.60
#                          >= 30 mm -> multiplier 2.00


def multiplier_from_rain(rain_mm: float) -> float:
    """Step-function: rainfall intensity (mm) -> dimensionless multiplier."""
    if rain_mm < DRY_THRESH:
        return 1.00
    elif rain_mm < LIGHT_THRESH:
        return 1.25
    elif rain_mm < MODERATE_THRESH:
        return 1.60
    else:
        return 2.00


def leadtime_index_for_minutes(minutes_ahead: int) -> int:
    """
    nowcast_api's forecast array is indexed such that grid[i] corresponds
    to (i + 1) * NOWCAST_TIMESTEP_MINUTES minutes ahead. Convert a target
    "minutes ahead" into that array index.
    """
    index = (minutes_ahead // NOWCAST_TIMESTEP_MINUTES) - 1
    return index


print("=" * 55)
print("RAINFALL MULTIPLIER - 4-STEP FORECAST (T+0 observed, T+1h/2h/3h real PySteps nowcast)")
print("=" * 55)

if not PYSTEPS_AVAILABLE:
    raise RuntimeError(
        "pysteps is not installed. Install it (pip install pysteps) before "
        "running this script - the forecast steps genuinely need it, "
        "there is no historical-replay fallback anymore."
    )

print("\nReading rainfall data:", INPUT)
df = load_point_rainfall(INPUT)

required = {"timestamp", "station_id", "lat", "lon", "rainfall_mm"}
missing = required - set(df.columns)
if missing:
    raise ValueError(f"Missing columns in {INPUT}: {missing}")

print(f"  Records loaded: {len(df)}")
print(f"  Unique timestamps: {df['timestamp'].nunique()}")

# ============================================================
# T+0: the most recent OBSERVED mean rainfall - not a forecast
# ============================================================

rain_by_ts = (
    df.groupby("timestamp")["rainfall_mm"]
    .mean()
    .reset_index()
    .rename(columns={"rainfall_mm": "mean_rainfall_mm"})
    .sort_values("timestamp")
    .reset_index(drop=True)
)

last_observed_row = rain_by_ts.iloc[-1]
t0_timestamp = pd.to_datetime(last_observed_row["timestamp"])
t0_rain = float(last_observed_row["mean_rainfall_mm"])

print(f"\n  T+0 (observed, not forecast): ts={t0_timestamp} rain={t0_rain:.2f} mm")

# ============================================================
# T+1h / T+2h / T+3h: real PySteps nowcasts
# ============================================================

print("\nBuilding PySteps nowcast from the same observed record...")
rainfall_stack, timestamps, bounds = interpolate_to_grid(df)
print(f"  Rainfall stack shape: {rainfall_stack.shape} ({len(timestamps)} observed frames)")

forecast = build_nowcast(rainfall_stack, n_leadtimes=18)
print(f"  Forecast grids produced: {forecast.shape[0]} leadtimes "
      f"(up to +{forecast.shape[0] * NOWCAST_TIMESTEP_MINUTES} min)")

steps = [{
    "timestamp": t0_timestamp.isoformat(),
    "mean_rainfall_mm": round(t0_rain, 3),
    "rainfall_multiplier": multiplier_from_rain(t0_rain),
    "horizon_label": "T+0",
    "is_forecast": False,
}]

for minutes_ahead, label in [(60, "T+1h"), (120, "T+2h"), (180, "T+3h")]:
    idx = leadtime_index_for_minutes(minutes_ahead)
    if idx >= forecast.shape[0]:
        raise ValueError(
            f"Need leadtime index {idx} for {label}, but only "
            f"{forecast.shape[0]} leadtimes were computed. Increase "
            f"n_leadtimes in build_nowcast()."
        )

    grid = forecast[idx]
    rain = float(np.nanmean(grid))
    mult = multiplier_from_rain(rain)
    ts = t0_timestamp + timedelta(minutes=minutes_ahead)

    steps.append({
        "timestamp": ts.isoformat(),
        "mean_rainfall_mm": round(rain, 3),
        "rainfall_multiplier": mult,
        "horizon_label": label,
        "is_forecast": True,
    })
    print(f"  {label:6s}  ts={ts.isoformat()}  rain={rain:.2f} mm (real nowcast)  "
          f"multiplier={mult:.2f}")


# ============================================================
# SAVE
# ============================================================

os.makedirs(OUTPUT_DIR, exist_ok=True)

out_df = pd.DataFrame(steps)
out_df.to_csv(OUTPUT, index=False)

print(f"\nSaved: {OUTPUT}")
print(f"Multiplier range: {out_df['rainfall_multiplier'].min()} "
      f"- {out_df['rainfall_multiplier'].max()}")

print("\n" + "=" * 55)
print("RAINFALL MULTIPLIER COMPLETE (T+1h/2h/3h are now real forecasts)")
print("=" * 55)