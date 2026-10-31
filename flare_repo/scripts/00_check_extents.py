"""
00_check_extents.py
===================
Read-only check: does each GIS input actually cover Koramangala ward?

The 29-Oct dashboard showed the risk segments sitting mostly EAST of the ward
boundary (towards Ejipura/Bellandur). This script reports, for every input
the pipeline uses, its extent and how much of the ward it covers, so we know
which inputs must be re-cut before rebuilding the lookup. Nothing is written.

Run from flare_repo/ (same environment as scripts 01-05):
    python scripts/00_check_extents.py

"Ward coverage" = share of a 200 m grid over the ward polygon whose cells
contain at least one feature (vectors) or one valid pixel (rasters).
"""

from pathlib import Path

import numpy as np

import geopandas as gpd
from shapely.geometry import box

try:
    import rasterio
    from rasterio.mask import mask as rio_mask
except ImportError:  # pragma: no cover
    rasterio = None

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
UTM = "EPSG:32643"
CELL_M = 200

VECTORS = [
    ("ward boundary (raw)", RAW / "ward_boundary.gpkg"),
    ("roads (processed/roads.gpkg)", PROC / "roads.gpkg"),
    ("roads clipped (roads_ward.gpkg)", PROC / "roads_ward.gpkg"),
    ("drains (processed/drains.gpkg)", PROC / "drains.gpkg"),
    ("drains clipped (drains_ward.gpkg)", PROC / "drains_ward.gpkg"),
    ("city SWD drains (raw KML)", RAW / "bengaluru_swd_2022.kml"),
    ("buildings (raw)", RAW / "buildings.gpkg"),
    ("buildings (processed)", PROC / "buildings.gpkg"),
    ("flood points (processed)", PROC / "flood_points.gpkg"),
]
RASTERS = [
    ("DEM (dem.tif)", PROC / "dem.tif"),
    ("DEM UTM (dem_utm.tif)", PROC / "dem_utm.tif"),
    ("slope.tif", PROC / "slope.tif"),
    ("distance_to_drain.tif", PROC / "distance_to_drain.tif"),
    ("imperviousness.tif", PROC / "imperviousness.tif"),
    ("susceptibility.tif", PROC / "susceptibility.tif"),
]


def ward_polygon():
    ward = gpd.read_file(RAW / "ward_boundary.gpkg").to_crs(UTM)
    return ward.geometry.union_all()


def ward_cells(ward):
    xmin, ymin, xmax, ymax = ward.bounds
    cells = []
    for x in np.arange(xmin, xmax, CELL_M):
        for y in np.arange(ymin, ymax, CELL_M):
            c = box(x, y, x + CELL_M, y + CELL_M)
            if c.intersects(ward):
                cells.append(c.intersection(ward))
    return gpd.GeoSeries(cells, crs=UTM)


def wgs_bounds(geoms):
    b = gpd.GeoSeries(geoms, crs=UTM).to_crs("EPSG:4326").total_bounds
    return f"lon {b[0]:.4f}-{b[2]:.4f}  lat {b[1]:.4f}-{b[3]:.4f}"


def west_east(ward, covered_cells):
    """Coverage of the ward's western vs eastern half - the suspected gap."""
    mid = (ward.bounds[0] + ward.bounds[2]) / 2
    xs = np.array([c.centroid.x for c in covered_cells.geometry]) if len(covered_cells) else np.array([])
    return int((xs < mid).sum()), int((xs >= mid).sum())


def check_vector(label, path, ward, cells):
    if not path.exists() or path.stat().st_size < 1000:
        print(f"  {label:<36} MISSING (or a git-LFS placeholder)")
        return
    try:
        gdf = gpd.read_file(path)
    except Exception as e:  # noqa: BLE001
        print(f"  {label:<36} could not read: {e}")
        return
    if gdf.empty:
        print(f"  {label:<36} EMPTY")
        return
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs(UTM)
    in_ward = gdf[gdf.intersects(ward)]
    hit = cells[cells.intersects(in_ward.geometry.union_all())] if len(in_ward) else cells.iloc[0:0]
    w, e = west_east(ward, hit)
    total_w, total_e = west_east(ward, cells)
    print(f"  {label:<36} {len(gdf):>6} feat, {len(in_ward):>5} in ward | {wgs_bounds(gdf.geometry)}"
          f" | ward coverage {100 * len(hit) / len(cells):5.1f}%  (west {w}/{total_w}, east {e}/{total_e} cells)")


def check_raster(label, path, ward, cells):
    if rasterio is None:
        print(f"  {label:<36} rasterio not installed")
        return
    if not path.exists() or path.stat().st_size < 1000:
        print(f"  {label:<36} MISSING (or a git-LFS placeholder)")
        return
    with rasterio.open(path) as src:
        crs = src.crs
        b = gpd.GeoSeries([box(*src.bounds)], crs=crs)
        extent = wgs_bounds(b.to_crs(UTM))
        hit = []
        for cell in cells.to_crs(crs):
            try:
                data, _ = rio_mask(src, [cell], crop=True, filled=False)
            except ValueError:  # cell outside raster
                continue
            arr = data[0]
            valid = ~np.ma.getmaskarray(arr)
            vals = np.ma.getdata(arr)
            valid &= np.isfinite(vals)
            if src.nodata is not None:
                valid &= vals != src.nodata
            if valid.any():
                hit.append(cell)
        hit = gpd.GeoSeries(hit, crs=crs).to_crs(UTM)
        w, e = west_east(ward, hit)
        total_w, total_e = west_east(ward, cells)
        print(f"  {label:<36} {src.width}x{src.height} px, {crs} | {extent}"
              f" | ward coverage {100 * len(hit) / len(cells):5.1f}%  (west {w}/{total_w}, east {e}/{total_e} cells)")


def check_lookup(ward, cells):
    import json
    path = PROC / "risk_lookup_koramangala.json"
    if not path.exists():
        return
    segs = next(iter(json.loads(path.read_text())["timesteps"].values()))
    from shapely.geometry import LineString
    lines = gpd.GeoSeries([LineString(s["geometry"]) for s in segs], crs="EPSG:4326").to_crs(UTM)
    in_ward = lines[lines.intersects(ward)]
    hit = cells[cells.intersects(in_ward.union_all())] if len(in_ward) else cells.iloc[0:0]
    w, e = west_east(ward, hit)
    total_w, total_e = west_east(ward, cells)
    print(f"  {'risk lookup segments (current)':<36} {len(lines):>6} seg,  {len(in_ward):>5} in ward | "
          f"{wgs_bounds(lines)} | ward coverage {100 * len(hit) / len(cells):5.1f}%  "
          f"(west {w}/{total_w}, east {e}/{total_e} cells)")


def main():
    ward = ward_polygon()
    cells = ward_cells(ward)
    print(f"Ward: {wgs_bounds([ward])}, {ward.area / 1e6:.2f} km2, {len(cells)} grid cells of {CELL_M} m\n")
    print("VECTORS")
    for label, path in VECTORS:
        check_vector(label, path, ward, cells)
    print("\nRASTERS")
    for label, path in RASTERS:
        check_raster(label, path, ward, cells)
    print("\nOUTPUT")
    check_lookup(ward, cells)
    print("\nAnything well under 100% ward coverage (especially 'west 0/...') is an input that "
          "has to be re-cut or re-sourced before the lookup can cover all of Koramangala.")


if __name__ == "__main__":
    main()
