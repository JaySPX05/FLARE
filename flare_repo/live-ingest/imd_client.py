"""
Thin, defensive client for the three IMD APIs FLARE has access to:

    GET {base}/aws_data?sid=13        automatic weather station readings (Karnataka)
    GET {base}/aws_data_mapping       station id -> name / location metadata
    GET {base}/stationnowcast?id=...  IMD's own 0-3 h nowcast warning for a station

Reference: https://api.imd.gov.in/public/api_reference.html

Why "defensive": IMD's documented sample for aws_data shows no rainfall field
and has a trailing comma (invalid JSON), and the docs don't state the time zone
or whether rainfall is cumulative. So this module:
  - repairs trailing commas before parsing,
  - accepts a list, a {"data": [...]} wrapper, or a dict-of-records,
  - finds the rainfall field from a list of likely names (or IMD_RAIN_FIELD),
  - treats "", "NA", "-", "-999" etc. as missing instead of crashing.
Run probe_imd.py once against the real API to confirm every assumption.
"""

import json
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import config

RAIN_FIELD_CANDIDATES = [
    "RAINFALL", "RAIN", "RF", "RAIN_FALL", "RAINFALL_MM", "RAIN_MM",
    "CUM_RAINFALL", "CUMULATIVE_RAINFALL", "RAINFALL_CUMULATIVE",
    "HOURLY_RAINFALL", "RAINFALL_1HR", "PAST_24_HRS_RAINFALL",
    "Rainfall", "Rain", "rainfall", "rain",
]

MISSING_VALUES = {"", "na", "n/a", "nan", "null", "none", "-", "--", "nil", "trace"}
MISSING_NUMBERS = {-999.0, -9999.0, -99.0, 999.0, 9999.0}

# Station-wise nowcast category values -> meaning (IMD api_reference.html)
NOWCAST_CATEGORIES = {
    1: "No weather",
    2: "Light rain (<5 mm/h)",
    3: "Light snow",
    4: "Light thunderstorm",
    5: "Slight dust storm",
    6: "Low lightning probability",
    7: "Moderate rain (5-15 mm/h)",
    8: "Moderate snow",
    9: "Moderate thunderstorm",
    10: "Moderate dust storm",
    11: "Moderate lightning probability",
    12: "Heavy rain (>15 mm/h)",
    13: "Heavy snow",
    14: "Severe thunderstorm",
    15: "Very severe thunderstorm",
    16: "High lightning probability",
    17: "High lightning probability",
    31: "Other warning",
    32: "Thunderstorm with hail",
    33: "Severe dust storm",
}
NOWCAST_COLORS = {1: "green", 2: "yellow", 3: "orange", 4: "red"}


class IMDError(RuntimeError):
    """Raised when IMD can't be reached or returns something unusable."""


# ---------------------------------------------------------------- transport

def _headers():
    headers = {"Accept": "application/json", "User-Agent": "FLARE/1.0 (REVA University SIH 2026)"}
    if config.IMD_API_KEY:
        headers[config.IMD_API_KEY_HEADER] = config.IMD_API_KEY
    return headers


def parse_lenient_json(text):
    """json.loads, but first strip trailing commas like IMD's sample has."""
    text = text.strip().lstrip("﻿")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(repaired)


def fetch_raw(endpoint, params=None):
    """GET one endpoint; returns (parsed_json, raw_text). Raises IMDError."""
    url = f"{config.IMD_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(url, params=params, headers=_headers(), timeout=config.IMD_TIMEOUT_S)
    except requests.RequestException as e:
        raise IMDError(f"Could not reach {url}: {e}") from e

    if resp.status_code in (401, 403):
        raise IMDError(
            f"{url} returned HTTP {resp.status_code}. IMD access is IP-whitelisted: "
            "this machine's public IP is probably not the one IMD approved."
        )
    if resp.status_code != 200:
        raise IMDError(f"{url} returned HTTP {resp.status_code}: {resp.text[:200]}")

    body = resp.text
    if body.lstrip().startswith("<"):
        raise IMDError(f"{url} returned HTML instead of JSON (often an access-denied page): {body[:200]}")
    try:
        return parse_lenient_json(body), body
    except json.JSONDecodeError as e:
        raise IMDError(f"{url} returned invalid JSON ({e}): {body[:200]}") from e


def as_records(payload):
    """Normalise IMD's possible response shapes into a list of dicts."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "Data", "DATA", "result", "results", "records"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
        values = list(payload.values())
        if values and all(isinstance(v, dict) for v in values):
            return values
        return [payload]
    return []


# ---------------------------------------------------------------- field helpers

def to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        s = str(value).strip()
        if s.lower() in MISSING_VALUES:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    if math.isnan(f) or f in MISSING_NUMBERS:
        return None
    return f


def get_ci(record, *names):
    """Case-insensitive field lookup; returns the first non-empty match."""
    lowered = {k.lower().strip(): v for k, v in record.items()}
    for name in names:
        v = lowered.get(name.lower())
        if v not in (None, ""):
            return v
    return None


def detect_rain_field(records):
    """Return the name of the rainfall field in aws_data records, or None."""
    if config.IMD_RAIN_FIELD:
        return config.IMD_RAIN_FIELD
    keys = set()
    for r in records[:50]:
        keys.update(r.keys())
    for candidate in RAIN_FIELD_CANDIDATES:
        if candidate in keys:
            return candidate
    for k in sorted(keys):  # last resort: anything with "rain" in its name
        if "rain" in k.lower():
            return k
    return None


def parse_obs_time(date_str, time_str):
    """IMD DATE ('2024-06-06') + TIME ('07:00:00') -> aware UTC datetime."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    time_str = str(time_str or "00:00:00").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H%M"):
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", fmt)
            break
        except ValueError:
            continue
    else:
        return None
    return naive.replace(tzinfo=ZoneInfo(config.IMD_TIME_ZONE)).astimezone(timezone.utc)


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- parsers

