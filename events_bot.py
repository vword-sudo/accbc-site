#!/usr/bin/env python3
"""
ACCBC events bot.

Finds upcoming events in Battle Creek that are led by local arts
organizations and writes them to docs/events.json. The website reads that
file to:
  - show the "art & culture events this month" number on the homepage, and
  - fill the "Coming up in Battle Creek" list on the Come Create page.

Source: the Calhoun County Visitors Bureau events calendar
(battlecreekvisitors.org), which runs on Simpleview. ACCBC's own events, and
anything the calendar misses, go in my_events.json and are merged in.

Uses only the Python standard library, so it runs anywhere Python 3.9+ does,
including the free GitHub Actions runner in .github/workflows.

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
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://www.battlecreekvisitors.org"
TZ = ZoneInfo("America/Detroit")
OUT_FILE = os.path.join(HERE, "docs", "events.json")
MY_EVENTS = os.path.join(HERE, "my_events.json")
DAYS_AHEAD = 90  # how far ahead the "Coming up" list looks

# Only events led by these Battle Creek arts organizations are included.
# An event matches when one of the names appears in its title, host or
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

HEADERS = {"User-Agent": "ACCBC-events-bot/1.0 (admin@artandculturebc.com)"}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def iso_utc(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fetch_events(start, end):
    """Pull every active event in the date range from the Simpleview REST API."""
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

    try:
        raw = fetch_events(iso_utc(now), iso_utc(now + dt.timedelta(days=DAYS_AHEAD)))
    except Exception as e:  # keep the last saved list on the site if the source is down
        print(f"Could not reach the events calendar: {e}", file=sys.stderr)
        return 1

    # One event can repeat on several days; keep its next date only.
    seen, upcoming = set(), []
    for ev in raw:
        day = event_day(ev)
        key = ev.get("recid") or ev.get("title")
        if not day or key in seen or not in_battle_creek(ev) or not led_by_arts_org(ev):
            continue
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
    upcoming = tbd + upcoming  # date-TBD events of ours show first
    this_month = [e for e in upcoming if e["date"][:7] == today.strftime("%Y-%m")]

    result = {
        "month": today.strftime("%Y-%m"),
        "count": len(this_month),
        "updated": now.isoformat(timespec="minutes"),
        "source": SITE + "/events/",
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
