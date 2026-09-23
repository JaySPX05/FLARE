# Routing API (backend-api/routing_api.py + routing.py). Build from the repo root:
#   docker build -f infra/docker/routing.Dockerfile -t flare-routing .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OSM_GRAPH_PATH=/app/data/processed/koramangala_drive.graphml

WORKDIR /app/backend-api

COPY backend-api/requirements-routing.txt .
RUN pip install -r requirements-routing.txt

COPY backend-api/routing.py backend-api/routing_api.py ./
# The risk JSON is required. The saved road graph (koramangala_drive.graphml, made by
# scripts/08_cache_osm_graph.py) is optional: with it the service starts offline in
# seconds; without it the service downloads the network from OpenStreetMap at start-up.
COPY data/processed/risk_lookup_*.json data/processed/koramangala_drive.graphm[l] /app/data/processed/

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8001
CMD ["uvicorn", "routing_api:app", "--host", "0.0.0.0", "--port", "8001"]
