# backend-api — Role D: Backend & API

**Goal:** Serve risk scores and power every downstream application.

## You own
- FastAPI backend exposing risk scores per street segment + the 0–3hr forecast timeline
- Database layer (PostgreSQL/PostGIS) for risk scores, history, citizen reports
- **Flood-safe routing:** start/end → route avoiding high-risk segments
- SMS/WhatsApp alert integration *(Phase 2 — cut from 2-day MVP)*
- Citizen reporting endpoint *(Phase 2 — cut from 2-day MVP)*

## Do this in hour one
Stand up the FastAPI skeleton returning **mock data** in the shape defined by `contracts/risk-api-schema.json`. This unblocks Frontend (Role E) immediately — they should never wait for real data to start building.

## Routing: read this before choosing
For the **2-day build**, use a plain Python Dijkstra over the OSM graph weighted by risk score. Self-hosting OSRM/GraphHopper is fiddly and has burned time before — it's a Phase 2 upgrade, not an MVP requirement.

## Suggested structure
```
backend-api/
  main.py              # FastAPI entrypoint
  routes/
    risk.py
    routing.py
  models/              # pydantic schemas — mirror contracts/risk-api-schema.json
  db/
  mock_data.py         # ships day one, deleted last
  requirements.txt
```

## Depends on
Role C's risk scores (main input), Role A's road network for routing.

## Others depend on you for
Every number Frontend displays and every route the demo shows.

## Contract discipline
If the API shape must change, say so in the team channel **before** changing it and bump `schema_version` in `contracts/risk-api-schema.json`. A silent shape change breaks Frontend without warning.
