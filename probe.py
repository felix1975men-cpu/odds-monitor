#!/usr/bin/env python3
"""Что лежит в nflverse games.csv и как устроен элемент травмы."""
import csv
import io
import json
import requests

T = 40


def get_json(url, params=None):
    for h in ({}, {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}):
        r = requests.get(url, params=params, timeout=T, headers=h)
        if r.status_code == 200:
            return r.json()
        if r.status_code != 403:
            print("  HTTP", r.status_code)
            return None
    print("  403")
    return None


print("=" * 55)
print("A) nflverse games.csv")
r = requests.get(
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv",
    timeout=T)
print("  HTTP", r.status_code, len(r.content), "байт")
if r.status_code == 200:
    rows = list(csv.DictReader(io.StringIO(r.text)))
    print("  строк:", len(rows))
    print("  КОЛОНКИ:", list(rows[0].keys()))

    s26 = [x for x in rows if x.get("season") == "2026"]
    s25 = [x for x in rows if x.get("season") == "2025"]
    print("  строк 2026:", len(s26), "| 2025:", len(s25))

    if s26:
        print("\n  ПЕРВАЯ СТРОКА 2026:")
        for k, v in s26[0].items():
            print(f"    {k} = {v!r}")

    if s25:
        done = [x for x in s25 if x.get("home_score") not in ("", "NA", None)]
        print("\n  сыгранных 2025:", len(done))
        if done:
            print("  ПОСЛЕДНЯЯ СЫГРАННАЯ 2025:")
            for k, v in done[-1].items():
                print(f"    {k} = {v!r}")

print()
print("=" * 55)
print("B) структура одной травмы")
d = get_json("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries")
if d:
    grp = (d.get("injuries") or [])[0]
    print("  displayName:", grp.get("displayName"))
    items = grp.get("injuries") or []
    print("  травм в группе:", len(items))
    if items:
        it = items[0]
        print("  ключи травмы:", list(it.keys()))
        print("  status:", it.get("status"))
        ath = it.get("athlete") or {}
        print("  ключи athlete:", list(ath.keys())[:20])
        print("  displayName:", ath.get("displayName"),
              "| shortName:", ath.get("shortName"))
        print("  position:", json.dumps(ath.get("position") or {}, ensure_ascii=False)[:200])
    print("\n  все displayName групп:")
    print("   ", [g.get("displayName") for g in (d.get("injuries") or [])])
