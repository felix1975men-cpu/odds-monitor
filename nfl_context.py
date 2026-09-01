#!/usr/bin/env python3
"""
NFL slate context.
Источники без ключей: nflverse games.csv (расписание, линии, отдых, крыша),
ESPN injuries (травмы), Open-Meteo (погода).
Кредиты Odds API не тратятся.
"""

import csv
import io
import os
import sys
import datetime as dt
from collections import defaultdict

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "96"))
MIN_CURRENT = int(os.environ.get("MIN_CURRENT", "4"))   # с какого числа матчей верим текущему сезону
TIMEOUT = 40

GAMES_CSV = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "schedules/games.csv")
ESPN_INJ = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

KEY_POS = {"QB", "RB", "WR", "TE", "LT", "RT", "OT", "G", "C",
           "CB", "S", "DE", "DT", "LB", "EDGE", "K"}
SEVERE = {"Out", "Injured Reserve", "Doubtful", "Suspension"}

ESPN2ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}
NAMES = {v: k.split()[-1] for k, v in ESPN2ABBR.items()}

COORDS = {
    "Lumen Field": (47.595, -122.332),
    "Levi's Stadium": (37.403, -121.970),
    "Empower Field at Mile High": (39.744, -105.020),
    "Arrowhead Stadium": (39.049, -94.484),
    "GEHA Field at Arrowhead Stadium": (39.049, -94.484),
    "Highmark Stadium": (42.774, -78.787),
    "Lambeau Field": (44.501, -88.062),
    "Soldier Field": (41.862, -87.617),
    "Gillette Stadium": (42.091, -71.264),
    "MetLife Stadium": (40.814, -74.074),
    "Lincoln Financial Field": (39.901, -75.168),
    "Acrisure Stadium": (40.447, -80.016),
    "Heinz Field": (40.447, -80.016),
    "M&T Bank Stadium": (39.278, -76.623),
    "Cleveland Browns Stadium": (41.506, -81.700),
    "Huntington Bank Field": (41.506, -81.700),
    "FirstEnergy Stadium": (41.506, -81.700),
    "Paycor Stadium": (39.095, -84.516),
    "Paul Brown Stadium": (39.095, -84.516),
    "Nissan Stadium": (36.166, -86.771),
    "EverBank Stadium": (30.324, -81.637),
    "TIAA Bank Field": (30.324, -81.637),
    "Hard Rock Stadium": (25.958, -80.239),
    "Raymond James Stadium": (27.976, -82.503),
    "Bank of America Stadium": (35.226, -80.853),
    "FedExField": (38.908, -76.864),
    "Northwest Stadium": (38.908, -76.864),
    "Commanders Field": (38.908, -76.864),
    "Ford Field": (42.340, -83.046),
    "U.S. Bank Stadium": (44.974, -93.258),
    "Caesars Superdome": (29.951, -90.081),
    "Mercedes-Benz Superdome": (29.951, -90.081),
    "Mercedes-Benz Stadium": (33.755, -84.401),
    "NRG Stadium": (29.685, -95.411),
    "Reliant Stadium": (29.685, -95.411),
    "AT&T Stadium": (32.748, -97.093),
    "State Farm Stadium": (33.528, -112.263),
    "Lucas Oil Stadium": (39.760, -86.164),
    "Allegiant Stadium": (36.091, -115.184),
    "SoFi Stadium": (33.953, -118.339),
    "Tottenham Hotspur Stadium": (51.604, -0.066),
    "Wembley Stadium": (51.556, -0.280),
    "Deutsche Bank Park": (50.068, 8.645),
    "Allianz Arena": (48.219, 11.625),
    "Estadio Azteca": (19.303, -99.150),
    "Melbourne Cricket Ground": (-37.820, 144.983),
    "Corinthians Arena": (-23.545, -46.474),
    "Neo Quimica Arena": (-23.545, -46.474),
}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_games():
    r = requests.get(GAMES_CSV, timeout=TIMEOUT)
    if r.status_code != 200:
        print("games.csv HTTP", r.status_code)
        return []
    return list(csv.DictReader(io.StringIO(r.text)))


