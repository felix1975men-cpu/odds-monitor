#!/usr/bin/env python3
"""
NFL slate context — бесплатные источники без ключей.
ESPN (расписание, форма, статистика, травмы) + Open-Meteo (погода).
Не тратит кредиты Odds API.
"""

import os
import sys
import datetime as dt

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ESPN_WEB = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl"
WINDOW_HOURS = 96
TIMEOUT = 25

KEY_POS = {"QB", "RB", "WR", "TE", "LT", "OT", "CB", "EDGE", "DE", "LB", "S", "K"}

VENUES = {
    "Ford Field": (42.340, -83.046, "dome"),
    "U.S. Bank Stadium": (44.974, -93.258, "dome"),
    "Caesars Superdome": (29.951, -90.081, "dome"),
    "Mercedes-Benz Stadium": (33.755, -84.401, "retractable"),
    "NRG Stadium": (29.685, -95.411, "retractable"),
    "AT&T Stadium": (32.748, -97.093, "retractable"),
    "State Farm Stadium": (33.528, -112.263, "retractable"),
    "Lucas Oil Stadium": (39.760, -86.164, "retractable"),
    "Allegiant Stadium": (36.091, -115.184, "dome"),
    "SoFi Stadium": (33.953, -118.339, "dome"),
    "Lumen Field": (47.595, -122.332, "open"),
    "Arrowhead Stadium": (39.049, -94.484, "open"),
    "GEHA Field at Arrowhead Stadium": (39.049, -94.484, "open"),
    "Highmark Stadium": (42.774, -78.787, "open"),
    "Lambeau Field": (44.501, -88.062, "open"),
    "Soldier Field": (41.862, -87.617, "open"),
    "Gillette Stadium": (42.091, -71.264, "open"),
    "MetLife Stadium": (40.814, -74.074, "open"),
    "Lincoln Financial Field": (39.901, -75.168, "open"),
    "Acrisure Stadium": (40.447, -80.016, "open"),
    "M&T Bank Stadium": (39.278, -76.623, "open"),
    "Cleveland Browns Stadium": (41.506, -81.700, "open"),
    "Huntington Bank Field": (41.506, -81.700, "open"),
    "Paycor Stadium": (39.095, -84.516, "open"),
    "Nissan Stadium": (36.166, -86.771, "open"),
    "EverBank Stadium": (30.324, -81.637, "open"),
    "Hard Rock Stadium": (25.958, -80.239, "open"),
    "Raymond James Stadium": (27.976, -82.503, "open"),
    "Bank of America Stadium": (35.226, -80.853, "open"),
    "FedExField": (38.908, -76.864, "open"),
    "Northwest Stadium": (38.908, -76.864, "open"),
    "Empower Field at Mile High": (39.744, -105.020, "open"),
    "Levi's Stadium": (37.403, -121.970, "open"),
}


