#!/usr/bin/env python3
"""
Daily results — final scores for the previous day's games.

Uses /scores with daysFrom, which costs 2 credits per league per run.
Writes results to data/<league>/results-YYYY-MM.csv (one file per month,
appended) so settled games can later be matched against the odds
snapshots for CLV.

Env required:
  ODDS_API_KEY, TG_TOKEN, TG_CHAT_ID, SPORT
"""

import os
import csv
import json
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

API = "https://api.the-odds-api.com/v4"

SPORTS = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "mlb": "baseball_mlb",
}

TITLES = {"nfl": "\U0001F3C8 NFL", "nba": "\U0001F3C0 NBA",
          "nhl": "\U0001F3D2 NHL", "mlb": "\u26BE MLB"}

DAYS_FROM = 2  # cost is the same for 1-3; 2 covers late finishes safely
HEADER = ["event_id", "completed_utc", "commence_utc", "home", "away",
          "home_score", "away_score", "winner"]


def api_get(path, **params):
    params["apiKey"] = os.environ["ODDS_API_KEY"]
    url = "%s%s?%s" % (API, path, urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")), r.headers.get("x-requests-remaining")
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


def score_of(game, team):
    for s in game.get("scores") or []:
        if s.get("name") == team:
            try:
                return int(s.get("score"))
            except (TypeError, ValueError):
                return None
    return None


def already_logged(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {row["event_id"] for row in csv.DictReader(f)}


def main():
    sport = os.environ["SPORT"].lower()
    now = datetime.now(timezone.utc)

    games, left = api_get("/sports/%s/scores" % SPORTS[sport],
                          daysFrom=DAYS_FROM, dateFormat="iso")

    done = [g for g in games if g.get("completed") and g.get("scores")]
    if not done:
        print("no completed games returned")
        return

    folder = os.path.join("data", sport)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "results-%s.csv" % now.strftime("%Y-%m"))
    seen = already_logged(path)

    rows, lines = [], []
    for g in sorted(done, key=lambda x: x.get("commence_time", "")):
        if g["id"] in seen:
            continue
        home, away = g.get("home_team", ""), g.get("away_team", "")
        hs, as_ = score_of(g, home), score_of(g, away)
        if hs is None or as_ is None:
            continue
        winner = home if hs > as_ else (away if as_ > hs else "draw")
        rows.append([g["id"], now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     g.get("commence_time", ""), home, away, hs, as_, winner])
        lines.append("%s %d : %d %s" % (away[:18], as_, hs, home[:18]))

    if not rows:
        print("nothing new since the last run")
        return

    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        w.writerows(rows)

    msg = ("%s \u2014 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b\n%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d\n\n%s"
           % (TITLES[sport], now.strftime("%Y-%m-%d %H:%M"), len(rows), "\n".join(lines)))
    if left:
        msg += "\n\n\u043a\u0440\u0435\u0434\u0438\u0442\u043e\u0432 \u043e\u0441\u0442\u0430\u043b\u043e\u0441\u044c: %s" % left
    tg_send(msg)

    print("logged %d results to %s, credits left %s" % (len(rows), path, left))


if __name__ == "__main__":
    main()
