#!/usr/bin/env python3
"""
ACCBC events bot.

Finds upcoming events in Battle Creek that are led by local arts
organizations and writes them to docs/events.json. The website reads that
file to:
  - show the "art & culture events this month" number on the homepage, and
  - fill the "Coming up in Battle Creek" list on the Come Create page.

Source: the Calhoun County Visitors Bureau events calendar
(battlecreekvisitors.org), read through its public RSS feed. ACCBC's own
events, and anything the calendar misses, go in my_events.json.

Run it by hand:   python3 events_bot.py
Preview only:     python3 events_bot.py --dry-run
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
import re
import html
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://www.battlecreekvisitors.org"
TZ = ZoneInfo("America/Detroit")
OUT_FILE = os.path.join(HERE, "docs", "events.json")
MY_EVENTS = os.path.join(HERE, "my_events.json")
DAYS_AHEAD = 90  # how far ahead the "Coming up" list looks

# Only events led by these Battle Creek arts organizations are included.
# An event matches when one of the names appears in its title or
# description. Add or remove organizations freely; lowercase is fine.
ARTS_ORGS = [
    "art and culture collective", "accbc",
    "music center", "battle creek symphony", "community music school",
    "what a do", "what-a-do",
    "brass band of battle creek",
    "art center of battle creek",
    "battle creek civic theatre",
    "battle creek youth orchestra",
    "cereal city concert band",
    "battle creek boychoir", "girls chorus",
    "battle creek society of artists",
    "color the creek",
    "masa center",
    "sweet adelines",
    "create in me art studio",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ACCBC-events-bot/1.1; +https://artandculturebc.com)",
    "Accept": "application/json, text/plain, */*",
    "Referer": SITE + "/events/",
}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def iso_utc(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fetch_events(start, end):
    """Backup source: the calendar's REST API (may be blocked for bots)."""
    token = get(SITE + "/plugins/core/get_simple_token/").strip()
    events, skip, page = [], 0, 200
    while True:
        query = {
            "filter": {
                "active": True,
                "date_range": {"start": {"$date": start}, "end": {"$date": end}},
            },
            "options": {
                "limit": page, "skip": skip, "count": True,
                "fields": {
                    "title": 1, "city": 1, "date": 1, "startDate": 1,
                    "startTime": 1, "url": 1, "recid": 1, "location": 1,
                    "host": 1, "description": 1,
                },
            },
        }
        url = (SITE + "/includes/rest_v2/plugins_events_events_by_date/find/?json="
               + urllib.parse.quote(json.dumps(query, separators=(",", ":")))
               + "&token=" + token)
        data = json.loads(get(url))
        docs = data.get("docs", {})
        batch = docs.get("docs", []) if isinstance(docs, dict) else docs
        events.extend(batch)
        total = docs.get("count", len(events)) if isinstance(docs, dict) else len(events)
        skip += page
        if not batch or skip >= total:
            break
    return events


RSS_URL = SITE + "/event/rss/"


def fetch_rss():
    """The calendar's public RSS feed: the way the site shares events with other programs."""
    root = ET.fromstring(get(RSS_URL))
    events = []
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc_html = it.findtext("description") or ""
        desc = html.unescape(re.sub(r"<[^>]+>", " ", desc_html))
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", desc)
        if not m:
            continue
        start = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        cats = " ".join(c.text or "" for c in it.findall("category"))
        events.append({
            "title": title, "date": start, "url": link, "recid": link,
            "description": desc + " " + cats,
            "city": "Battle Creek",
        })
    return events


def led_by_arts_org(ev):
    text = " ".join(str(ev.get(k) or "") for k in ("title", "host", "description")).lower()
    return any(org in text for org in ARTS_ORGS)


def in_battle_creek(ev):
    place = " ".join(str(ev.get(k) or "") for k in ("city", "location")).lower()
    return "battle creek" in place


def event_day(ev):
    raw = str(ev.get("date") or ev.get("startDate") or "")[:10]
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def load_my_events():
    """ACCBC's own events, typed by hand. See my_events.json for the format."""
    if not os.path.exists(MY_EVENTS):
        return []
    with open(MY_EVENTS) as f:
        items = json.load(f)
    for it in items:
        it["ours"] = True
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--dry-run", action="store_true", help="print results, don't write the file")
    args = ap.parse_args()

    now = dt.datetime.now(TZ)
    today = now.date()
    horizon = today + dt.timedelta(days=DAYS_AHEAD)

    calendar_ok = True
    try:
        raw = fetch_rss()
        print(f"Read {len(raw)} events from the calendar's RSS feed")
    except Exception as e:
        print(f"RSS feed failed ({e!r}), trying the calendar API")
        try:
            raw = fetch_events(iso_utc(now), iso_utc(now + dt.timedelta(days=DAYS_AHEAD)))
        except Exception as e2:
            print(f"WARNING: could not reach the events calendar: {e2!r}")
            calendar_ok, raw = False, []

    seen, upcoming = set(), []
    if not calendar_ok and os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE) as f:
                old = json.load(f).get("events", [])
            upcoming = [e for e in old if not e.get("ours") and e.get("date", "") >= today.isoformat()]
        except Exception:
            upcoming = []
    for ev in raw:
        day = event_day(ev)
        key = ev.get("recid") or ev.get("title")
        if not day or key in seen or not in_battle_creek(ev) or not led_by_arts_org(ev):
            continue
        if day < today:
            day = today
        seen.add(key)
        url = ev.get("url") or ""
        upcoming.append({
            "title": ev.get("title", ""),
            "date": day.isoformat(),
            "time": ev.get("startTime") or "",
            "place": ev.get("location") or "",
            "url": url if url.startswith("http") else SITE + url,
            "ours": False,
        })

    tbd = []
    for it in load_my_events():
        if str(it.get("date", "")).upper() == "TBD":
            it["date"] = "TBD"
            tbd.append(it)
            continue
        d = dt.date.fromisoformat(it["date"])
        if today <= d <= horizon:
            upcoming.append(it)

    upcoming.sort(key=lambda e: e["date"])
    upcoming = tbd + upcoming
    this_month = [e for e in upcoming if e["date"][:7] == today.strftime("%Y-%m")]

    result = {
        "month": today.strftime("%Y-%m"),
        "count": len(this_month),
        "updated": now.isoformat(timespec="minutes"),
        "source": SITE + "/events/",
        "calendar_ok": calendar_ok,
        "events": upcoming,
    }

    print(f"{result['count']} arts-org events in Battle Creek this month, {len(upcoming)} in the next {DAYS_AHEAD} days")
    for e in upcoming:
        print(f"  {e['date']}  {'[ACCBC] ' if e.get('ours') else ''}{e['title']}")

    if not args.dry_run:
        os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
        with open(OUT_FILE, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Wrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
