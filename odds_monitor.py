#!/usr/bin/env python3
"""
Odds monitor — 4 sports, one script.

Modes:
  scheduled : plain snapshot of the whole league (3 credits)
  closing   : checks /events (FREE), and only pulls odds if a game
              starts in the next ~10-30 minutes (3 credits)

Env required:
  ODDS_API_KEY, TG_TOKEN, TG_CHAT_ID, SPORT
"""

import os
import sys
import csv
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

API = "https://api.the-odds-api.com/v4"

SPORTS = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "mlb": "baseball_mlb",
}

TITLES = {"nfl": "\U0001F3C8 NFL", "nba": "\U0001F3C0 NBA",
          "nhl": "\U0001F3D2 NHL", "mlb": "\u26BE MLB"}

# 9 bookmakers = counts as ONE region for billing (up to 10 = 1 region).
# Pinnacle + exchanges as the sharp anchor, US books for spreads/totals depth.
BOOKS = ("pinnacle,betfair_ex_eu,matchbook,betsson,coolbet,"
         "draftkings,fanduel,betmgm,betrivers")
MARKETS = "h2h,spreads,totals"

# Closing trigger window, minutes before kickoff.
CLOSE_MIN = 10
CLOSE_MAX = 30

CSV_HEADER = ["snapshot_utc", "mode", "event_id", "commence_utc",
              "home", "away", "book", "market", "outcome", "point", "price"]


def api_get(path, **params):
    params["apiKey"] = os.environ["ODDS_API_KEY"]
    url = "%s%s?%s" % (API, path, urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            body = json.loads(r.read().decode("utf-8"))
            left = r.headers.get("x-requests-remaining")
            used = r.headers.get("x-requests-last")
            return body, left, used
    except urllib.error.HTTPError as e:
        print("API error %s: %s" % (e.code, e.read().decode("utf-8")[:300]))
        sys.exit(1)


def tg_send(text):
    url = "https://api.telegram.org/bot%s/sendMessage" % os.environ["TG_TOKEN"]
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()
    except Exception as e:
        print("Telegram send failed: %s" % e)


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def flatten(events, stamp, mode):
    """Turn the API response into flat CSV rows."""
    rows = []
    for ev in events:
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                for oc in mk.get("outcomes", []):
                    rows.append([
                        stamp, mode, ev["id"], ev["commence_time"],
                        ev.get("home_team", ""), ev.get("away_team", ""),
                        bm["key"], mk["key"], oc.get("name", ""),
                        oc.get("point", ""), oc.get("price", ""),
                    ])
    return rows


def write_rows(sport, rows):
    """One CSV per league per day; snapshots append, never overwrite."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    folder = os.path.join("data", sport)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s.csv" % day)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CSV_HEADER)
        w.writerows(rows)
    return path


def done_ids(sport):
    path = os.path.join("data", sport, "closing_done.txt")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())


def mark_done(sport, ids):
    folder = os.path.join("data", sport)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "closing_done.txt")
    with open(path, "a", encoding="utf-8") as f:
        for i in ids:
            f.write(i + "\n")


def summarise(sport, events, stamp, mode, left):
    """Short Telegram digest: Pinnacle as the anchor, best available price."""
    head = "%s \u2014 %s\n%s UTC\n" % (
        TITLES[sport],
        "\u0417\u0410\u041a\u0420\u042b\u0422\u0418\u0415" if mode == "closing" else "\u0441\u043d\u0438\u043c\u043e\u043a",
        stamp[:16].replace("T", " "))
    lines = []
    for ev in sorted(events, key=lambda e: e["commence_time"])[:12]:
        start = parse_iso(ev["commence_time"]).strftime("%d.%m %H:%M")
        lines.append("\n%s \u2014 %s  (%s)" % (ev.get("away_team", "?"),
                                               ev.get("home_team", "?"), start))
        prices = {}
        pin = {}
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                if mk["key"] != "h2h":
                    continue
                for oc in mk.get("outcomes", []):
                    n = oc.get("name", "")
                    p = oc.get("price")
                    if p is None:
                        continue
                    if p > prices.get(n, (0, ""))[0]:
                        prices[n] = (p, bm["key"])
                    if bm["key"] == "pinnacle":
                        pin[n] = p
        for team, (best, book) in prices.items():
            anchor = (" | pin %.2f" % pin[team]) if team in pin else ""
            lines.append("  %s: %.2f (%s)%s" % (team[:22], best, book, anchor))
    tail = "\n\n\u0441\u043e\u0431\u044b\u0442\u0438\u0439: %d" % len(events)
    if left:
        tail += "  |  \u043a\u0440\u0435\u0434\u0438\u0442\u043e\u0432 \u043e\u0441\u0442\u0430\u043b\u043e\u0441\u044c: %s" % left
    return head + "".join(lines) + tail


def main():
    sport = os.environ["SPORT"].lower()
    mode = os.environ.get("MODE", "scheduled").lower()
    key = SPORTS[sport]
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    targets = None

    if mode == "closing":
        # FREE call — costs no credits.
        events, _, _ = api_get("/sports/%s/events" % key)
        already = done_ids(sport)
        lo = now + timedelta(minutes=CLOSE_MIN)
        hi = now + timedelta(minutes=CLOSE_MAX)
        targets = [e["id"] for e in events
                   if e["id"] not in already and lo <= parse_iso(e["commence_time"]) <= hi]
        if not targets:
            print("no games in the closing window; exiting without spending credits")
            return
        print("closing window hit for %d event(s)" % len(targets))

    odds, left, used = api_get("/sports/%s/odds" % key,
                               bookmakers=BOOKS, markets=MARKETS,
                               oddsFormat="decimal", dateFormat="iso")

    if mode == "closing":
        odds = [e for e in odds if e["id"] in targets]

    if not odds:
        print("no events returned; nothing written")
        return

    rows = flatten(odds, stamp, mode)
    path = write_rows(sport, rows)
    if mode == "closing":
        mark_done(sport, [e["id"] for e in odds])

    tg_send(summarise(sport, odds, stamp, mode, left))
    print("wrote %d rows to %s | credits used %s, left %s" % (len(rows), path, used, left))
    # Signal the workflow that there is something to commit.
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write("changed=true\n")


if __name__ == "__main__":
    main()
