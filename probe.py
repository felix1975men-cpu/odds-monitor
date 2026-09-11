#!/usr/bin/env python3
"""
probe.py — NHL API, шаг 2: структура ответа на дате, где игры были.
api-web.nhle.com ТРЕБУЕТ браузерный User-Agent (проверено: без него 403).
"""

import json
import urllib.request
import urllib.error

BASE = "https://api-web.nhle.com/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Даты прошлого сезона — ищем первую, где массив games не пустой.
CANDIDATES = ["2026-04-10", "2026-03-15", "2026-02-10",
              "2026-01-15", "2025-12-10", "2025-11-12"]


def get(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("  HTTP %s на %s" % (e.code, path))
    except Exception as e:
        print("  FAILED %s: %s" % (path, e))
    return None


def dump(label, obj, limit=2500):
    print("\n" + "=" * 50)
    print(label)
    print("=" * 50)
    print(json.dumps(obj, ensure_ascii=False, indent=1)[:limit])


def main():
    print("NHL probe шаг 2 — структура\n")

    # 1. ищем день с играми
    day, data = None, None
    for d in CANDIDATES:
        js = get("/score/%s" % d)
        n = len(js.get("games", [])) if js else 0
        print("score %s -> игр: %d" % (d, n))
        if n:
            day, data = d, js
            break
    if not data:
        print("\nни одна дата не дала игр — расширить CANDIDATES")
        return

    games = data["games"]
    g = games[0]
    print("\nвзят день %s, игр %d" % (day, len(games)))
    print("ключи матча: " + ", ".join(sorted(g.keys())))
    dump("ПЕРВЫЙ МАТЧ ИЗ /score — целиком", g)

    # 2. как помечаются ОТ и буллиты: смотрим все игры дня
    print("\n" + "=" * 50)
    print("ПОМЕТКИ ОКОНЧАНИЯ по всем играм дня")
    print("=" * 50)
    for x in games:
        out = x.get("gameOutcome") or {}
        pd = x.get("periodDescriptor") or {}
        home = (x.get("homeTeam") or {})
        away = (x.get("awayTeam") or {})
        print("  id %s | %s %s : %s %s | gameOutcome=%s | periodDescriptor=%s | state=%s" % (
            x.get("id"),
            (home.get("abbrev") or "?"), home.get("score"),
            away.get("score"), (away.get("abbrev") or "?"),
            json.dumps(out, ensure_ascii=False),
            json.dumps(pd, ensure_ascii=False),
            x.get("gameState")))

    gid = g.get("id")

    # 3. boxscore того же матча
    box = get("/gamecenter/%s/boxscore" % gid)
    if box:
        print("\nключи boxscore: " + ", ".join(sorted(box.keys())))
        dump("BOXSCORE — начало", box, 1800)

    # 4. landing — там предматчевая информация и вратари
    land = get("/gamecenter/%s/landing" % gid)
    if land:
        print("\nключи landing: " + ", ".join(sorted(land.keys())))
        for k in ("matchup", "summary", "gameOutcome", "periodDescriptor"):
            if k in land:
                dump("LANDING -> %s" % k, land[k], 1400)

    # 5. расписание: какие поля у матча до игры
    sch = get("/schedule/%s" % day)
    if sch:
        wk = sch.get("gameWeek") or []
        for d in wk:
            if d.get("games"):
                sg = d["games"][0]
                print("\nключи матча в /schedule: " + ", ".join(sorted(sg.keys())))
                dump("SCHEDULE — первый матч", sg, 2000)
                break


if __name__ == "__main__":
    main()
