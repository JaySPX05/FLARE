# ml-nowcasting — Role C: Nowcasting & Machine Learning

**Goal:** Predict rainfall 0–3 hours ahead, and refine the flood risk score.

## You own
- **Nowcasting:** PySteps optical-flow extrapolation of rainfall grids (live data, or a historical storm replayed as if live)
- **Static GIS Susceptibility Index:** weighted overlay of elevation, slope, distance-to-drain, imperviousness — plain raster math, no training needed
- **ML refinement layer:** Random Forest trained on historical outcomes + the 211 known flood points, to correct the static weights
- **Explainability:** SHAP output so the model's reasoning is inspectable

## The compute rule (important)
**Train once on Google Colab's free tier. Deploy CPU-only inference.**
- Training notebooks live in `notebooks/` and are committed
- Trained weights (`.pkl`, `.joblib`) are **gitignored** — they're build outputs
- Anyone should be able to regenerate a model by re-running the notebook

## Suggested structure
```
ml-nowcasting/
  nowcast_pysteps.py
  susceptibility_overlay.py   # build this FIRST — fastest path to a working pipeline
  notebooks/train_rf.ipynb    # Colab training, committed
  inference.py                # CPU-only, must run on a mid-range laptop
  requirements.txt
```

## Depends on
Role A's rainfall history + flood-point labels. Role B's SWMM output is a bonus signal, not a requirement.

## Others depend on you for
The actual risk score that Backend serves and Frontend displays.

## Build order (matters under time pressure)
1. Static GIS overlay — fast, no training data, unblocks everyone
2. Rainfall multiplier
3. ML refinement — last, most optional. **Do not let it block the pipeline.**
