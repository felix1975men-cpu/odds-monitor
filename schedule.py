#!/usr/bin/env python3
"""
Blind slate — the fixtures only, sent BEFORE the 09:00 digest.

The point is the absence of prices. Seeing the line first anchors every
estimate to it, so this message carries pairings and start times and
nothing else. The digest follows half an hour later and can then be
compared against an opinion already formed.

Uses /v4/sports/{sport}/events, which does NOT consume API credits.

Env required:
  ODDS_API_KEY, TG_TOKEN, TG_CHAT_ID, SPORT
"""

import os
import json
import sys
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

# Same window as the digest, so the two messages cover the same games.
WINDOW_HOURS = {"nfl": 96, "nba": 30, "nhl": 30, "mlb": 30}

MSG_LIMIT = 3500


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
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()
    except Exception as e:
        print("Telegram send failed: %s" % e)


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    sport = os.environ["SPORT"].lower()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=WINDOW_HOURS[sport])

    events, left = api_get("/sports/%s/events" % SPORTS[sport], dateFormat="iso")

    games = sorted([e for e in events
                    if now <= parse_iso(e["commence_time"]) <= horizon],
                   key=lambda e: e["commence_time"])

    if not games:
        print("no games inside the %dh window" % WINDOW_HOURS[sport])
        return

    header = ("%s \u2014 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u0442\u0443\u0440\u0430\n"
              "%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d  |  \u043e\u043a\u043d\u043e: %d\u0447\n"
              "\u0431\u0435\u0437 \u043a\u043e\u044d\u0444\u0444\u0438\u0446\u0438\u0435\u043d\u0442\u043e\u0432 \u2014 \u043e\u0446\u0435\u043d\u043a\u0430 \u0434\u043e \u0446\u0435\u043d\u044b\n\n"
              % (TITLES[sport], now.strftime("%Y-%m-%d %H:%M"),
                 len(games), WINDOW_HOURS[sport]))

    lines = []
    for i, ev in enumerate(games, 1):
        start = parse_iso(ev["commence_time"]).strftime("%d.%m %H:%M")
        lines.append("%2d. %s @ %s  (%s UTC)"
                     % (i, ev.get("away_team", "?"), ev.get("home_team", "?"), start))

    chunk, sent = header, 0
    for ln in lines:
        if len(chunk) + len(ln) + 1 > MSG_LIMIT:
            tg_send(chunk)
            sent += 1
            chunk = ""
        chunk += ln + "\n"
    if chunk.strip():
        tg_send(chunk)
        sent += 1

    print("schedule sent in %d message(s), %d games, credits left %s"
          % (sent, len(games), left))


if __name__ == "__main__":
    main()
