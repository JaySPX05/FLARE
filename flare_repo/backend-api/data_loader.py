"""
Loads risk data from the precomputed lookup table (contracts/risk-lookup-table-format.md).

Two sources share that format (see risk_source.py for how one is chosen):
  - live: data/live/risk_lookup_<ward_id>.json, rewritten by live-ingest/ingest.py
          from IMD AWS rainfall + nowcast every few minutes
  - demo: data/processed/risk_lookup_<ward_id>.json, the recorded Sept-7 storm
          from scripts/04-05
Swap the file, not the code: nothing here depends on where the data came from.
"""

import json
from pathlib import Path
from typing import Optional

import risk_source

_LOOKUP_DIR = Path(__file__).parent.parent / "data" / "processed"

# Ward center coordinates, used by routing.py to build its road graph.
# Add an entry here for any ward you generate a lookup table for.
WARD_CENTERS = {
    "koramangala": (77.627, 12.935),
    "bellandur": (77.678, 12.926),
    "yelahanka": (77.596, 13.100),
}


def _load_ward_file(ward_id: str, source: Optional[str] = None) -> Optional[dict]:
    """
    The lookup for a ward from the live IMD feed or the demo storm, whichever
    risk_source.py picks (FLARE_DATA_MODE / ?source=). risk_source re-reads
    the file whenever it changes on disk, so new IMD data shows up without a
    restart.
    """
    data, _ = risk_source.load_lookup(ward_id, source)
    return data


def _nearest_timestep(available: list[str], requested: str) -> str:
    """
    The frontend may ask for a timestamp that doesn't exactly match one of
    the precomputed steps. Snap to the closest available one rather than
    returning nothing.

    Real pipeline output (05_build_lookup.py) writes NAIVE timestamps (no
    timezone), while this codebase generates timezone-AWARE ones by default
    (datetime.now(timezone.utc)). Comparing the two directly raises
    TypeError - confirmed by running this against the actual real
    risk_lookup_koramangala.json. Strip timezone info from both sides
    before comparing so either format works.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")

    def parse_naive(ts):
        # Live keys carry +05:30; demo keys are naive IST wall-clock time.
        # Convert aware values to IST first so a UTC "now" snaps correctly.
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is not None:
            dt = dt.astimezone(ist)
        return dt.replace(tzinfo=None)

    target = parse_naive(requested)
    return min(available, key=lambda ts: abs((parse_naive(ts) - target).total_seconds()))


def get_risk_segments(ward_id: str, timestamp: str, source: Optional[str] = None):
    """
    Returns the list of risk segments for a ward at (the nearest precomputed
    timestep to) the given timestamp, or None if no lookup file exists for
    that ward — in which case main.py returns a 404. There is no further
    fallback: a missing file means the ward genuinely isn't set up yet, and
    that should be visible, not silently papered over.
    """
    ward_id = ward_id.lower()
    file_data = _load_ward_file(ward_id, source)
    if file_data is None:
        return None

    available_timestamps = list(file_data["timesteps"].keys())
    if not available_timestamps:
        return None

    nearest = _nearest_timestep(available_timestamps, timestamp)
    return file_data["timesteps"][nearest]


def get_flood_points(ward_id: str):
    """
    Real, known flood-prone locations (not synthetic) - static list, no
    timesteps. Complementary to get_risk_segments(), not a replacement:
    this answers "where has flooding actually happened historically,"
    the risk lookup answers "how flooded will this street be right now."
    """
    path = _LOOKUP_DIR / f"flood_points_{ward_id.lower()}.geojson"
    if not path.exists():
        return None

    data = json.loads(path.read_text())
    return data.get("features", [])


def known_wards() -> list[str]:
    """Every ward with an actual lookup file on disk."""
    if not _LOOKUP_DIR.exists():
        return []
    return sorted(p.stem.replace("risk_lookup_", "") for p in _LOOKUP_DIR.glob("risk_lookup_*.json"))
