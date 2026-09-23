import os
import numpy as np
import rasterio


# ============================================================
# PATHS
# ============================================================

DATA_DIR = "data/processed"

DEM = os.path.join(DATA_DIR, "dem_utm.tif")
SLOPE = os.path.join(DATA_DIR, "slope.tif")
DISTANCE = os.path.join(DATA_DIR, "distance_to_drain.tif")
IMPERVIOUS = os.path.join(DATA_DIR, "imperviousness.tif")

OUTPUT = os.path.join(DATA_DIR, "susceptibility.tif")


# ============================================================
# WEIGHTS
# ============================================================

ELEVATION_WEIGHT = 0.30
SLOPE_WEIGHT = 0.20
DISTANCE_WEIGHT = 0.25
IMPERVIOUS_WEIGHT = 0.25


# ============================================================
# NORMALIZATION FUNCTION
# ============================================================

def normalize(array):
    """
    Normalize values to 0-1.
    Higher value = higher susceptibility.
    """

    valid = np.isfinite(array)

    minimum = np.nanmin(array)
    maximum = np.nanmax(array)

    if maximum == minimum:
        return np.zeros_like(array, dtype="float32")

    result = (array - minimum) / (maximum - minimum)

    result[~valid] = np.nan

    return result.astype("float32")


# ============================================================
# READ RASTER
# ============================================================

print("=" * 55)
print("STATIC SUSCEPTIBILITY MODEL")
print("=" * 55)

print("\nReading raster layers...")

with rasterio.open(DEM) as src:
    elevation = src.read(1).astype("float32")
    profile = src.profile.copy()

with rasterio.open(SLOPE) as src:
    slope = src.read(1).astype("float32")

with rasterio.open(DISTANCE) as src:
    distance = src.read(1).astype("float32")

with rasterio.open(IMPERVIOUS) as src:
    impervious = src.read(1).astype("float32")


# ============================================================
# HANDLE NODATA
# ============================================================

def clean_nodata(array, nodata):
    if nodata is not None:
        array[array == nodata] = np.nan
    return array


with rasterio.open(DEM) as src:
    elevation = clean_nodata(elevation, src.nodata)

with rasterio.open(SLOPE) as src:
    slope = clean_nodata(slope, src.nodata)

with rasterio.open(DISTANCE) as src:
    distance = clean_nodata(distance, src.nodata)

with rasterio.open(IMPERVIOUS) as src:
    impervious = clean_nodata(impervious, src.nodata)


print("Raster dimensions:", elevation.shape)


# ============================================================
# NORMALIZE FACTORS
# ============================================================

print("\nNormalizing factors...")

# Elevation:
# Lower elevation = higher flood susceptibility
elevation_norm = normalize(elevation)
elevation_risk = 1.0 - elevation_norm

# Slope:
# Lower slope = higher flood susceptibility
slope_norm = normalize(slope)
slope_risk = 1.0 - slope_norm

# Distance:
# Closer to drain = higher susceptibility
distance_norm = normalize(distance)
distance_risk = 1.0 - distance_norm

# Imperviousness:
# Higher imperviousness = higher susceptibility
impervious_risk = normalize(impervious)


# ============================================================
# WEIGHTED OVERLAY
# ============================================================

print("\nApplying weighted overlay...")

susceptibility = (
    elevation_risk * ELEVATION_WEIGHT
    + slope_risk * SLOPE_WEIGHT
    + distance_risk * DISTANCE_WEIGHT
    + impervious_risk * IMPERVIOUS_WEIGHT
)


# ============================================================
# SAVE RESULT
# ============================================================

susceptibility = susceptibility.astype("float32")

profile.update(
    dtype="float32",
    count=1,
    nodata=-9999,
    compress="deflate"
)

output_array = np.where(
    np.isfinite(susceptibility),
    susceptibility,
    -9999
)

with rasterio.open(OUTPUT, "w", **profile) as dst:
    dst.write(output_array, 1)


# ============================================================
# SUMMARY
# ============================================================

valid_values = susceptibility[np.isfinite(susceptibility)]

print("\nSusceptibility statistics:")
print("Minimum:", float(np.min(valid_values)))
print("Maximum:", float(np.max(valid_values)))
print("Mean:", float(np.mean(valid_values)))

print("\nWeights:")
print("Elevation:", ELEVATION_WEIGHT)
print("Slope:", SLOPE_WEIGHT)
print("Distance to drain:", DISTANCE_WEIGHT)
print("Imperviousness:", IMPERVIOUS_WEIGHT)

print("\nSaved:")
print(OUTPUT)

print("\n" + "=" * 55)
print("STATIC SUSCEPTIBILITY COMPLETE")
print("=" * 55)