# JalDrishti

**Real-time urban flood nowcasting for Bengaluru — street-level, 0–3 hour lead time.**

> "Predicting the puddle before the rain stops."
---

## Repo layout

| Folder | Role | Owner |
|---|---|---|
| `contracts/` | Shared API schema — **the thing that unblocks parallel work** | Everyone |
| `data-pipeline/` | Role A — Data Engineering & GIS | |
| `hydrology-swmm/` | Role B — Hydraulic Modeling *(Phase 2)* | |
| `ml-nowcasting/` | Role C — Nowcasting & ML | |
| `backend-api/` | Role D — Backend & API | |
| `frontend-dashboard/` | Role E — Frontend & Dashboard | |
| `infra/` | Role F — Integration & Deployment | |
| `data/` | Local data (gitignored — regenerate, don't commit) | |
| `docs/` | Workflow + planning docs | |

Each folder has its own README with that role's scope, structure, and dependencies. **Read yours before starting.**

---

## Getting started

```bash
git clone <repo-url>
cd jaldrishti
git checkout dev

# Populate local data (nothing large is committed)
bash data-pipeline/download_data.sh
```

Then follow the README in your own folder.

---

## How the pipeline fits together

```
[1] Data Ingestion       rainfall · terrain (DEM) · drainage network · roads
         ↓
[2] Nowcasting           0–3 hr rainfall forecast
         ↓
[3] Hybrid Risk Engine   static GIS susceptibility × rainfall multiplier
                         (+ ML refinement layer)
         ↓
[4] Applications         dashboard · flood-safe routing · (alerts, citizen reports)
```

**The compute rule:** anything heavy (ML training, SWMM calibration) runs **once, offline, on free cloud compute**. Anything running live must be light enough for a mid-range laptop.




