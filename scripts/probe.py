#!/usr/bin/env python3
"""Probe YouTube feed behavior for the priority queue project.

Usage: python scripts/probe.py <UC id or @handle> [...] [--n 8] [--no-watch]
"""
import argparse
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
PREFIXES = ["UU", "UULF", "UUSH", "UULV"]
LETTER = {"UU": "A", "UULF": "L", "UUSH": "S", "UULV": "V"}
FLAGS = ["isLiveContent", "isUpcoming", "isLive", "isLiveNow"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get(url, follow=True):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    opener = (urllib.request.build_opener() if follow
              else urllib.request.build_opener(NoRedirect))
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, url, body
    except Exception as e:  # network error, timeout, etc.
        return 0, url, str(e)


def resolve(arg):
    if re.fullmatch(r"UC[\w-]{22}", arg):
        return arg
    handle = arg if arg.startswith("@") else "@" + arg
    url = arg if arg.startswith("http") else f"https://www.youtube.com/{handle}"
    status, final, body = get(url)
    print(f"  resolve {url}: http {status}"
          + (" CONSENT REDIRECT" if "consent.youtube.com" in final else ""))
    for pat in (r'"(?:channelId|externalId)":"(UC[\w-]{22})"',
                r'channel/(UC[\w-]{22})'):
        m = re.search(pat, body)
        if m:
            return m.group(1)
    return None


def fetch_feed(prefix, cid):
    url = ("https://www.youtube.com/feeds/videos.xml?playlist_id="
           + prefix + cid[2:])
    status, final, body = get(url)
    if status != 200:
        return status, None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return status, None
    entries = []
    for e in root.findall("a:entry", NS):
        entries.append({
            "id": e.findtext("yt:videoId", namespaces=NS),
            "title": e.findtext("a:title", namespaces=NS) or "",
            "published": e.findtext("a:published", namespaces=NS) or "",
        })
    return status, entries


def short_status(vid):
    """200 on /shorts/<id> suggests a Short; a 3xx suggests it is not."""
    status, _, _ = get(f"https://www.youtube.com/shorts/{vid}", follow=False)
    return status


def watch_flags(vid):
    status, final, body = get(f"https://www.youtube.com/watch?v={vid}")
    out = {"http": status}
    if "consent.youtube.com" in final:
        out["consent"] = True
    for f in FLAGS:
        m = re.search(rf'"{f}":(true|false)', body)
        out[f] = m.group(1) if m else "-"
    out["endTs"] = "y" if '"endTimestamp"' in body else "n"
    return out


def probe(cid, n, watch):
    feeds = {}
    for p in PREFIXES:
        status, entries = fetch_feed(p, cid)
        feeds[p] = entries or []
        print(f"  {p:<5} http {status}  entries {len(entries) if entries is not None else 'n/a'}")
        time.sleep(0.5)

    ids = {p: {e["id"] for e in feeds[p]} for p in PREFIXES}
    lf, sh, lv, al = ids["UULF"], ids["UUSH"], ids["UULV"], ids["UU"]
    print("  overlaps:")
    print(f"    UULF and UUSH : {len(lf & sh)}")
    print(f"    UULF and UULV : {len(lf & lv)}")
    print(f"    UUSH and UULV : {len(sh & lv)}")
    print(f"    in UU, in none of LF/SH/LV : {len(al - (lf | sh | lv))}")
    print(f"    in LF/SH/LV, not in UU     : {len((lf | sh | lv) - al)}")

    merged = {}
    for p in PREFIXES:
        for e in feeds[p]:
            rec = merged.setdefault(e["id"], dict(e, src=set()))
            rec["src"].add(LETTER[p])
    latest = sorted(merged.values(), key=lambda r: r["published"], reverse=True)[:n]

    print(f"  latest {len(latest)} unique videos "
          "(src: A=UU L=UULF S=UUSH V=UULV; shorts=/shorts/ http status)")
    for r in latest:
        line = (f"    {r['id']} {r['published'][:16]} "
                f"src={''.join(sorted(r['src'])):<4} {r['title'][:40]!r}")
        if watch:
            line += f" shorts={short_status(r['id'])}"
            time.sleep(1)
            line += f" watch={watch_flags(r['id'])}"
            time.sleep(1)
        print(line)

    print("  UULV entries (all):")
    for e in feeds["UULV"]:
        print(f"    {e['id']} {e['published'][:16]} {e['title'][:50]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channels", nargs="+")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--no-watch", action="store_true")
    args = ap.parse_args()
    for arg in args.channels:
        print(f"\n== {arg}")
        cid = resolve(arg)
        if not cid:
            print("  could not resolve a channel ID")
            continue
        print(f"  channel id: {cid}")
        probe(cid, args.n, not args.no_watch)


if __name__ == "__main__":
    main()