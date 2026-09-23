# FLARE — Flood Localization & Alert Real-time Engine

**Real-time, street-level flood risk prediction and flood-aware routing for Koramangala ward, Bengaluru.**
Built for Smart India Hackathon 2026.

> "Predicting the puddle before the rain stops."

---

## What this is

FLARE takes real road, terrain, and drainage data for one Bengaluru ward, combines it with a live rainfall nowcast, and shows which streets are likely to flood over the next three hours — then routes you around them. There are two things running at once:

1. **A risk map** — every street segment colored by predicted flood risk, on a 4-step timeline (now, +1h, +2h, +3h), with real historical flood points and rain gauges overlaid.
2. **Flood-aware routing** — click a start and end point, and get both the shortest route and a route that avoids high-risk segments, computed with a custom Dijkstra over the real road network.

**What's real vs. not yet:** the road network, terrain, drainage proximity, and historical flood points are all real, sourced data. The rainfall nowcast (0–3h) is a genuine PySteps forecast, not a canned number — but it currently extrapolates from a single recorded storm event, not a live feed. The risk score itself is a GIS-based relative indicator (road susceptibility × rainfall), **not** a physically calibrated hydraulic model — that's the planned SWMM integration, gated on getting real pipe-network data from BBMP. Live rainfall access (IMD/KSNDMC) is requested and pending.

---

## Architecture

```
Offline pipeline (scripts/01–08, run manually / periodically)
  Raw data (roads, terrain, drainage, buildings, flood points, rainfall)
    → static per-segment susceptibility score
    → combined with a live rainfall-nowcast multiplier
    → risk_lookup_koramangala.json + flood_points_koramangala.geojson

Live stack (three containers, one docker compose up)
  ┌─────────────┐   /api/      ┌──────────────┐
  │             │ ───────────► │  Risk API    │  serves the precomputed
  │   nginx     │              │  (FastAPI)   │  risk JSON + flood points
  │  (frontend, │   /routing/  ├──────────────┤
  │  port 8080) │ ───────────► │ Routing API  │  real OSM road graph +
  │             │              │  (FastAPI)   │  hand-written Dijkstra
  └─────────────┘              └──────────────┘
        │
        ▼
  Leaflet.js dashboard (single index.html, real OSM tiles)
```

The two backend services are independent on purpose — the Risk API is stateless and fast, the Routing API holds a multi-thousand-node road graph in memory. nginx is the only public entry point, so the browser only ever talks to one origin.

---

## Tech stack

| Layer | Technology | Why (short) |
|---|---|---|
| Backend framework | FastAPI + Uvicorn | Built-in validation, async, auto docs |
| Routing | OSMnx + NetworkX + a **hand-written Dijkstra** | Real road graph; custom risk-weighted cost function |
| Rainfall forecasting | PySteps | Real optical-flow nowcasting, right lead time (0–3h) for street-level flooding |
| GIS pipeline | GeoPandas + Rasterio + Shapely + pyproj | Standard Python geospatial stack for vector + raster data |
| Frontend | Plain HTML/CSS/JS + Leaflet.js | No build step, one self-contained file, fast to iterate |
| Map tiles | Real OpenStreetMap raster tiles | Free, no API key, keeps place/landmark markers |
| Data format | JSON / GeoJSON, no database | Small, static-per-run data; diffable and debuggable |
| Containers | Docker + Docker Compose + nginx | Reproducible across machines; one public origin, no CORS |
| CI / images | GitHub Actions → GitHub Container Registry | Free for a public student repo |
| Hosting (planned) | Azure Container Apps | Scale-to-zero fits a fixed student credit |

Full reasoning for every choice, including what was tried and rejected, is in **[`docs/TECH_STACK.md`](docs/TECH_STACK.md)**.

---

## Quick start

### With Docker (recommended)

```bash
git clone https://github.com/JaySPX05/jaldrishti.git
cd jaldrishti/flare_repo
python scripts/08_cache_osm_graph.py      # one-time: caches the road graph (needs internet)
docker compose up --build
```

Open **http://localhost:8080**. Check everything came up with:

```bash
powershell -ExecutionPolicy Bypass -File .\infra\docker\smoke-test.ps1   # or run on any OS via pwsh
```

See [`infra/docker/README.md`](infra/docker/README.md) for the full guide, troubleshooting, and how to run the offline data pipeline in its own container.

### Without Docker (three terminals)

```bash
# Terminal 1
cd backend-api && uvicorn main:app --reload

# Terminal 2
cd backend-api && uvicorn routing_api:app --reload --port 8001

# Terminal 3
cd frontend-dashboard && python -m http.server 5500
```

Open **http://127.0.0.1:5500/index.html**.

---

## Repo layout

| Path | What's there |
|---|---|
| `backend-api/` | The two FastAPI services: `main.py` (Risk API) and `routing_api.py` + `routing.py` (Routing API), plus tests |
| `frontend-dashboard/` | The single-file Leaflet dashboard |
| `scripts/` | The real offline pipeline, run in order: `01_prepare_data.py` → `08_cache_osm_graph.py` |
| `data/` | Pipeline inputs/outputs (gitignored except the small files the containers need — see `.gitignore`) |
| `ml-nowcasting/` | The PySteps nowcasting integration |
| `infra/docker/` | Dockerfiles, `docker-compose.yml` support files, and the smoke test |
| `infra/azure/` | Azure Container Apps deployment script (hosting currently on hold) |
| `docs/` | `TECH_STACK.md` (this project's decision record) and `GIT_WORKFLOW.md` (team git policy) |
| `contracts/` | The original shared API schema |
| `data-pipeline/`, `hydrology-swmm/`, `qgis/` | Early planning notes from before the pipeline moved into `scripts/`; kept for history, not the current source of truth |

Several subfolders (`backend-api/README.md`, `frontend-dashboard/README.md`, etc.) still hold their original Day-1 role-assignment briefs rather than a description of what's actually built — useful as project history, but this file and `docs/TECH_STACK.md` are the accurate current picture.

---

## Current status

- Core pipeline, risk map, and flood-aware routing: working, containerized, tested.
- Rainfall data: real nowcast, but from one recorded storm — IMD/KSNDMC live access requested, pending.
- Hydraulic calibration (SWMM): planned for the December runway, not started.
- Hosting: containers are built and tested locally; Azure deployment is prepared but on hold.
