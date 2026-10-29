"""
probe_imd.py - run this FIRST, on the machine whose IP IMD whitelisted.

    cd flare_repo/live-ingest
    pip install -r requirements.txt
    python probe_imd.py

It calls each approved IMD API once, saves the raw responses to
data/live/raw/probe_*.json, and prints what it found:
  - whether access works from this machine (and its public IP),
  - which field holds rainfall, and sample values,
  - which IMD stations are near Koramangala,
  - whether DATE/TIME look like UTC or IST,
  - which station name works for the station-wise nowcast,
followed by the .env lines to use. Nothing here changes the running app.
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import config
import imd_client as imd

NOWCAST_NAME_GUESSES = [
    config.IMD_NOWCAST_STATION, "Bengaluru", "Bangalore", "BENGALURU", "BANGALORE",
    "Bengaluru City", "Bangalore City", "Bengaluru Urban", "Bangalore Urban",
    "Bengaluru HAL", "HAL Airport", "Kempegowda International Airport",
]


def save(name, text):
    config.RAW_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.RAW_SNAPSHOT_DIR / f"probe_{name}.json"
    path.write_text(text, encoding="utf-8")
    return path


def heading(text):
    print("\n" + "=" * 70 + f"\n{text}\n" + "=" * 70)


def main():
    heading("0. This machine")
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
        print(f"Public IP: {ip}   <- must match the IP IMD whitelisted")
    except requests.RequestException:
        print("Couldn't look up public IP (no internet?)")
    print(f"IMD base URL: {config.IMD_BASE_URL}")

    suggestions = {}
    ok = True

    # ---------------------------------------------------------- mapping
    heading("1. aws_data_mapping")
    mapping = {}
    try:
        records, raw = imd.fetch_mapping()
        print(f"OK - {len(records)} records, saved {save('aws_data_mapping', raw)}")
        if records:
            print("Fields:", sorted(records[0].keys()))
            print("First record:", json.dumps(records[0], indent=1)[:600])
        mapping = imd.parse_station_mapping(records)
    except imd.IMDError as e:
        ok = False
        print("FAILED:", e)

    # ---------------------------------------------------------- aws data
    heading(f"2. aws_data?sid={config.IMD_STATE_ID} (Karnataka)")
    try:
        records, raw = imd.fetch_aws()
        print(f"OK - {len(records)} records, saved {save('aws_data', raw)}")
        if records:
            print("Fields:", sorted(records[0].keys()))
            districts = Counter(str(imd.get_ci(r, "DISTRICT")) for r in records)
            print("Districts (top 10):", districts.most_common(10))

        obs, rain_field = imd.parse_aws_records(records, mapping)
        print(f"\nParsed observations with location + time: {len(obs)}/{len(records)}")
        if rain_field:
            print(f"Rainfall field detected: '{rain_field}'")
            suggestions["IMD_RAIN_FIELD"] = rain_field
        else:
            print("!! No rainfall field found. Look at the fields above; if one is rainfall,")
            print("   set IMD_RAIN_FIELD to it. If none is, aws_data alone can't drive FLARE -")
            print("   ask IMD whether rainfall comes from a different endpoint or an ARG feed.")
            ok = False

        near = sorted(imd.stations_near_ward(obs), key=lambda o: o["distance_km"])
        print(f"\nStations within {config.STATION_RADIUS_KM:.0f} km of Koramangala: {len(near)}")
        for o in near[:25]:
            print(f"  {o['station_id']:>10}  {str(o['name'])[:28]:28}  {o['distance_km']:5.1f} km  "
                  f"obs {o['obs_time']:%Y-%m-%d %H:%M} UTC  rain={o['rain_raw_mm']}")
        if len(near) < 3:
            print("!! Fewer than 3 nearby stations - the nowcast will fall back to persistence.")
            print("   Try STATION_RADIUS_KM=50, and keep pushing KSNDMC for ward-level gauges.")

        # Time zone sanity check: the newest reading should be a little in the past.
        if obs:
            newest = max(obs, key=lambda o: o["obs_time"])
            naive = newest["obs_time"].astimezone(ZoneInfo(config.IMD_TIME_ZONE)).replace(tzinfo=None)
            now = datetime.now(timezone.utc)
            as_utc = (now - naive.replace(tzinfo=timezone.utc)).total_seconds() / 60
            as_ist = (now - naive.replace(tzinfo=ZoneInfo("Asia/Kolkata"))).total_seconds() / 60
            print(f"\nNewest DATE/TIME in the feed: {naive}")
            print(f"  read as UTC -> {as_utc:6.0f} min old")
            print(f"  read as IST -> {as_ist:6.0f} min old")
            # The wrong reading lands 5.5 h in the future (IST read as UTC) or
            # 5.5 h too old (UTC read as IST); the right one is the freshest
            # reading that isn't in the future.
            plausible = {k: v for k, v in {"UTC": as_utc, "Asia/Kolkata": as_ist}.items() if v >= -5}
            if plausible:
                guess = min(plausible, key=plausible.get)
                print(f"  -> looks like {guess}")
                suggestions["IMD_TIME_ZONE"] = guess
            else:
                print("  -> unclear (both readings are in the future); check the raw file")

            # Cumulative check: values that only grow through the day are cumulative.
            vals = [o["rain_raw_mm"] for o in near if o["rain_raw_mm"] is not None]
            if vals:
                print(f"\nRain values near ward: min={min(vals)} max={max(vals)} "
                      f"(non-zero: {sum(v > 0 for v in vals)}/{len(vals)})")
                print("  Run this probe again in an hour: if values only go up (and reset after "
                      "08:30 IST), keep IMD_RAIN_MODE=cumulative; otherwise set it to interval.")
    except imd.IMDError as e:
        ok = False
        print("FAILED:", e)

    # ---------------------------------------------------------- nowcast
    heading("3. stationnowcast")
    found = None
    for name in dict.fromkeys(NOWCAST_NAME_GUESSES):
        try:
            records, raw = imd.fetch_station_nowcast(name)
        except imd.IMDError as e:
            print(f"  id={name!r}: FAILED ({e})")
            if "whitelisted" in str(e):
                break
            continue
        summary = imd.parse_station_nowcast(records, name)
        print(f"  id={name!r}: {len(records)} records -> {summary['station'] if summary else 'no match'}")
        if summary:
            found = name
            save("stationnowcast", raw)
            print("    ", json.dumps(summary)[:300])
            break
    if found:
        suggestions["IMD_NOWCAST_STATION"] = found
    else:
        print("No station name matched. Try the endpoint with no id to list all stations:")
        print(f"  {config.IMD_BASE_URL}/stationnowcast")

    # ---------------------------------------------------------- summary
    heading("Suggested settings (put these in flare_repo/.env)")
    for k, v in suggestions.items():
        print(f"{k}={v}")
    print("\nAll three APIs reachable." if ok else "\nSome checks failed - see above.")
    print("Share the files in data/live/raw/ if anything looks off.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
