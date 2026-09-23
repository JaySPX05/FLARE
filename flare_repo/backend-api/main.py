"""
FLARE Backend — Risk API

Real GIS pipeline data from Pair 1 is now live: data/processed/risk_lookup_<ward_id>.json,
served via data_loader.py, matching contracts/risk-lookup-table-format.md.

ARCHITECTURE NOTE: routing lives in its own standalone service,
routing_api.py (run separately: uvicorn routing_api:app --port 8001),
which does real OSM-based routing against the real risk data. This
file does not serve /api/v1/route. Run both services side by side; the
dashboard should call this one for risk/flood-point data and
routing_api.py (on its own port) for routes.

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload
    # then open http://127.0.0.1:8000/docs for interactive API docs
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import data_loader

app = FastAPI(title="FLARE Risk API", version="1.0.0")

# Allow the frontend dev server to call this API from a different port/origin.
# Tighten this before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Response models — these mirror contracts/risk-api-schema.json exactly.
# If you change a field here, you MUST update the contract file too and
# tell the team, per the contract rule in the git workflow doc.
# ---------------------------------------------------------------------------

class RiskSegment(BaseModel):
    segment_id: str
    geometry: list[list[float]]  # [[lng, lat], [lng, lat], ...]
    risk_score: float
    predicted_depth_cm: Optional[float] = None
    confidence: Optional[float] = None
    drain_proximity_m: Optional[float] = None
    mean_rainfall_mm: Optional[float] = None


class RiskResponse(BaseModel):
    ward_id: str
    timestamp: str
    forecast_window_minutes: int
    segments: list[RiskSegment]


class FloodPointsResponse(BaseModel):
    ward_id: str
    points: list[dict]


@app.get("/")
def health_check():
    return {"status": "ok", "service": "flare-backend"}


@app.get("/api/v1/risk", response_model=RiskResponse)
def get_risk(
    ward_id: str = Query(..., description="e.g. 'koramangala'"),
    timestamp: Optional[str] = Query(
        None, description="ISO8601. Defaults to now if omitted."
    ),
):
    """
    Returns risk scores for every street segment in a ward at a given timestep,
    read from the real lookup table via data_loader.py.
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat()

    segments = data_loader.get_risk_segments(ward_id, ts)
    if segments is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data for ward_id='{ward_id}'. "
                   f"Known wards: {data_loader.known_wards()}. ",
        )

    return RiskResponse(
        ward_id=ward_id,
        timestamp=ts,
        forecast_window_minutes=180,
        segments=segments,
    )


@app.get("/api/v1/flood-points", response_model=FloodPointsResponse)
def get_flood_points(ward_id: str = Query(..., description="e.g. 'koramangala'")):
    """
    Real, known flood-prone locations for a ward. Static - no timestamp
    parameter, since these don't change per-timestep like risk scores do.
    """
    points = data_loader.get_flood_points(ward_id)
    if points is None:
        raise HTTPException(
            status_code=404,
            detail=f"No flood points file for ward_id='{ward_id}'. "
                   f"Expected data/processed/flood_points_{ward_id.lower()}.geojson",
        )
    return FloodPointsResponse(ward_id=ward_id, points=points)