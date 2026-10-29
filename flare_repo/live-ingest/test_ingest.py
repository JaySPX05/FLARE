"""
Offline tests for the live ingest - no IMD access needed.

    cd live-ingest && pytest -q test_ingest.py

IMD responses are faked in the documented shape (api_reference.html sample,
trailing comma included), with an assumed RAINFALL field holding rain
cumulative since 08:30 IST. A rain cell drifts east across ten stations
around Bengaluru over four hourly reports.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

import config
import imd_client as imd
import ingest
import risk_engine

STATIONS = [  # (id, name, lat, lon) - roughly Bengaluru Urban, plus one far-away station
    ("KA01", "BENGALURU CITY", 12.973, 77.585), ("KA02", "HAL AIRPORT", 12.950, 77.668),
    ("KA03", "JAYANAGAR", 12.925, 77.583), ("KA04", "WHITEFIELD", 12.970, 77.750),
    ("KA05", "ELECTRONIC CITY", 12.845, 77.660), ("KA06", "YELAHANKA", 13.100, 77.596),
    ("KA07", "KENGERI", 12.912, 77.483), ("KA08", "HEBBAL", 13.035, 77.597),
    ("KA09", "SARJAPUR", 12.860, 77.786), ("KA99", "MYSURU", 12.295, 76.639),
]


def cell_rain_mm_hr(lon, hour_index):
    """A storm cell centred west of the city at hour 0, drifting ~0.06 deg east per hour."""
    centre = 77.48 + 0.06 * hour_index
    return max(0.0, 24.0 * (1 - abs(lon - centre) / 0.18))


def aws_payload(obs_time_utc, hour_index):
    """Fake aws_data text for one report, cumulative rain since 08:30 IST, trailing comma included."""
    rows = []
    for sid, name, lat, lon in STATIONS:
        cumulative = sum(cell_rain_mm_hr(lon, h) for h in range(hour_index + 1))
        rows.append(
            '{"ID": "%s", "CALL_SIGN": "%s", "DISTRICT": "BENGALURU_URBAN", "STATE": "KARNATAKA", '
            '"STATION": "%s", "DATE": "%s", "TIME": "%s", "CURR_TEMP": "24.1", "RH": "88", '
            '"Latitude": "%s", "Longitude": "%s", "RAINFALL": "%.1f", "Feel Like": "25.0",}'
            % (sid, sid, name, obs_time_utc.strftime("%Y-%m-%d"), obs_time_utc.strftime("%H:%M:%S"),
               lat, lon, cumulative)
        )
    return "[" + ",".join(rows) + "]"


NOWCAST_PAYLOAD = json.dumps([{
    "Station": "Bengaluru", "Date": "2026-10-29", "Cat1": "0", "Cat2": "0", "Cat7": "7",
    "Cat9": "9", "message": "Moderate rain with thunderstorm likely", "toi": "1530",
    "Vupto": "1830", "color": "3",
}])


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    for name in ("LIVE_DIR",):
        monkeypatch.setattr(config, name, tmp_path)
    monkeypatch.setattr(config, "OBSERVATIONS_FILE", tmp_path / "observations.csv")
    monkeypatch.setattr(config, "STATIONS_FILE", tmp_path / "stations.json")
    monkeypatch.setattr(config, "LIVE_LOOKUP_FILE", tmp_path / "risk_lookup_koramangala.json")
    monkeypatch.setattr(config, "GAUGES_FILE", tmp_path / "gauges_koramangala.json")
    monkeypatch.setattr(config, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(config, "RAW_SNAPSHOT_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "IMD_TIME_ZONE", "UTC")
    monkeypatch.setattr(config, "IMD_RAIN_FIELD", "")
    monkeypatch.setattr(config, "IMD_RAIN_MODE", "cumulative")
    monkeypatch.setattr(config, "FRAME_MINUTES", 60)
    monkeypatch.setattr(config, "RAIN_THRESHOLD_SCHEME", "imd")
    return tmp_path


def fake_imd(monkeypatch, report_times):
    """Route imd.fetch_raw to fake payloads; returns a function to advance to the next report."""
    state = {"i": 0}

    def fetch_raw(endpoint, params=None):
        if endpoint == "aws_data":
            text = aws_payload(report_times[state["i"]], state["i"])
        elif endpoint == "aws_data_mapping":
            raise imd.IMDError("mapping not needed - aws_data carries lat/lon")
        elif endpoint == "stationnowcast":
            text = NOWCAST_PAYLOAD
        else:
            raise AssertionError(endpoint)
        return imd.parse_lenient_json(text), text

    monkeypatch.setattr(imd, "fetch_raw", fetch_raw)
    return lambda: state.__setitem__("i", state["i"] + 1)


def hourly_reports(n):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [now - timedelta(hours=n - 1 - k) for k in range(n)]


# ---------------------------------------------------------------- unit tests

def test_lenient_json_repairs_imd_trailing_comma():
    assert imd.parse_lenient_json('[{"a": "1", "b": "2",}]') == [{"a": "1", "b": "2"}]


def test_parse_aws_detects_rain_field_and_filters_by_distance(live_dir):
    t = hourly_reports(1)[0]
    records = imd.as_records(imd.parse_lenient_json(aws_payload(t, 0)))
    obs, field = imd.parse_aws_records(records)
    assert field == "RAINFALL"
    assert len(obs) == len(STATIONS)
    near = imd.stations_near_ward(obs)
    assert {o["station_id"] for o in near} == {s[0] for s in STATIONS} - {"KA99"}  # Mysuru dropped
    assert obs[0]["obs_time"] == t


def test_missing_values_are_none():
    for v in ("", "NA", "-", "-999", None, "trace"):
        assert imd.to_float(v) is None
    assert imd.to_float("3.5") == 3.5


def test_station_nowcast_parse():
    nc = imd.parse_station_nowcast(json.loads(NOWCAST_PAYLOAD), "Bengaluru")
    assert nc["color"] == "orange"
    assert "Moderate rain (5-15 mm/h)" in nc["categories"]
    assert nc["valid_upto"] == "1830"


def test_multiplier_schemes():
    assert risk_engine.rain_multiplier(10, "imd") == 1.25
    assert risk_engine.rain_multiplier(10, "legacy") == 1.00   # 10 mm/h = 2.5 mm/15 min
    assert risk_engine.rain_multiplier(80, "legacy") == 1.60
    assert risk_engine.rain_multiplier(40, "imd") == 2.00


def test_cumulative_reset_at_0830_is_handled(live_dir):
    import pandas as pd
    t0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc)   # 07:30 IST
    df = pd.DataFrame({
        "station_id": ["A", "A", "A"], "lat": [12.9] * 3, "lon": [77.6] * 3,
        "obs_time": pd.to_datetime([t0, t0 + timedelta(hours=1), t0 + timedelta(hours=2)], utc=True),
        "rain_raw_mm": [40.0, 3.0, 8.0],   # counter reset between the first two readings
    })
    rates = ingest.add_rain_rates(df)["rain_rate_mm_hr"].tolist()
    assert pd.isna(rates[0]) and rates[1] == 3.0 and rates[2] == 5.0


# ---------------------------------------------------------------- end to end

def test_full_cycle_writes_lookup_gauges_and_status(live_dir, monkeypatch):
    times = hourly_reports(4)
    advance = fake_imd(monkeypatch, times)

    # First report: nothing to difference against yet -> warm-up failure, no lookup.
    assert ingest.run_cycle() is False
    status = json.loads((live_dir / "status.json").read_text())
    assert "Not enough consecutive readings" in status["last_error"]
    assert not (live_dir / "risk_lookup_koramangala.json").exists()

    for _ in range(3):
        advance()
        assert ingest.run_cycle() is True

    lookup = json.loads((live_dir / "risk_lookup_koramangala.json").read_text())
    keys = list(lookup["timesteps"])
    assert len(keys) == 4
    assert [lookup["timesteps"][k][0]["horizon_label"] for k in keys] == ["T+0", "T+1h", "T+2h", "T+3h"]
    t0 = datetime.fromisoformat(keys[0])
    assert t0.utcoffset() == timedelta(hours=5, minutes=30)
    assert t0 == times[-1]
    assert datetime.fromisoformat(keys[3]) - t0 == timedelta(hours=3)

    base = risk_engine.load_base_segments()
    seg = lookup["timesteps"][keys[0]]
    assert len(seg) == len(base) and seg[0]["segment_id"] == base[0]["segment_id"]
    assert lookup["live"]["nowcast_method"] in ("pysteps", "persistence")
    rate0 = seg[0]["rain_rate_mm_hr"]
    assert rate0 > 0, "storm should be raining over Koramangala at T+0"
    mult = risk_engine.rain_multiplier(rate0, "imd")
    assert seg[0]["risk_score"] == round(min(1.0, base[0]["base_risk"] * mult), 2)

    gauges = json.loads((live_dir / "gauges_koramangala.json").read_text())
    assert gauges["frames"][0]["timestamp"] == keys[0]
    assert len(gauges["frames"][0]["gauges"]) == 9
    assert all(g["rain_rate_mm_hr"] is not None for g in gauges["frames"][0]["gauges"])

    status = json.loads((live_dir / "status.json").read_text())
    assert status["ok"] and status["last_error"] is None
    assert status["imd_nowcast"]["color"] == "orange"


def test_imd_outage_keeps_last_good_data(live_dir, monkeypatch):
    times = hourly_reports(2)
    advance = fake_imd(monkeypatch, times)
    ingest.run_cycle()
    advance()
    assert ingest.run_cycle() is True
    before = (live_dir / "risk_lookup_koramangala.json").read_text()

    def down(endpoint, params=None):
        raise imd.IMDError("HTTP 403 - not whitelisted")
    monkeypatch.setattr(imd, "fetch_raw", down)
    assert ingest.run_cycle() is False
    assert (live_dir / "risk_lookup_koramangala.json").read_text() == before
    status = json.loads((live_dir / "status.json").read_text())
    assert status["ok"] is False and "403" in status["last_error"] and status["last_success"]
