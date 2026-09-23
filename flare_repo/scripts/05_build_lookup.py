"""
05_build_lookup.py
==================
Builds the time-varying flood-risk lookup table for Koramangala.

Pipeline
--------
  road network (roads.gpkg, bbox-clipped to Koramangala)
    +
  susceptibility raster  (data/processed/susceptibility.tif)
    +
  drain proximity        (data/processed/drains.gpkg — Shapely distance)
    +
  rainfall multiplier    (data/processed/rainfall_multiplier.csv)
    =
  data/processed/risk_lookup_koramangala.json

Formula  (fully documented, no physical calibration claimed)
-------
  static_susc   = weighted overlay from script 03
                  [elevation 0.30, slope 0.20, drain_dist 0.25, impervious 0.25]
                  — value already normalised 0–1

  drain_prox_norm(seg) = 1 − clamp(nearest_drain_m, 0, MAX_DRAIN_M) / MAX_DRAIN_M
                         MAX_DRAIN_M = 500 m
                         → 1.0 = road is ON a drain channel
                         → 0.0 = road ≥ 500 m from any drain

  base_risk(seg)  = SUSC_W × static_susc + DRAIN_W × drain_prox_norm
                    SUSC_W  = 0.70,  DRAIN_W = 0.30

  dynamic_risk(seg, t) = clamp(base_risk × rain_factor(t), 0, 1)
                         rain_factor values taken from rainfall_multiplier.csv

  predicted_depth_cm = dynamic_risk × MAX_DEPTH_CM   (illustrative, NOT calibrated)
  MAX_DEPTH_CM = 30

  confidence = 0.60  (fixed, GIS-only model)

Notes
-----
- Drains come from data/processed/drains.gpkg (118 features, Bellandur SWD network).
  They lie just east of the Koramangala ward boundary; the eastern roads are
  therefore genuinely closest to this drain network — a geographically meaningful signal.
- Pipe capacities are NOT invented; only spatial proximity is used.
- The ward boundary is NOT used for clipping roads (it clips to only 2 features due to
  the ward polygon extent). Instead, a Koramangala bounding box is used.
- Highway types: footway / steps / path / construction / proposed / track excluded.
"""

import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import rowcol
from shapely.geometry import LineString, MultiLineString, box
from shapely.ops import nearest_points


# ============================================================
# CONFIGURATION
# ============================================================

WARD_ID = "koramangala"

# Data inputs
ROAD_FILE          = Path("data/processed/roads_ward.gpkg")    # prepared study roads
SUSCEPTIBILITY_FILE= Path("data/processed/susceptibility.tif")
DRAIN_FILE         = Path("data/processed/drains_ward.gpkg")   # prepared study drains
RAINFALL_FILE      = Path("data/processed/rainfall_multiplier.csv")

OUTPUT_FILE = Path(f"data/processed/risk_lookup_{WARD_ID}.json")

# The same reproducible pilot-study extent used by script 01. The supplied
# ward polygon and source OSM/drain layers have an extent mismatch.
KORM_BBOX_WGS84 = (77.618, 12.918, 77.650, 12.953)   # (lon_min, lat_min, lon_max, lat_max)

# Highway types to INCLUDE (navigable roads only)
INCLUDE_HIGHWAY = {
    "residential", "tertiary", "tertiary_link",
    "secondary", "secondary_link",
    "primary", "primary_link",
    "trunk", "trunk_link",
    "unclassified", "living_street", "service",
}

# Raster sampling: points along each road
NUM_SAMPLES = 10

# Drainage proximity cap
MAX_DRAIN_M = 500.0        # beyond this, no drainage influence

# Formula weights (must sum to 1.0)
SUSC_W  = 0.70             # weight on static susceptibility (script 03 output)
DRAIN_W = 0.30             # weight on drain proximity

# Illustrative depth scale — NOT a physically calibrated flood model
MAX_DEPTH_CM = 30.0

# Fixed confidence for a GIS-only model
CONFIDENCE = 0.60


# ============================================================
# HELPERS
# ============================================================

def to_linestring(geometry):
    """Return a single LineString from LineString or MultiLineString.
    For MultiLineString, picks the longest component."""
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, LineString):
        return geometry if geometry.length > 0 else None
    if isinstance(geometry, MultiLineString):
        parts = [g for g in geometry.geoms if g.length > 0]
        if not parts:
            return None
        return max(parts, key=lambda g: g.length)
    return None


def sample_raster_at_points(line, raster_src):
    """Sample a rasterio dataset along a projected LineString.
    Returns mean of valid sampled values, or None."""
    if line is None or line.is_empty or line.length == 0:
        return None

    length = line.length
    if length == 0:
        points = [line.interpolate(0)]
    else:
        points = [
            line.interpolate(length * i / max(NUM_SAMPLES - 1, 1))
            for i in range(NUM_SAMPLES)
        ]

    values = []
    for pt in points:
        try:
            row, col = rowcol(raster_src.transform, pt.x, pt.y)
            if 0 <= row < raster_src.height and 0 <= col < raster_src.width:
                val = raster_src.read(
                    1,
                    window=((row, row + 1), (col, col + 1)),
                    masked=True,
                )[0, 0]
                if not np.ma.is_masked(val):
                    v = float(val)
                    if math.isfinite(v) and v != raster_src.nodata:
                        values.append(v)
        except Exception:
            continue

    return float(np.mean(values)) if values else None


