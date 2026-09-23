# Offline GIS + nowcasting pipeline (scripts/01-08). Not part of the running app -
# run on demand, with ./data mounted in:
#   docker compose run --rm pipeline scripts/04_rainfall_multiplier.py
#
# pysteps ships source only, so the build stage needs a compiler; the final
# image keeps just the finished virtualenv.
FROM python:3.12-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY infra/docker/requirements-pipeline.txt .
RUN pip install --no-cache-dir -r requirements-pipeline.txt

FROM python:3.12-slim
# libgomp: OpenMP runtime used by numpy/scipy/opencv wheels
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

WORKDIR /app
COPY scripts/ scripts/
COPY ml-nowcasting/ ml-nowcasting/
COPY backend-api/routing.py backend-api/routing.py

# Scripts use paths like data/raw and data/processed relative to /app
ENTRYPOINT ["python"]
