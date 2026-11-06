#!/usr/bin/env python3
"""Ask the Internet Archive to save public election websites, one page at a time.

  wayback_save.py <to_capture.csv> <results.csv> [--limit N]

Takes rows that are live with a 200. Homepages go first so every site gets something, then up to
MAX_PAGES same-site inner pages each. Form, account and media URLs are skipped. Safe to re-run:
anything already recorded as saved is skipped. Stops if the Archive keeps refusing.
"""
import csv, html, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

IN, OUT = Path(sys.argv[1]), Path(sys.argv[2])
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
UA = "toronto-gaps-check/0.1"
GAP_S, MAX_PAGES, BACKOFF_S, MAX_REFUSALS = 20, 12, 300, 4
SKIP = re.compile(r"(donat|contribut|login|log-in|signin|sign-in|register|account|cart|checkout|wp-admin|wp-json|xmlrpc|"
                  r"feed|\.(?:jpe?g|png|gif|svg|webp|pdf|zip|mp4|mp3|css|js|ico|xml)(?:$|\?))", re.I)

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read(1_500_000).decode("utf-8", "ignore")

def inner_pages(url):
    try:
        final, body = fetch(url)
    except Exception:
        return []
    host = urllib.parse.urlsplit(final).netloc.lower().removeprefix("www.")
    found = []
    for href in re.findall(r"""<a\b[^>]*\bhref=["']([^"'#]+)""", body, re.I):
        u = urllib.parse.urljoin(final, html.unescape(href.strip()))
        p = urllib.parse.urlsplit(u)
        if p.scheme not in ("http", "https") or p.netloc.lower().removeprefix("www.") != host:
            continue
        if p.query or SKIP.search(p.path) or p.path.rstrip("/") == urllib.parse.urlsplit(final).path.rstrip("/"):
            continue
        u = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
        if u not in found:
            found.append(u)
    return found[:MAX_PAGES]

def save(url):
    """Returns (status, snapshot). Status is saved, refused or failed."""
    try:
        final, _ = fetch("https://web.archive.org/save/" + url, 150)
        m = re.search(r"/web/(\d{14})/", final)
        return ("saved", m.group(1)) if m else ("failed", final[:120])
    except urllib.error.HTTPError as ex:
        return ("refused", str(ex.code)) if ex.code in (429, 403) else ("failed", str(ex.code))
    except Exception as ex:
        return "failed", type(ex).__name__

def main():
    rows = [r for r in csv.DictReader(open(IN)) if r["live"] == "200"]
    done = set()
    if OUT.exists():
        done = {r["url"] for r in csv.DictReader(open(OUT)) if r["status"] == "saved"}
    new = not OUT.exists()
    f = open(OUT, "a", newline="")
    w = csv.DictWriter(f, ["time", "site", "kind", "url", "status", "detail"])
    if new:
        w.writeheader()
    count = refusals = 0
    def submit(site, kind, url):
        nonlocal count, refusals
        if url in done:
            return True
        while True:
            status, detail = save(url)
            if status != "refused":
                break
            refusals += 1
            print(f"refused {detail}, pause {BACKOFF_S}s ({refusals}/{MAX_REFUSALS})", flush=True)
            if refusals >= MAX_REFUSALS:
                w.writerow(dict(time=int(time.time()), site=site, kind=kind, url=url, status=status, detail=detail))
                f.flush()
                return False
            time.sleep(BACKOFF_S)
        refusals = 0
        w.writerow(dict(time=int(time.time()), site=site, kind=kind, url=url, status=status, detail=detail))
        f.flush()
        count += 1
        if count % 10 == 0:
            print(f"submitted {count}", flush=True)
        time.sleep(GAP_S)
        return not (LIMIT and count >= LIMIT)
    for r in rows:
        if not submit(r["url"], "home", r["url"]):
            print("stopped during homepages", flush=True)
            return 1
    print("homepages finished", flush=True)
    for r in rows:
        for u in inner_pages(r["url"]):
            if not submit(r["url"], "inner", u):
                print("stopped during inner pages", flush=True)
                return 1
    print("all finished", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
