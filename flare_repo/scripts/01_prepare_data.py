"""
01_prepare_data.py
==================
Prepares every input layer for Koramangala ward, in UTM 43N (EPSG:32643).

Study area = the real ward polygon (data/raw/ward_boundary.gpkg), plus a
STUDY_BUFFER_M margin for the layers whose influence crosses the boundary
(drains, terrain). Changed 31 Oct 2026, after scripts/00_check_extents.py
showed the old inputs barely touched the ward:

  roads   : now the ward's own streets from the cached OSM drive graph
            (data/processed/koramangala_drive.graphml, the same network the
            routing service uses). The old roads.gpkg was a Bellandur-side
            extract with 0% coverage of the western ward.
  drains  : now BBMP's city storm-water-drain network (data/raw/
            bengaluru_swd_2022.kml) clipped to the study area. The old
            drains.gpkg was the Bellandur SWD network, entirely outside the ward.
  DEM     : now cropped to the study area, so script 03 normalises slope,
            elevation and drain distance WITHIN Koramangala rather than
            across ~25 km of the city (which flattened the ward's variation).
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import rasterio
from shapely.geometry import box
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.warp import calculate_default_transform, reproject
from rasterio.enums import Resampling


# --------------------------------------------------
# Paths
# --------------------------------------------------

RAW = Path("data/raw")
PROCESSED = Path("data/processed")

PROCESSED.mkdir(parents=True, exist_ok=True)

# Target projected CRS: UTM Zone 43N
TARGET_CRS = "EPSG:32643"

# Margin around the ward for drains and terrain: a road near the boundary
# should still "see" the drain or low ground just across it.
STUDY_BUFFER_M = 1000

# A road segment belongs to the ward if its midpoint is inside the ward
# polygon grown by this much (keeps boundary roads like 80 Feet Road).
ROAD_MIDPOINT_TOLERANCE_M = 15

ROAD_GRAPH = PROCESSED / "koramangala_drive.graphml"
CITY_DRAINS = RAW / "bengaluru_swd_2022.kml"


# --------------------------------------------------
# Helper: save vector layer
# --------------------------------------------------

def prepare_vector(input_file, output_file, layer_name, ward):
    print(f"\nProcessing {input_file}...")

    gdf = gpd.read_file(input_file)

    print(f"  Original features: {len(gdf)}")
    print(f"  Original CRS: {gdf.crs}")

    # Reproject to target CRS
    gdf = gdf.to_crs(TARGET_CRS)

    # Clip to ward
    gdf = gpd.clip(gdf, ward)

    print(f"  Features after clipping: {len(gdf)}")

    # Save
    gdf.to_file(
        output_file,
        driver="GPKG",
        layer=layer_name
    )

    print(f"  Saved: {output_file}")


# --------------------------------------------------
# 1. Read Ward 186
# --------------------------------------------------

print("========================================")
print("PAIR 1 DATA PREPARATION")
print("Koramangala Ward 186")
print("========================================")

ward = gpd.read_file(
    RAW / "ward_boundary.gpkg"
)

print("\nWard boundary:")
print("  Features:", len(ward))
print("  CRS:", ward.crs)

ward = ward.to_crs(TARGET_CRS)

ward_geom = ward.geometry.union_all()
study_area = gpd.GeoDataFrame(
    geometry=[ward_geom.buffer(STUDY_BUFFER_M)],
    crs=TARGET_CRS,
)
print(f"  Study area: ward + {STUDY_BUFFER_M} m, {study_area.area.iloc[0] / 1e6:.1f} km2")

# Save processed ward
ward.to_file(
    PROCESSED / "ward.gpkg",
    driver="GPKG",
    layer="ward"
)

print("  Saved: data/processed/ward.gpkg")


# --------------------------------------------------
# 2. Drains (BBMP city storm-water-drain network)
# --------------------------------------------------

print(f"\nProcessing {CITY_DRAINS}...")
drains = gpd.read_file(CITY_DRAINS)
print(f"  Original features: {len(drains)}  CRS: {drains.crs}")
if drains.crs is None:                       # KML is always WGS84
    drains = drains.set_crs("EPSG:4326")
drains = drains.to_crs(TARGET_CRS)
drains["geometry"] = drains.geometry.force_2d()   # KML carries Z=0
drains = drains[drains.geometry.notna() & ~drains.geometry.is_empty]
drains = gpd.clip(drains, study_area)
drains = drains[~drains.geometry.is_empty]
# Keep only simple columns; KML extras (descriptions, styles) break GPKG writes.
keep = [c for c in ("Name", "name") if c in drains.columns]
drains = drains[keep + ["geometry"]]
print(f"  Features in study area: {len(drains)}  "
      f"(inside the ward itself: {int(drains.intersects(ward_geom).sum())})")
if drains.empty:
    raise ValueError("No city drains in the study area - check bengaluru_swd_2022.kml")
drains.to_file(PROCESSED / "drains_ward.gpkg", driver="GPKG", layer="drains")
print("  Saved: data/processed/drains_ward.gpkg")


# --------------------------------------------------
# 3. Roads (the ward's streets from the OSM drive graph)
# --------------------------------------------------

def first_value(value):
    """OSMnx stores merged attributes as lists; keep one plain value."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


