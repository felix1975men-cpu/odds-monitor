#!/usr/bin/env python3
"""
nba_totals.py — 10:03 UTC.
Строки метода по тоталам: дома / гости / очные, плюсы и минусы против
сегодняшней линии. Глубина 60 с добором из прошлых сезонов.
Линия — консенсус книг из снапшота, только .5, якорь от 2 книг.

Источник игр — ESPN site.api.espn.com (без ключа, отвечает из Actions
без User-Agent и с ним; cdn.nba.com и stats.nba.com из Actions не
работают). Расписание команды за сезон весит около 15 МБ, поэтому ответ
сразу сворачивается в короткие строки и кэшируется на время прогона.

Снимок даёт ПОЛНЫЕ имена ("Los Angeles Clippers"), а у ESPN это
"LA Clippers". Соответствие ищется по полному имени без акцентов, а если
не сошлось — по прозвищу команды ("Clippers", "Trail Blazers").

Считаются регулярка, плей-ин И плей-офф; не считается только предсезонка.
Очные берутся на площадке сегодняшних хозяев и тянутся вглубь на
H2H_SEASONS сезонов: пара встречается на этой площадке 1-2 раза за сезон,
поэтому набор до глубины 60 потребовал бы десятков сезонов. Строки
дома/гости набирают свои 60 за пару сезонов.

Тотал — по финальному счёту, с овертаймом.
Env: TG_TOKEN, TG_CHAT_ID. Необязательно: WINDOW_H, H2H_SEASONS.
"""

import os
import csv
import json
import time
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

WINDOW_H = int(os.environ.get("WINDOW_H", "30"))
DEPTH = 60                 # глубина строк дома / в гостях
H2H_DEPTH = 60             # потолок очных
H2H_SEASONS = int(os.environ.get("H2H_SEASONS", "10"))   # сколько сезонов вглубь для очных
MIN_BOOKS = 2
EMPTY_STOP = 2             # столько подряд пустых сезонов — и хватит
COUNT_TYPES = (2, 3, 5)    # регулярка, плей-ин, плей-офф; 1 — предсезонка, не берём
PAUSE = 0.15               # пауза между запросами к ESPN
TG_CHUNK = 3800


# ---------- telegram ----------

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


# ---------- api ----------

