#!/usr/bin/env python3
"""
nhl_totals.py — 10:00 UTC.
Строки метода по тоталам: дома / гости / очные, плюсы и минусы против
сегодняшней линии. Глубина 60 с добором из прошлых сезонов.
Линия — консенсус книг из снапшота, только .5, якорь от 2 книг.

Снимок даёт ПОЛНЫЕ имена команд ("Carolina Hurricanes"), а api-web
понимает только коды ("CAR"). Соответствие берётся из /standings/now,
без учёта акцентов, иначе "Montréal Canadiens" не сходится.

Считаются регулярка И плей-офф; не считается только предсезонка.
Очные тянутся вглубь, пока API отдаёт сезоны: на своей площадке пара
встречается 1-2 раза за сезон, и на трёх сезонах строка упиралась в 5-6
знаков. Строки дома/гости набирают свои 60 за пару сезонов, поэтому
для гостей вглубь не лезем — только для хозяев, чьи игры нужны и очным.

Тотал — по финальному счёту, с ОТ и буллитами.
Env: TG_TOKEN, TG_CHAT_ID
"""

import os
import csv
import json
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api-web.nhle.com/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

WINDOW_H = 30
DEPTH = 60            # глубина строк дома / в гостях
H2H_DEPTH = 60        # потолок очных
MIN_BOOKS = 2
MAX_SEASONS = 25      # предохранитель на обход вглубь
EMPTY_STOP = 2        # столько подряд пустых сезонов — и хватит
COUNT_TYPES = (2, 3)  # регулярка и плей-офф; 1 — предсезонка, не берём
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
    req = urllib.request.Request(BASE + urllib.parse.quote(path, safe="/?=&"))
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


def txt(v):
    if isinstance(v, dict):
        return v.get("default") or "?"
    return v if v else "?"


def abbrev(d):
    return txt(d.get("abbrev")) if isinstance(d, dict) else "?"


