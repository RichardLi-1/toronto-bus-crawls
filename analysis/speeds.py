#!/usr/bin/env python3
"""Turn raw TTC fixes into speed aggregates, following the CTA Bus Speed Scorecard method.

Each pair of consecutive fixes on one vehicle and trip becomes a measurement: distance
along the GTFS shape divided by elapsed time, so time at stops and red lights counts.
Distance and time are spread along the shape into ~220 m grid cells and pooled as total
distance / total time, never as an average of speeds.

Writes data/agg/cells.csv.gz (cell x route x period x day) and data/agg/qa.json.
"""
import argparse, glob, json, os
import numpy as np, pandas as pd
from scipy.spatial import cKDTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = "America/Toronto"
LAT0, LON0 = 43.70, -79.40
MX, MY = 111320 * np.cos(np.radians(LAT0)), 110950   # metres per degree at Toronto
THETA = np.radians(16.7)   # Toronto's street grid sits this far off true north
CELL = 220.0
STEP = 20.0                # resolution for spreading an interval along the shape
MAX_OFF = 50.0             # fix further than this from its shape: detour or bad GPS
MIN_DT, MAX_DT = 5, 120    # seconds between fixes; longer gaps hide too much
MAX_KMH = 100.0
MAX_BACK = 30.0            # backward jitter tolerated, counted as zero movement
TERMINAL = 250.0           # intervals this close to a shape end count as layover

PERIODS = {"am": (7, 9), "midday": (10, 14), "pm": (16, 18)}

def xy(lat, lon):
    return (np.asarray(lon) - LON0) * MX, (np.asarray(lat) - LAT0) * MY

def grid_xy(x, y):
    """Rotate so Yonge St runs straight up: cells then follow streets, not a staircase."""
    return x * np.cos(THETA) + y * np.sin(THETA), -x * np.sin(THETA) + y * np.cos(THETA)

def cell_id(x, y):
    gx, gy = grid_xy(x, y)
    return (np.floor(gx / CELL).astype(np.int64) + 1000) * 10000 + np.floor(gy / CELL).astype(np.int64) + 1000

