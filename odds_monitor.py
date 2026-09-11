#!/usr/bin/env python3
"""
Odds monitor — 4 sports, one script.

Modes:
  scheduled : plain snapshot of the whole league (3 credits)
  closing   : checks /events (FREE), and only pulls odds if a game
              starts inside the closing window (3 credits)

Env required:
  ODDS_API_KEY, TG_TOKEN, TG_CHAT_ID, SPORT
Env optional:
  MODE        scheduled (default) | closing
  CLOSE_MIN   minutes before kickoff, near edge  (default 10)
  CLOSE_MAX   minutes before kickoff, far edge   (default 30)
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


def env_int(name, default):
    """Closing window edges are per-league: a cron that slips past a narrow
    window loses the snapshot for good, so NHL runs wider than the default."""
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Closing trigger window, minutes before kickoff.
CLOSE_MIN = env_int("CLOSE_MIN", 10)
CLOSE_MAX = env_int("CLOSE_MAX", 30)

# Telegram hard limit is 4096; leave room for the chunk counter.
TG_CHUNK = 3800

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


def _tg_post(text):
    url = "https://api.telegram.org/bot%s/sendMessage" % os.environ["TG_TOKEN"]
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()


def chunks(text, size=TG_CHUNK):
    """Split on line boundaries so a message never cuts a price in half."""
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > size and buf:
            out.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        out.append(buf)
    return out


def tg_send(text):
    """Send, splitting long digests. Raises after logging so the job turns red."""
    parts = chunks(text)
    try:
        for i, part in enumerate(parts, 1):
            suffix = "" if len(parts) == 1 else "\n\n(%d/%d)" % (i, len(parts))
            _tg_post(part + suffix)
    except Exception as e:
        print("Telegram send failed: %s" % e)
        raise


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def is_three_way(mk):
    """NHL preseason/regulation 1X2 arrives under the h2h key. Not the same market."""
    if mk.get("key") != "h2h":
        return False
    outcomes = mk.get("outcomes", [])
    if len(outcomes) > 2:
        return True
    return any((oc.get("name", "") or "").strip().lower() == "draw" for oc in outcomes)


def market_key(mk):
    """Three-way h2h is stored under its own name so h2h stays one clean market."""
    return "h2h_3way" if is_three_way(mk) else mk["key"]


def flatten(events, stamp, mode):
    """Turn the API response into flat CSV rows."""
    rows = []
    for ev in events:
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                mkey = market_key(mk)
                for oc in mk.get("outcomes", []):
                    rows.append([
                        stamp, mode, ev["id"], ev["commence_time"],
                        ev.get("home_team", ""), ev.get("away_team", ""),
                        bm["key"], mkey, oc.get("name", ""),
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


def pending_soon(events, already, now, hours=1):
    """Games starting within the next hour that still have no closing snapshot.
    Printed in the digest so a missed window is visible the same evening."""
    hi = now + timedelta(hours=hours)
    out = 0
    for e in events:
        if e["id"] in already:
            continue
        t = parse_iso(e["commence_time"])
        if now <= t <= hi:
            out += 1
    return out


def summarise(sport, events, stamp, mode, left, rows, waiting=None):
    """Short Telegram digest: Pinnacle as the anchor, best available price.
    European order — HOME team first. Two-way h2h only."""
    head = "%s \u2014 %s\n%s UTC\n" % (
        TITLES[sport],
        "\u0417\u0410\u041a\u0420\u042b\u0422\u0418\u0415" if mode == "closing" else "\u0441\u043d\u0438\u043c\u043e\u043a",
        stamp[:16].replace("T", " "))
    lines = []
    three_way = 0
    for ev in sorted(events, key=lambda e: e["commence_time"]):
        start = parse_iso(ev["commence_time"]).strftime("%d.%m %H:%M")
        home = ev.get("home_team", "?")
        away = ev.get("away_team", "?")
        lines.append("\n%s \u2014 %s  (%s)" % (home, away, start))
        prices = {}
        pin = {}
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                if is_three_way(mk):
                    three_way += 1
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
        if not prices:
            lines.append("  \u0434\u0432\u0443\u0445\u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0433\u043e ML \u043d\u0435\u0442 \u2014 \u0442\u043e\u043b\u044c\u043a\u043e 1X2")
            continue
        # home first, then away, then anything else
        order = {home: 0, away: 1}
        for team in sorted(prices, key=lambda t: (order.get(t, 2), t)):
            best, book = prices[team]
            anchor = (" | pin %.2f" % pin[team]) if team in pin else ""
            lines.append("  %s: %.2f (%s)%s" % (team[:22], best, book, anchor))

    counts = {}
    for r in rows:
        counts[r[7]] = counts.get(r[7], 0) + 1
    mk_line = ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts))

    tail = "\n\n\u0441\u043e\u0431\u044b\u0442\u0438\u0439: %d" % len(events)
    if left:
        tail += "  |  \u043a\u0440\u0435\u0434\u0438\u0442\u043e\u0432 \u043e\u0441\u0442\u0430\u043b\u043e\u0441\u044c: %s" % left
    if mk_line:
        tail += "\n\u0432 csv: %s" % mk_line
    if mode == "closing":
        tail += "\n\u043e\u043a\u043d\u043e: %d-%d \u043c\u0438\u043d \u0434\u043e \u0441\u0442\u0430\u0440\u0442\u0430" % (CLOSE_MIN, CLOSE_MAX)
        if waiting:
            tail += "\n\u0431\u0435\u0437 \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u044f \u0432 \u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0438\u0439 \u0447\u0430\u0441: %d" % waiting
    if three_way:
        tail += ("\n1X2 (\u043e\u0441\u043d\u043e\u0432\u043d\u043e\u0435 \u0432\u0440\u0435\u043c\u044f) \u043e\u0442\u0431\u0440\u043e\u0448\u0435\u043d\u043e \u0443 %d \u043a\u043d\u0438\u0433 \u2014 "
                 "\u0432 csv \u043a\u0430\u043a h2h_3way" % three_way)
    return head + "".join(lines) + tail


def main():
    sport = os.environ["SPORT"].lower()
    mode = os.environ.get("MODE", "scheduled").lower()
    key = SPORTS[sport]
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    targets = None
    waiting = None

    if mode == "closing":
        if CLOSE_MIN >= CLOSE_MAX:
            print("bad window: CLOSE_MIN %d >= CLOSE_MAX %d" % (CLOSE_MIN, CLOSE_MAX))
            sys.exit(1)
        # FREE call — costs no credits.
        events, _, _ = api_get("/sports/%s/events" % key)
        already = done_ids(sport)
        lo = now + timedelta(minutes=CLOSE_MIN)
        hi = now + timedelta(minutes=CLOSE_MAX)
        targets = [e["id"] for e in events
                   if e["id"] not in already and lo <= parse_iso(e["commence_time"]) <= hi]
        waiting = pending_soon(events, already, now)
        if not targets:
            print("no games in the %d-%d min window; exiting without spending credits "
                  "(%d game(s) pending in the next hour)" % (CLOSE_MIN, CLOSE_MAX, waiting))
            return
        print("closing window (%d-%d min) hit for %d event(s)" % (
            CLOSE_MIN, CLOSE_MAX, len(targets)))

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

    tg_send(summarise(sport, odds, stamp, mode, left, rows, waiting))
    print("wrote %d rows to %s | credits used %s, left %s" % (len(rows), path, used, left))
    # Signal the workflow that there is something to commit.
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write("changed=true\n")


if __name__ == "__main__":
    main()
