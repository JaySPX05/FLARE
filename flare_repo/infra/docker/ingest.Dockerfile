# Live IMD ingest (live-ingest/ingest.py). Build from the repo root:
#   docker build --network host -f infra/docker/ingest.Dockerfile -t flare-ingest .
#
# IMD authorises by IP: this container's requests leave through the host's
# internet connection, so it only gets data on a machine/network whose public
# IP IMD whitelisted. Check first with:
#   docker compose run --rm ingest python live-ingest/probe_imd.py
#
# pysteps ships source only, so the build stage needs a compiler.
FROM python:3.12-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
# Slow or flaky links to PyPI: wait longer and retry more before giving up.
ENV PATH=/opt/venv/bin:$PATH \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10
COPY live-ingest/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

WORKDIR /app
COPY live-ingest/ live-ingest/
COPY ml-nowcasting/nowcasting_api.py ml-nowcasting/
# Static per-segment base risk: segments_base_*.json from scripts/05 if present,
# otherwise it is recovered from the demo lookup + rainfall_multiplier.csv.
COPY data/processed/risk_lookup_*.json data/processed/rainfall_multiplier.csv data/processed/segments_base_*.jso[n] /app/data/processed/

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/data/live \
 && chown -R app /app
USER app

CMD ["python", "live-ingest/ingest.py"]
