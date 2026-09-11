#!/usr/bin/env python3
"""
probe.py — NHL API reconnaissance. Run from GitHub Actions, not a phone browser.
Costs nothing: api-web.nhle.com needs no key.

Usage in a workflow step:
    run: python probe.py
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api-web.nhle.com/v1"

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

# (label, path, list of json keys we hope to find somewhere in the body)
TARGETS = [
    ("schedule day",    "/schedule/%s" % today,              ["gameWeek", "games", "startTimeUTC"]),
    ("scores day",      "/score/%s" % yesterday,             ["games", "homeTeam", "awayTeam", "gameOutcome", "lastPeriodType"]),
    ("standings",       "/standings/now",                    ["standings", "teamAbbrev", "goalFor", "goalAgainst"]),
    ("club schedule",   "/club-schedule-season/BOS/now",     ["games", "gameType"]),
    ("team stats",      "/club-stats/BOS/now",               ["skaters", "goalies"]),
    ("boxscore probe",  "/gamecenter/2025020001/boxscore",   ["homeTeam", "awayTeam", "periodDescriptor"]),
]


def probe(label, path, keys, ua):
    url = BASE + path
    req = urllib.request.Request(url)
    if ua:
        req.add_header("User-Agent", ua)
    tag = "browser-UA" if ua else "no-UA     "
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            size = len(raw)
            found, missing = [], []
            try:
                json.loads(raw)
                parsed = True
            except Exception:
                parsed = False
            for k in keys:
                (found if '"%s"' % k in raw else missing).append(k)
            print("  %s  %s  %d bytes  json=%s" % (tag, r.status, size, parsed))
            print("      есть: %s" % (", ".join(found) or "—"))
            if missing:
                print("      НЕТ:  %s" % ", ".join(missing))
            return raw if parsed else None
    except urllib.error.HTTPError as e:
        print("  %s  HTTP %s  %s" % (tag, e.code, e.read().decode("utf-8", "replace")[:120]))
    except Exception as e:
        print("  %s  FAILED  %s" % (tag, e))
    return None


def main():
    print("NHL API probe | %s UTC" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("base: %s\n" % BASE)
    samples = {}
    for label, path, keys in TARGETS:
        print("%s  ->  %s" % (label, path))
        a = probe(label, path, keys, None)
        b = probe(label, path, keys, UA_BROWSER)
        samples[label] = a or b
        print()

    # Structure of the scores payload matters most — that is what results.py parses.
    raw = samples.get("scores day")
    if raw:
        try:
            data = json.loads(raw)
            games = data.get("games", [])
            print("=" * 52)
            print("scores day: игр в ответе %d" % len(games))
            if games:
                g = games[0]
                print("ключи верхнего уровня матча:")
                print("  " + ", ".join(sorted(g.keys())))
                print("\nпервый матч целиком (обрезано 1500):")
                print(json.dumps(g, ensure_ascii=False, indent=1)[:1500])
        except Exception as e:
            print("scores parse failed: %s" % e)


if __name__ == "__main__":
    main()