print(f"\nProcessing {ROAD_GRAPH}...")
if not ROAD_GRAPH.exists():
    raise FileNotFoundError(f"{ROAD_GRAPH} missing - run scripts/08_cache_osm_graph.py first")
graph = ox.convert.to_undirected(ox.load_graphml(ROAD_GRAPH))   # one segment per street piece
edges = ox.graph_to_gdfs(graph, nodes=False).reset_index().to_crs(TARGET_CRS)
print(f"  Graph street segments: {len(edges)}")

midpoints = edges.geometry.interpolate(0.5, normalized=True)
in_ward = midpoints.within(ward_geom.buffer(ROAD_MIDPOINT_TOLERANCE_M))
roads = edges[in_ward.values].copy()

roads = gpd.GeoDataFrame({
    "osmid": roads["osmid"].map(lambda v: str(first_value(v))),
    "highway": roads["highway"].map(first_value).astype(str),
    "name": roads.get("name", gpd.pd.Series(index=roads.index, dtype=object)).map(first_value),
    "length_m": roads["length"].astype(float).round(1),
}, geometry=roads.geometry.values, crs=TARGET_CRS)
roads["name"] = roads["name"].where(roads["name"].notna(), None)
print(f"  Segments inside the ward: {len(roads)} "
      f"({roads['length_m'].sum() / 1000:.1f} km of street)")
print("  By type:", roads["highway"].value_counts().to_dict())
roads.to_file(PROCESSED / "roads_ward.gpkg", driver="GPKG", layer="roads")
print("  Saved: data/processed/roads_ward.gpkg")


# --------------------------------------------------
# 4. Buildings
# --------------------------------------------------

prepare_vector(
    RAW / "buildings.gpkg",
    PROCESSED / "buildings.gpkg",
    "buildings",
    ward
)


# --------------------------------------------------
# 5. Flood points
# --------------------------------------------------

prepare_vector(
    PROCESSED / "flood_points_final_clean.gpkg",
    PROCESSED / "flood_points.gpkg",
    "flood_points",
    ward
)


# --------------------------------------------------
# 6. DEM
# --------------------------------------------------

print("\nProcessing DEM...")

dem_path = PROCESSED / "dem.tif"
output_dem = PROCESSED / "dem_utm.tif"

with rasterio.open(dem_path) as src:

    print("  Original CRS:", src.crs)
    print("  Original size:", src.width, src.height)

    transform, width, height = calculate_default_transform(
        src.crs,
        TARGET_CRS,
        src.width,
        src.height,
        *src.bounds
    )

    profile = src.profile.copy()

    profile.update(
        {
            "crs": TARGET_CRS,
            "transform": transform,
            "width": width,
            "height": height
        }
    )

    # Reproject in memory, then crop to the study area before saving.
    with MemoryFile() as memfile:
        with memfile.open(**profile) as tmp:
            for band in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, band),
                    destination=rasterio.band(tmp, band),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=TARGET_CRS,
                    resampling=Resampling.bilinear
                )

        with memfile.open() as tmp:
            # A rectangle, so every cell keeps real terrain (no masked corners).
            study_box = box(*study_area.total_bounds)
            cropped, crop_transform = mask(tmp, [study_box], crop=True, filled=False)
            nodata = tmp.nodata if tmp.nodata is not None else -9999
            cropped = np.ma.filled(cropped, nodata)
            crop_profile = tmp.profile.copy()
            crop_profile.update(
                height=cropped.shape[1], width=cropped.shape[2],
                transform=crop_transform, nodata=nodata,
            )

    with rasterio.open(output_dem, "w", **crop_profile) as dst:
        dst.write(cropped)
    print("  Cropped to study area:", crop_profile["width"], "x", crop_profile["height"], "px")

print("  Saved:", output_dem)

print("\n========================================")
print("DATA PREPARATION COMPLETE")
print("========================================")