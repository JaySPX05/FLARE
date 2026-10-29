#!/usr/bin/env bash
# Build every FLARE image on the host's network, then start the stack.
#
# Why: on the Azure VM, pip's downloads from PyPI stalled inside BuildKit's own
# build network, while the host itself reached PyPI instantly. `docker build
# --network host` sidesteps that. (Compose's `build: network: host` needs an extra
# permission flag that `docker compose build` can't pass, so we build directly.)
# The tags match what docker compose expects, so `docker compose up -d` then uses
# these images without rebuilding.
#
# Usage (from anywhere):   bash infra/docker/build-host-network.sh
#        only some images: bash infra/docker/build-host-network.sh ingest api
set -euo pipefail
cd "$(dirname "$0")/../.."          # repo's flare_repo/ folder

services=("$@")
[ ${#services[@]} -eq 0 ] && services=(api routing frontend ingest)

for svc in "${services[@]}"; do
  echo "== Building flare-$svc"
  docker build --network host -f "infra/docker/$svc.Dockerfile" -t "flare-$svc" .
done

echo "== Starting the stack"
docker compose up -d
docker compose ps
