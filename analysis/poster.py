#!/usr/bin/env python3
"""Render the 'Where the TTC crawls' poster from data/agg/cells.csv.gz as one HTML file.

  --mode surface   20 busiest bus and streetcar routes (default)
  --mode bus       20 busiest bus routes only
  --hours A B      use local hours [A,B) instead of the 4-6 PM rush (for previews)
  --preview TEXT   stamp a banner: use for anything short of a full sample
"""
import argparse, html, json, os
import numpy as np, pandas as pd
from speeds import ROOT, CELL, xy, grid_xy

BINS = [8, 10.5, 13, 16, 19, 24]                  # km/h, from the original's mph breaks
COLORS = ["#8f241d", "#d8432f", "#f29a7c", "#dcd9d1", "#7fb0f2", "#2f7fe0", "#123f8c"]
LABELS = ["under 8 (slower)", "8–10.5", "10.5–13", "13–16", "16–19", "19–24", "24+ (faster)"]
STREETCARS = {"501", "503", "504", "505", "506", "507", "508", "509", "510", "511", "512"}
# Ranked on October 2025 boardings, but since cut back or split by Lines 5 and 6.
RESTRUCTURED = {"36": "Finch West", "32": "Eglinton West"}
CORE = ((43.6365, -79.4000), (43.6717, -79.3780))  # Bathurst/Queens Quay to Jarvis/Bloor
MAP_W = 1180

def pick_routes(mode):
    b = pd.read_csv(f"{ROOT}/analysis/boardings_2025.csv", dtype={"route": str})
    b = b[~b.route.isin(RESTRUCTURED)]
    if mode == "bus":
        b = b[~b.route.isin(STREETCARS)]
    return b.head(20).reset_index(drop=True)

def kmh(g):
    return g.dist.sum() / g.time.sum() * 3.6

