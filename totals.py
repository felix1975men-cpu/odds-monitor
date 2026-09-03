#!/usr/bin/env python3
"""
Метод по тоталам — картина плюсов и минусов, три строки на матч.

По каждому матчу тура печатаются три строки:
  1. домашние игры хозяев в текущем регулярном сезоне
  2. выездные игры гостей в текущем регулярном сезоне
  3. очные встречи НА СТАДИОНЕ ХОЗЯЕВ, вглубь по сезонам, до 30 игр

Каждая прошлая игра помечается + (сыграло больше сегодняшней линии)
или − (меньше). Линия — консенсус книг, только .5, никогда целое.
Старые слева, свежие справа.

Вердикты не считаются. Картину читает человек.

Линия берётся из снимка, который уже пишет odds_monitor.py, поэтому
кредиты The Odds API не тратятся. История — MLB Stats API, бесплатно.

Env required: TG_TOKEN, TG_CHAT_ID
"""

import os
import csv
import json
import urllib.request
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

STATS = "https://statsapi.mlb.com/api/v1"
WINDOW_HOURS = 30
H2H_CAP = 30
H2H_OLDEST = 2000          # глубже не копаем
MSG_LIMIT = 3500


def jget(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "odds-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("fetch failed %s: %s" % (url[:90], e))
        return None


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


def is_half(x):
    return x is not None and abs((x * 2) % 2 - 1) < 1e-9


# ------------------------------------------------------------------- линия

def load_snapshot_rows():
    """Строки сегодняшнего и вчерашнего снимка, которые пишет odds_monitor."""
    now = datetime.now(timezone.utc)
    rows = []
    for d in (now - timedelta(days=1), now):
        p = os.path.join("data", "mlb", "%s.csv" % d.strftime("%Y-%m-%d"))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rows.extend(list(csv.DictReader(f)))
    return rows


def consensus_lines(rows):
    """{(хозяева, гости): линия} — точка .5, на которой стоит больше всего книг."""
    latest = {}
    for r in rows:
        if r.get("market") != "totals" or r.get("mode") != "scheduled":
            continue
        eid, snap = r.get("event_id"), r.get("snapshot_utc", "")
        if eid not in latest or snap > latest[eid]:
            latest[eid] = snap
    per = defaultdict(lambda: {"pts": Counter(), "sides": defaultdict(set),
                               "home": None, "away": None})
    for r in rows:
        if r.get("market") != "totals" or r.get("mode") != "scheduled":
            continue
        eid = r.get("event_id")
        if r.get("snapshot_utc") != latest.get(eid):
            continue
        try:
            pt = float(r.get("point"))
        except (TypeError, ValueError):
            continue
        e = per[eid]
        e["home"], e["away"] = r.get("home"), r.get("away")
        e["sides"][pt].add(r.get("outcome"))
        e["pts"][pt] += 1
    out = {}
    for e in per.values():
        halves = [(n, p) for p, n in e["pts"].items()
                  if is_half(p) and len(e["sides"][p]) >= 2]
        if halves and e["home"]:
            halves.sort(reverse=True)
            out[(e["home"], e["away"])] = halves[0][1]
    return out


# ----------------------------------------------------------------- история

def season_finals(team_id, season):
    """[(дата, id хозяев, id гостей, всего очков)] — завершённые игры сезона."""
    data = jget("%s/schedule?sportId=1&teamId=%d&season=%d"
                "&startDate=%d-03-01&endDate=%d-11-30&hydrate=linescore"
                % (STATS, team_id, season, season, season))
    out = []
    for day in (data or {}).get("dates", []):
        for g in day.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            if (g.get("gameType") or "R") != "R":
                continue                      # только регулярный сезон
            t = g.get("teams") or {}
            h, a = t.get("home") or {}, t.get("away") or {}
            if h.get("score") is None or a.get("score") is None:
                continue
            out.append((g.get("gameDate", ""),
                        (h.get("team") or {}).get("id"),
                        (a.get("team") or {}).get("id"),
                        h["score"] + a["score"]))
    out.sort()
    return out


_SEASON = {}


def cached_season(team_id, season):
    k = (team_id, season)
    if k not in _SEASON:
        _SEASON[k] = season_finals(team_id, season)
    return _SEASON[k]


def h2h_at_home(home_id, away_id, season):
    """Очные на стадионе хозяев, вглубь по сезонам, пока не наберётся H2H_CAP."""
    out = []
    yr = season
    while yr >= H2H_OLDEST and len(out) < H2H_CAP:
        data = jget("%s/schedule?sportId=1&teamId=%d&opponentId=%d&season=%d"
                    "&startDate=%d-03-01&endDate=%d-11-30&hydrate=linescore"
                    % (STATS, home_id, away_id, yr, yr, yr))
        rows = []
        for day in (data or {}).get("dates", []):
            for g in day.get("games", []):
                if (g.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                if (g.get("gameType") or "R") != "R":
                    continue
                t = g.get("teams") or {}
                h, a = t.get("home") or {}, t.get("away") or {}
                if (h.get("team") or {}).get("id") != home_id:
                    continue              # только на поле сегодняшних хозяев
                if h.get("score") is None or a.get("score") is None:
                    continue
                rows.append((g.get("gameDate", ""), h["score"] + a["score"]))
        rows.sort()
        out = [t for _, t in rows] + out
        yr -= 1
    return out[-H2H_CAP:]


def picture(totals, line, group=5):
    """Последовательность + и − против сегодняшней линии, от старых к новым."""
    seq = ["+" if t > line else "-" for t in totals]
    if not seq:
        return "нет игр"
    return " ".join("".join(seq[i:i + group]) for i in range(0, len(seq), group))


# -------------------------------------------------------------------- main

def main():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=WINDOW_HOURS)
    season = now.year

    lines = consensus_lines(load_snapshot_rows())
    if not lines:
        print("нет снимка с тоталами — odds_monitor ещё не отработал?")
        return

    games = []
    for d in (now, now + timedelta(days=1)):
        data = jget("%s/schedule?sportId=1&date=%s&hydrate=team"
                    % (STATS, d.strftime("%m/%d/%Y")))
        for day in (data or {}).get("dates", []):
            for g in day.get("games", []):
                t = parse_iso(g["gameDate"])
                if now <= t <= horizon:
                    games.append(g)
    games.sort(key=lambda g: g["gameDate"])

    blocks, n = [], 0
    for g in games:
        t = g.get("teams", {})
        H = (t.get("home") or {}).get("team") or {}
        A = (t.get("away") or {}).get("team") or {}
        line = lines.get((H.get("name"), A.get("name")))
        if line is None:
            continue                       # линии нет — игра следующего дня
        n += 1
        hid, aid = H.get("id"), A.get("id")
        home_tot = [tot for _, h, _a, tot in cached_season(hid, season) if h == hid]
        away_tot = [tot for _, _h, a, tot in cached_season(aid, season) if a == aid]
        h2h_tot = h2h_at_home(hid, aid, season)

        rows = [("%s дома" % H.get("abbreviation", "?"), home_tot),
                ("%s гости" % A.get("abbreviation", "?"), away_tot),
                ("очные дом", h2h_tot)]
        body = "\n".join("    %s (%d)\n      %s" % (nm, len(v), picture(v, line))
                         for nm, v in rows)
        blocks.append("%2d. %s - %s   тотал %s\n%s"
                      % (n, H.get("abbreviation", "?"), A.get("abbreviation", "?"),
                         line, body))

    if not blocks:
        print("нет матчей с линией в снимке")
        return

    head = ("\u26BE MLB \u2014 \u043c\u0435\u0442\u043e\u0434 \u043f\u043e \u0442\u043e\u0442\u0430\u043b\u0430\u043c\n"
            "%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d\n"
            "\u043b\u0438\u043d\u0438\u044f = \u043a\u043e\u043d\u0441\u0435\u043d\u0441\u0443\u0441 \u043a\u043d\u0438\u0433, \u0442\u043e\u043b\u044c\u043a\u043e .5\n"
            "\u0434\u043e\u043c\u0430/\u0433\u043e\u0441\u0442\u0438 \u2014 \u0441\u0435\u0437\u043e\u043d %d  |  "
            "\u043e\u0447\u043d\u044b\u0435 \u2014 \u043d\u0430 \u043f\u043e\u043b\u0435 \u0445\u043e\u0437\u044f\u0435\u0432, \u0434\u043e %d\n"
            "\u0441\u0442\u0430\u0440\u044b\u0435 \u0441\u043b\u0435\u0432\u0430, \u0441\u0432\u0435\u0436\u0438\u0435 \u0441\u043f\u0440\u0430\u0432\u0430\n\n"
            % (now.strftime("%Y-%m-%d %H:%M"), n, season, H2H_CAP))

    chunk, sent = head, 0
    for b in blocks:
        if len(chunk) + len(b) + 2 > MSG_LIMIT:
            tg_send(chunk)
            sent += 1
            chunk = ""
        chunk += b + "\n\n"
    if chunk.strip():
        tg_send(chunk)
        sent += 1
    print("totals table sent in %d message(s), %d games with a line" % (sent, n))


if __name__ == "__main__":
    main()
