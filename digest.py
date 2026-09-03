#!/usr/bin/env python3
"""
Pre-round digest — one compact message per league at 09:00 UTC.

Does NOT write CSV and does NOT commit. It sends a short summary of
upcoming games for forwarding into chat.

Edge is measured against Pinnacle with the vig removed. Raw Pinnacle
prices carry ~2% margin, so comparing a best price to them flags
noise; the fair price is what a real edge is measured against.

Totals and spreads are priced on ONE anchored line — Pinnacle's number,
or the most common across books if Pinnacle has none. Without the anchor
each side's best price was hunted independently across every rung, so
Over and Under came back on different numbers and were not two sides of
the same bet.

Env required:
  ODDS_API_KEY, TG_TOKEN, TG_CHAT_ID, SPORT
"""

import os
import json
import sys
import urllib.request
import urllib.parse
import urllib.error
from collections import Counter
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
        # раньше ошибка глушилась и job оставался зелёным при пустом телеграме
        print("Telegram send failed: %s" % e)
        raise


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


def is_half(g):
    """True for 8.5, false for 9.0 — the .5 lines are the ones that cannot push."""
    return g is not None and abs((g * 2) % 2 - 1) < 1e-9


def two_sided_groups(offers):
    """Groups where at least two different outcomes are quoted somewhere."""
    names = {}
    for name, entries in offers.items():
        for _, p, _ in entries:
            names.setdefault(group_key(p), set()).add(name)
    return {g for g, ns in names.items() if g is not None and len(ns) >= 2}


def anchor_group(offers, pinn, market_key):
    """The single line the whole market is priced on.

    For totals a .5 line wins even when Pinnacle is standing on a whole
    number: a whole total can push, and we would rather lose the fair
    price than lose the bet. Spreads keep Pinnacle's line as before.
    """
    counts = Counter(group_key(p) for e in offers.values() for _, p, _ in e)
    counts.pop(None, None)
    two_sided = two_sided_groups(offers)
    pin_two = [g for g, q in pinn.items() if g is not None and len(q) >= 2]

    if market_key == "totals":
        pin_half = [g for g in pin_two if is_half(g)]
        if pin_half:
            return max(pin_half, key=lambda g: counts.get(g, 0))
        any_half = [g for g in two_sided if is_half(g)]
        if any_half:
            return max(any_half, key=lambda g: counts.get(g, 0))

    if pin_two:
        return max(pin_two, key=lambda g: counts.get(g, 0))
    if two_sided:
        return max(two_sided, key=lambda g: counts.get(g, 0))
    if pinn:
        return next((g for g in pinn if g is not None), None)
    return counts.most_common(1)[0][0] if counts else None


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
    """Spreads/totals: both sides priced on one anchored line."""
    rows = []
    offers, pinn = collect(ev, market_key)
    if not offers:
        return rows

    g = anchor_group(offers, pinn, market_key)
    quotes = pinn.get(g, {})
    fair = devig(quotes)
    pin_point = {n: p for n, (_, p) in quotes.items()}

    if market_key == "spreads" and not pin_point and g is not None:
        # No Pinnacle to name the favourite. Settle it by majority on the home
        # side and mirror it, or a book quoting the other favourite at the same
        # number would be printed as if it were the same bet.
        home = ev.get("home_team")
        home_pts = [p for _, p, _ in offers.get(home, []) if group_key(p) == g]
        if home_pts:
            hp = Counter(home_pts).most_common(1)[0][0]
            pin_point = {n: (hp if n == home else -hp) for n in offers}

    for name, entries in offers.items():
        # Only books standing on the anchored line. Where Pinnacle names the
        # exact point for this side, match its sign too — otherwise a book
        # with the opposite favourite would slip into the same group.
        want = pin_point.get(name)
        if want is not None:
            at = [e for e in entries if e[1] == want]
        else:
            at = [e for e in entries if group_key(e[1]) == g]
        if not at:
            pt = ("%+g" % want) if want is not None else (("%g" % g) if g is not None else "")
            rows.append("  %s %s %s: \u043d\u0435\u0442 \u0446\u0435\u043d\u044b \u043d\u0430 \u044d\u0442\u043e\u0439 \u043b\u0438\u043d\u0438\u0438"
                        % (label, name[:14], pt))
            continue
        best, point, book = max(at, key=lambda x: x[0])
        pt = ("%+g" % point) if point is not None else ""
        src = "%s, %d\u043a\u043d" % (book, len(at))
        if name in fair:
            edge = (best / fair[name] - 1) * 100
            flag = "  <<" if edge >= EDGE_PCT else ""
            rows.append("  %s %s %s: %.2f (%s) | fair %.2f  %+.1f%%%s"
                        % (label, name[:14], pt, best, src, fair[name], edge, flag))
        else:
            rows.append("  %s %s %s: %.2f (%s) | pin -"
                        % (label, name[:14], pt, best, src))
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
              "fair = Pinnacle \u0431\u0435\u0437 \u043c\u0430\u0440\u0436\u0438  |  "
              "\u0442\u043e\u0442\u0430\u043b \u0438 \u0444\u043e\u0440\u0430 \u2014 \u043e\u0434\u043d\u0430 \u043b\u0438\u043d\u0438\u044f  |  "
              "<< = \u043f\u0435\u0440\u0435\u0432\u0435\u0441 \u043e\u0442 %.0f%%\n"
              % (TITLES[sport], now.strftime("%Y-%m-%d %H:%M"), len(games),
                 WINDOW_HOURS[sport], EDGE_PCT))

    blocks = []
    for ev in games:
        start = parse_iso(ev["commence_time"]).strftime("%d.%m %H:%M")
        # европейская подача: хозяева первыми
        block = ["\n%s \u2014 %s  (%s UTC)" % (ev.get("home_team", "?"),
                                               ev.get("away_team", "?"), start)]
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
