"""
JalDrishti flood-aware routing API.

This file:
1. Loads the real Koramangala road network from OpenStreetMap.
2. Loads the processed Koramangala flood-risk lookup JSON.
3. Matches predicted risk segments to nearby OSM road edges.
4. Calls the custom Dijkstra logic in routing.py.
5. Returns GeoJSON for the Leaflet dashboard.

Run from backend-api:
    python -m uvicorn routing_api:app --reload

Test:
    http://127.0.0.1:8000/docs
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from routing import compute_route, load_osm_graph



app = FastAPI(
    title="JalDrishti Routing API",
    version="1.0.0",
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

_state = {
    "graph": None,
    "risk_data": None,
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
        print(
            f"Loading OSM road network for {PLACE_NAME}..."
        )

        _state["graph"] = load_osm_graph()

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

    usable_segments = [
        segment
        for segment in segments
        if segment.get("geometry")
    ]

    if not usable_segments:
        return graph, timestep_used

    segment_points = [
        segment_midpoint(segment["geometry"])
        for segment in usable_segments
    ]

    segment_risks = [
        float(segment.get("risk_score", 0.0))
        for segment in usable_segments
    ]

    MAX_MATCH_DISTANCE_SQUARED = 0.000003228  # ~200m at this latitude

    for source, destination, _, edge_data in graph.edges(
        keys=True,
        data=True,
    ):
        edge_latitude = (
            graph.nodes[source]["y"]
            + graph.nodes[destination]["y"]
        ) / 2

        edge_longitude = (
            graph.nodes[source]["x"]
            + graph.nodes[destination]["x"]
        ) / 2

        nearest_distance = float("inf")
        nearest_risk = 0.0

        for (
            segment_latitude,
            segment_longitude,
        ), risk_score in zip(
            segment_points,
            segment_risks,
        ):
            distance = (
                (edge_latitude - segment_latitude) ** 2
                + (edge_longitude - segment_longitude) ** 2
            )

            if distance < nearest_distance:
                nearest_distance = distance
                nearest_risk = risk_score

        if nearest_distance <= MAX_MATCH_DISTANCE_SQUARED:
            edge_data["risk_score"] = nearest_risk

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


def get_severe_risk_segments(
    timestep,
    risk_threshold=0.55,
):
    """
    Return IDs of source forecast segments considered unsafe.

    The current dataset peaks around 0.59, so 0.55 creates a visible
    safe-route demonstration. Use 0.80 later if your model has higher
    confidence/risk values and that is your team's agreed threshold.
    """
    segments, _ = get_segments_for_timestep(
        timestep
    )

    return [
        segment.get("segment_id", "unknown")
        for segment in segments
        if float(
            segment.get("risk_score", 0.0)
        ) >= risk_threshold
    ]


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
            "avoided_segments": get_severe_risk_segments(
                timestep=timestep_used,
                risk_threshold=0.55,
            ),
            "safe_route_available": bool(
                safe_result["path"]
            ),
            "algorithm": "Custom Dijkstra",
        },
    }
