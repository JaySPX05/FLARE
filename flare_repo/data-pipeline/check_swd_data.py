#!/usr/bin/env python3
"""
check_swd_data.py — Verify whether the BBMP stormwater drain data is usable
for a chosen pilot ward BEFORE committing two days to it.

Run this FIRST, in Hour 0-1. It is cheap now and expensive on Day 1 Hour 5.

Usage:
    pip install geopandas shapely fiona
    python check_swd_data.py

What it tells you:
    1. Is the SWD file real vector geometry, or just a scanned PDF/raster?
    2. How many drain segments actually fall inside your candidate ward?
    3. Do the segments carry the ATTRIBUTES hydraulic modelling needs
       (width, depth, type) or only geometry?
    4. Which candidate ward has the best coverage -> pick that one.
"""

import sys
import geopandas as gpd
import pandas as pd

# ---------------------------------------------------------------------------
# STEP 0 — Download these by hand first (browser), put them in ./data/raw/
#
#   Citywide SWD layer (all primary/secondary/tertiary, has a `type` field):
#     https://data.opencity.in/dataset/bengaluru-stormwater-drains-maps
#     -> "Bengaluru Stormwater Drains Map 2022"
#
#   BBMP ward boundaries:
#     https://data.opencity.in/dataset/bbmp-ward-information
#     -> ward map KML (243-ward 2022 version)
#
# NOTE: OpenCity serves these as .kml in most cases. If the only Yelahanka-
# specific resource you can find is a PDF, that is the answer to question 1:
# it is NOT usable geometry, and you must clip the citywide layer instead.
# ---------------------------------------------------------------------------

SWD_PATH = "data/raw/bengaluru_swd_2022.kml"
WARDS_PATH = "data/raw/bbmp-wards-map.kml"

# Candidate pilot wards, in the order we're considering them.
CANDIDATES = ["Yelahanka", "Koramangala", "Bellandur", "Hoodi", "HSR Layout"]

# A working metric CRS for Bengaluru (UTM zone 43N) so lengths come out in metres
METRIC_CRS = "EPSG:32643"


def load(path, label):
    try:
        gdf = gpd.read_file(path)
    except Exception as e:
        print(f"  ✗ FAILED to read {label} at {path}")
        print(f"    {type(e).__name__}: {e}")
        print(f"    -> If this is a PDF, it is NOT usable geometry.")
        sys.exit(1)
    print(f"  ✓ Loaded {label}: {len(gdf)} features, CRS={gdf.crs}")
    print(f"    Geometry types: {gdf.geom_type.value_counts().to_dict()}")
    print(f"    Columns: {list(gdf.columns)}")
    return gdf


def main():
    print("=" * 70)
    print("STEP 1 — Can we even read the files as vector geometry?")
    print("=" * 70)
    swd = load(SWD_PATH, "stormwater drains")
    wards = load(WARDS_PATH, "ward boundaries")

    print()
    print("=" * 70)
    print("STEP 2 — Do the drains carry hydraulic attributes?")
    print("=" * 70)
    # SWMM needs width/depth/slope/type. Geometry alone is not enough.
    wanted = ["type", "width", "depth", "slope", "name", "zone"]
    cols_lower = {c.lower(): c for c in swd.columns}
    found, missing = [], []
    for w in wanted:
        hit = [orig for low, orig in cols_lower.items() if w in low]
        (found if hit else missing).append(f"{w} -> {hit}" if hit else w)
    for f in found:
        print(f"  ✓ {f}")
    for m in missing:
        print(f"  ✗ missing: {m}")
    if any("width" in str(f) for f in found) or any("depth" in str(f) for f in found):
        print("  => Some hydraulic attributes present. SWMM is *maybe* feasible (Phase 2).")
    else:
        print("  => Geometry only, no width/depth. This CONFIRMS the 2-day decision")
        print("     to cut SWMM and use the GIS susceptibility overlay instead.")

    # Show what values the `type` field actually takes, if present
    type_col = next((orig for low, orig in cols_lower.items() if "type" in low), None)
    if type_col:
        print(f"\n  '{type_col}' value counts:")
        print(swd[type_col].value_counts().to_string())

    print()
    print("=" * 70)
    print("STEP 3 — Coverage per candidate ward (THE DECIDING NUMBER)")
    print("=" * 70)

    # Find the ward-name column. KML files always carry a generic 'Name'
    # column that is often empty, so try candidates and keep the first one
    # that actually matches a ward we're looking for.
    candidate_cols = [
        c for c in wards.columns
        if any(k in c.lower() for k in ("ward", "name", "kgis", "division"))
    ]
    # Prefer more specific columns over the generic KML 'Name'
    candidate_cols.sort(key=lambda c: (c.lower() == "name", c.lower() == "description"))

    name_col = None
    for c in candidate_cols:
        vals = wards[c].astype(str)
        if any(vals.str.contains(cand, case=False, na=False).any() for cand in CANDIDATES):
            name_col = c
            break

    if name_col is None:
        print("  ✗ Could not find a ward-name column matching any candidate ward.")
        print(f"    Columns available: {list(wards.columns)}")
        print("    Sample of each text column:")
        for c in wards.columns:
            if wards[c].dtype == object and c != "geometry":
                sample = wards[c].dropna().astype(str).head(3).tolist()
                if sample:
                    print(f"      {c}: {sample}")
        print("\n    -> Open the file in QGIS, find which field holds the ward name,")
        print("       and set name_col manually before re-running.")
        sys.exit(1)

    print(f"  Using ward name column: '{name_col}'\n")

    swd_m = swd.to_crs(METRIC_CRS)
    wards_m = wards.to_crs(METRIC_CRS)

    rows = []
    for cand in CANDIDATES:
        match = wards_m[wards_m[name_col].astype(str).str.contains(cand, case=False, na=False)]
        if match.empty:
            rows.append({"ward": cand, "found": False, "segments": 0, "km": 0.0})
            continue
        poly = match.geometry.union_all() if hasattr(match.geometry, "union_all") \
            else match.geometry.unary_union
        clipped = swd_m[swd_m.intersects(poly)].copy()
        clipped["geometry"] = clipped.geometry.intersection(poly)
        clipped = clipped[~clipped.geometry.is_empty]
        km = clipped.geometry.length.sum() / 1000.0
        rows.append({
            "ward": cand,
            "found": True,
            "segments": len(clipped),
            "km": round(km, 2),
        })

    df = pd.DataFrame(rows).sort_values("km", ascending=False)
    print(df.to_string(index=False))

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    usable = df[(df["found"]) & (df["segments"] >= 20)]
    if usable.empty:
        print("  ✗ No candidate ward has >=20 drain segments.")
        print("    The citywide layer may be too sparse. Fall back to:")
        print("      - a hand-built synthetic drain graph for the demo, OR")
        print("      - pick the ward by flood-point density instead of drain density")
    else:
        best = usable.iloc[0]
        print(f"  ✓ Best coverage: {best['ward']} "
              f"({best['segments']} segments, {best['km']} km)")
        print(f"    -> Recommend making this the pilot ward.")
        if best["ward"] != "Yelahanka":
            y = df[df["ward"] == "Yelahanka"]
            if not y.empty:
                yr = y.iloc[0]
                print(f"    NOTE: Yelahanka has {yr['segments']} segments / {yr['km']} km "
                      f"— weaker than {best['ward']}.")
                print("    Combined with Yelahanka having only ~11 of the 211 BBMP")
                print("    flood-vulnerable points, switching is likely the right call.")

    print()
    print("Report this result in the team channel before anyone starts building.")


if __name__ == "__main__":
    main()
