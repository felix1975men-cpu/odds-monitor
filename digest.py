#!/usr/bin/env python3
"""
Pre-round digest — one compact message per league at 09:00 UTC.

Unlike odds_monitor.py this does NOT write CSV and does NOT commit.
It only sends a short summary of upcoming games, built for forwarding
into the chat for analysis: Pinnacle as the anchor, best available
price next to it, and the gap between them flagged.

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

# How far ahead to look, per league. NFL plays weekly, so its round
# spans several days; the others have a daily slate.
WINDOW_HOURS = {"nfl": 96, "nba": 30, "nhl": 30, "mlb": 30}

BOOKS = ("pinnacle,betfair_ex_eu,matchbook,betsson,coolbet,"
         "draftkings,fanduel,betmgm,betrivers")
MARKETS = "h2h,spreads,totals"

# Only flag a gap worth looking at.
EDGE_PCT = 2.0
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


def collect(ev, market_key):
    """Return {outcome_name: [(price, point, book), ...]} for one market."""
    out = {}
    for bm in ev.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk["key"] != market_key:
                continue
            for oc in mk.get("outcomes", []):
                price = oc.get("price")
                if price is None:
                    continue
                out.setdefault(oc.get("name", "?"), []).append(
                    (price, oc.get("point"), bm["key"]))
    return out


def pin_and_best(entries):
    """Pinnacle quote (if any) and the best price on offer."""
    pin = next(((p, pt) for p, pt, b in entries if b == "pinnacle"), None)
    best = max(entries, key=lambda x: x[0])
    return pin, best


def line_for_h2h(ev):
    rows = []
    data = collect(ev, "h2h")
    for team, entries in data.items():
        pin, best = pin_and_best(entries)
        bp, _, bbook = best
        if pin:
            gap = (bp / pin[0] - 1) * 100
            flag = "  <<" if gap >= EDGE_PCT else ""
            rows.append("  %s: %.2f (%s) | pin %.2f  %+.1f%%%s"
                        % (team[:20], bp, bbook, pin[0], gap, flag))
        else:
            rows.append("  %s: %.2f (%s) | pin -" % (team[:20], bp, bbook))
    return rows


def line_for_pointed(ev, market_key, label):
    """Spreads/totals: show Pinnacle's line if present, else the best offer."""
    rows = []
    data = collect(ev, market_key)
    for name, entries in data.items():
        pin, best = pin_and_best(entries)
        if pin:
            pt = pin[1]
            rows.append("  %s %s %s: pin %.2f" % (label, name[:14],
                                                  ("%+g" % pt) if pt is not None else "", pin[0]))
        else:
            bp, pt, bbook = best
            rows.append("  %s %s %s: %.2f (%s)" % (label, name[:14],
                                                   ("%+g" % pt) if pt is not None else "", bp, bbook))
    return rows


def main():
    sport = os.environ["SPORT"].lower()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=WINDOW_HOURS[sport])

    odds, left = api_get("/sports/%s/odds" % SPORTS[sport],
                         bookmakers=BOOKS, markets=MARKETS,
                         oddsFormat="decimal", dateFormat="iso")

    games = sorted([e for e in odds if now <= parse_iso(e["commence_time"]) <= horizon],
                   key=lambda e: e["commence_time"])

    if not games:
        print("no games inside the %dh window" % WINDOW_HOURS[sport])
        return

    header = ("%s \u2014 \u0441\u0432\u043e\u0434\u043a\u0430 \u043d\u0430 \u0442\u0443\u0440\n"
              "%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d\n"
              "\u043e\u043a\u043d\u043e: %d\u0447  |  << = \u0446\u0435\u043d\u0430 \u043b\u0443\u0447\u0448\u0435 Pinnacle \u043d\u0430 %.0f%%+\n"
              % (TITLES[sport], now.strftime("%Y-%m-%d %H:%M"), len(games),
                 WINDOW_HOURS[sport], EDGE_PCT))

    blocks = []
    for ev in games:
        start = parse_iso(ev["commence_time"]).strftime("%d.%m %H:%M")
        block = ["\n%s \u2014 %s  (%s UTC)" % (ev.get("away_team", "?"),
                                               ev.get("home_team", "?"), start)]
        block += line_for_h2h(ev)
        block += line_for_pointed(ev, "totals", "T")
        block += line_for_pointed(ev, "spreads", "F")
        blocks.append("\n".join(block))

    # Telegram caps a message at 4096 chars, so send in chunks.
    chunk = header
    sent = 0
    for b in blocks:
        if len(chunk) + len(b) > MSG_LIMIT:
            tg_send(chunk)
            sent += 1
            chunk = ""
        chunk += b + "\n"
    if chunk.strip():
        chunk += "\n\u043a\u0440\u0435\u0434\u0438\u0442\u043e\u0432 \u043e\u0441\u0442\u0430\u043b\u043e\u0441\u044c: %s" % left
        tg_send(chunk)
        sent += 1

    print("digest sent in %d message(s), %d games, credits left %s"
          % (sent, len(games), left))


if __name__ == "__main__":
    main()
