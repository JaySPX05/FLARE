"""
Generate flood_points_koramangala.geojson from the already-processed,
ward-clipped flood points layer (data/processed/flood_points.gpkg).

This is the file backend-api/data_loader.py's get_flood_points() expects
to find at data/processed/flood_points_<ward_id>.geojson - it's what
powers the dashboard's real "Known flood points" data (as opposed to
the synthetic rain-gauge markers currently shown instead).

flood_points.gpkg was already reprojected to EPSG:32643 (UTM Zone 43N)
and clipped to the ward boundary by scripts/01_prepare_data.py. GeoJSON
and Leaflet expect plain lat/lng (EPSG:4326), so this script reprojects
back before writing.

Run from the repo root:
    python scripts/06_generate_flood_points_geojson.py
"""

import json
from pathlib import Path

import geopandas as gpd

PROCESSED = Path("data/processed")
INPUT_FILE = PROCESSED / "flood_points.gpkg"
OUTPUT_FILE = PROCESSED / "flood_points_koramangala.geojson"


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {INPUT_FILE}. Run scripts/01_prepare_data.py "
            "first to generate the ward-clipped flood points layer."
        )

    # This file has multiple layers (the raw KML-derived source plus the
    # actual ward-clipped output) - must specify the correct one explicitly,
    # or geopandas silently defaults to whichever layer happens to be first,
    # which is the wrong, unclipped one.
    gdf = gpd.read_file(INPUT_FILE, layer="flood_points")
    print(f"Loaded {len(gdf)} flood points from {INPUT_FILE}")
    print(f"Original CRS: {gdf.crs}")
    print(f"Columns available: {list(gdf.columns)}")

    # Reproject back to WGS84 lat/lng for GeoJSON / Leaflet.
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
        print("Reprojected to EPSG:4326")

    # Drop rows with missing/invalid geometry, just in case.
    gdf = gdf[gdf.geometry.notnull()]
    print(f"{len(gdf)} points remain after dropping any null geometries")

    # Write as a standard GeoJSON FeatureCollection. geopandas handles
    # the coordinate formatting and properties (every non-geometry
    # column) automatically.
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUTPUT_FILE, driver="GeoJSON")

    # Sanity-check what actually got written.
    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    feature_count = len(written.get("features", []))
    print(f"\nWrote {feature_count} features to {OUTPUT_FILE}")
    if feature_count:
        print("Example feature properties:", written["features"][0].get("properties"))
        first_coords = written["features"][0]["geometry"]["coordinates"]
        print("Example coordinates (should be [lng, lat] in Bengaluru's range,")
        print(f"lng ~77.x, lat ~12.x): {first_coords}")


if __name__ == "__main__":
    main()