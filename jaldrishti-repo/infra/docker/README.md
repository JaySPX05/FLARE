# Running FLARE in containers

Three runtime images plus one on-demand pipeline image. All builds run from the **repo root**.

| Image | What it is | Port |
|---|---|---|
| `api` | Risk API (`backend-api/main.py`) | 8000 |
| `routing` | Flood-aware routing API (`routing_api.py` + `routing.py`) | 8001 |
| `frontend` | nginx: serves `index.html`, proxies `/api/` -> api and `/routing/` -> routing | 8080 |
| `pipeline` | Offline GIS + nowcasting scripts 01-08 (not started by default) | - |

## Run the whole app
```powershell
docker compose up --build
# open http://localhost:8080
```
Stop with `Ctrl+C`, remove with `docker compose down`.

## Check that it works
In a second terminal, once `docker compose up` shows all three services running:
```powershell
./infra/docker/smoke-test.ps1
```
It checks the dashboard, the Risk API, the flood points, the routing API and a real route through
nginx, prints PASS/FAIL for each, and exits non-zero if anything fails.
(Routing takes a little longer to become healthy - it loads the road graph on start-up.)

## One-time: save the road graph into the image
Without this the routing container downloads the network from OpenStreetMap every start
(slow, and breaks if Overpass is down). Do it once, commit the file, rebuild:
```powershell
python scripts/08_cache_osm_graph.py           # or: docker compose run --rm pipeline scripts/08_cache_osm_graph.py
git add data/processed/koramangala_drive.graphml
docker compose up --build
```

## Run a pipeline script (needs ./data populated, incl. the Git-LFS files)
```powershell
docker compose run --rm pipeline scripts/04_rainfall_multiplier.py
```
Then rebuild `api` and `routing` so they pick up the new `risk_lookup_*.json`
(or mount `./data` into them as a volume while developing).

## Data baked into the images
`data/processed/risk_lookup_*.json`, `flood_points_*.geojson`, `*.graphml` (small, plain files).
Raw data, rasters and GeoPackages are never sent to Docker (see `.dockerignore`).

## Configuration
| Variable | Service | Default | Meaning |
|---|---|---|---|
| `API_UPSTREAM` | frontend | `http://api:8000` | where nginx sends `/api/` |
| `ROUTING_UPSTREAM` | frontend | `http://routing:8001` | where nginx sends `/routing/` |
| `OSM_GRAPH_PATH` | routing | `/app/data/processed/koramangala_drive.graphml` | saved road graph |

The dashboard calls `localhost:8000/8001` directly when opened at port 5500 or as a file
(the old `python -m http.server 5500` workflow); everywhere else it uses same-origin `/api` and `/routing`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `port is already allocated` (8000 / 8001 / 8080) | Stop your local `uvicorn` / `http.server` terminals first - the containers use the same ports. |
| `docker: command not found` / daemon not running | Install Docker Desktop (needs WSL 2), start it, wait for "Engine running". |
| Routing container restarts or shows `Killed` | Out of memory. Docker Desktop -> Settings -> Resources: give it 4 GB+. |
| Dashboard loads but shows no flood-point markers | `flood_points_koramangala.geojson` wasn't committed / present at build time. Add it, then `docker compose up --build`. |
| Dashboard says "Can't reach the backend" | `docker compose ps` - is `api` healthy? `docker compose logs api`. |
| Routing is slow on every start | No saved road graph in the image: run `scripts/08_cache_osm_graph.py`, commit, rebuild. |
| Changed code but nothing changed | `docker compose up --build` (images are not rebuilt automatically). |
| Want a clean slate | `docker compose down --rmi local` then `docker compose up --build`. |
