# frontend-dashboard — Role E: Frontend & Dashboard

**Goal:** Make the prediction visible, understandable, and usable. This is the demo-facing piece.

## You own
- Live map, streets color-coded by predicted water depth, with a 0–3hr time slider
- Legend + color scale that reads clearly on a projector
- Known-flood-point overlay (adds credibility in the demo)
- Bilingual UI, Kannada + English *(Phase 2 — but keep all strings in a translation file from day one so it's cheap to add later)*
- Graceful handling of null `predicted_depth_cm` / `confidence` — see the contract

## Map library choice
**Leaflet** is faster to stand up than Mapbox if nobody on the team already knows Mapbox. Under a 2-day constraint, pick the one someone already knows.

## Do this in hour one
Build against `contracts/risk-api-schema.json` using Role D's mock endpoint. **Do not wait for real data.**

## Suggested structure
```
frontend-dashboard/
  src/
    components/Map.jsx
    components/TimeSlider.jsx
    components/Legend.jsx
    api/client.js        # single place the API shape is consumed
    i18n/                # en.json / kn.json
  package.json
```

## Depends on
Role D's API — you're a pure consumer of it.

## Others depend on you for
This is what judges actually look at. Budget real polish time.
