"""
Decides WHICH risk data the APIs serve, and reloads it when it changes.

Two sources, same file format:
  live  data/live/risk_lookup_<ward>.json       rewritten every cycle by live-ingest/ingest.py
  demo  data/processed/risk_lookup_<ward>.json  the recorded Sept-7 storm (scripts/04-05)

FLARE_DATA_MODE (env) picks the default:
  auto (default)  live if the ingest has produced a file, otherwise demo
  live            live only (404 until the ingest has run)
  demo            always the recorded storm (e.g. for a presentation on a dry day)
A request can override it with ?source=live|demo.

Files are cached in memory and re-read whenever their modification time
changes, so neither API needs a restart when new IMD data lands.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROCESSED_DIR = DATA_DIR / "processed"
LIVE_DIR = Path(os.environ.get("FLARE_LIVE_DIR", DATA_DIR / "live"))
STALE_AFTER_MINUTES = int(os.environ.get("STALE_AFTER_MINUTES", "120"))

VALID_SOURCES = ("auto", "live", "demo")

_cache: dict[Path, tuple[float, object]] = {}


def default_mode() -> str:
    mode = os.environ.get("FLARE_DATA_MODE", "auto").strip().lower()
    return mode if mode in VALID_SOURCES else "auto"


def _read_cached(path: Path):
    """Parsed JSON for path, re-read only when the file's mtime changes."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        _cache.pop(path, None)
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read {path} ({e}); keeping previous copy")
        return hit[1] if hit else None
    _cache[path] = (mtime, data)
    return data


def _paths(kind: str, ward_id: str, source: str) -> Path:
    base = LIVE_DIR if source == "live" else PROCESSED_DIR
    ext = "geojson" if kind == "flood_points" else "json"
    return base / f"{kind}_{ward_id.lower()}.{ext}"


def resolve_source(ward_id: str, requested: Optional[str] = None) -> str:
    """'live' or 'demo' - which source a request should actually be served from."""
    mode = (requested or default_mode()).lower()
    if mode not in VALID_SOURCES:
        mode = "auto"
    if mode == "auto":
        return "live" if _paths("risk_lookup", ward_id, "live").exists() else "demo"
    return mode


def load_lookup(ward_id: str, requested: Optional[str] = None):
    """(lookup_dict or None, source_used)."""
    source = resolve_source(ward_id, requested)
    data = _read_cached(_paths("risk_lookup", ward_id, source))
    if data is not None and not isinstance(data.get("timesteps"), dict):
        print(f"WARNING: {source} risk lookup for {ward_id} has no 'timesteps' object")
        data = None
    return data, source


def load_gauges(ward_id: str, requested: Optional[str] = None):
    source = resolve_source(ward_id, requested)
    return _read_cached(_paths("gauges", ward_id, source)), source


def load_status():
    return _read_cached(LIVE_DIR / "status.json")


def meta(ward_id: str, requested: Optional[str] = None) -> Optional[dict]:
    """Everything the dashboard needs to know before fetching risk steps."""
    lookup, source = load_lookup(ward_id, requested)
    if lookup is None:
        return None

    timesteps = list(lookup["timesteps"].keys())
    horizons = [
        (lookup["timesteps"][ts][0].get("horizon_label") if lookup["timesteps"][ts] else None)
        or f"T+{i}h".replace("T+0h", "T+0")
        for i, ts in enumerate(timesteps)
    ]
    info = {
        "ward_id": ward_id.lower(),
        "source": source,
        "timesteps": timesteps,
        "horizon_labels": horizons,
        "generated_at": lookup.get("generated_at"),
        "stale": False,
        "attribution": None,
        "live": None,
    }
    if source == "live":
        status = load_status() or {}
        live = lookup.get("live", {})
        # Station mode: age of the newest rain reading. City-nowcast mode: when the
        # ingest last fetched a current IMD nowcast (there is no reading to age).
        fresh = live.get("freshness_time") or live.get("newest_observation")
        age_min = None
        if fresh:
            age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(fresh)).total_seconds() / 60
        info.update({
            "stale": age_min is None or age_min > STALE_AFTER_MINUTES,
            "attribution": live.get("attribution"),
            "live": {
                **live,
                "observation_age_minutes": None if age_min is None else round(age_min),
                "last_attempt": status.get("last_attempt"),
                "last_success": status.get("last_success"),
                "last_error": status.get("last_error"),
                "imd_nowcast": status.get("imd_nowcast"),
            },
        })
    return info
