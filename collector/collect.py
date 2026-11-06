#!/usr/bin/env python3
"""Poll the TTC GTFS-Realtime vehicle feed and append compact rows to hourly gzip files.

Rows are de-duplicated on (vehicle, fix timestamp), so polling faster than the feed
updates costs no disk. Vehicles with no trip assignment are kept with empty route and trip,
because anything not recorded now is gone; the analysis ignores them.
Exits on its own when free disk falls below MIN_FREE_MB.

  data/raw/YYYY-MM-DD/HH.csv.gz    (Toronto local date and hour of the poll)
  columns: ts,vehicle,route,trip,lat,lon,bearing,speed_ms,stop_seq,stop_id,status,occ
"""
import gzip, os, shutil, signal, sys, time, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from google.transit import gtfs_realtime_pb2 as rt

URL = "https://gtfsrt.ttc.ca/vehicles/position?format=binary"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
TZ = ZoneInfo("America/Toronto")
POLL_S = 30        # fixes arrive about every 20 s
FLUSH_S = 300      # larger gzip members compress better; a crash loses at most this much
MIN_FREE_MB = 120
HEADER = "ts,vehicle,route,trip,lat,lon,bearing,speed_ms,stop_seq,stop_id,status,occ\n"

stop = False
def _stop(*_):
    global stop
    stop = True
signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

def log(msg):
    print(f"{datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}", flush=True)

def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "toronto-bus-crawls/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        feed = rt.FeedMessage()
        feed.ParseFromString(r.read())
    return feed

def flush(buffers):
    for path, rows in buffers.items():
        if not rows:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with gzip.open(path, "at", compresslevel=9) as f:
            if new:
                f.write(HEADER)
            f.writelines(rows)
    buffers.clear()

def main():
    last_ts, buffers = {}, {}
    last_flush = last_beat = time.time()
    polls = errors = rows_total = 0
    log(f"collector start pid={os.getpid()} poll={POLL_S}s")
    while not stop:
        t0 = time.time()
        try:
            feed = fetch()
            now = datetime.now(TZ)
            path = RAW / f"{now:%Y-%m-%d}" / f"{now:%H}.csv.gz"
            rows = buffers.setdefault(path, [])
            for e in feed.entity:
                if not e.HasField("vehicle"):
                    continue
                v = e.vehicle
                vid = v.vehicle.id
                if last_ts.get(vid) == v.timestamp:
                    continue
                last_ts[vid] = v.timestamp
                p = v.position
                rows.append(f"{v.timestamp},{vid},{v.trip.route_id},{v.trip.trip_id},"
                            f"{p.latitude:.5f},{p.longitude:.5f},{p.bearing:.0f},{p.speed:.1f},"
                            f"{v.current_stop_sequence},{v.stop_id},{v.current_status},{v.occupancy_status}\n")
                rows_total += 1
            polls += 1
        except Exception as ex:
            errors += 1
            log(f"poll error: {type(ex).__name__}: {ex}")
        if time.time() - last_flush >= FLUSH_S:
            flush(buffers)
            last_flush = time.time()
            free = shutil.disk_usage(ROOT).free // 2**20
            if time.time() - last_beat >= 1800:
                log(f"ok polls={polls} errors={errors} rows={rows_total} free_mb={free}")
                last_beat = time.time()
            if free < MIN_FREE_MB:
                log(f"STOP low disk: {free} MB free")
                return 2
        time.sleep(max(0, POLL_S - (time.time() - t0)))
    flush(buffers)
    log(f"collector stop polls={polls} errors={errors} rows={rows_total}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
