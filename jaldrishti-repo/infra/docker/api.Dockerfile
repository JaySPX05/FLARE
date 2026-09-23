# Risk API (backend-api/main.py). Build from the repo root:
#   docker build -f infra/docker/api.Dockerfile -t flare-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Keep the repo layout: data_loader.py finds data at ../data/processed
WORKDIR /app/backend-api

COPY backend-api/requirements-api.txt .
RUN pip install -r requirements-api.txt

COPY backend-api/main.py backend-api/data_loader.py ./
# risk_lookup_*.json is required; the flood-points file is picked up if present
COPY data/processed/risk_lookup_*.json data/processed/flood_points_*.geojson /app/data/processed/

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