def parse_station_mapping(records):
    """aws_data_mapping -> {station_id: {name, district, lat, lon}} (shape not documented)."""
    stations = {}
    for r in records:
        sid = get_ci(r, "ID", "STATION_ID", "STN_ID", "CALL_SIGN", "STATIONID")
        if sid is None:
            continue
        stations[str(sid).strip()] = {
            "name": get_ci(r, "STATION", "STATION_NAME", "NAME", "STN_NAME"),
            "district": get_ci(r, "DISTRICT", "DISTRICT_NAME"),
            "call_sign": get_ci(r, "CALL_SIGN"),
            "lat": to_float(get_ci(r, "Latitude", "LAT", "LATITUDE")),
            "lon": to_float(get_ci(r, "Longitude", "LON", "LONG", "LONGITUDE")),
        }
    return stations


def parse_aws_records(records, mapping=None, rain_field=None):
    """
    aws_data records -> list of observation dicts:
        station_id, name, district, lat, lon, obs_time (UTC datetime), rain_raw_mm
    rain_raw_mm is whatever the rain field holds (cumulative or per-interval,
    see config.IMD_RAIN_MODE) - differencing happens in ingest.py.
    """
    mapping = mapping or {}
    rain_field = rain_field or detect_rain_field(records)
    out = []
    for r in records:
        sid = get_ci(r, "ID", "STATION_ID", "CALL_SIGN")
        if sid is None:
            continue
        sid = str(sid).strip()
        meta = mapping.get(sid) or mapping.get(str(get_ci(r, "CALL_SIGN") or "")) or {}
        lat = to_float(get_ci(r, "Latitude", "LAT", "LATITUDE")) or meta.get("lat")
        lon = to_float(get_ci(r, "Longitude", "LON", "LONGITUDE")) or meta.get("lon")
        obs_time = parse_obs_time(get_ci(r, "DATE", "OBS_DATE"), get_ci(r, "TIME", "OBS_TIME"))
        if lat is None or lon is None or obs_time is None:
            continue
        out.append({
            "station_id": sid,
            "name": get_ci(r, "STATION", "STATION_NAME") or meta.get("name") or sid,
            "district": get_ci(r, "DISTRICT") or meta.get("district"),
            "lat": lat,
            "lon": lon,
            "obs_time": obs_time,
            "rain_raw_mm": to_float(r.get(rain_field)) if rain_field else None,
        })
    return out, rain_field


def stations_near_ward(observations, radius_km=None):
    radius_km = radius_km or config.STATION_RADIUS_KM
    near = []
    for o in observations:
        d = haversine_km(config.WARD_CENTER_LAT, config.WARD_CENTER_LON, o["lat"], o["lon"])
        if d <= radius_km:
            near.append({**o, "distance_km": round(d, 2)})
    return near


def parse_station_nowcast(records, station_name=None):
    """stationnowcast -> one summary dict for our station, or None."""
    station_name = (station_name or config.IMD_NOWCAST_STATION).lower()
    pick = None
    for r in records:
        name = str(get_ci(r, "Station", "STATION", "Station name") or "")
        if name.lower() == station_name or station_name in name.lower():
            pick = r
            break
    if pick is None and len(records) == 1:
        pick = records[0]
    if pick is None:
        return None

    codes = []
    for i in range(1, 20):
        raw = get_ci(pick, f"Cat{i}")
        for part in re.split(r"[/,\s]+", str(raw or "")):
            v = to_float(part)
            if v is not None and int(v) in NOWCAST_CATEGORIES and int(v) != 1:
                codes.append(int(v))
    color_code = int(to_float(get_ci(pick, "color")) or 1)
    return {
        "station": get_ci(pick, "Station", "STATION"),
        "date": get_ci(pick, "Date"),
        "time_of_issue": get_ci(pick, "toi"),
        "valid_upto": get_ci(pick, "Vupto"),
        "color_code": color_code,
        "color": NOWCAST_COLORS.get(color_code, "green"),
        "categories": [NOWCAST_CATEGORIES[c] for c in sorted(set(codes))] or ["No weather"],
        "message": get_ci(pick, "message"),
    }


# ---------------------------------------------------------------- high level

def fetch_aws(state_id=None):
    payload, raw = fetch_raw("aws_data", {"sid": state_id or config.IMD_STATE_ID})
    return as_records(payload), raw


def fetch_mapping():
    payload, raw = fetch_raw("aws_data_mapping")
    return as_records(payload), raw


def fetch_station_nowcast(station=None):
    payload, raw = fetch_raw("stationnowcast", {"id": station or config.IMD_NOWCAST_STATION})
    return as_records(payload), raw