def drain_proximity_score(line_midpoint, drain_union_geom) -> float:
    """
    Returns a 0–1 drain-proximity score.
    1.0 = road midpoint ON a drain channel.
    0.0 = road midpoint ≥ MAX_DRAIN_M from nearest drain.
    """
    if drain_union_geom is None or drain_union_geom.is_empty:
        return 0.0
    dist = line_midpoint.distance(drain_union_geom)
    clamped = max(0.0, min(dist, MAX_DRAIN_M))
    return round(1.0 - clamped / MAX_DRAIN_M, 4)


def lonlat_coords(line, transformer):
    """Return [[lon, lat], ...] for the LineString vertices."""
    coords = []
    for x, y, *_ in line.coords:
        lon, lat = transformer.transform(x, y)
        coords.append([round(float(lon), 6), round(float(lat), 6)])
    return coords


# ============================================================
# MAIN
# ============================================================

print("=" * 60)
print("BUILDING RISK LOOKUP — KORAMANGALA")
print("=" * 60)

# ---- Check inputs ----
for p in [ROAD_FILE, SUSCEPTIBILITY_FILE, DRAIN_FILE, RAINFALL_FILE]:
    if not p.exists():
        raise FileNotFoundError(f"Required file missing: {p}")


# ---- Bounding box in UTM (EPSG:32643) ----
t_to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
x_min, y_min = t_to_utm.transform(KORM_BBOX_WGS84[0], KORM_BBOX_WGS84[1])
x_max, y_max = t_to_utm.transform(KORM_BBOX_WGS84[2], KORM_BBOX_WGS84[3])
korm_box = box(x_min, y_min, x_max, y_max)
print(f"\nKoramangala UTM bbox: [{x_min:.0f}, {y_min:.0f}, {x_max:.0f}, {y_max:.0f}]")


# ---- Load & clip roads ----
print("\nLoading roads...")
roads = gpd.read_file(ROAD_FILE)   # already EPSG:32643
print(f"  Total road features: {len(roads)}")

# Clip to Koramangala bbox
roads = roads[roads.geometry.intersects(korm_box)].copy()
print(f"  After bbox clip: {len(roads)}")

# Filter by highway type
if "highway" in roads.columns:
    roads = roads[roads["highway"].isin(INCLUDE_HIGHWAY)].copy()
    print(f"  After highway filter: {len(roads)}")
else:
    print("  WARNING: no 'highway' column — using all features")

if roads.empty:
    raise ValueError("No road features remain after filtering. Check KORM_BBOX_WGS84.")


# ---- Open susceptibility raster ----
print("\nOpening susceptibility raster...")
susc_raster = rasterio.open(SUSCEPTIBILITY_FILE)
print(f"  CRS: {susc_raster.crs},  size: {susc_raster.width}×{susc_raster.height}")


# ---- Load drain geometry (union for fast proximity) ----
print("\nLoading drain network...")
drains = gpd.read_file(DRAIN_FILE)
print(f"  Drain features: {len(drains)}")
if drains.crs and str(drains.crs) != str(susc_raster.crs):
    drains = drains.to_crs(susc_raster.crs)
drain_union = drains.geometry.union_all() if not drains.empty else None
print(f"  Drain union geom valid: {drain_union is not None and not drain_union.is_empty}")


# ---- Transformer for output coordinates ----
t_to_wgs = Transformer.from_crs(susc_raster.crs, "EPSG:4326", always_xy=True)


# ---- Reproject roads to raster CRS if needed ----
if str(roads.crs) != str(susc_raster.crs):
    roads = roads.to_crs(susc_raster.crs)


# ---- Per-segment static risk ----
print("\nComputing per-segment static risk + drain proximity...")

segments = []
skipped = 0

for _, row in roads.iterrows():
    line = to_linestring(row.geometry)
    if line is None:
        skipped += 1
        continue

    # Susceptibility (0–1, from script 03 weighted overlay)
    static_susc = sample_raster_at_points(line, susc_raster)
    if static_susc is None:
        skipped += 1
        continue
    static_susc = max(0.0, min(1.0, static_susc))

    # Drain proximity (0–1)
    midpoint = line.interpolate(0.5, normalized=True)
    d_prox = drain_proximity_score(midpoint, drain_union)
    drain_m = float(midpoint.distance(drain_union)) if drain_union else 9999.0
    drain_m = round(min(drain_m, 9999.0), 1)

    # Combined base risk
    base_risk = SUSC_W * static_susc + DRAIN_W * d_prox
    base_risk = round(max(0.0, min(1.0, base_risk)), 4)

    # Geometry in lon/lat
    coords = lonlat_coords(line, t_to_wgs)
    if len(coords) < 2:
        skipped += 1
        continue

    seg_id = f"seg_{len(segments) + 1:04d}"

    segments.append({
        "segment_id": seg_id,
        "geometry": coords,
        "base_risk": base_risk,
        "static_susc": round(static_susc, 4),
        "drain_proximity_norm": d_prox,
        "drain_proximity_m": drain_m,
    })

