#!/usr/bin/env python3
"""Дамп реальной структуры ESPN: byteam, injuries, погода на 10 дней вперёд."""
import json
import datetime as dt
import requests

T = 25


def get(url, params=None):
    for h in ({}, {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}):
        try:
            r = requests.get(url, params=params, timeout=T, headers=h)
            if r.status_code == 200:
                return r.json()
            if r.status_code != 403:
                print(f"  HTTP {r.status_code}")
                return None
        except Exception as e:
            print(f"  ОШИБКА {e}")
            return None
    print("  403 обе попытки")
    return None


print("=" * 55)
print("A) byteam — сезон 2025, regular")
d = get("https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/statistics/byteam",
        {"region": "us", "lang": "en", "season": 2025, "seasontype": 2})
if d:
    print("  верхние ключи:", list(d.keys())[:12])
    teams = d.get("teams") or []
    print("  команд:", len(teams))
    if teams:
        t = teams[0]
        print("  ключи команды:", list(t.keys()))
        print("  team:", json.dumps(t.get("team", {}), ensure_ascii=False)[:160])
        for cat in (t.get("categories") or []):
            names = [s.get("name") for s in (cat.get("stats") or [])]
            print(f"  [{cat.get('name')}] {names[:14]}")

print()
print("=" * 55)
print("B) byteam — без season")
d2 = get("https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/statistics/byteam",
         {"region": "us", "lang": "en"})
if d2:
    print("  верхние ключи:", list(d2.keys())[:12])
    print("  команд:", len(d2.get("teams") or []))

print()
print("=" * 55)
print("C) injuries")
d3 = get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries")
if d3:
    print("  верхние ключи:", list(d3.keys()))
    arr = d3.get("injuries") or []
    print("  групп:", len(arr))
    if arr:
        g = arr[0]
        print("  ключи группы:", list(g.keys()))
        print("  фрагмент:", json.dumps(g, ensure_ascii=False)[:600])

print()
print("=" * 55)
print("D) погода через 10 дней, forecast_days=16")
when = dt.datetime.utcnow() + dt.timedelta(days=10)
d4 = get("https://api.open-meteo.com/v1/forecast", {
    "latitude": 47.595, "longitude": -122.332,
    "hourly": "temperature_2m,wind_speed_10m",
    "forecast_days": 16, "timezone": "UTC", "wind_speed_unit": "ms",
})
if d4:
    times = (d4.get("hourly") or {}).get("time") or []
    print("  точек:", len(times), "| первая:", times[:1], "| последняя:", times[-1:])
    tgt = when.strftime("%Y-%m-%dT%H:00")
    print("  цель", tgt, "найдена:", tgt in times)

print()
print("=" * 55)
print("E) команда 25 (SEA) — статистика напрямую")
d5 = get("https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/teams/25/statistics",
         {"region": "us", "lang": "en", "season": 2025, "seasontype": 2})
if d5:
    print("  верхние ключи:", list(d5.keys())[:12])
    res = (d5.get("results") or {})
    print("  results ключи:", list(res.keys())[:10])
    for cat in (res.get("stats") or {}).get("categories", [])[:4]:
        names = [s.get("name") for s in (cat.get("stats") or [])]
        print(f"  [{cat.get('name')}] {names[:14]}")
