#!/usr/bin/env python3
"""Разовая проверка: какие источники доступны из GitHub Actions."""
import requests

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

TESTS = [
    ("1 site.api scoreboard (browser UA)",
     "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
     {"User-Agent": UA_BROWSER, "Accept": "application/json",
      "Referer": "https://www.espn.com/"}),

    ("2 site.api scoreboard (без UA)",
     "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
     {}),

    ("3 cdn.espn core scoreboard",
     "https://cdn.espn.com/core/nfl/scoreboard?xhr=1&limit=50",
     {"User-Agent": UA_BROWSER}),

    ("4 sports.core.api events",
     "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events?limit=50",
     {"User-Agent": UA_BROWSER}),

    ("5 site.web.api byteam",
     "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/statistics/byteam",
     {"User-Agent": UA_BROWSER}),

    ("6 site.api injuries",
     "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
     {"User-Agent": UA_BROWSER}),

    ("7 nflverse games.csv",
     "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv",
     {}),

    ("8 open-meteo",
     "https://api.open-meteo.com/v1/forecast?latitude=40&longitude=-75&hourly=temperature_2m",
     {}),
]

for name, url, headers in TESTS:
    try:
        r = requests.get(url, headers=headers, timeout=25)
        size = len(r.content)
        head = r.text[:70].replace("\n", " ")
        print(f"{name}\n    {r.status_code}  {size} байт  {head}\n")
    except Exception as e:
        print(f"{name}\n    ОШИБКА: {e}\n")
