"""
Settings for the live IMD ingest, read from environment variables (or a .env
file in the repo root / live-ingest/). Every value has a default, so the only
thing you normally need to set is whatever IMD's approval email asks for.

IMD authorises access by WHITELISTING THE IP ADDRESS that makes the request.
That means this code only gets data when it runs on the machine / network
whose public IP you gave IMD. Run probe_imd.py there first.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv  # optional; plain env vars work without it
    _here = Path(__file__).resolve().parent
    load_dotenv(_here.parent / ".env")
    load_dotenv(_here / ".env")
except ImportError:
    pass


def _env(name, default):
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- IMD access
# Per the IMD API Portal User Guide, every request needs TWO headers:
#   X-API-KEY: <key from the portal's API Keys page>   - bound to this server's static IP
#   Authorization: Bearer <JWT>                         - from POST /api/oauth/token.php
#                                                         with the portal email + password;
#                                                         expires (~1 h), renewed automatically
# Keep all three in .env (gitignored, chmod 600) - never in code or the repo.
IMD_BASE_URL = _env("IMD_BASE_URL", "https://api.imd.gov.in/api/v1").rstrip("/")
IMD_TOKEN_URL = _env("IMD_TOKEN_URL", "https://api.imd.gov.in/api/oauth/token.php")
IMD_API_KEY = _env("IMD_API_KEY", "")
IMD_API_KEY_HEADER = _env("IMD_API_KEY_HEADER", "X-API-KEY")
IMD_EMAIL = _env("IMD_EMAIL", "")
IMD_PASSWORD = _env("IMD_PASSWORD", "")
IMD_TIMEOUT_S = float(_env("IMD_TIMEOUT_S", "20"))

# Karnataka is state id 13 in IMD's state list (api_reference.html).
IMD_STATE_ID = _env("IMD_STATE_ID", "13")
# Station name to use for the station-wise nowcast. IMD publishes no station
# list endpoint; probe_imd.py tries common spellings and tells you which works.
IMD_NOWCAST_STATION = _env("IMD_NOWCAST_STATION", "Bengaluru City")

# Name of the rainfall field in aws_data. Leave blank to auto-detect from a
# list of likely names (see imd_client.RAIN_FIELD_CANDIDATES).
IMD_RAIN_FIELD = _env("IMD_RAIN_FIELD", "")
# IMD AWS rainfall is normally CUMULATIVE since 08:30 IST (resets daily).
# "cumulative" -> we difference consecutive readings to get rain per interval.
# "interval"   -> the field already holds rain since the previous report.
IMD_RAIN_MODE = _env("IMD_RAIN_MODE", "cumulative")
# Time zone of aws_data DATE/TIME. IMD observations are usually in UTC;
# probe_imd.py checks this against the clock and prints a recommendation.
IMD_TIME_ZONE = _env("IMD_TIME_ZONE", "UTC")

# ------------------------------------------------------------ study area
WARD_ID = "koramangala"
WARD_CENTER_LAT = 12.935
WARD_CENTER_LON = 77.627
# Same pilot-study box as scripts/01 and scripts/05: (lon_min, lat_min, lon_max, lat_max)
WARD_BBOX = (77.618, 12.918, 77.650, 12.953)
# Use every IMD station within this distance of Koramangala.
STATION_RADIUS_KM = float(_env("STATION_RADIUS_KM", "30"))

# ------------------------------------------------------------ ingest loop
POLL_MINUTES = int(_env("POLL_MINUTES", "15"))
# Observations are binned into frames of this length for the nowcast. IMD AWS
# typically reports every 15 min to 1 h; 60 is the safe default.
FRAME_MINUTES = int(_env("FRAME_MINUTES", "60"))
# How much observation history to keep / feed the nowcast.
HISTORY_HOURS = int(_env("HISTORY_HOURS", "24"))
NOWCAST_FRAMES = int(_env("NOWCAST_FRAMES", "4"))
# If the newest IMD reading is older than this, the dashboard marks data stale.
STALE_AFTER_MINUTES = int(_env("STALE_AFTER_MINUTES", "120"))

# Rain -> risk factors are fixed in risk_engine.py (IMD bands, team decision
# 31 Oct 2026); there is no longer a RAIN_THRESHOLD_SCHEME setting.

# ------------------------------------------------------------ paths
LIVE_DIR = Path(_env("FLARE_LIVE_DIR", str(REPO_ROOT / "data" / "live")))
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
BASE_SEGMENTS_FILE = PROCESSED_DIR / f"segments_base_{WARD_ID}.json"
DEMO_LOOKUP_FILE = PROCESSED_DIR / f"risk_lookup_{WARD_ID}.json"

OBSERVATIONS_FILE = LIVE_DIR / "observations.csv"
STATIONS_FILE = LIVE_DIR / "stations.json"
LIVE_LOOKUP_FILE = LIVE_DIR / f"risk_lookup_{WARD_ID}.json"
GAUGES_FILE = LIVE_DIR / f"gauges_{WARD_ID}.json"
STATUS_FILE = LIVE_DIR / "status.json"
RAW_SNAPSHOT_DIR = LIVE_DIR / "raw"