def fetch_injuries():
    out = {}
    for headers in ({}, {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}):
        try:
            r = requests.get(ESPN_INJ, timeout=TIMEOUT, headers=headers)
        except Exception as e:
            print("injuries:", e)
            return out
        if r.status_code == 200:
            data = r.json()
            break
        if r.status_code != 403:
            print("injuries HTTP", r.status_code)
            return out
    else:
        print("injuries 403")
        return out

    for grp in data.get("injuries", []):
        ab = ESPN2ABBR.get(grp.get("displayName", ""))
        if not ab:
            continue
        rows = []
        for it in grp.get("injuries", []):
            status = (it.get("status") or "").strip()
            if status not in SEVERE:
                continue
            ath = it.get("athlete") or {}
            pos = ((ath.get("position") or {}).get("abbreviation") or "").upper()
            if pos not in KEY_POS:
                continue
            rows.append((ath.get("shortName") or ath.get("displayName") or "?",
                         pos, status))
        if rows:
            rank = {"Out": 0, "Injured Reserve": 1, "Suspension": 2, "Doubtful": 3}
            rows.sort(key=lambda r: (0 if r[1] == "QB" else 1, rank.get(r[2], 9)))
            out[ab] = rows
    return out


def team_form(games, season):
    """
    Только регулярный сезон — плей-офф выбрасывается, он искажает выборку
    (финалисты играют против элиты, аутсайдеры доигрывают вторым составом).
    Текущий сезон используется с MIN_CURRENT матчей, иначе берётся прошлый.
    """
    cur, prev = defaultdict(list), defaultdict(list)
    for g in games:
        if g.get("game_type") != "REG":
            continue
        hs, as_ = num(g.get("home_score")), num(g.get("away_score"))
        s = num(g.get("season"))
        if hs is None or as_ is None or s is None:
            continue
        bucket = cur if s == season else (prev if s == season - 1 else None)
        if bucket is None:
            continue
        bucket[g.get("home_team")].append((hs, as_))
        bucket[g.get("away_team")].append((as_, hs))

    out = {}
    for ab in set(list(cur) + list(prev)):
        rows, stale = cur.get(ab, []), False
        if len(rows) < MIN_CURRENT:
            rows, stale = prev.get(ab, []), True
        if not rows:
            continue
        out[ab] = {
            "pf": sum(r[0] for r in rows) / len(rows),
            "pa": sum(r[1] for r in rows) / len(rows),
            "n": len(rows),
            "stale": stale,
        }
    return out


