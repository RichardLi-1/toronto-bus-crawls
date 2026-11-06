# Where the TTC crawls

A Toronto version of Michael McLean's CTA Bus Speed Scorecard. No usable archive of past TTC
vehicle positions was found, so this project records its own.

## Data

| What | Source |
|---|---|
| Vehicle positions (live, no key) | https://gtfsrt.ttc.ca/vehicles/position?format=binary |
| Routes, trips, shapes | City of Toronto Open Data, `surface-routes-and-schedules-for-bustime` (SurfaceGTFS), valid to 2026-12-19 |
| Ridership ranking | TTC 2025 Fall Service Hours and Boardings by Route (October 2025) |
| City outline | City of Toronto Open Data, `city-wards` |

Licence: Open Government Licence – Toronto. Attribution is on the poster.

Checked and rejected: `windrim/ttc-gtfs-rt` is hourly snapshots from Feb–Mar 2025, too sparse for speeds.

## Run

    .venv/bin/python -I collector/collect.py     # keep running; writes data/raw/DATE/HH.csv.gz
    .venv/bin/python -P analysis/speeds.py       # raw fixes to data/agg/
    .venv/bin/python analysis/poster.py          # 4-6 PM poster to out/

## On the VM

Project `toronto-bus-crawls-261106`, instance `ttc-collector`, zone `us-east1-b`. Data is in `/opt/ttc/data`.

- `ttc-collector`: TTC positions every 30 s, to `data/raw`
- `ttc-plow`: PlowTO changes, vehicles every 60 s and routes every 5 min, to `data/plow`

PlowTO layers are undocumented with no stated licence, so that data stays private for now.

## Things to know

- The poster covers the 20 busiest surface routes, streetcars included. Downtown is served by
  streetcars, so a bus-only map leaves the core empty (`--mode bus` still exists for comparison).
- The GTFS is valid to 2026-12-19. Trip IDs change with each new schedule, so download the new
  feed before that date and keep the old trips and shapes for the days it covers.
- The ridership PDF drops trailing zeros in its Hours, Kilometres and Vehicles columns. Only
  Customers per Weekday is used here, and that column is intact.
- October 2025 ridership predates Lines 5 and 6. Routes 36 and 32 are left out of the ranking;
  35 was also split at Eglinton (the south end is now 27) and stays in on its old figure.
- Fixes more than 50 m from the shape are dropped, so detours are not measured.
