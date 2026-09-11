#!/usr/bin/env python3
"""
nhl_totals.py — 10:00 UTC.
Строки метода по тоталам: дома / гости / очные, плюсы и минусы против
сегодняшней линии. Глубина 60 с добором из прошлых сезонов.
Линия — консенсус книг из снапшота, только .5, якорь от 2 книг.

Тотал считается по финальному счёту, включая ОТ и буллиты.
Env: TG_TOKEN, TG_CHAT_ID
"""

import os
import csv
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api-web.nhle.com/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

WINDOW_H = 30
DEPTH = 60          # глубина строк дома / в гостях
H2H_DEPTH = 60      # потолок очных
MIN_BOOKS = 2
SEASONS_BACK = 3    # сколько сезонов добирать
TG_CHUNK = 3800


def _post(text):
    url = "https://api.telegram.org/bot%s/sendMessage" % os.environ["TG_TOKEN"]
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()


def chunks(text, size=TG_CHUNK):
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
    parts = chunks(text)
    try:
        for i, part in enumerate(parts, 1):
            suffix = "" if len(parts) == 1 else "\n\n(%d/%d)" % (i, len(parts))
            _post(part + suffix)
    except Exception as e:
        print("Telegram send failed: %s" % e)
        raise


def api(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("HTTP %s on %s" % (e.code, path))
    except Exception as e:
        print("failed %s: %s" % (path, e))
    return None


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def abbrev(d):
    v = d.get("abbrev")
    return v.get("default", "?") if isinstance(v, dict) else (v or "?")


# ---------- линии из снапшота ----------

def lines_from_snapshot(now):
    """{event_id: (home, away, start, линия .5)} — якорь от MIN_BOOKS книг."""
    best = {}
    for back in (1, 0):
        path = os.path.join("data", "nhl", "%s.csv" % (now - timedelta(days=back)).strftime("%Y-%m-%d"))
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("mode") in ("live", "closing") or r.get("market") != "totals":
                    continue
                k = (r["event_id"], r["book"], r.get("point", ""))
                prev = best.get(k)
                if prev is None or r["snapshot_utc"] > prev["snapshot_utc"]:
                    best[k] = r

    ev = {}
    for r in best.values():
        p = fnum(r.get("point"))
        if p is None or p == int(p):      # целые не берём
            continue
        e = ev.setdefault(r["event_id"], {"home": r.get("home", ""), "away": r.get("away", ""),
                                          "start": r.get("commence_utc", ""), "books": {}})
        e["books"].setdefault(p, set()).add(r["book"])

    out = {}
    for eid, e in ev.items():
        if not e["books"]:
            continue
        line, bs = max(e["books"].items(), key=lambda kv: (len(kv[1]), -kv[0]))
        if len(bs) < MIN_BOOKS:
            continue
        out[eid] = (e["home"], e["away"], e["start"], line)
    return out


# ---------- игры команд ----------

_cache = {}


def team_games(team):
    """Игры за текущий и прошлые сезоны, по возрастанию даты."""
    if team in _cache:
        return _cache[team]
    games = []
    js = api("/club-schedule-season/%s/now" % team)
    season = (js or {}).get("currentSeason")
    games += (js or {}).get("games", [])
    if season:
        s = int(season)
        for i in range(1, SEASONS_BACK + 1):
            prev = "%d%d" % (s // 10000 - i, s % 10000 - i)
            js2 = api("/club-schedule-season/%s/%s" % (team, prev))
            games += (js2 or {}).get("games", [])
    fin = [g for g in games if g.get("gameState") in ("FINAL", "OFF")]
    fin.sort(key=lambda g: g.get("gameDate", ""))
    _cache[team] = fin
    return fin


def total_of(g):
    h = (g.get("homeTeam") or {}).get("score")
    a = (g.get("awayTeam") or {}).get("score")
    if h is None or a is None:
        return None
    return h + a


def marks(games, line):
    """+ тотал выше линии, - ниже. Возвращает строку по пять символов."""
    syms = []
    for g in games:
        t = total_of(g)
        if t is None:
            continue
        syms.append("+" if t > line else "-")
    grouped = [" ".join(["".join(syms[i:i + 5]) for i in range(0, len(syms), 5)])]
    return grouped[0], syms


def row_home(team, line):
    g = [x for x in team_games(team) if abbrev(x.get("homeTeam", {})) == team]
    return marks(g[-DEPTH:], line)


def row_away(team, line):
    g = [x for x in team_games(team) if abbrev(x.get("awayTeam", {})) == team]
    return marks(g[-DEPTH:], line)


def row_h2h(home, away, line):
    g = [x for x in team_games(home)
         if abbrev(x.get("homeTeam", {})) == home and abbrev(x.get("awayTeam", {})) == away]
    return marks(g[-H2H_DEPTH:], line)


def main():
    now = datetime.now(timezone.utc)
    lines = lines_from_snapshot(now)

    items = []
    hi = now + timedelta(hours=WINDOW_H)
    for eid, (home, away, start, line) in lines.items():
        try:
            t = parse_iso(start)
        except Exception:
            continue
        if now <= t <= hi:
            items.append((t, home, away, line))
    items.sort()

    head = "\U0001F3D2 NHL — метод по тоталам\n%s UTC  |  матчей: %d\nлиния = консенсус книг, только .5, якорь от %d кн\nдома/гости — %d с добором  |  очные — на площадке хозяев, до %d\nстарые слева, свежие справа\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(items), MIN_BOOKS, DEPTH, H2H_DEPTH)
    if not items:
        tg_send(head + "\nматчей с линией .5 в окне нет")
        return

    blocks = []
    for i, (t, home, away, line) in enumerate(items, 1):
        b = ["\n%2d. %s - %s  %s   тотал %g" % (i, home, away, t.strftime("%H:%M"), line)]
        s, raw = row_home(home, line)
        b.append("    %s дома (%d)" % (home, len(raw)))
        b.append("      " + (s or "нет игр"))
        s, raw = row_away(away, line)
        b.append("    %s гости (%d)" % (away, len(raw)))
        b.append("      " + (s or "нет игр"))
        s, raw = row_h2h(home, away, line)
        b.append("    очные дом (%d)" % len(raw))
        b.append("      " + (s or "нет игр"))
        blocks.append("\n".join(b))

    tg_send(head + "\n".join(blocks))
    print("totals: %d game(s)" % len(items))


if __name__ == "__main__":
    main()
