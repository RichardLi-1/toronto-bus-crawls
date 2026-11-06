#!/usr/bin/env python3
"""Record the City of Toronto's PlowTO layers, saving only what changes.

  data/plow/vehicles/YYYY-MM-DD/HH.csv.gz   ts,master_id,id,name,type,lat,lon,speed
  data/plow/routes/YYYY-MM-DD/HH.jsonl.gz   {"ts":..,"layer":..,"a":{attributes}}

Vehicles have no timestamp of their own, so ts is the poll time and a parked vehicle whose GPS
stopped looks the same as one standing still. A row is written the first time it is seen after
start-up, then only when it changes, so each restart writes one full snapshot.

The layers are undocumented and have no stated licence: save privately, and don't publish
until that's settled. The _QA_ layers are deliberately left alone. A moving plow may not be
plowing. Exits on its own when free disk falls below MIN_FREE_MB.
"""
import gzip, hashlib, json, shutil, signal, sys, time, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://services3.arcgis.com/b9WvedVPoizGfvfD/arcgis/rest/services"
VEHICLES = "TS_PlowTOGT_Vehicles_view"
ROUTES = [f"TS_PlowTOG2_{k}_Route_view_{n}" for k in ("Plow", "Salt", "Cycle", "Sidewalk") for n in (0, 1, 2)]
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "plow"
TZ = ZoneInfo("America/Toronto")
VEHICLE_S, ROUTE_S, FLUSH_S = 60, 300, 300
PAGE = 1000
MIN_FREE_MB = 1000
HEADER = "ts,master_id,id,name,type,lat,lon,speed\n"

stop = False
def _stop(*_):
    global stop
    stop = True
signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

def log(msg):
    print(f"{datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}", flush=True)

def query(layer, fields="*"):
    rows, offset = [], 0
    while True:
        params = dict(where="1=1", outFields=fields, returnGeometry="false", orderByFields="OBJECTID",
                      resultOffset=offset, resultRecordCount=PAGE, f="json")
        url = f"{BASE}/{layer}/FeatureServer/0/query?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "toronto-bus-crawls/0.1"})
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.load(r)
        if "error" in d:
            raise RuntimeError(f"{layer}: {d['error']}")
        page = d.get("features", [])
        rows += [f["attributes"] for f in page]
        offset += len(page)
        if not page or not d.get("exceededTransferLimit"):
            return rows

def path_for(kind, ext):
    now = datetime.now(TZ)
    return OUT / kind / f"{now:%Y-%m-%d}" / f"{now:%H}.{ext}.gz"

def flush(buffers):
    for path, rows in buffers.items():
        if not rows:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with gzip.open(path, "at", compresslevel=9) as f:
            if new and path.name.endswith(".csv.gz"):
                f.write(HEADER)
            f.writelines(rows)
    buffers.clear()

def poll_vehicles(last, buffers):
    ts, n = int(time.time()), 0
    rows = buffers.setdefault(path_for("vehicles", "csv"), [])
    for a in query(VEHICLES, "id,name,longitude,latitude,speed,type,master_id"):
        key = a.get("master_id") or a.get("id")
        state = (a.get("latitude"), a.get("longitude"), a.get("speed"))
        if last.get(key) == state:
            continue
        last[key] = state
        lat, lon = state[0], state[1]
        if lat is None or lon is None:
            continue
        rows.append(f"{ts},{key},{a.get('id')},{a.get('name')},{a.get('type')},{lat:.5f},{lon:.5f},{a.get('speed')}\n")
        n += 1
    return n

def poll_routes(seen, buffers):
    ts, n = int(time.time()), 0
    rows = buffers.setdefault(path_for("routes", "jsonl"), [])
    for layer in ROUTES:
        known = seen.setdefault(layer, set())
        for a in query(layer):
            a.pop("OBJECTID", None)
            body = json.dumps(a, sort_keys=True, separators=(",", ":"))
            digest = hashlib.blake2b(body.encode(), digest_size=8).digest()
            if digest in known:
                continue
            known.add(digest)
            rows.append(f'{{"ts":{ts},"layer":"{layer}","a":{body}}}\n')
            n += 1
    return n

def main():
    once = "--once" in sys.argv
    last, seen, buffers = {}, {}, {}
    next_route = last_flush = last_beat = time.time()
    polls = errors = v_rows = r_rows = 0
    log(f"plow collector start vehicles={VEHICLE_S}s routes={ROUTE_S}s")
    while not stop:
        t0 = time.time()
        try:
            v_rows += poll_vehicles(last, buffers)
            if t0 >= next_route:
                r_rows += poll_routes(seen, buffers)
                next_route = t0 + ROUTE_S
            polls += 1
        except Exception as ex:
            errors += 1
            log(f"poll error: {type(ex).__name__}: {str(ex)[:200]}")
        if once:
            break
        if time.time() - last_flush >= FLUSH_S:
            flush(buffers)
            last_flush = time.time()
            free = shutil.disk_usage(ROOT).free // 2**20
            if time.time() - last_beat >= 1800:
                log(f"ok polls={polls} errors={errors} vehicle_rows={v_rows} route_rows={r_rows} free_mb={free}")
                last_beat = time.time()
            if free < MIN_FREE_MB:
                log(f"STOP low disk: {free} MB free")
                return 2
        time.sleep(max(0, VEHICLE_S - (time.time() - t0)))
    flush(buffers)
    log(f"plow collector stop polls={polls} errors={errors} vehicle_rows={v_rows} route_rows={r_rows}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
