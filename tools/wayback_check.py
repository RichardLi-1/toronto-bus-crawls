#!/usr/bin/env python3
"""Read-only: how well does the Wayback Machine cover 2026 Toronto election websites?

  wayback_check.py <folder with the City's candidate JSON files> <out.csv>

Stores names, offices and URLs only, never emails or phone numbers. A failed Archive lookup is
recorded as 'error', so it can't be mistaken for 'not archived'. Saves nothing to the Archive.
"""
import csv, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
UA = "toronto-gaps-check/0.1"
NEAR = "20261026"  # election day
SKIP = re.compile(r"(facebook|fb\.me|twitter|x\.com|instagram|linkedin|youtube|youtu\.be|tiktok|threads\.net|bsky\.app|mailto:)", re.I)

def get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(2_000_000), r.geturl()

def available(k):
    """Closest snapshot to polling day. This API can return nothing by mistake, so 'none' is rechecked."""
    url = "https://archive.org/wayback/available?" + urllib.parse.urlencode(dict(url=k, timestamp=NEAR))
    for wait in (0, 10, 30):
        time.sleep(wait)
        try:
            _, body, _ = get(url, 25)
            c = json.loads(body).get("archived_snapshots", {}).get("closest")
            return c["timestamp"][:8] if c and c.get("available") else ""
        except Exception as ex:
            last = ex
    raise last

def recheck(k):
    """Second opinion on 'none': the capture index, homepage only. Newest capture, or empty."""
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
        dict(url=k, output="json", fl="timestamp", filter="statuscode:200", limit=-1))
    for wait in (0, 15):
        time.sleep(wait)
        try:
            _, body, _ = get(url, 30)
            rows = json.loads(body or b"[]")[1:]
            return rows[-1][0][:8] if rows else ""
        except Exception as ex:
            last = ex
    raise last

def key(u):
    u = u.strip()
    if not re.match(r"https?://", u, re.I):
        u = "https://" + u
    p = urllib.parse.urlsplit(u)
    host = re.sub(r"^www\.", "", p.netloc.lower())
    path = p.path.rstrip("/")
    return u, host + path

def collect():
    seen = {}
    def add(office, ward, name, url):
        if not url or SKIP.search(url):
            return
        u, k = key(url)
        if k and k not in seen:
            seen[k] = dict(office=office, ward=ward, name=name, url=u, key=k)
    def person(office, ward, c):
        for s in c.get("socialMedias") or []:
            if s.get("name") in ("web", "other"):
                add(office, ward, c.get("name"), s.get("url"))
    for c in json.load(open(SRC / "mayorCandidates.json"))["candidates"]:
        person("Mayor", "", c)
    for w in json.load(open(SRC / "councilorCandidates.json"))["ward"]:
        for c in w["candidate"]:
            person("Councillor", w.get("num"), c)
    for b in json.load(open(SRC / "trusteeCandidates.json"))["schoolBoard"]:
        for w in b["ward"]:
            for c in w["candidate"]:
                person(f"Trustee {b.get('id')}", w.get("num"), c)
    for row in csv.DictReader(open(SRC / "elected.csv", encoding="utf-8-sig")):
        name = f"{row.get('First name', '')} {row.get('Last name', '')}".strip()
        for col in ("Website", "Personal Website"):
            add("Sitting 2022-26", row.get("District ID") or "", name, (row.get(col) or "").replace("None", ""))
    return list(seen.values())

def main():
    sites = collect()
    print(f"sites to check: {len(sites)}", flush=True)
    fields = ["office", "ward", "name", "url", "key", "live", "snapshot", "lookup"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for i, s in enumerate(sites, 1):
            try:
                s["live"] = get(s["url"], 15)[0]
            except urllib.error.HTTPError as ex:
                s["live"] = ex.code
            except Exception as ex:
                s["live"] = type(ex).__name__
            try:
                s["snapshot"] = available(s["key"])
                s["lookup"] = "ok"
                if not s["snapshot"]:
                    s["snapshot"] = recheck(s["key"])
                    s["lookup"] = "ok rechecked"
            except Exception as ex:
                s["snapshot"], s["lookup"] = "", f"error {type(ex).__name__}"
            w.writerow(s)
            f.flush()
            if i % 50 == 0:
                print(f"{i}/{len(sites)}", flush=True)
            time.sleep(1)
    print("done", flush=True)

if __name__ == "__main__":
    main()
