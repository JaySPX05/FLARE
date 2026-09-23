# hydrology-swmm — Role B: Hydrology / Hydraulic Modeling

**Goal:** Model how water moves through the drainage network and where it overflows.

> **2-day build note:** This module is **cut from the 2-day MVP scope** (see `docs/`). It stays in the repo as the Phase 2 workstream. If you're on the 2-day sprint, do not block on this.

## You own
- Converting the cleaned drain shapefile (from Role A) into an EPA SWMM network: nodes (manholes/inlets) with capacity attributes, edges (pipes/canals) with diameter/slope/roughness
- Running SWMM per pilot ward, calibrated against real historical storm events
- Producing a "surcharge map" — which nodes/segments overflow at what rainfall intensity

## Suggested structure
```
hydrology-swmm/
  shapefile_to_swmm.py   # the hard part — topology cleanup lives here
  models/                # .inp files (commit these, they're text)
  run_simulation.py
  calibration/           # notes + parameter sets per storm event
  requirements.txt
```

## Committing rules
- `.inp` files are text — **commit them**, they're your source of truth
- `.out` and `.rpt` are simulation outputs — **gitignored**, regenerate them

## Depends on
Role A's cleaned drain network + DEM, plus historical storm-event data.

## Others depend on you for
A physically accurate risk signal that validates/feeds the Hybrid Risk Engine. Not an MVP blocker — the GIS overlay runs without you.

## Known bottleneck
Shapefile→SWMM conversion is the slowest piece of the whole project (missing pipe diameters, disconnected topology). **Start with ONE ward.** If it stalls, a synthetic-but-plausible network is an acceptable documented fallback.
