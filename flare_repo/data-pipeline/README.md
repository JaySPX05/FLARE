# data-pipeline — Role A: Data Engineering & GIS Pipeline

**Goal:** Get every dataset the team needs into a clean, common format, in `data/processed/`.

## You own
- Sourcing + cleaning: KSNDMC rainfall, BBMP drain shapefiles, CartoDEM/SRTM, OSM roads/buildings, BBMP's 211 flood points
- Reprojecting all spatial layers to one consistent CRS
- Clipping everything to the pilot ward boundary
- The precomputed risk lookup table that Backend (Role D) serves directly — this is the single biggest time-saver in the 2-day plan

## Where things go
- Raw downloads → `data/raw/` (gitignored — do not commit, see root `.gitignore`)
- Cleaned/processed outputs → `data/processed/` (gitignored too — regenerate via `download_data.sh`, don't rely on committed files)
- Scripts that produce those outputs → this folder, committed normally

## Suggested structure
```
data-pipeline/
  download_data.sh       # pulls raw data into data/raw/
  clean_terrain.py
  clean_drains.py
  build_risk_overlay.py  # static GIS susceptibility index
  precompute_lookup.py   # risk score × rainfall multiplier time series
  requirements.txt
```

## Depends on
Nothing upstream — you're the start of the pipeline.

## Others depend on you for
Everything downstream. Hydrology needs your drain network + DEM. ML needs your rainfall history + flood-point labels. Frontend needs your ward boundary + road network.

## First commit checklist
- [ ] `download_data.sh` runs and populates `data/raw/`
- [ ] One cleaned, reprojected layer committed as a *script*, not as data
- [ ] Confirm the ward you picked with the team in the first hour — see root `README.md`