def load_shapes():
    sh = pd.read_csv(f"{ROOT}/data/gtfs/shapes.txt", dtype={"shape_id": str})
    sh = sh.sort_values(["shape_id", "shape_pt_sequence"])
    out = {}
    for sid, g in sh.groupby("shape_id", sort=False):
        x, y = xy(g.shape_pt_lat.values, g.shape_pt_lon.values)
        seg = np.hypot(np.diff(x), np.diff(y))
        keep = np.r_[True, seg > 0]
        x, y = x[keep], y[keep]
        if len(x) < 2:
            continue
        s = np.r_[0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
        grid = np.arange(0, s[-1] + STEP, STEP)
        gx, gy = np.interp(grid, s, x), np.interp(grid, s, y)
        cell = cell_id(gx, gy)
        out[sid] = dict(x=x, y=y, s=s, tree=cKDTree(np.c_[x, y]), L=s[-1], cell=cell)
    return out

def project(shape, px, py):
    """Distance along the shape and offset from it, refined on the two segments at the nearest vertex."""
    x, y, s = shape["x"], shape["y"], shape["s"]
    _, i = shape["tree"].query(np.c_[px, py])
    best_s, best_d = np.zeros(len(px)), np.full(len(px), np.inf)
    for a in (i - 1, i):
        a = np.clip(a, 0, len(x) - 2)
        dx, dy = x[a + 1] - x[a], y[a + 1] - y[a]
        t = np.clip(((px - x[a]) * dx + (py - y[a]) * dy) / (dx * dx + dy * dy), 0, 1)
        d = np.hypot(px - (x[a] + t * dx), py - (y[a] + t * dy))
        better = d < best_d
        best_d[better] = d[better]
        best_s[better] = (s[a] + t * np.hypot(dx, dy))[better]
    return best_s, best_d

def period_of(hour):
    p = np.full(len(hour), "other", dtype=object)
    for name, (a, b) in PERIODS.items():
        p[(hour >= a) & (hour < b)] = name
    return p

def process_day(files, shapes, trips, qa):
    df = pd.concat([pd.read_csv(f, dtype={"vehicle": str, "route": str, "trip": str},
                                usecols=["ts", "vehicle", "route", "trip", "lat", "lon"]) for f in files])
    qa["fixes"] += len(df)
    df = df.drop_duplicates(["vehicle", "ts"])
    df["shape"] = df.trip.map(trips)
    qa["no_shape"] += int(df["shape"].isna().sum())
    df = df.dropna(subset=["shape"])
    px, py = xy(df.lat.values, df.lon.values)
    df["px"], df["py"] = px, py
    df["s"], df["off"] = np.nan, np.nan
    for sid, idx in df.groupby("shape").indices.items():
        sh = shapes.get(sid)
        if sh is None:
            continue
        s, d = project(sh, px[idx], py[idx])
        df.iloc[idx, df.columns.get_loc("s")] = s
        df.iloc[idx, df.columns.get_loc("off")] = d
    off = (df.off > MAX_OFF) | df.off.isna()
    qa["off_route"] += int(off.sum())
    df = df[~off].sort_values(["vehicle", "trip", "ts"])

    same = (df.vehicle.values[1:] == df.vehicle.values[:-1]) & (df.trip.values[1:] == df.trip.values[:-1])
    a, b = df.iloc[:-1][same], df.iloc[1:][same]
    dt = b.ts.values - a.ts.values
    ds = b.s.values - a.s.values
    pairs = pd.DataFrame(dict(route=a.route.values, shape=a["shape"].values, s0=a.s.values,
                              ds=ds, dt=dt, mid=(a.ts.values + b.ts.values) / 2))
    qa["pairs"] += len(pairs)
    bad_dt = (pairs.dt < MIN_DT) | (pairs.dt > MAX_DT)
    bad_back = pairs.ds < -MAX_BACK
    bad_fast = pairs.ds / pairs.dt.clip(lower=1) * 3.6 > MAX_KMH
    qa["bad_gap"] += int(bad_dt.sum()); qa["bad_backward"] += int((bad_back & ~bad_dt).sum())
    qa["bad_fast"] += int((bad_fast & ~bad_dt & ~bad_back).sum())
    pairs = pairs[~(bad_dt | bad_back | bad_fast)].copy()
    pairs["ds"] = pairs.ds.clip(lower=0)
    L = pairs["shape"].map(lambda k: shapes[k]["L"]).values
    term = (pairs.s0 < TERMINAL) | (pairs.s0 + pairs.ds > L - TERMINAL)
    qa["terminal"] += int(term.sum())
    pairs = pairs[~term]
    qa["kept"] += len(pairs)

    local = pd.to_datetime(pairs.mid, unit="s", utc=True).dt.tz_convert(TZ)
    pairs["period"] = period_of(local.dt.hour.values)
    pairs["hour"] = local.dt.hour.values

    parts = []
    for k, (sid, g) in enumerate(pairs.groupby("shape")):
        cell = shapes[sid]["cell"]
        i0 = np.minimum((g.s0.values / STEP).astype(int), len(cell) - 1)
        i1 = np.minimum(((g.s0.values + g.ds.values) / STEP).astype(int), len(cell) - 1)
        n = i1 - i0 + 1
        rep = np.repeat(np.arange(len(g)), n)
        step = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n) + i0[rep]
        parts.append(pd.DataFrame(dict(cell=cell[step], route=g.route.values[rep], period=g.period.values[rep],
                                       hour=g.hour.values[rep], pair=rep + k * 10**8,
                                       dist=(g.ds.values / n)[rep], time=(g.dt.values / n)[rep])))
    if not parts:
        return pd.DataFrame()
    x = pd.concat(parts)
    return (x.groupby(["cell", "route", "period", "hour"], as_index=False)
             .agg(dist=("dist", "sum"), time=("time", "sum"), n=("pair", "nunique")))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-weekends", action="store_true")
    ap.add_argument("--days", nargs="*", help="only these YYYY-MM-DD folders")
    args = ap.parse_args()
    trips = pd.read_csv(f"{ROOT}/data/gtfs/trips.txt", dtype=str, usecols=["trip_id", "shape_id"])
    trips = dict(zip(trips.trip_id, trips.shape_id))
    shapes = load_shapes()
    qa = dict.fromkeys(["fixes", "no_shape", "off_route", "pairs", "bad_gap", "bad_backward",
                        "bad_fast", "terminal", "kept"], 0)
    frames, days = [], []
    for d in sorted(os.path.basename(p) for p in glob.glob(f"{ROOT}/data/raw/*")):
        if args.days and d not in args.days:
            continue
        if not args.include_weekends and pd.Timestamp(d).weekday() >= 5:
            continue
        files = sorted(glob.glob(f"{ROOT}/data/raw/{d}/*.csv.gz"))
        if not files:
            continue
        r = process_day(files, shapes, trips, qa)
        if len(r):
            r.insert(0, "day", d)
            frames.append(r)
            days.append(d)
    os.makedirs(f"{ROOT}/data/agg", exist_ok=True)
    out = pd.concat(frames)
    out.to_csv(f"{ROOT}/data/agg/cells.csv.gz", index=False)
    qa["days"] = days
    json.dump(qa, open(f"{ROOT}/data/agg/qa.json", "w"), indent=1)
    print(json.dumps(qa, indent=1))
    print("rows", len(out), "cells", out.cell.nunique())

if __name__ == "__main__":
    main()
