#!/usr/bin/env python3
"""
Pre-round digest — one compact message per league at 09:00 UTC.

Does NOT write CSV and does NOT commit. It sends a short summary of
upcoming games for forwarding into chat.

Edge is measured against Pinnacle with the vig removed. Raw Pinnacle
prices carry ~2% margin, so comparing a best price to them flags
noise; the fair price is what a real edge is measured against.

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

WINDOW_HOURS = {"nfl": 96, "nba": 30, "nhl": 30, "mlb": 30}

BOOKS = ("pinnacle,betfair_ex_eu,matchbook,betsson,coolbet,"
         "draftkings,fanduel,betmgm,betrivers")
MARKETS = "h2h,spreads,totals"

# Edge against the DEVIGGED Pinnacle price. 3% is roughly where a gap
# stops looking like noise; the old 2%-vs-raw threshold mostly flagged
# the margin itself.
EDGE_PCT = 3.0
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


def group_key(point):
    """Two sides of the same line share a group; +1.5 and -1.5 belong together."""
    return None if point is None else abs(point)


def collect(ev, market_key):
    """{outcome: [(price, point, book)]} plus pinnacle quotes by group."""
    offers = {}
    pinn = {}
    for bm in ev.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk["key"] != market_key:
                continue
            for oc in mk.get("outcomes", []):
                price = oc.get("price")
                if price is None:
                    continue
                name, point = oc.get("name", "?"), oc.get("point")
                offers.setdefault(name, []).append((price, point, bm["key"]))
                if bm["key"] == "pinnacle":
                    pinn.setdefault(group_key(point), {})[name] = (price, point)
    return offers, pinn


def devig(quotes):
    """Proportional vig removal. quotes: {name: (price, point)} -> {name: fair_price}.

    Needs every side of the market; a one-sided quote cannot be cleaned.
    """
    if len(quotes) < 2:
        return {}
    total = sum(1.0 / p for p, _ in quotes.values())
    if total <= 0:
        return {}
    return {n: 1.0 / ((1.0 / p) / total) for n, (p, _) in quotes.items()}


def h2h_rows(ev):
    rows = []
    offers, pinn = collect(ev, "h2h")
    fair = devig(pinn.get(None, {}))
    for name, entries in offers.items():
        best, _, book = max(entries, key=lambda x: x[0])
        raw = pinn.get(None, {}).get(name)
        if name in fair:
            edge = (best / fair[name] - 1) * 100
            flag = "  <<" if edge >= EDGE_PCT else ""
            rows.append("  %s: %.2f (%s) | pin %.2f \u2192 fair %.2f  %+.1f%%%s"
                        % (name[:20], best, book, raw[0], fair[name], edge, flag))
        elif raw:
            rows.append("  %s: %.2f (%s) | pin %.2f (\u043e\u0434\u043d\u043e\u0441\u0442\u043e\u0440\u043e\u043d\u043d\u044f\u044f)"
                        % (name[:20], best, book, raw[0]))
        else:
            rows.append("  %s: %.2f (%s) | pin -" % (name[:20], best, book))
    return rows


def pointed_rows(ev, market_key, label):
    """Spreads/totals: fair Pinnacle price where both sides exist."""
    rows = []
    offers, pinn = collect(ev, market_key)
    fair_by_group = {g: devig(q) for g, q in pinn.items()}
    for name, entries in offers.items():
        best, point, book = max(entries, key=lambda x: x[0])
        pt = ("%+g" % point) if point is not None else ""
        placed = False
        for g, fair in fair_by_group.items():
            if name in fair and g == group_key(point):
                edge = (best / fair[name] - 1) * 100
                flag = "  <<" if edge >= EDGE_PCT else ""
                rows.append("  %s %s %s: %.2f (%s) | fair %.2f  %+.1f%%%s"
                            % (label, name[:14], pt, best, book, fair[name], edge, flag))
                placed = True
                break
        if not placed:
            rows.append("  %s %s %s: %.2f (%s) | pin -" % (label, name[:14], pt, best, book))
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
              "%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d  |  \u043e\u043a\u043d\u043e: %d\u0447\n"
              "fair = Pinnacle \u0431\u0435\u0437 \u043c\u0430\u0440\u0436\u0438  |  << = \u043f\u0435\u0440\u0435\u0432\u0435\u0441 \u043e\u0442 %.0f%%\n"
              % (TITLES[sport], now.strftime("%Y-%m-%d %H:%M"), len(games),
                 WINDOW_HOURS[sport], EDGE_PCT))

    blocks = []
    for ev in games:
        start = parse_iso(ev["commence_time"]).strftime("%d.%m %H:%M")
        block = ["\n%s \u2014 %s  (%s UTC)" % (ev.get("away_team", "?"),
                                               ev.get("home_team", "?"), start)]
        block += h2h_rows(ev)
        block += pointed_rows(ev, "totals", "T")
        block += pointed_rows(ev, "spreads", "F")
        blocks.append("\n".join(block))

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
