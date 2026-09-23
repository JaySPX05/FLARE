"""
FLARE flood-aware routing API.

This file:
1. Loads the real Koramangala road network from OpenStreetMap.
2. Loads the processed Koramangala flood-risk lookup JSON.
3. Matches predicted risk segments to nearby OSM road edges.
4. Calls the custom Dijkstra logic in routing.py.
5. Returns GeoJSON for the Leaflet dashboard.

Run from backend-api:
    python -m uvicorn routing_api:app --reload

Test:
    http://127.0.0.1:8001/docs
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from shapely import STRtree
from shapely.geometry import LineString

from routing import (
    build_edge_index,
    compute_route,
    edge_line,
    load_osm_graph,
)


@asynccontextmanager
async def lifespan(_app):
    """
    Download the OSM graph and build the lookup tables when the server
    starts, so the first route click isn't the one that waits for the
    download. If it fails (e.g. offline), the first /route call retries.
    """
    try:
        graph = get_graph()
        build_edge_index(graph)
        segments, _ = get_segments_for_timestep(None)
        get_edge_segment_map(graph, segments)
    except Exception as error:
        print(f"Startup preload skipped ({error}); will retry on first route request.")
    yield


app = FastAPI(
    title="FLARE Routing API",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


PLACE_NAME = "Koramangala, Bengaluru, India"

BASE_DIR = Path(__file__).resolve().parent

RISK_LOOKUP_PATH = (
    BASE_DIR.parent
    / "data"
    / "processed"
    / "risk_lookup_koramangala.json"
)

# Saved copy of the road graph (create it with scripts/08_cache_osm_graph.py).
# When the file exists the service starts in seconds with no internet; when it
# doesn't, the graph is downloaded live as before. Override with OSM_GRAPH_PATH.
OSM_GRAPH_PATH = Path(
    os.environ.get(
        "OSM_GRAPH_PATH",
        BASE_DIR.parent / "data" / "processed" / "koramangala_drive.graphml",
    )
)

_state = {
    "graph": None,
    "risk_data": None,
    "edge_segments": None,
}


# ---------------------------------------------------------------------------
# Real OSM road graph
# ---------------------------------------------------------------------------

def get_graph():
    """
    Load the real Koramangala road graph once and keep it in memory.

    This avoids downloading OpenStreetMap data every time the frontend
    requests a route.
    """
    if _state["graph"] is None:
        source = (
            f"saved graph {OSM_GRAPH_PATH.name}"
            if OSM_GRAPH_PATH.exists()
            else "OpenStreetMap (live download)"
        )

        print(
            f"Loading road network for {PLACE_NAME} from {source}..."
        )

        _state["graph"] = load_osm_graph(graph_path=OSM_GRAPH_PATH)

        print("OSM road graph loaded.")

    return _state["graph"]

# ---------------------------------------------------------------------------
# Real flood-risk data
# ---------------------------------------------------------------------------

def load_risk_data():
    """
    Load Pair 1's real flood-risk lookup JSON.

    Expected structure:

    {
      "ward_id": "koramangala",
      "generated_at": "...",
      "timesteps": {
        "2026-09-07T15:00:00": [
          {
            "segment_id": "seg_0001",
            "geometry": [[longitude, latitude], ...],
            "risk_score": 0.39,
            "predicted_depth_cm": 11.7,
            "confidence": 0.6
          }
        ]
      }
    }
    """
    if _state["risk_data"] is not None:
        return _state["risk_data"]

    if not RISK_LOOKUP_PATH.exists():
        raise FileNotFoundError(
            "Could not find risk lookup data at: "
            f"{RISK_LOOKUP_PATH}"
        )

    with RISK_LOOKUP_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        _state["risk_data"] = json.load(file)

    timestep_count = len(
        _state["risk_data"].get(
            "timesteps",
            {},
        )
    )

    print(
        f"Loaded flood-risk lookup data with "
        f"{timestep_count} timesteps."
    )

    return _state["risk_data"]


def segment_midpoint(geometry):
    """
    Input geometry:
    [
        [longitude, latitude],
        [longitude, latitude],
        ...
    ]

    Output:
        (latitude, longitude)
    """
    longitudes = [
        point[0]
        for point in geometry
    ]

    latitudes = [
        point[1]
        for point in geometry
    ]

    return (
        sum(latitudes) / len(latitudes),
        sum(longitudes) / len(longitudes),
    )


def get_segments_for_timestep(timestep=None):
    """
    Get risk segments for the selected timestamp.

    If the frontend does not send a timestamp, or sends an invalid timestamp,
    use the first available forecast timestep.

    Returns:
        (segments, timestep_used)
    """
    risk_data = load_risk_data()

    timesteps = risk_data.get(
        "timesteps",
        {},
    )

    if not timesteps:
        return [], None

    if timestep and timestep in timesteps:
        return (
            timesteps[timestep],
            timestep,
        )

    first_timestamp = next(iter(timesteps))

    return (
        timesteps[first_timestamp],
        first_timestamp,
    )


# ---------------------------------------------------------------------------
# Risk-to-road matching
# ---------------------------------------------------------------------------

# A road edge takes the risk of a forecast segment only if it actually runs
# along that segment: within ~8 m of it for a meaningful stretch (25 m, or
# half the edge if it is shorter). The stretch requirement stops a road that
# merely crosses a flooded one from inheriting its risk. (The old version
# compared edge midpoints to segment midpoints within ~200 m, which handed
# risk to unrelated neighbouring roads and missed real flooded ones.)
MATCH_TOLERANCE_DEG = 0.00007      # ~8 m
METRES_PER_DEGREE = 111_000
MIN_OVERLAP_M = 25.0


def _build_edge_segment_map(graph, segments):
    """
    Work out which forecast segments each road edge lies on.

    Returns {(u, v, key): [segment_id, ...]}.

    This is purely geometric - segment shapes are identical at every
    timestep - so it is computed once and only the risk values are looked
    up per timestep.
    """
    usable = [
        segment
        for segment in segments
        if segment.get("geometry") and len(segment["geometry"]) >= 2
    ]

    if not usable:
        return {}

    zones = [
        LineString(segment["geometry"]).buffer(
            MATCH_TOLERANCE_DEG,
            cap_style="flat",
        )
        for segment in usable
    ]

    tree = STRtree(zones)
    edge_segments = {}

    for u, v, key in graph.edges(keys=True):
        line = edge_line(graph, u, v, key)

        edge_length_m = float(
            graph[u][v][key].get(
                "length",
                line.length * METRES_PER_DEGREE,
            )
        )

        needed_m = min(MIN_OVERLAP_M, 0.5 * edge_length_m)

        matched = []

        for index in tree.query(line, predicate="intersects"):
            overlap_m = (
                line.intersection(zones[index]).length
                * METRES_PER_DEGREE
            )

            if overlap_m >= needed_m:
                matched.append(usable[index]["segment_id"])

        if matched:
            edge_segments[(u, v, key)] = matched

    return edge_segments


def get_edge_segment_map(graph, segments):
    if _state["edge_segments"] is None:
        _state["edge_segments"] = _build_edge_segment_map(
            graph,
            segments,
        )

        print(
            f"Matched {len(_state['edge_segments'])} road edges "
            "to flood-risk segments."
        )

    return _state["edge_segments"]


def apply_risk_to_graph(graph, timestep=None):

    segments, timestep_used = get_segments_for_timestep(
        timestep
    )

    for _, _, _, edge_data in graph.edges(
        keys=True,
        data=True,
    ):
        edge_data["risk_score"] = 0.0

    if not segments:
        return graph, timestep_used

    edge_segments = get_edge_segment_map(graph, segments)

    risk_by_segment = {
        segment["segment_id"]: float(segment.get("risk_score", 0.0))
        for segment in segments
    }

    for (u, v, key), segment_ids in edge_segments.items():
        # Worst stretch wins: a road is only as passable as its most
        # flooded part.
        graph[u][v][key]["risk_score"] = max(
            risk_by_segment.get(segment_id, 0.0)
            for segment_id in segment_ids
        )

    return graph, timestep_used


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def path_to_geojson_feature(
    path,
    properties=None,
):
    """
    Convert a path in this form:

    [
        [longitude, latitude],
        ...
    ]

    into a GeoJSON LineString Feature.
    """
    if not path:
        return None

    return {
        "type": "Feature",
        "properties": properties or {},
        "geometry": {
            "type": "LineString",
            "coordinates": path,
        },
    }


def flooded_segments_on_route(
    edge_ids,
    timestep,
    risk_threshold=0.55,
):
    """
    IDs of forecast segments at/above the risk threshold that a route
    travels along. Used to report which flooded roads the shortest route
    would have crossed and the safe route avoids.
    """
    edge_segments = _state["edge_segments"] or {}

    segments, _ = get_segments_for_timestep(
        timestep
    )

    risk_by_segment = {
        segment["segment_id"]: float(segment.get("risk_score", 0.0))
        for segment in segments
    }

    return {
        segment_id
        for edge_id in edge_ids
        for segment_id in edge_segments.get(edge_id, [])
        if risk_by_segment.get(segment_id, 0.0) >= risk_threshold
    }


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    """
    Health check plus real-data status.
    """
    risk_data = load_risk_data()

    return {
        "status": "ok",
        "ward_id": risk_data.get(
            "ward_id",
            "koramangala",
        ),
        "using_real_risk_data": bool(
            risk_data.get("timesteps")
        ),
        "available_timesteps": list(
            risk_data.get(
                "timesteps",
                {},
            ).keys()
        ),
    }


@app.get("/route")
def route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    timestep: str = None,
):
    """
    Return two route options as GeoJSON for Leaflet:

    - shortest: real-road route based only on road length
    - safe: risk-aware custom Dijkstra route

    Frontend styling:
    - shortest -> blue, dashed
    - safe -> green, solid
    """
    graph = get_graph()

    graph, timestep_used = apply_risk_to_graph(
        graph,
        timestep=timestep,
    )

    shortest_result = compute_route(
        G=graph,
        from_lng=start_lon,
        from_lat=start_lat,
        to_lng=end_lon,
        to_lat=end_lat,
        risk_penalty_factor=0.0,
        risk_threshold=1.1,
    )

    safe_result = compute_route(
        G=graph,
        from_lng=start_lon,
        from_lat=start_lat,
        to_lng=end_lon,
        to_lat=end_lat,
        risk_penalty_factor=15.0,
        risk_threshold=0.55,
    )

    if not shortest_result["path"] and not safe_result["path"]:
        raise HTTPException(
            status_code=404,
            detail="No route found between selected points.",
        )

    features = []

    shortest_feature = path_to_geojson_feature(
        shortest_result["path"],
        properties={
            "route_type": "shortest",
            "color": "#2563eb",
            "route_cost": shortest_result[
                "route_cost"
            ],
        },
    )

    safe_feature = path_to_geojson_feature(
        safe_result["path"],
        properties={
            "route_type": "safe",
            "color": "#16a34a",
            "route_cost": safe_result[
                "route_cost"
            ],
        },
    )

    if shortest_feature:
        features.append(shortest_feature)

    if safe_feature:
        features.append(safe_feature)

    risk_data = load_risk_data()

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "ward_id": risk_data.get(
                "ward_id",
                "koramangala",
            ),
            "timestep_used": timestep_used,
            "using_real_risk_data": bool(
                risk_data.get("timesteps")
            ),
            "risk_penalty_applied": True,
            "risk_penalty_factor": 15.0,
            "risk_threshold": 0.55,
            "avoided_segments": sorted(
                flooded_segments_on_route(
                    shortest_result["edge_ids"],
                    timestep_used,
                )
                - flooded_segments_on_route(
                    safe_result["edge_ids"],
                    timestep_used,
                )
            ),
            "safe_route_available": bool(
                safe_result["path"]
            ),
            "algorithm": "Custom Dijkstra",
        },
    }