def outline():
    feats = json.load(open(f"{ROOT}/data/geo/wards.geojson"))["features"]
    rings = []
    for f in feats:
        for poly in f["geometry"]["coordinates"]:
            for ring in poly:
                a = np.array(ring)[::3]
                x, y = xy(a[:, 1], a[:, 0])
                rings.append(np.c_[grid_xy(x, y)])
    return rings

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["surface", "bus"], default="surface")
    ap.add_argument("--hours", nargs=2, type=int, default=[16, 18])
    ap.add_argument("--min-n", type=int, default=40)
    ap.add_argument("--preview", default="")
    ap.add_argument("--out", default=f"{ROOT}/out/where-the-ttc-crawls.html")
    a = ap.parse_args()

    routes = pick_routes(a.mode)
    d = pd.read_csv(f"{ROOT}/data/agg/cells.csv.gz", dtype={"route": str})
    qa = json.load(open(f"{ROOT}/data/agg/qa.json"))
    d = d[d.route.isin(routes.route) & (d.hour >= a.hours[0]) & (d.hour < a.hours[1])]
    if d.empty:
        raise SystemExit("no measurements for these routes and hours")

    c = d.groupby("cell").agg(dist=("dist", "sum"), time=("time", "sum"), n=("n", "sum"),
                              routes=("route", lambda s: " ".join(sorted(set(s), key=lambda r: int(r))))).reset_index()
    c = c[(c.n >= a.min_n) & (c.dist > 0)].copy()
    c["kmh"] = c.dist / c.time * 3.6
    c["bin"] = np.digitize(c.kmh, BINS)
    c["ix"], c["iy"] = c.cell // 10000 - 1000, c.cell % 10000 - 1000
    if c.empty:
        raise SystemExit("no cells reach the measurement floor")

    rings = outline()
    allp = np.vstack(rings)
    x0, x1, y0, y1 = allp[:, 0].min(), allp[:, 0].max(), allp[:, 1].min(), allp[:, 1].max()
    k = MAP_W / (x1 - x0)
    H = (y1 - y0) * k
    px = lambda x: (x - x0) * k
    py = lambda y: (y1 - y) * k
    paths = "".join("M" + "L".join(f"{px(p[0]):.1f} {py(p[1]):.1f}" for p in r) + "Z" for r in rings)

    cs = CELL * k
    rects = "".join(
        f'<rect x="{px(r.ix * CELL):.1f}" y="{py((r.iy + 1) * CELL):.1f}" width="{cs:.1f}" height="{cs:.1f}" '
        f'fill="{COLORS[r.bin]}" data-k="{r.kmh:.1f}" data-n="{int(r.n)}" data-r="{r.routes}"/>'
        for r in c.itertuples())

    # Badge at each route's far end, nudged apart so none overlap.
    dd = d.merge(c[["cell", "ix", "iy"]], on="cell")
    cx, cy = grid_xy(*xy(43.65, -79.38))
    placed, badges = [], ""
    for rt in routes.route:
        g = dd[dd.route == rt]
        if g.empty:
            continue
        far = g.iloc[np.argmax(np.hypot(g.ix * CELL - cx, g.iy * CELL - cy))]
        bx, by = px(far.ix * CELL), py(far.iy * CELL)
        for _ in range(40):
            hit = next((p for p in placed if abs(p[0] - bx) < 36 and abs(p[1] - by) < 22), None)
            if hit is None:
                break
            by += 22 if by >= hit[1] else -22
        placed.append((bx, by))
        badges += (f'<g transform="translate({bx:.1f},{by:.1f})"><rect x="-17" y="-10" width="34" height="20" rx="4"/>'
                   f'<text y="4.5" text-anchor="middle">{rt}</text></g>')

    gx, gy = zip(*[grid_xy(*xy(*p)) for p in CORE])
    core = c[(c.ix * CELL).between(min(gx), max(gx)) & (c.iy * CELL).between(min(gy), max(gy))]
    core_kmh = kmh(core) if len(core) else float("nan")
    slow_share = (c.kmh < 13).mean() * 100
    all_kmh = kmh(c)

    per = d.groupby("route").agg(dist=("dist", "sum"), time=("time", "sum"), n=("n", "sum"))
    per["kmh"] = per.dist / per.time * 3.6
    per = per[per.n >= 100].join(routes.set_index("route"))
    table = per.sort_values("kmh")
    slowest = "".join(f'<div class="row"><span>{i} {html.escape(r["name"])}</span><b>{r.kmh:.1f}</b></div>'
                      for i, r in table.head(3).iterrows())
    trows = "".join(f'<tr><td>{i}</td><td>{html.escape(r["name"])}</td><td>{int(r.weekday_boardings_oct2025):,}</td>'
                    f'<td>{r.kmh:.1f}</td><td>{int(r.n):,}</td></tr>' for i, r in table.iterrows())
    legend = "".join(f'<div class="lg"><i style="background:{col}"></i>{lab}</div>' for col, lab in zip(COLORS, LABELS))

    h0, h1 = a.hours
    clock = lambda h: f"{(h - 1) % 12 + 1} {'AM' if h < 12 else 'PM'}"
    window = f"{clock(h0)}–{clock(h1)}"
    what = "bus" if a.mode == "bus" else "bus and streetcar"
    days = qa["days"]
    span = days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"
    banner = f'<div class="pv">{html.escape(a.preview)}</div>' if a.preview else ""
    note_ex = ", ".join(f"{k} {v}" for k, v in RESTRUCTURED.items())

    doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Where the TTC crawls</title><style>
