"""
08_cache_osm_graph.py
=====================
Download the drivable OpenStreetMap road network for the study area ONCE and
save it as GraphML, so the routing service (and its Docker image) can start
in seconds without depending on the Overpass API at run time.

Output
------
data/processed/koramangala_drive.graphml

Run (needs internet):
    python scripts/08_cache_osm_graph.py
    # or, in Docker:  docker compose run --rm pipeline scripts/08_cache_osm_graph.py

Then commit the file (see .gitignore) and rebuild the routing image.
Re-run it whenever you want fresher OSM data.
"""

import sys
from pathlib import Path

import osmnx as ox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend-api"))

from routing import download_osm_graph  # noqa: E402

OUTPUT_FILE = ROOT / "data" / "processed" / "koramangala_drive.graphml"


def main():
    print("Downloading drivable road network from OpenStreetMap...")
    graph = download_osm_graph()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, OUTPUT_FILE)

    size_mb = OUTPUT_FILE.stat().st_size / 1_000_000
    print(
        f"Saved {OUTPUT_FILE.relative_to(ROOT)}: "
        f"{graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"{size_mb:.1f} MB"
    )


if __name__ == "__main__":
    main()
