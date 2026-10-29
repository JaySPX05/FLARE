# live-ingest — live IMD rainfall into FLARE

Replaces the recorded 7 Sep storm with live rainfall from the India Meteorological
Department. It uses the three APIs IMD approved for us:

| API | Used for |
|---|---|
| `aws_data?sid=13` (Karnataka) | rain readings from automatic weather stations near Koramangala |
| `aws_data_mapping` | station names and locations |
| `stationnowcast` | IMD's own 0–3 h warning for Bengaluru, shown as a chip on the dashboard |

```
IMD aws_data ──► stations within 30 km ──► mm/h per station ──► IDW grid ──► PySteps +1/+2/+3 h
                                                                                   │
            base_risk per road segment (scripts/05, static) ──── × rain multiplier ┘
                                                                                   ▼
                     data/live/risk_lookup_koramangala.json  ◄── Risk API + Routing API reload it
                     data/live/gauges_koramangala.json           automatically; the dashboard
                     data/live/status.json                       re-checks every 5 min
```

The output files have exactly the same format as the demo lookup, so nothing
downstream had to change shape.

## Step 1 — check access (do this first)

Every IMD request needs two headers (IMD API Portal User Guide, section 7):
`X-API-KEY` (generated on the portal's API Keys page and bound to one **static public
IP**) and `Authorization: Bearer <JWT>` (from `POST /api/oauth/token.php` with your portal
email and password; it expires after about an hour and the client renews it automatically).
Put all three in `flare_repo/.env` on the server whose IP the key is bound to:

```
IMD_API_KEY=...
IMD_EMAIL=...
IMD_PASSWORD='...'      # single quotes, so characters like $ or # are kept as-is
```

Then run the probe on that machine:

```bash
cd flare_repo/live-ingest
pip install -r requirements.txt
python probe_imd.py
```

It prints this machine's public IP, then for each API: whether it answered, the field
names, which field holds rainfall, the stations near Koramangala, whether times are UTC
or IST, and which station name works for the nowcast. It ends with lines to paste into
`flare_repo/.env` (copy `.env.example` first). Raw responses are saved in
`data/live/raw/` — keep them; they're the ground truth if anything looks wrong.

**Assumptions to check in the probe output.** IMD's docs don't settle these, so the code
handles both cases and the probe tells you which applies:
- **Rainfall field name.** The documented aws_data sample shows no rainfall field at all.
  The code tries common names (`RAINFALL`, `RF`, …). If none match, set `IMD_RAIN_FIELD`.
  If there is no rainfall field, aws_data alone can't drive FLARE; ask IMD where rainfall
  comes from.
- **Cumulative or per-report.** IMD AWS rain is normally a running total since 08:30 IST.
  Run the probe twice an hour apart: if values only grow, keep `IMD_RAIN_MODE=cumulative`.
- **Time zone of DATE/TIME.** The probe compares both readings with the clock.

## Step 2 — run it

Without Docker (four terminals, the usual three plus this one):

```bash
cd flare_repo/live-ingest
python ingest.py --once   # one cycle, prints what it did
python ingest.py          # keep polling every POLL_MINUTES (default 15)
```

With Docker, `docker compose up --build` now also starts an `ingest` container that
writes to a shared volume the two APIs read from.

**The first cycle always says "not enough consecutive readings".** Rain rate is the
difference between two reports, so live data appears after IMD's *second* report,
typically within an hour. Until then the dashboard keeps showing the demo storm.

## When no station is close enough: IMD's city nowcast

As of 29 Oct 2026 the probe found that IMD's `aws_data` feed serves only 16 of
Karnataka's 91 mapped stations. The nearest reporting one is ~47 km from Koramangala.
The BANGALORE AWS (7.6 km) returns "No Data Available", and the four Bengaluru rain
gauges (ARG) return "Invalid ID". Rain 47 km away says almost nothing about a
Koramangala street, so the ingest does **not** widen its radius to reach them.

Instead, when no station within `STATION_RADIUS_KM` has usable readings, each cycle
falls back to **IMD's station-wise nowcast for "Bengaluru City"**. Its rain category is
turned into a representative rate for the band (light 2.5, moderate 10, heavy 20 mm/h;
thunderstorm categories map to the matching band). Steps inside the warning's validity
window get that rate; steps after it get 0. The dashboard labels this mode
"Live · IMD city nowcast" and says plainly that it's a city-wide band, not a measurement.

As soon as a nearby station (e.g. BANGALORE) reports again, the ingest switches back to
station readings automatically. No configuration change is needed.

## Choosing live or demo

| How | Effect |
|---|---|
| `FLARE_DATA_MODE=auto` (default) | live once the ingest has written data, else demo |
| `FLARE_DATA_MODE=demo` | always the recorded storm |
| dashboard URL `?source=demo` / `?source=live` | override for one browser tab |

On a dry day the live map is uniformly low-risk, which is correct but not much to look
at. For a presentation, open `?source=demo` in a second tab to show a storm.

The brand box on the dashboard shows which one you're looking at: **Live · IMD · obs HH:MM**,
**IMD feed stale** (no new reading for 2 h), or **Demo · recorded storm**.

## Things worth knowing

- **Station density is the real limit.** IMD AWS stations are kilometres apart, so the
  rain over Koramangala is interpolated from the nearest few (inverse-distance weighting).
  That's coarser than the 10 demo gauges inside the ward, which is why the KSNDMC
  ward-level gauge request still matters.
- **Too few stations or frames → persistence.** PySteps needs at least 3 stations and 2
  frames; otherwise the forecast holds the current rate flat and the dashboard says so.
- **Rain thresholds.** The demo pipeline's multiplier steps at 5/15/30 mm *per 15 minutes*
  (20/60/120 mm/h), so with live data almost any real rain leaves the multiplier at 1.0.
  `RAIN_THRESHOLD_SCHEME=imd` uses 5/15/30 mm *per hour* instead, matching IMD's own
  light/moderate/heavy categories. This changes the results, so it is a team decision;
  the default stays `legacy` for consistency with the demo.
- **Base risk.** Each cycle multiplies the static GIS score by live rain. That score comes
  from `data/processed/segments_base_koramangala.json`, which `scripts/05` now writes. Until
  you re-run scripts/05 it's recovered from the demo lookup, which is rounded to 2 decimals.
- **Attribution.** IMD's terms require crediting them. The dashboard does this in the map
  footer and the info card whenever live data is shown.
- **Hosting later.** If this moves to Azure, the cloud's outbound IP must be whitelisted
  with IMD too, or the ingest has to keep running on the approved network.

## Tests

```bash
cd live-ingest && pytest -q test_ingest.py
```

These run fully offline with faked IMD responses in the documented format: a storm cell
crossing ten stations, the 08:30 counter reset, an IMD outage (the last good data is kept),
and the nowcast warning parser.
