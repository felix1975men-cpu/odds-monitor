#!/usr/bin/env python3
"""
Метод по тоталам — три строки, голосование, один отбор в день.

По каждому матчу тура считаются три строки:
  1. домашние игры хозяев в текущем регулярном сезоне
  2. выездные игры гостей в текущем регулярном сезоне
  3. очные встречи НА СТАДИОНЕ ХОЗЯЕВ, не больше 30, вглубь по сезонам

Каждая прошлая игра помечается + (сыграло больше сегодняшней линии)
или − (меньше). Линия — консенсус книг, только .5, никогда целое.
Вердикт строки — простое большинство. Три вердикта голосуют.

Отбор:
  3-0            — бесспорный
  2-1            — только если совпали строка хозяев и строка очных;
                   строка гостей не учитывает фактор стадиона

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
H2H_SEASONS_BACK = 6
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


# ------------------------------------------------------------------ линия

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


def is_half(x):
    return x is not None and abs((x * 2) % 2 - 1) < 1e-9


def consensus_lines(rows):
    """{(home, away): линия} — точка .5, на которой стоит больше всего книг."""
    latest = {}
    for r in rows:
        if r.get("market") != "totals" or r.get("mode") != "scheduled":
            continue
        eid = r.get("event_id")
        snap = r.get("snapshot_utc", "")
        if eid not in latest or snap > latest[eid]:
            latest[eid] = snap
    per_event = defaultdict(lambda: {"pts": Counter(), "sides": defaultdict(set),
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
        e = per_event[eid]
        e["home"], e["away"] = r.get("home"), r.get("away")
        e["sides"][pt].add(r.get("outcome"))
        e["pts"][pt] += 1
    out = {}
    for eid, e in per_event.items():
        halves = [(n, p) for p, n in e["pts"].items()
                  if is_half(p) and len(e["sides"][p]) >= 2]
        if not halves or not e["home"]:
            continue
        halves.sort(reverse=True)
        out[(e["home"], e["away"])] = halves[0][1]
    return out


# --------------------------------------------------------------- история

def finals(url):
    """[(дата, хозяева_id, гости_id, всего очков)] по завершённым играм."""
    data = jget(url)
    out = []
    for day in (data or {}).get("dates", []):
        for g in day.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
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


_CACHE = {}


def season_games(team_id, season):
    k = (team_id, season)
    if k not in _CACHE:
        _CACHE[k] = finals("%s/schedule?sportId=1&teamId=%d&season=%d"
                           "&startDate=%d-03-01&endDate=%d-12-31&hydrate=linescore"
                           % (STATS, team_id, season, season, season))
    return _CACHE[k]


def h2h_at_home(home_id, away_id, season):
    """Очные на стадионе хозяев, вглубь по сезонам, не больше H2H_CAP."""
    out = []
    for yr in range(season, season - H2H_SEASONS_BACK, -1):
        g = jget("%s/schedule?sportId=1&teamId=%d&opponentId=%d&season=%d"
                 "&startDate=%d-03-01&endDate=%d-12-31&hydrate=linescore"
                 % (STATS, home_id, away_id, yr, yr, yr))
        rows = []
        for day in (g or {}).get("dates", []):
            for gm in day.get("games", []):
                if (gm.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                t = gm.get("teams") or {}
                h, a = t.get("home") or {}, t.get("away") or {}
                if (h.get("team") or {}).get("id") != home_id:
                    continue          # только на поле сегодняшних хозяев
                if h.get("score") is None or a.get("score") is None:
                    continue
                rows.append((gm.get("gameDate", ""), h["score"] + a["score"]))
        rows.sort()
        out = rows + out
        if len(out) >= H2H_CAP:
            break
    return out[-H2H_CAP:]


def verdict(totals, line):
    """Вердикт строки: + больше, - меньше, = поровну."""
    plus = sum(1 for t in totals if t > line)
    minus = sum(1 for t in totals if t < line)
    return "+" if plus > minus else ("-" if minus > plus else "=")


def picture(totals, line, group=5):
    """Последовательность + и − против сегодняшней линии, от старых к новым.

    Группами по пять, чтобы картина читалась глазом и не рассыпалась
    при переносе строки в телеграме.
    """
    seq = ["+" if t > line else "-" for t in totals]
    if not seq:
        return "нет игр"
    return " ".join("".join(seq[i:i + group]) for i in range(0, len(seq), group))


# ------------------------------------------------------------------- main

def main():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=WINDOW_HOURS)
    season = now.year

    lines = consensus_lines(load_snapshot_rows())
    if not lines:
        print("нет снимка с тоталами — odds_monitor ещё не отработал?")
        return

    sched = jget("%s/schedule?sportId=1&date=%s&hydrate=team"
                 % (STATS, now.strftime("%m/%d/%Y")))
    sched2 = jget("%s/schedule?sportId=1&date=%s&hydrate=team"
                  % (STATS, (now + timedelta(days=1)).strftime("%m/%d/%Y")))
    games = []
    for data in (sched, sched2):
        for day in (data or {}).get("dates", []):
            for g in day.get("games", []):
                t = parse_iso(g["gameDate"])
                if now <= t <= horizon:
                    games.append(g)
    games.sort(key=lambda g: g["gameDate"])

    blocks, verdicts = [], []
    for i, g in enumerate(games, 1):
        t = g.get("teams", {})
        H = (t.get("home") or {}).get("team") or {}
        A = (t.get("away") or {}).get("team") or {}
        line = lines.get((H.get("name"), A.get("name")))
        if line is None:
            blocks.append("%2d. %s - %s\n    нет линии в снимке"
                          % (i, H.get("abbreviation", "?"), A.get("abbreviation", "?")))
            continue

        hid, aid = H.get("id"), A.get("id")
        home_tot = [tot for _, h, a, tot in season_games(hid, season) if h == hid]
        away_tot = [tot for _, h, a, tot in season_games(aid, season) if a == aid]
        h2h_tot = [tot for _, tot in h2h_at_home(hid, aid, season)]

        rows = [("%s дома" % H.get("abbreviation", "?"), home_tot),
                ("%s гости" % A.get("abbreviation", "?"), away_tot),
                ("очные дом", h2h_tot)]
        body = "\n".join("    %s (%d)\n      %s" % (n, len(v), picture(v, line))
                         for n, v in rows)

        # вердикты — только для статистики, на картину не влияют
        vh, va, vx = (verdict(v, line) for _, v in rows)
        over, under = [vh, va, vx].count("+"), [vh, va, vx].count("-")
        pair = "%s - %s" % (H.get("abbreviation", "?"), A.get("abbreviation", "?"))
        if max(over, under) == 3:
            verdicts.append(("3-0", pair, "ТБ" if over == 3 else "ТМ", line))
        elif max(over, under) == 2 and vh == vx and vh != "=":
            verdicts.append(("2-1", pair, "ТБ" if vh == "+" else "ТМ", line))
        blocks.append("%2d. %s - %s   тотал %s\n%s"
                      % (i, H.get("abbreviation", "?"), A.get("abbreviation", "?"),
                         line, body))

    head = ("\u26BE MLB \u2014 \u043c\u0435\u0442\u043e\u0434 \u043f\u043e \u0442\u043e\u0442\u0430\u043b\u0430\u043c\n"
            "%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d\n"
            "\u043b\u0438\u043d\u0438\u044f = \u043a\u043e\u043d\u0441\u0435\u043d\u0441\u0443\u0441 \u043a\u043d\u0438\u0433, \u0442\u043e\u043b\u044c\u043a\u043e .5\n"
            "\u0434\u043e\u043c\u0430/\u0433\u043e\u0441\u0442\u0438 \u2014 \u0441 \u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u0435\u0437\u043e\u043d\u0430  |  "
            "\u043e\u0447\u043d\u044b\u0435 \u2014 \u043d\u0430 \u043f\u043e\u043b\u0435 \u0445\u043e\u0437\u044f\u0435\u0432, \u0434\u043e %d\n"
            "\u0441\u0442\u0430\u0440\u044b\u0435 \u0441\u043b\u0435\u0432\u0430, \u0441\u0432\u0435\u0436\u0438\u0435 \u0441\u043f\u0440\u0430\u0432\u0430\n\n"
            % (now.strftime("%Y-%m-%d %H:%M"), len(games), H2H_CAP))

    tail = "\n\u0412\u0415\u0420\u0414\u0418\u041a\u0422\u042b \u0414\u041b\u042f \u0421\u0422\u0410\u0422\u0418\u0421\u0422\u0418\u041a\u0418\n"
    for grp in ("3-0", "2-1"):
        items = [v for v in verdicts if v[0] == grp]
        if items:
            tail += "%s (%d): " % (grp, len(items)) + " | ".join(
                "%s %s %s" % (p, s_, l) for _, p, s_, l in items) + "\n"
        else:
            tail += "%s: нет\n" % grp

    chunk, sent = head, 0
    for b in blocks:
        if len(chunk) + len(b) + 2 > MSG_LIMIT:
            tg_send(chunk)
            sent += 1
            chunk = ""
        chunk += b + "\n\n"
    chunk += tail
    tg_send(chunk)
    sent += 1
    print("totals table sent in %d message(s), %d games" % (sent, len(games)))


if __name__ == "__main__":
    main()
