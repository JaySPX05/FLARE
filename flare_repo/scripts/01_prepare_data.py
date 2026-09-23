from pathlib import Path

import geopandas as gpd
import rasterio
from shapely.geometry import box
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

# The source ward polygon does not overlap the supplied OSM/drain layers
# consistently. Keep one reproducible pilot-study extent for those layers.
STUDY_BBOX_WGS84 = (77.618, 12.918, 77.650, 12.953)


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

study_area = gpd.GeoDataFrame(
    geometry=[box(*STUDY_BBOX_WGS84)],
    crs="EPSG:4326",
).to_crs(TARGET_CRS)

# Save processed ward
ward.to_file(
    PROCESSED / "ward.gpkg",
    driver="GPKG",
    layer="ward"
)

print("  Saved: data/processed/ward.gpkg")


# --------------------------------------------------
# 2. Drains
# --------------------------------------------------

prepare_vector(
    PROCESSED / "drains.gpkg",
    PROCESSED / "drains_ward.gpkg",
    "drains",
    study_area
)


# --------------------------------------------------
# 3. Roads
# --------------------------------------------------

prepare_vector(
    PROCESSED / "roads.gpkg",
    PROCESSED / "roads_ward.gpkg",
    "roads",
    study_area
)


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

    with rasterio.open(output_dem, "w", **profile) as dst:

        for band in range(1, src.count + 1):

            reproject(
                source=rasterio.band(src, band),
                destination=rasterio.band(dst, band),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=TARGET_CRS,
                resampling=Resampling.bilinear
            )

print("  Saved:", output_dem)

print("\n========================================")
print("DATA PREPARATION COMPLETE")
print("========================================")