def norm(s):
    """Ключ для сравнения имён: без акцентов, без регистра и лишних пробелов."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


# ---------- имя команды -> код ----------

_codes = None


def team_codes():
    """{нормализованное имя: код}. Из standings — там и полное имя, и код."""
    global _codes
    if _codes is not None:
        return _codes
    _codes = {}
    js = api("/standings/now")
    for t in (js or {}).get("standings", []):
        code = txt(t.get("teamAbbrev"))
        if not code or code == "?":
            continue
        for key in ("teamName", "teamCommonName", "teamPlaceName"):
            nm = txt(t.get(key))
            if nm and nm != "?":
                _codes[norm(nm)] = code
        _codes[norm(code)] = code
    return _codes


def code_of(name):
    """Код команды по имени из снимка, иначе None."""
    m = team_codes()
    n = norm(name)
    if n in m:
        return m[n]
    for k, v in m.items():
        if k and (k in n or n in k):
            return v
    parts = n.split()
    if parts and parts[-1] in m:
        return m[parts[-1]]
    return None


# ---------- игры команд ----------

_season_cache = {}
_walked = {}


def season_games(code, season):
    """Сыгранные матчи команды за один сезон, без предсезонки."""
    key = (code, season)
    if key not in _season_cache:
        js = api("/club-schedule-season/%s/%s" % (code, season))
        games = [g for g in (js or {}).get("games", [])
                 if g.get("gameState") in ("FINAL", "OFF")
                 and g.get("gameType") in COUNT_TYPES]
        games.sort(key=lambda g: g.get("gameDate", ""))
        _season_cache[key] = (games, js or {})
    return _season_cache[key][0]


def current_season(code):
    js = api("/club-schedule-season/%s/now" % code)
    if js:
        _season_cache[(code, "now")] = (
            [g for g in js.get("games", [])
             if g.get("gameState") in ("FINAL", "OFF")
             and g.get("gameType") in COUNT_TYPES],
            js)
        c = js.get("currentSeason")
        if c:
            return int(c)
    now = datetime.now(timezone.utc)
    start = now.year if now.month >= 8 else now.year - 1
    return int("%d%d" % (start, start + 1))


def walk(code, need=None, want=None):
    """Матчи команды от свежих к старым, по сезонам вглубь.

    need — сколько подходящих матчей достаточно (None = пока API отдаёт).
    want(g) — фильтр, по которому считается достаточность.
    Возвращает список по возрастанию даты."""
    cached = _walked.get(code)
    if cached is not None and (need is None or cached[1] is None):
        return cached[0]

    s = current_season(code)
    out, empties = [], 0
    for i in range(MAX_SEASONS + 1):
        sid = s - 10001 * i
        games = season_games(code, "now") if i == 0 else season_games(code, str(sid))
        if not games:
            empties += 1
            if empties >= EMPTY_STOP and i > 0:
                break
            continue
        empties = 0
        out = games + out
        if need is not None and want is not None:
            if sum(1 for g in out if want(g)) >= need:
                break
    _walked[code] = (out, need)
    return out


def total_of(g):
    h = (g.get("homeTeam") or {}).get("score")
    a = (g.get("awayTeam") or {}).get("score")
    if h is None or a is None:
        return None
    return h + a


def marks(games, line):
    """+ тотал выше линии, - ниже. Строка группами по пять."""
    syms = []
    for g in games:
        t = total_of(g)
        if t is None:
            continue
        syms.append("+" if t > line else "-")
    grouped = " ".join(["".join(syms[i:i + 5]) for i in range(0, len(syms), 5)])
    return grouped, syms


def row_home(code, line):
    at_home = lambda g: abbrev(g.get("homeTeam", {})) == code
    g = [x for x in walk(code) if at_home(x)]
    return marks(g[-DEPTH:], line)


def row_away(code, line):
    at_away = lambda g: abbrev(g.get("awayTeam", {})) == code
    g = [x for x in walk(code, need=DEPTH, want=at_away) if at_away(x)]
    return marks(g[-DEPTH:], line)


def row_h2h(home_code, away_code, line):
    g = [x for x in walk(home_code)
         if abbrev(x.get("homeTeam", {})) == home_code
         and abbrev(x.get("awayTeam", {})) == away_code]
    return marks(g[-H2H_DEPTH:], line)


def main():
    now = datetime.now(timezone.utc)
    lines = {}
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
    for eid, e in ev.items():
        if not e["books"]:
            continue
        line, bs = max(e["books"].items(), key=lambda kv: (len(kv[1]), -kv[0]))
        if len(bs) >= MIN_BOOKS:
            lines[eid] = (e["home"], e["away"], e["start"], line)

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

    head = "\U0001F3D2 NHL — метод по тоталам\n%s UTC  |  матчей: %d\nлиния = консенсус книг, только .5, якорь от %d кн\nдома/гости — %d с добором  |  очные — на площадке хозяев, вся доступная история\nрегулярка и плей-офф, без предсезонки  |  старые слева, свежие справа\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(items), MIN_BOOKS, DEPTH)
    if not items:
        tg_send(head + "\nматчей с линией .5 в окне нет")
        return

    blocks = []
    for i, (t, home, away, line) in enumerate(items, 1):
        hc, ac = code_of(home), code_of(away)
        b = ["\n%2d. %s - %s  %s   тотал %g" % (i, home, away, t.strftime("%H:%M"), line)]
        if not hc or not ac:
            miss = ", ".join(x for x, c in ((home, hc), (away, ac)) if not c)
            b.append("    код команды не определён: %s — строки не построены" % miss)
            blocks.append("\n".join(b))
            continue

        s, raw = row_home(hc, line)
        b.append("    %s дома (%d)" % (hc, len(raw)))
        b.append("      " + (s or "нет игр"))
        s, raw = row_away(ac, line)
        b.append("    %s гости (%d)" % (ac, len(raw)))
        b.append("      " + (s or "нет игр"))
        s, raw = row_h2h(hc, ac, line)
        b.append("    очные дом (%d)" % len(raw))
        b.append("      " + (s or "нет игр"))
        blocks.append("\n".join(b))

    tg_send(head + "\n".join(blocks))
    print("totals: %d game(s)" % len(items))


if __name__ == "__main__":
    main()