def api(url, retries=2):
    for attempt in range(retries):
        try:
            time.sleep(PAUSE)
            with urllib.request.urlopen(urllib.request.Request(url), timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            print("HTTP %s on %s" % (e.code, url))
            if e.code in (400, 404):
                return None
        except Exception as e:
            print("failed %s (try %d): %s" % (url, attempt + 1, e))
    return None


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def num(v):
    """Счёт приходит то числом/строкой, то {"value": 115.0, "displayValue": "115"}."""
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def norm(s):
    """Ключ для сравнения имён: без акцентов, без регистра и лишних пробелов."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def season_year(now):
    """2026-27 в ESPN — это season=2027."""
    return now.year + 1 if now.month >= 8 else now.year


def team_id_of(c):
    t = c.get("team") or {}
    return str(t.get("id") or c.get("id") or "")


# ---------- имя команды -> ESPN id ----------

_teams = None


def team_index():
    """Список {id, abbr, full, nick}: из /teams, там и полное имя, и прозвище."""
    global _teams
    if _teams is not None:
        return _teams
    _teams = []
    js = api("%s/teams" % ESPN) or {}
    try:
        lst = js["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError):
        lst = []
    for it in lst:
        t = it.get("team") or {}
        if not t.get("id"):
            continue
        _teams.append({
            "id": str(t["id"]),
            "abbr": t.get("abbreviation", "?"),
            "full": norm(t.get("displayName", "")),
            "nick": norm(t.get("name") or t.get("shortDisplayName") or ""),
        })
    return _teams


def find_team(name):
    """(id, abbr) по имени из снимка, иначе (None, None)."""
    n = norm(name)
    teams = team_index()
    for t in teams:
        if t["full"] and t["full"] == n:
            return t["id"], t["abbr"]
    hits = [t for t in teams if t["nick"] and (n == t["nick"] or n.endswith(" " + t["nick"]))]
    if len(hits) == 1:
        return hits[0]["id"], hits[0]["abbr"]
    return None, None


# ---------- игры команд ----------

_cache = {}


def rows_for(tid, season, stype):
    """Короткие строки по сыгранным играм команды за сезон и тип."""
    key = (tid, season, stype)
    if key in _cache:
        return _cache[key]
    js = api("%s/teams/%s/schedule?season=%d&seasontype=%d" % (ESPN, tid, season, stype)) or {}
    out = []
    for ev in js.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue
        cs = comp.get("competitors") or []
        us = next((c for c in cs if team_id_of(c) == tid), None)
        them = next((c for c in cs if c is not us), None)
        if not us or not them:
            continue
        pf, pa = num(us.get("score")), num(them.get("score"))
        if pf is None or pa is None:
            continue
        try:
            typ = int((ev.get("seasonType") or {}).get("id") or stype)
        except (TypeError, ValueError):
            typ = stype
        if typ not in COUNT_TYPES:
            continue
        try:
            when = parse_iso(comp.get("date") or ev.get("date"))
        except Exception:
            continue
        out.append({"id": str(ev.get("id")), "t": when,
                    "home": us.get("homeAway") == "home",
                    "opp": team_id_of(them), "total": pf + pa})
    _cache[key] = out
    return out


def season_rows(tid, season):
    seen, out = set(), []
    for typ in COUNT_TYPES:
        for r in rows_for(tid, season, typ):
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
    out.sort(key=lambda r: r["t"])
    return out


def walk(tid, now, seasons, need=None, want=None):
    """Игры команды от свежих сезонов к старым, до `seasons` сезонов вглубь.
    need — сколько подходящих игр достаточно (None = все `seasons`).
    want(r) — фильтр, по которому считается достаточность.
    Возвращает список по возрастанию даты."""
    out, empties = [], 0
    base = season_year(now)
    for i in range(seasons):
        rows = season_rows(tid, base - i)
        if not rows:
            empties += 1
            if empties >= EMPTY_STOP and i > 0:
                break
            continue
        empties = 0
        out = rows + out
        if need is not None and want is not None:
            if sum(1 for r in out if want(r)) >= need:
                break
    return out


def marks(rows, line):
    """+ тотал выше линии, - ниже. Строка группами по пять."""
    syms = ["+" if r["total"] > line else "-" for r in rows]
    grouped = " ".join("".join(syms[i:i + 5]) for i in range(0, len(syms), 5))
    return grouped, syms


# ---------- линии из снимков ----------

def consensus_lines(now):
    """{event_id: (home, away, start, line)}: .5-линия, которую держат больше
    всего книг, не меньше MIN_BOOKS."""
    best = {}
    for back in (1, 0):
        path = os.path.join("data", "nba", "%s.csv" % (now - timedelta(days=back)).strftime("%Y-%m-%d"))
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
    lines = {}
    for eid, e in ev.items():
        if not e["books"]:
            continue
        line, bs = max(e["books"].items(), key=lambda kv: (len(kv[1]), -kv[0]))
        if len(bs) >= MIN_BOOKS:
            lines[eid] = (e["home"], e["away"], e["start"], line)
    return lines


# ---------- вывод ----------

def main():
    now = datetime.now(timezone.utc)
    lines = consensus_lines(now)

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

    head = "\U0001F3C0 NBA — метод по тоталам\n%s UTC  |  матчей: %d\nлиния = консенсус книг, только .5, якорь от %d кн\nдома/гости — %d с добором  |  очные — на площадке хозяев, до %d сезонов вглубь\nрегулярка, плей-ин и плей-офф, без предсезонки  |  старые слева, свежие справа\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(items), MIN_BOOKS, DEPTH, H2H_SEASONS)
    if not items:
        tg_send(head + "\nматчей с линией .5 в окне нет")
        print("no games with a .5 line in window")
        return

    blocks = []
    for i, (t, home, away, line) in enumerate(items, 1):
        hid, hab = find_team(home)
        aid, aab = find_team(away)
        b = ["\n%2d. %s - %s  %s   тотал %g" % (i, home, away, t.strftime("%H:%M"), line)]
        if not hid or not aid:
            miss = ", ".join(x for x, c in ((home, hid), (away, aid)) if not c)
            b.append("    команда не найдена в ESPN: %s — строки не построены" % miss)
            blocks.append("\n".join(b))
            continue

        home_hist = walk(hid, now, H2H_SEASONS)
        away_hist = walk(aid, now, H2H_SEASONS, need=DEPTH, want=lambda r: not r["home"])

        s, raw = marks([r for r in home_hist if r["home"]][-DEPTH:], line)
        b.append("    %s дома (%d)" % (hab, len(raw)))
        b.append("      " + (s or "нет игр"))
        s, raw = marks([r for r in away_hist if not r["home"]][-DEPTH:], line)
        b.append("    %s гости (%d)" % (aab, len(raw)))
        b.append("      " + (s or "нет игр"))
        s, raw = marks([r for r in home_hist if r["home"] and r["opp"] == aid][-H2H_DEPTH:], line)
        b.append("    очные дом (%d)" % len(raw))
        b.append("      " + (s or "нет игр"))
        blocks.append("\n".join(b))

    tg_send(head + "\n".join(blocks))
    print("totals: %d game(s)" % len(items))


if __name__ == "__main__":
    main()