def weather(lat, lon, when):
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=TIMEOUT, params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_direction_10m",
            "forecast_days": 16, "timezone": "UTC",
            "wind_speed_unit": "ms", "temperature_unit": "celsius"})
        if r.status_code != 200:
            return None
        h = r.json().get("hourly", {})
    except Exception:
        return None
    times = h.get("time") or []
    target = when.strftime("%Y-%m-%dT%H:00")
    if target not in times:
        return None
    i = times.index(target)

    def at(k):
        a = h.get(k) or []
        return a[i] if i < len(a) else None

    deg = at("wind_direction_10m")
    dirs = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
    return {"t": at("temperature_2m"), "rain": at("precipitation_probability"),
            "wind": at("wind_speed_10m"),
            "dir": dirs[int((deg + 22.5) % 360 // 45)] if deg is not None else "?"}


def kickoff(g):
    day, tm = g.get("gameday", ""), g.get("gametime", "")
    if not day or not tm:
        return None
    try:
        local = dt.datetime.strptime(f"{day} {tm}", "%Y-%m-%d %H:%M")
        offset = 4 if 3 <= local.month <= 10 else 5
        return local.replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=offset)
    except Exception:
        return None


def build():
    games = fetch_games()
    if not games:
        return None

    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(hours=WINDOW_HOURS)

    upcoming = []
    for g in games:
        if num(g.get("home_score")) is not None:
            continue
        ko = kickoff(g)
        if ko and now <= ko <= horizon:
            upcoming.append((ko, g))
    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[0])

    season = max(num(g.get("season")) or 0 for _, g in upcoming)
    form = team_form(games, season)
    inj = fetch_injuries()

    head = (f"📋 <b>NFL — контекст слейта</b>  "
            f"<i>{dt.datetime.utcnow():%d.%m %H:%M} UTC · {len(upcoming)} матчей</i>")
    blocks = []

    for ko, g in upcoming:
        ha, aa = g.get("home_team"), g.get("away_team")
        hn, an = NAMES.get(ha, ha), NAMES.get(aa, aa)

        L = [f"<b>{an} @ {hn}</b>  <i>нед.{g.get('week','?')} · {ko:%d.%m %H:%M} UTC</i>"]

        sl, tl = num(g.get("spread_line")), num(g.get("total_line"))
        mk = []
        if sl is not None:
            fav, pts = (hn, sl) if sl > 0 else (an, -sl)
            mk.append(f"{fav} −{abs(pts):g}" if pts else "ровно")
        if tl is not None:
            mk.append(f"тотал {tl:g}")
        if g.get("home_moneyline"):
            mk.append(f"ML {g.get('away_moneyline')}/{g.get('home_moneyline')}")
        if mk:
            L.append("   линия: " + " · ".join(mk))

        hf, af = form.get(ha), form.get(aa)
        for tag, f in ((an, af), (hn, hf)):
            if f:
                src = "прошлый сезон" if f["stale"] else "текущий сезон"
                L.append(f"   {tag}: забив {f['pf']:.1f} · проп {f['pa']:.1f} "
                         f"<i>({f['n']} матчей, {src})</i>")

        if hf and af:
            proj = (hf["pf"] + af["pa"]) / 2 + (af["pf"] + hf["pa"]) / 2
            edge = ((hf["pf"] + af["pa"]) / 2) - ((af["pf"] + hf["pa"]) / 2)
            if hf["stale"] or af["stale"]:
                L.append(f"   ⌀ прошлый сезон, для справки: тотал {proj:.1f} · "
                         f"{hn} {edge:+.1f} дома")
                L.append("   <i>состав и штаб сменились — с линией не сравниваю</i>")
            else:
                tail = ""
                if tl is not None:
                    d = proj - tl
                    tail = f"  <b>{'выше' if d > 0 else 'ниже'} линии на {abs(d):.1f}</b>"
                L.append(f"   ⌀ по форме: тотал {proj:.1f}{tail}")
                L.append(f"   ⌀ по форме: {hn} {edge:+.1f} дома")

        hr, ar = num(g.get("home_rest")), num(g.get("away_rest"))
        if hr is not None and ar is not None:
            def mark(d):
                return " ⚠️" if d <= 4 else (" 💤" if d >= 10 else "")
            L.append(f"   отдых: {an} {ar:g}д{mark(ar)} · {hn} {hr:g}д{mark(hr)}")

        for tag, ab in ((an, aa), (hn, ha)):
            rows = inj.get(ab) or []
            if not rows:
                continue
            qb = any(r[1] == "QB" for r in rows)
            top = ", ".join(f"{n} {p}" for n, p, _ in rows[:4])
            more = f" +{len(rows) - 4}" if len(rows) > 4 else ""
            L.append(f"   травмы {tag}{' 🚨QB' if qb else ''}: {top}{more}")

        roof = (g.get("roof") or "").lower()
        stadium = g.get("stadium") or ""
        if roof in ("dome", "closed"):
            L.append(f"   {stadium}: крытый")
        else:
            c = COORDS.get(stadium)
            if not c:
                L.append(f"   {stadium}: нет координат")
            else:
                w = weather(c[0], c[1], ko)
                if not w or w.get("wind") is None:
                    L.append(f"   {stadium}: прогноза пока нет")
                else:
                    bits = []
                    if w.get("t") is not None:
                        bits.append(f"{w['t']:.0f}°C")
                    bits.append(f"ветер {w['wind']:.0f} м/с {w['dir']}"
                                + (" ⚠️" if w["wind"] >= 8 else ""))
                    if w.get("rain") is not None:
                        bits.append(f"дождь {w['rain']}%")
                    label = {"outdoors": " (открытый)", "open": " (открытый)",
                             "retractable": " (раздвижная)"}.get(roof, "")
                    L.append(f"   {stadium}{label}: " + " · ".join(bits))

        if g.get("div_game") == "1":
            L.append("   дивизионный матч")

        blocks.append("\n".join(L))

    return head + "\n\n" + "\n\n".join(blocks)


def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram не настроен\n")
        print(text)
        return
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) > 3600:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    for p in parts:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          data={"chat_id": TG_CHAT, "text": p, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=30)
        print("TG:", r.status_code, r.text[:160])
        if r.status_code != 200:
            sys.exit(1)


def main():
    msg = build()
    if not msg:
        print("Матчей в окне нет")
        return
    tg_send(msg)


if __name__ == "__main__":
    main()
