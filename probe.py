#!/usr/bin/env python3
"""
probe.py — NBA, шаг 1: какие источники отвечают из GitHub Actions.
Каждый адрес пробуется два раза: без User-Agent и с браузерным
(ESPN отдаёт 403 браузерному, NHL API наоборот требует его).
Печатает по одной строке на адрес, затем структуру лучших ответов.
"""

import json
import urllib.request
import urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

URLS = [
    ("espn scoreboard", ESPN + "/scoreboard?dates=20260420"),
    ("espn teams", ESPN + "/teams"),
    ("espn team schedule", ESPN + "/teams/2/schedule?season=2026&seasontype=2"),
    ("espn team stats", ESPN + "/teams/2/statistics"),
    ("espn injuries", ESPN + "/injuries"),
    ("espn core stats", "https://sports.core.api.espn.com/v2/sports/basketball/"
     "leagues/nba/seasons/2026/types/2/teams/2/statistics"),
    ("espn standings", "https://site.web.api.espn.com/apis/v2/sports/"
     "basketball/nba/standings?season=2026"),
    ("nba cdn schedule", "https://cdn.nba.com/static/json/staticData/"
     "scheduleLeagueV2.json"),
    ("nba cdn today", "https://cdn.nba.com/static/json/liveData/scoreboard/"
     "todaysScoreboard_00.json"),
    ("nba stats gamelogs", "https://stats.nba.com/stats/teamgamelogs?"
     "LeagueID=00&Season=2025-26&SeasonType=Regular%20Season"),
]


def get(url, ua):
    req = urllib.request.Request(url)
    if ua:
        req.add_header("User-Agent", UA)
        req.add_header("Referer", "https://www.nba.com/")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            try:
                return r.status, len(raw), json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return r.status, len(raw), None
    except urllib.error.HTTPError as e:
        return e.code, 0, None
    except Exception as e:
        return str(e)[:60], 0, None


def keys(js):
    if isinstance(js, dict):
        return ",".join(list(js.keys())[:8])
    if isinstance(js, list):
        return "list[%d]" % len(js)
    return "-"


def dump(label, obj, limit=1800):
    print("\n=== " + label + " ===")
    print(json.dumps(obj, ensure_ascii=False, indent=1)[:limit])


def main():
    print("NBA probe шаг 1\n")
    ok = {}
    for name, url in URLS:
        row = []
        for ua in (False, True):
            st, n, js = get(url, ua)
            row.append("%s %s %dб" % ("UA" if ua else "noUA", st, n))
            if st == 200 and js is not None and name not in ok:
                ok[name] = js
        print("%-20s %s | %s" % (name, " | ".join(row),
                                 keys(ok.get(name))))

    sb = ok.get("espn scoreboard")
    if sb and sb.get("events"):
        ev = sb["events"][0]
        comp = ev["competitions"][0]
        print("\nESPN матч: %s, статус %s" % (ev.get("shortName"),
              comp.get("status", {}).get("type", {}).get("name")))
        for c in comp.get("competitors", []):
            print("  %s %s id=%s score=%s" % (c.get("homeAway"),
                  c["team"].get("displayName"), c["team"].get("id"),
                  c.get("score")))
        print("  ключи competition: " + ",".join(comp.keys()))
        print("  odds: " + json.dumps(comp.get("odds", "нет"))[:300])

    sc = ok.get("espn team schedule")
    if sc and sc.get("events"):
        e = sc["events"][0]
        print("\nESPN расписание команды: событий %d" % len(sc["events"]))
        dump("первое событие", e, 1500)

    st = ok.get("espn team stats")
    if st:
        cats = st.get("results", {}).get("stats", {}).get("categories", [])
        print("\nESPN статистика команды: категорий %d" % len(cats))
        for c in cats[:6]:
            print("  %s: %d показателей" % (c.get("name"), len(c.get("stats", []))))

    cdn = ok.get("nba cdn schedule")
    if cdn:
        ls = cdn.get("leagueSchedule", {})
        gd = ls.get("gameDates", [])
        print("\nNBA cdn: сезон %s, дат %d" % (ls.get("seasonYear"), len(gd)))
        if gd and gd[0].get("games"):
            dump("первый матч cdn", gd[0]["games"][0], 1500)

    inj = ok.get("espn injuries")
    if inj:
        g = inj.get("injuries", [])
        print("\nESPN травмы: групп %d" % len(g))
        if g:
            dump("первая группа", g[0], 900)


main()
