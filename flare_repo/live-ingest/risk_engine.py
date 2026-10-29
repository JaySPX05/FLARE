"""
Turns live rainfall into the same risk_lookup JSON that scripts/05 produces,
without re-running the heavy GIS pipeline.

The GIS part of the risk score (susceptibility + drain proximity = base_risk)
doesn't change with the weather, so it is computed once by scripts/05 and
saved to data/processed/segments_base_koramangala.json. Every ingest cycle
only redoes the cheap part, exactly as scripts/05 does it:

    dynamic_risk(seg, t) = clamp(base_risk(seg) x rain_multiplier(t), 0, 1)
    predicted_depth_cm   = dynamic_risk x 30     (illustrative, not calibrated)
"""

import json

import pandas as pd

import config

MAX_DEPTH_CM = 30.0   # same constants as scripts/05_build_lookup.py
CONFIDENCE = 0.60


# ---------------------------------------------------------------- multiplier

def rain_multiplier(rate_mm_hr, scheme=None):
    """
    Rain intensity -> risk multiplier, same step values as scripts/04.

    "legacy" keeps scripts/04's thresholds exactly: 5/15/30 mm per 15-min
    accumulation, i.e. 20/60/120 mm/h. "imd" applies the same 5/15/30 numbers
    per HOUR, which lines up with IMD's light (<5), moderate (5-15) and heavy
    (>15 mm/h) nowcast categories.
    """
    scheme = scheme or config.RAIN_THRESHOLD_SCHEME
    value = rate_mm_hr / 4.0 if scheme == "legacy" else rate_mm_hr
    if value < 5.0:
        return 1.00
    if value < 15.0:
        return 1.25
    if value < 30.0:
        return 1.60
    return 2.00


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
                "geometry": seg["geometry"],
                "risk_score": round(risk, 2),
                "predicted_depth_cm": round(risk * MAX_DEPTH_CM, 1),
                "confidence": CONFIDENCE,
                "drain_proximity_m": seg.get("drain_proximity_m"),
                "mean_rainfall_mm": round(rate, 2),     # mm over the hour = mm/h
                "rain_rate_mm_hr": round(rate, 2),
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
                "dynamic_risk = clamp(base_risk x rain_multiplier, 0, 1); base_risk from "
                "scripts/05 (0.70 x static susceptibility + 0.30 x drain proximity). "
                f"rain_multiplier from IMD AWS rainfall over the ward ({config.RAIN_THRESHOLD_SCHEME} "
                "thresholds), nowcast with PySteps. predicted_depth_cm = dynamic_risk x 30 "
                "(illustrative, NOT calibrated). confidence = 0.60 (GIS-only model)."
            ),
            "threshold_scheme": config.RAIN_THRESHOLD_SCHEME,
        },
        "timesteps": timesteps,
    }