:root{{--bg:#fbfaf6;--land:#ebeae5;--ink:#111;--ink2:#555;--ink3:#8b8b86;--accent:#d9663a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 "Helvetica Neue",Helvetica,Arial,sans-serif}}
.wrap{{max-width:1280px;margin:0 auto;padding:44px 50px 30px}}
.kick{{color:var(--accent);font-weight:700;font-size:15px;letter-spacing:.02em;text-transform:uppercase}}
h1{{font:900 86px/1 "Helvetica Neue",Impact,Arial,sans-serif;letter-spacing:-.02em;margin:10px 0 12px;text-transform:uppercase}}
.sub{{color:var(--ink2);font-size:19px;max-width:960px;margin:0 0 18px}}
.pv{{background:#111;color:#fff;font-weight:700;padding:8px 14px;border-radius:4px;margin:0 0 16px}}
svg{{display:block;width:100%;height:auto}}#cells rect:hover{{stroke:#111;stroke-width:1.2}}
.badge rect{{fill:#111}}.badge text{{fill:#fff;font:700 12px "Helvetica Neue",Arial,sans-serif}}
.lake{{fill:#8fa3bd;font:italic 19px "Helvetica Neue",Arial,sans-serif}}
.grid{{display:grid;grid-template-columns:1.05fr 1fr 1fr 1fr;gap:34px;margin-top:22px}}
.lgt,.st{{font-weight:700;color:var(--ink2);margin-bottom:8px}}.lg{{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:15px}}
.lg i{{width:34px;height:20px;border-radius:3px;display:block}}
.big{{font-size:44px;font-weight:800;line-height:1.05}}.cap{{color:var(--ink2);font-size:15px;margin:4px 0 20px}}
.row{{display:flex;justify-content:space-between;margin:6px 0}}
details{{margin-top:18px;color:var(--ink2)}}table{{border-collapse:collapse;width:100%;margin-top:8px;font-size:14px}}td,th{{text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid #e3e1da}}
.foot{{color:var(--ink3);font-size:13px;border-top:1px solid #dcdad3;margin-top:18px;padding-top:10px}}
#tip{{position:fixed;display:none;background:#111;color:#fff;padding:6px 9px;border-radius:4px;font-size:13px;pointer-events:none;z-index:5}}
@media(max-width:900px){{h1{{font-size:56px}}.grid{{grid-template-columns:1fr 1fr}}.wrap{{padding:24px 18px}}}}
</style></head><body><div class="wrap">
<div class="kick">TTC speed scorecard · Toronto's 20 busiest {what} routes</div>
<h1>Where the TTC crawls</h1>
<p class="sub">Average speed, {window}, every ~220 m along Toronto's 20 busiest {what} routes. Both directions combined; time at stops and red lights included.</p>
{banner}
<svg viewBox="0 0 {MAP_W} {H:.0f}" role="img" aria-label="Map of Toronto with average {what} speed in 220 metre cells">
<path d="{paths}" fill="#ebeae5" stroke="#ebeae5" stroke-width="1.5"/>
<g id="cells">{rects}</g><g class="badge">{badges}</g>
<text class="lake" x="{px(cx) + 150:.0f}" y="{H - 24:.0f}">Lake Ontario</text></svg>
<div class="grid">
<div><div class="lgt">Kilometres per hour</div>{legend}</div>
<div><div class="big">{slow_share:.0f}%</div><div class="cap">of mapped stretches average under 13 km/h</div></div>
<div><div class="big">{core_kmh:.1f} km/h</div><div class="cap">downtown core, Bathurst to Jarvis, lake to Bloor</div>
<div class="big">{all_kmh:.1f} km/h</div><div class="cap">all 20 routes combined</div></div>
<div><div class="st">Slowest routes (km/h)</div>{slowest}</div>
</div>
<details><summary>All routes as a table</summary><table><tr><th>Route</th><th>Name</th><th>Weekday boardings, Oct 2025</th><th>km/h</th><th>Measurements</th></tr>{trows}</table></details>
<div class="foot">{len(days)} weekday(s), {span}. Speed = distance along the route ÷ time between GPS fixes, stops included; terminal layovers excluded; fixes more than 50 m off the route, as on detours, not counted.
Cells with fewer than {a.min_n} measurements not drawn. Routes ranked on TTC October 2025 weekday boardings, leaving out {note_ex} (cut back or split since Lines 5 and 6 opened).
Map turned about 17° so Yonge St runs straight up. Data: TTC GTFS-Realtime and GTFS, City of Toronto ward boundaries. Contains information licensed under the Open Government Licence – Toronto. Method after Michael McLean's CTA Bus Speed Scorecard.</div>
</div><div id="tip"></div><script>
const t=document.getElementById('tip');const g=document.getElementById('cells');
g.addEventListener('mousemove',e=>{{const r=e.target;if(!r.dataset.k){{t.style.display='none';return}}
t.textContent=r.dataset.k+' km/h · '+r.dataset.n+' measurements · routes '+r.dataset.r;t.style.display='block';t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY+12)+'px'}});
g.addEventListener('mouseleave',()=>t.style.display='none');
</script></body></html>"""
    open(a.out, "w").write(doc)
    print(f"{a.out} cells={len(c)} slow<13={slow_share:.0f}% core={core_kmh:.1f} (cells {len(core)}) all={all_kmh:.1f}")
    print(table[["name", "kmh", "n"]].round(1).to_string())

if __name__ == "__main__":
    main()