susc_raster.close()

print(f"  Usable segments: {len(segments)}")
print(f"  Skipped: {skipped}")

if not segments:
    raise ValueError("No usable road segments — check bounding box and raster coverage.")

# Summarise base risk spread
base_risks = [s["base_risk"] for s in segments]
print(f"  base_risk range: {min(base_risks):.3f} – {max(base_risks):.3f}")
print(f"  base_risk mean:  {sum(base_risks)/len(base_risks):.3f}")

# Check we have real variation (need this for visible colour differences)
unique_risks = len(set(round(r, 2) for r in base_risks))
print(f"  Distinct risk bands (2 dp): {unique_risks}")


# ---- Read rainfall multiplier ----
print("\nReading rainfall multiplier...")
rainfall = pd.read_csv(RAINFALL_FILE)
required_cols = ["timestamp", "mean_rainfall_mm", "rainfall_multiplier", "horizon_label"]
missing = [c for c in required_cols if c not in rainfall.columns]
if missing:
    raise ValueError(f"Missing rainfall columns: {missing}  (re-run script 04)")

print(f"  Forecast steps: {len(rainfall)}")
for _, r in rainfall.iterrows():
    print(f"  {r['horizon_label']:6s}  ts={r['timestamp']}  "
          f"rain={r['mean_rainfall_mm']:.2f} mm  ×{r['rainfall_multiplier']:.2f}")


# ---- Build lookup ----
print("\nBuilding lookup table...")

timesteps = {}

for _, rain_row in rainfall.iterrows():
    ts        = str(rain_row["timestamp"])
    mult      = float(rain_row["rainfall_multiplier"])
    rain_mm   = float(rain_row["mean_rainfall_mm"])
    horizon   = str(rain_row["horizon_label"])

    step_segs = []
    for seg in segments:
        dynamic_risk = max(0.0, min(1.0, seg["base_risk"] * mult))
        depth_cm     = dynamic_risk * MAX_DEPTH_CM

        step_segs.append({
            "segment_id":         seg["segment_id"],
            "geometry":           seg["geometry"],
            "risk_score":         round(dynamic_risk, 2),
            "predicted_depth_cm": round(depth_cm, 1),
            "confidence":         CONFIDENCE,
            # Metadata for dashboard popup (not in contract, additive)
            "drain_proximity_m":  seg["drain_proximity_m"],
            "mean_rainfall_mm":   round(rain_mm, 2),
            "horizon_label":      horizon,
        })

    timesteps[ts] = step_segs
    print(f"  {horizon:6s}  ts={ts}  segments={len(step_segs)}  "
          f"risk range: {min(s['risk_score'] for s in step_segs):.2f}"
          f"–{max(s['risk_score'] for s in step_segs):.2f}")


# ---- Build final payload ----
payload = {
    "ward_id":      WARD_ID,
    # Tie build metadata to the deterministic input window rather than wall time.
    "generated_at": pd.to_datetime(rainfall["timestamp"]).max().isoformat(),
    "formula": {
        "description": (
            "dynamic_risk = clamp(base_risk × rain_factor, 0, 1)  "
            "where base_risk = 0.70×static_susc + 0.30×drain_prox_norm. "
            "static_susc from script 03 weighted overlay (elevation 0.30, "
            "slope 0.20, dist_to_drain 0.25, imperviousness 0.25). "
            "drain_prox_norm = 1 - clamp(nearest_drain_m, 0, 500)/500. "
            "predicted_depth_cm = dynamic_risk × 30 (illustrative, NOT calibrated). "
            "confidence = 0.60 (GIS-only model)."
        ),
        "weights": {
            "susceptibility": SUSC_W,
            "drain_proximity": DRAIN_W,
            "max_drain_m": MAX_DRAIN_M,
            "max_depth_cm": MAX_DEPTH_CM,
        },
    },
    "timesteps": timesteps,
}


# ---- Save ----
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---- Summary ----
print("\n" + "=" * 60)
print("RISK LOOKUP COMPLETE")
print("=" * 60)
print(f"\nSaved: {OUTPUT_FILE}")
print(f"Timesteps:       {len(timesteps)}")
print(f"Road segments:   {len(segments)}")
print(f"Total records:   {len(timesteps) * len(segments)}")
print(
    "\nFormula: dynamic_risk = clamp("
    f"(0.70 × static_susc + 0.30 × drain_prox_norm) × rain_factor, 0, 1)"
)
print("\nDone.")