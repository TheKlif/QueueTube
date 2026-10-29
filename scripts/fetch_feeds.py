#!/usr/bin/env python3
"""Fetch channel feeds and merge them into data/queue.json."""
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# Later entries override earlier ones when a video appears in several feeds.
FEEDS = [("UU", "video"), ("UULF", "video"), ("UUSH", "short"), ("UULV", "vod")]
WATCH_MAX_AGE_DAYS = 7
SEED_DAYS = 90
WATCH_DELAY = 2.0
WATCH_MAX_FAILS = 3


def get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def save(name, obj):
    DATA.mkdir(exist_ok=True)
    text = json.dumps(obj, indent=1, ensure_ascii=False) + "\n"
    (DATA / name).write_text(text, "utf-8")


def resolve(text):
    text = text.strip()
    if re.fullmatch(r"UC[\w-]{22}", text):
        return text
    if text.startswith("http"):
        url = text
    else:
        url = "https://www.youtube.com/" + (text if text.startswith("@") else "@" + text)
    status, body = get(url)
    for pat in (r'"(?:channelId|externalId)":"(UC[\w-]{22})"',
                r"channel/(UC[\w-]{22})"):
        m = re.search(pat, body)
        if m:
            return m.group(1)
    return None


def fetch_feed(prefix, cid):
    """Entries list, [] if the feed does not exist (404), None on failure."""
    status, body = get(
        "https://www.youtube.com/feeds/videos.xml?playlist_id=" + prefix + cid[2:])
    if status == 404:
        return []
    if status != 200:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    out = []
    for e in root.findall("a:entry", NS):
        out.append({
            "id": e.findtext("yt:videoId", namespaces=NS),
            "title": e.findtext("a:title", namespaces=NS) or "",
            "published": e.findtext("a:published", namespaces=NS) or "",
            "author": e.findtext("a:author/a:name", namespaces=NS) or "",
        })
    return out


def watch_state(vid):
    """'upcoming', 'live', 'ok', or 'unknown'. Provisional detection."""
    status, body = get(f"https://www.youtube.com/watch?v={vid}")
    if status != 200 or "ytInitialPlayerResponse" not in body:
        return "unknown"
    if '"isLiveNow":true' in body:
        return "live"
    m = re.search(r'"playabilityStatus":\{"status":"(\w+)"', body)
    if (m and m.group(1) == "LIVE_STREAM_OFFLINE") or '"isUpcoming":true' in body:
        return "upcoming"
    return "ok"


def parse_time(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    cfg = load("channels.json", {"channels": [], "pending": []})
    queue = load("queue.json", {"generated_at": None, "videos": []})
    videos = {v["id"]: v for v in queue["videos"]}
    now = datetime.now(timezone.utc)
    seeding = not videos

    still_pending = []
    for p in cfg.get("pending", []):
        cid = resolve(p["input"])
        if cid is None:
            print(f"warn: could not resolve {p['input']}")
            still_pending.append(p)
        elif not any(c["id"] == cid for c in cfg["channels"]):
            cfg["channels"].append({
                "id": cid, "name": "", "tier": p.get("tier", 5),
                "shorts": "hide", "live": "hide", "muted": False})
    cfg["pending"] = still_pending

    new_ids = []
    for ch in cfg["channels"]:
        if ch.get("muted"):
            continue
        for prefix, kind in FEEDS:
            entries = fetch_feed(prefix, ch["id"])
            time.sleep(0.5)
            if entries is None:
                print(f"warn: {ch['id']} {prefix} failed")
                continue
            for e in entries:
                v = videos.get(e["id"])
                if v is None and seeding:
                    t = parse_time(e["published"])
                    if t is not None and t < now - timedelta(days=SEED_DAYS):
                        continue
                if v is None:
                    v = {"id": e["id"], "channel_id": ch["id"],
                         "published": e["published"], "state": "ready",
                         "first_seen": now.isoformat(timespec="seconds")}
                    videos[e["id"]] = v
                    new_ids.append(e["id"])
                v["title"] = e["title"]
                # Do not let a general feed overwrite a short or vod tag.
                if kind != "video" or v.get("kind") not in ("short", "vod"):
                    v["kind"] = kind
                if e["author"] and not ch.get("name"):
                    ch["name"] = e["author"]

    # States: ready (shown), hold (upcoming or live), check (not yet verified).
    cutoff = now - timedelta(days=WATCH_MAX_AGE_DAYS)
    to_check = []
    for i in new_ids:
        t = parse_time(videos[i]["published"])
        if t is not None and t >= cutoff:
            videos[i]["state"] = "check"
    to_check = [i for i, v in videos.items() if v["state"] in ("hold", "check")]
    fails = 0
    for vid in to_check:
        s = watch_state(vid)
        time.sleep(WATCH_DELAY)
        if s == "unknown":
            print(f"warn: watch check unknown for {vid}")
            fails += 1
            if fails >= WATCH_MAX_FAILS:
                break
            continue
        fails = 0
        videos[vid]["state"] = "hold" if s in ("upcoming", "live") else "ready"

    save("queue.json", {
        "generated_at": now.isoformat(timespec="seconds"),
        "videos": sorted(videos.values(), key=lambda v: v["published"], reverse=True),
    })
    save("channels.json", cfg)
    print(f"done: {len(videos)} videos, {len(new_ids)} new")


if __name__ == "__main__":
    main()