def get(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            print(f"HTTP {r.status_code} {url}")
            return None
        return r.json()
    except Exception as e:
        print(f"fetch failed {url}: {e}")
        return None


def scoreboard_events():
    now = dt.datetime.now(dt.timezone.utc)
    seen, events = set(), []
    for off in range(0, WINDOW_HOURS // 24 + 2):
        day = (now + dt.timedelta(days=off)).strftime("%Y%m%d")
        data = get(f"{ESPN}/scoreboard", {"dates": day})
        if not data:
            continue
        for ev in data.get("events", []):
            if ev.get("id") in seen:
                continue
            try:
                start = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            except Exception:
                continue
            if not (now <= start <= now + dt.timedelta(hours=WINDOW_HOURS)):
                continue
            seen.add(ev["id"])
            events.append((start, ev))
    return sorted(events, key=lambda x: x[0])


def team_stats():
    out = {}
    data = get(f"{ESPN_WEB}/statistics/byteam", {"region": "us", "lang": "en"})
    if not data:
        return out
    for t in data.get("teams", []):
        team = t.get("team", {}) or {}
        ab = team.get("abbreviation")
        if not ab:
            continue
        vals = {}
        for cat in t.get("categories", []):
            for st in cat.get("stats", []):
                name = st.get("name") or ""
                v = st.get("value")
                if v is not None:
                    vals[name] = v
        out[ab] = {
            "ppg": vals.get("avgPointsPerGame") or vals.get("pointsPerGame"),
            "papg": vals.get("avgPointsAgainstPerGame") or vals.get("pointsAgainstPerGame"),
            "ypp": vals.get("yardsPerPlay") or vals.get("netYardsPerPlay"),
            "plays": vals.get("totalOffensivePlays") or vals.get("offensivePlays"),
            "to_diff": vals.get("turnOverDifferential") or vals.get("turnoverDifferential"),
        }
    return out


def injuries():
    out = {}
    data = get(f"{ESPN}/injuries")
    if not data:
        return out
    for grp in data.get("injuries", []):
        team = grp.get("team", {}) or {}
        ab = team.get("abbreviation") or grp.get("displayName")
        if not ab:
            continue
        rows = []
        for inj in grp.get("injuries", []):
            ath = inj.get("athlete", {}) or {}
            pos = ((ath.get("position") or {}).get("abbreviation") or "").upper()
            status = (inj.get("status") or "").strip()
            if pos not in KEY_POS:
                continue
            if status.lower() in ("active", ""):
                continue
            rows.append((ath.get("shortName") or ath.get("displayName") or "?", pos, status))
        if rows:
            order = {"Out": 0, "Doubtful": 1, "Questionable": 2}
            rows.sort(key=lambda r: (order.get(r[2], 3), r[1]))
            out[ab] = rows
    return out


def weather(lat, lon, when):
    data = get("https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_direction_10m",
        "forecast_days": 7, "timezone": "UTC",
        "wind_speed_unit": "ms", "temperature_unit": "celsius",
    })
    if not data:
        return None
    h = data.get("hourly", {})
    times = h.get("time", [])
    if not times:
        return None
    target = when.strftime("%Y-%m-%dT%H:00")
    idx = times.index(target) if target in times else None
    if idx is None:
        return None

    def at(key):
        arr = h.get(key) or []
        return arr[idx] if idx < len(arr) else None

    deg = at("wind_direction_10m")
    dirs = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
    compass = dirs[int((deg + 22.5) % 360 // 45)] if deg is not None else "?"
    return {
        "t": at("temperature_2m"),
        "rain": at("precipitation_probability"),
        "wind": at("wind_speed_10m"),
        "dir": compass,
    }


def rest_days(ev_start, team_id, played):
    last = played.get(team_id)
    if not last:
        return None
    return (ev_start.date() - last.date()).days


def previous_games():
    out = {}
    now = dt.datetime.now(dt.timezone.utc)
    for back in range(1, 15):
        day = (now - dt.timedelta(days=back)).strftime("%Y%m%d")
        data = get(f"{ESPN}/scoreboard", {"dates": day})
        if not data:
            continue
        for ev in data.get("events", []):
            st = ((ev.get("status") or {}).get("type") or {}).get("completed")
            if not st:
                continue
            try:
                d = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            except Exception:
                continue
            for c in ev.get("competitions", [{}])[0].get("competitors", []):
                tid = (c.get("team") or {}).get("id")
                if tid and tid not in out:
                    out[tid] = d
    return out


def build():
    events = scoreboard_events()
    if not events:
        return None

    stats = team_stats()
    inj = injuries()
    played = previous_games()

    blocks = []
    for start, ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        ht, at_ = home.get("team", {}) or {}, away.get("team", {}) or {}
        ha, aa = ht.get("abbreviation", "?"), at_.get("abbreviation", "?")
        hn = ht.get("shortDisplayName") or ha
        an = at_.get("shortDisplayName") or aa

        def rec(c):
            for r in c.get("records") or []:
                if r.get("type") in ("total", "overall") or r.get("name") == "overall":
                    return r.get("summary", "")
            recs = c.get("records") or []
            return recs[0].get("summary", "") if recs else ""

        lines = [f"<b>{an} ({rec(away)}) @ {hn} ({rec(home)})</b>",
                 f"<i>{start:%d.%m %H:%M} UTC</i>"]

        for tag, ab in ((an, aa), (hn, ha)):
            s = stats.get(ab) or {}
            bits = []
            if s.get("ppg") is not None:
                bits.append(f"забив {s['ppg']:.1f}")
            if s.get("papg") is not None:
                bits.append(f"проп {s['papg']:.1f}")
            if s.get("ypp") is not None:
                bits.append(f"{s['ypp']:.1f} я/розыгр")
            if s.get("to_diff") is not None:
                bits.append(f"TO {int(s['to_diff']):+d}")
            if bits:
                lines.append(f"   {tag}: " + " · ".join(bits))

        hs, as_ = stats.get(ha) or {}, stats.get(aa) or {}
        if all(x is not None for x in (hs.get("ppg"), hs.get("papg"),
                                       as_.get("ppg"), as_.get("papg"))):
            proj = (hs["ppg"] + as_["papg"]) / 2 + (as_["ppg"] + hs["papg"]) / 2
            lines.append(f"   ⌀ ожидаемый тотал по форме: <b>{proj:.1f}</b>")

        rd = []
        for tag, c in ((an, away), (hn, home)):
            d = rest_days(start, (c.get("team") or {}).get("id"), played)
            if d is not None:
                mark = " ⚠️" if d <= 4 else (" 💤" if d >= 10 else "")
                rd.append(f"{tag} {d}д{mark}")
        if rd:
            lines.append("   отдых: " + " · ".join(rd))

        for tag, ab in ((an, aa), (hn, ha)):
            rows = inj.get(ab) or []
            if not rows:
                continue
            out_n = sum(1 for r in rows if r[2] == "Out")
            top = ", ".join(f"{n} {p} ({s[:1]})" for n, p, s in rows[:4])
            more = f" +{len(rows) - 4}" if len(rows) > 4 else ""
            flag = " ⚠️" if out_n else ""
            lines.append(f"   травмы {tag}{flag}: {top}{more}")

        venue = ((comp.get("venue") or {}).get("fullName")) or ""
        vinfo = VENUES.get(venue)
        if vinfo:
            lat, lon, roof = vinfo
            if roof == "dome":
                lines.append("   погода: крытый стадион")
            else:
                w = weather(lat, lon, start)
                if w and w.get("wind") is not None:
                    wind_flag = " ⚠️" if w["wind"] >= 8 else ""
                    parts = [f"{w['t']:.0f}°C" if w.get("t") is not None else "",
                             f"ветер {w['wind']:.0f} м/с {w['dir']}{wind_flag}",
                             f"дождь {w['rain']}%" if w.get("rain") is not None else ""]
                    tail = " · ".join(x for x in parts if x)
                    prefix = "погода (раздвижная крыша)" if roof == "retractable" else "погода"
                    lines.append(f"   {prefix}: {tail}")
        elif venue:
            lines.append(f"   стадион: {venue} (нет в таблице)")

        blocks.append("\n".join(lines))

    if not blocks:
        return None
    head = f"📋 <b>NFL — контекст слейта</b>  <i>{dt.datetime.utcnow():%d.%m %H:%M} UTC</i>"
    return head + "\n\n" + "\n\n".join(blocks)


def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram не настроен")
        print(text)
        return
    for i in range(0, len(text), 3800):
        chunk = text[i:i + 3800]
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": chunk,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30)
        print("TG:", r.status_code, r.text[:200])
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
