#!/usr/bin/env python3
"""
Slate context — everything that shapes a baseball game EXCEPT the price.

Sent after schedule.py and before the digest, so an opinion can be formed
on facts rather than on the line.

Sources, all free and keyless:
  MLB Stats API (statsapi.mlb.com) — probable starters and their season
      line, team records, last ten, streak, head-to-head this season,
      venue and roof type
  Open-Meteo (api.open-meteo.com) — temperature, wind and rain chance at
      first pitch, skipped for closed roofs

Park factors are a STATIC table below. They drift year to year and are
not fetched; treat them as a rough ordering, not a measurement, and
update the table when better numbers are at hand.

MLB only. The other leagues need different sources entirely.

Env required: TG_TOKEN, TG_CHAT_ID
"""

import os
import json
import urllib.request
import urllib.parse
import time
import urllib.error
from datetime import datetime, timezone, timedelta

STATS = "https://statsapi.mlb.com/api/v1"
METEO = "https://api.open-meteo.com/v1/forecast"

WINDOW_HOURS = 30
MSG_LIMIT = 3500

# Rough multi-year run factors, 100 = neutral. Static on purpose — see the
# module docstring. Keyed by venue NAME as the Stats API reports it; a name
# that is missing simply prints without a factor rather than guessing.
PARK = {
    "Coors Field": 112,
    "Fenway Park": 104,
    "Great American Ball Park": 105,
    "Chase Field": 103,
    "Yankee Stadium": 103,
    "Citizens Bank Park": 103,
    "Guaranteed Rate Field": 102,
    "Rate Field": 102,
    "Angel Stadium": 102,
    "Rogers Centre": 102,
    "Truist Park": 101,
    "Oriole Park at Camden Yards": 101,
    "Wrigley Field": 101,
    "American Family Field": 101,
    "Minute Maid Park": 101,
    "Daikin Park": 101,
    "Globe Life Field": 100,
    "Target Field": 100,
    "Kauffman Stadium": 100,
    "Nationals Park": 100,
    "Comerica Park": 99,
    "Progressive Field": 99,
    "Tropicana Field": 99,
    "Dodger Stadium": 98,
    "Busch Stadium": 97,
    "PNC Park": 97,
    "loanDepot park": 96,
    "Citi Field": 96,
    "Sutter Health Park": 104,
    "George M. Steinbrenner Field": 104,
    "T-Mobile Park": 94,
    "Oracle Park": 94,
    "Petco Park": 93,
}


def park_factor(name):
    """Match on substring — venues pick up sponsor prefixes mid-season."""
    if not name:
        return None
    if name in PARK:
        return PARK[name]
    for k, v in PARK.items():
        if k.lower() in name.lower() or name.lower() in k.lower():
            return v
    return None


_VENUE_COORDS = {}


def venue_coords(v):
    """Coordinates from the hydrated game, else fetched from the venue itself."""
    coords = ((v.get("location") or {}).get("defaultCoordinates")) or {}
    lat, lon = coords.get("latitude"), coords.get("longitude")
    if lat is not None and lon is not None:
        return lat, lon
    vid = v.get("id")
    if vid is None:
        return None, None
    if vid in _VENUE_COORDS:
        return _VENUE_COORDS[vid]
    data = jget("%s/venues/%d?hydrate=location" % (STATS, vid))
    got = (None, None)
    for ven in (data or {}).get("venues", []):
        c = (ven.get("location") or {}).get("defaultCoordinates") or {}
        if c.get("latitude") is not None:
            got = (c["latitude"], c["longitude"])
    _VENUE_COORDS[vid] = got
    return got


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


# ---------------------------------------------------------------- schedule

def slate(now, horizon):
    """Games inside the window, with probable starters and venue hydrated."""
    games = []
    seen = set()
    for d in (now, now + timedelta(days=1), now + timedelta(days=2)):
        url = ("%s/schedule?sportId=1&date=%s"
               "&hydrate=probablePitcher,team,venue(fieldInfo,location)"
               % (STATS, d.strftime("%m/%d/%Y")))
        data = jget(url)
        if not data:
            continue
        for day in data.get("dates", []):
            for g in day.get("games", []):
                gid = g.get("gamePk")
                if gid in seen:
                    continue
                gd = g.get("gameDate")
                if not gd:
                    continue
                try:
                    t = parse_iso(gd)
                except ValueError:
                    continue
                if not (now <= t <= horizon):
                    continue
                seen.add(gid)
                games.append(g)
    return sorted(games, key=lambda g: g["gameDate"])


# ---------------------------------------------------------------- pitchers

def pitcher_line(pid, season):
    """Season pitching line for one probable starter."""
    if not pid:
        return None
    url = ("%s/people/%d/stats?stats=season&group=pitching&season=%d"
           % (STATS, pid, season))
    data = jget(url)
    if not data:
        return None
    for st in data.get("stats", []):
        for sp in st.get("splits", []):
            s = sp.get("stat", {})
            if not s:
                continue
            return {
                "era": s.get("era"),
                "whip": s.get("whip"),
                "ip": s.get("inningsPitched"),
                "so": s.get("strikeOuts"),
                "bb": s.get("baseOnBalls"),
                "hr": s.get("homeRuns"),
                "gs": s.get("gamesStarted"),
            }
    return None


def ip_to_float(ip):
    """Baseball innings are thirds: 140.1 is 140 + 1/3, not 140.1."""
    try:
        whole, _, frac = str(ip).partition(".")
        return float(whole) + {"": 0.0, "0": 0.0, "1": 1.0 / 3, "2": 2.0 / 3}.get(frac, 0.0)
    except (TypeError, ValueError):
        return None


def fmt_pitcher(p, line):
    if not p:
        return "не объявлен"
    name = p.get("fullName", "?")
    hand = (p.get("pitchHand") or {}).get("code")
    tag = " (%s)" % hand if hand else ""
    if not line:
        return "%s%s — нет статистики" % (name, tag)
    bits = []
    if line.get("era") is not None:
        bits.append("ERA %s" % line["era"])
    if line.get("whip") is not None:
        bits.append("WHIP %s" % line["whip"])
    ip = line.get("ip")
    ipf = ip_to_float(ip)
    so, bb, hr = line.get("so"), line.get("bb"), line.get("hr")
    if ipf:
        if so is not None:
            bits.append("K/9 %.1f" % (9.0 * float(so) / ipf))
        if bb is not None:
            bits.append("BB/9 %.1f" % (9.0 * float(bb) / ipf))
        if hr is not None:
            bits.append("HR/9 %.1f" % (9.0 * float(hr) / ipf))
    if ip:
        bits.append("%s IP" % ip)
    return "%s%s — %s" % (name, tag, ", ".join(bits) if bits else "нет статистики")


# ------------------------------------------------------------------- form

def standings_form(season):
    """{team_id: record, last ten, streak, runs, home/away splits}."""
    out = {}
    url = ("%s/standings?leagueId=103,104&season=%d&standingsTypes=regularSeason"
           % (STATS, season))
    data = jget(url)
    if not data:
        return out
    for rec in data.get("records", []):
        for tr in rec.get("teamRecords", []):
            tid = (tr.get("team") or {}).get("id")
            if tid is None:
                continue
            splits = {}
            for sp in ((tr.get("records") or {}).get("splitRecords") or []):
                splits[sp.get("type")] = "%s-%s" % (sp.get("wins"), sp.get("losses"))
            out[tid] = {
                "wl": "%s-%s" % (tr.get("wins"), tr.get("losses")),
                "last10": splits.get("lastTen", ""),
                "home": splits.get("home", ""),
                "away": splits.get("away", ""),
                "streak": ((tr.get("streak") or {}).get("streakCode") or ""),
                "rs": tr.get("runsScored"),
                "ra": tr.get("runsAllowed"),
            }
    return out


_RECENT = {}


def team_recent(team_id, season, n=15):
    """Record and run differential over the last n completed games."""
    if (team_id, n) in _RECENT:
        return _RECENT[(team_id, n)]
    url = ("%s/schedule?sportId=1&teamId=%d&season=%d&startDate=%d-03-01&endDate=%s"
           % (STATS, team_id, season, season,
              datetime.now(timezone.utc).strftime("%Y-%m-%d")))
    data = jget(url)
    res = None
    if data:
        games = []
        for day in data.get("dates", []):
            for g in day.get("games", []):
                if (g.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                t = g.get("teams") or {}
                for side, other in (("home", "away"), ("away", "home")):
                    s = t.get(side) or {}
                    if ((s.get("team") or {}).get("id")) != team_id:
                        continue
                    o = t.get(other) or {}
                    if s.get("score") is None or o.get("score") is None:
                        continue
                    games.append((g.get("gameDate", ""), s["score"], o["score"]))
        games.sort()
        tail = games[-n:]
        if tail:
            w = sum(1 for _, f, a in tail if f > a)
            res = {"wl": "%d-%d" % (w, len(tail) - w),
                   "rs": sum(f for _, f, _ in tail),
                   "ra": sum(a for _, _, a in tail),
                   "n": len(tail)}
    _RECENT[(team_id, n)] = res
    return res


_INJ = {}


def injuries(team_id, limit=3):
    """Players actually unavailable — injured lists, suspensions, leave.

    The 40-man carries a lot of 'Reassigned to Minors', which is roster
    housekeeping rather than an absence, so it is filtered out.
    """
    if team_id in _INJ:
        return _INJ[team_id]
    data = jget("%s/teams/%d/roster?rosterType=40Man&hydrate=person"
                % (STATS, team_id))
    keep = ("injured", "il", "suspend", "restricted", "bereavement",
            "paternity", "family")
    out = []
    for e in (data or {}).get("roster", []):
        desc = ((e.get("status") or {}).get("description") or "")
        low = desc.lower()
        if not any(k in low for k in keep):
            continue
        name = (e.get("person") or {}).get("fullName", "?")
        pos = ((e.get("position") or {}).get("abbreviation") or "")
        short = desc.replace("Injured ", "IL ")
        out.append("%s %s (%s)" % (pos, name, short))
    _INJ[team_id] = out
    return out


def pitcher_recent(pid, season, n=3):
    """The starter's last n outings: date, innings, earned runs."""
    if not pid:
        return []
    data = jget("%s/people/%d/stats?stats=gameLog&group=pitching&season=%d"
                % (STATS, pid, season))
    rows = []
    for st in (data or {}).get("stats", []):
        for sp in st.get("splits", []):
            s = sp.get("stat") or {}
            if not (s.get("gamesStarted") or s.get("inningsPitched")):
                continue
            rows.append((sp.get("date", ""), s.get("inningsPitched"),
                         s.get("earnedRuns"), s.get("strikeOuts")))
    rows.sort()
    out = []
    for d, ip, er, so in rows[-n:][::-1]:
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
        except (ValueError, TypeError):
            dd = d
        out.append("%s %sIP %sER %sK" % (dd, ip, er, so))
    return out


# -------------------------------------------------------------------- h2h

def head_to_head(team_id, opp_id, season, limit=5):
    """Final scores of this season's meetings, most recent first."""
    url = ("%s/schedule?sportId=1&teamId=%d&opponentId=%d&season=%d"
           "&startDate=%d-03-01&endDate=%s&hydrate=team,linescore"
           % (STATS, team_id, opp_id, season, season,
              datetime.now(timezone.utc).strftime("%Y-%m-%d")))
    data = jget(url)
    if not data:
        return []

    def tag(side):
        t = side.get("team") or {}
        return t.get("abbreviation") or t.get("teamName") or t.get("name", "?")[:12]

    out = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            t = g.get("teams", {})
            h, a = t.get("home", {}), t.get("away", {})
            hs, as_ = h.get("score"), a.get("score")
            if hs is None or as_ is None:
                continue
            out.append("%s %d:%d %s" % (tag(a), as_, hs, tag(h)))
    return out[-limit:][::-1]


# ---------------------------------------------------------------- weather

_WX = {}


def weather_at(lat, lon, when):
    """Temperature, wind and rain chance for the hour of first pitch.

    Cached per venue and retried once — firing fifteen calls back to back
    was silently losing a few of them.
    """
    key = (round(lat, 3), round(lon, 3))
    if key not in _WX:
        url = ("%s?latitude=%.4f&longitude=%.4f&timezone=UTC&forecast_days=3"
               "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,"
               "precipitation_probability" % (METEO, lat, lon))
        data = jget(url)
        if not data:
            time.sleep(2)
            data = jget(url)
        _WX[key] = data
    data = _WX[key]
    if not data:
        return None
    h = data.get("hourly") or {}
    times = h.get("time") or []
    target = when.strftime("%Y-%m-%dT%H:00")
    if target not in times:
        return None
    i = times.index(target)

    def at(k):
        v = h.get(k) or []
        return v[i] if i < len(v) else None

    return {"t": at("temperature_2m"), "ws": at("wind_speed_10m"),
            "wd": at("wind_direction_10m"), "rain": at("precipitation_probability")}


def compass(deg):
    if deg is None:
        return "?"
    dirs = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ",
            "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]
    return dirs[int((deg / 22.5) + 0.5) % 16]


# ------------------------------------------------------------------- main

def build_block(g, season, form):
    t = g.get("teams", {})
    home_t = (t.get("home") or {}).get("team") or {}
    away_t = (t.get("away") or {}).get("team") or {}
    hid, aid = home_t.get("id"), away_t.get("id")
    start = parse_iso(g["gameDate"])

    out = ["\n%s @ %s  (%s UTC)"
           % (away_t.get("name", "?"), home_t.get("name", "?"),
              start.strftime("%d.%m %H:%M"))]

    # records, splits and recent form
    for side, team, where in (("гости", away_t, "away"), ("хозяева", home_t, "home")):
        tid = team.get("id")
        f = form.get(tid) or {}
        if f:
            out.append("  %s: %s, дома %s, в гостях %s, посл.10 %s, серия %s  RS/RA %s/%s"
                       % (side, f.get("wl", "?"), f.get("home") or "?",
                          f.get("away") or "?", f.get("last10") or "?",
                          f.get("streak") or "?", f.get("rs"), f.get("ra")))
        r15 = team_recent(tid, season, 15) if tid else None
        if r15:
            out.append("    посл.%d: %s, забито %d, пропущено %d"
                       % (r15["n"], r15["wl"], r15["rs"], r15["ra"]))

    # probable starters with their last outings
    for side, key in (("гости", "away"), ("хозяева", "home")):
        p = (t.get(key) or {}).get("probablePitcher")
        pid = (p or {}).get("id")
        line = pitcher_line(pid, season)
        out.append("  P %s: %s" % (side, fmt_pitcher(p, line)))
        last = pitcher_recent(pid, season)
        if last:
            out.append("    старты: " + " | ".join(last))

    # venue, roof, park factor
    v = g.get("venue") or {}
    roof = ((v.get("fieldInfo") or {}).get("roofType") or "").lower()
    vname = v.get("name", "?")
    pf = park_factor(vname)
    pf_txt = ("  парк %d" % pf) if pf else ""
    out.append("  Стадион: %s%s%s"
               % (vname, pf_txt, ("  крыша: %s" % roof) if roof else ""))

    # weather, only where it can matter
    lat, lon = venue_coords(v)
    if roof in ("dome", "closed", "indoor", "fixed"):
        out.append("  Погода: не важна, закрытый стадион")
    elif lat is not None and lon is not None:
        w = weather_at(lat, lon, start)
        if w:
            note = "  (крыша раздвижная — может быть закрыта)" if "retract" in roof else ""
            out.append("  Погода: %s°C, ветер %s км/ч %s, осадки %s%%%s"
                       % (w.get("t"), w.get("ws"), compass(w.get("wd")),
                          w.get("rain"), note))
        else:
            out.append("  Погода: нет данных")
    else:
        out.append("  Погода: нет координат стадиона")

    # head to head this season
    if hid and aid:
        h2h = head_to_head(hid, aid, season)
        if h2h:
            out.append("  Очные: " + " | ".join(h2h))

    # not-active players on the 40-man
    for side, team in (("гости", away_t), ("хозяева", home_t)):
        tid = team.get("id")
        if not tid:
            continue
        inj = injuries(tid)
        if inj:
            more = ("  +ещё %d" % (len(inj) - 3)) if len(inj) > 3 else ""
            out.append("  Вне строя (%s, %d): %s%s"
                       % (side, len(inj), "; ".join(inj[:3]), more))

    return "\n".join(out)


def main():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=WINDOW_HOURS)
    season = now.year

    games = slate(now, horizon)
    if not games:
        print("no games inside the %dh window" % WINDOW_HOURS)
        return

    form = standings_form(season)

    header = ("\u26BE MLB \u2014 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u0442\u0443\u0440\u0430\n"
              "%s UTC  |  \u043c\u0430\u0442\u0447\u0435\u0439: %d\n"
              "\u0431\u0435\u0437 \u043a\u043e\u044d\u0444\u0444\u0438\u0446\u0438\u0435\u043d\u0442\u043e\u0432  |  "
              "\u043f\u0430\u0440\u043a-\u0444\u0430\u043a\u0442\u043e\u0440\u044b \u2014 \u0441\u0442\u0430\u0442\u0438\u0447\u043d\u0430\u044f \u0442\u0430\u0431\u043b\u0438\u0446\u0430\n"
              % (now.strftime("%Y-%m-%d %H:%M"), len(games)))

    blocks = [build_block(g, season, form) for g in games]

    chunk, sent = header, 0
    for b in blocks:
        if len(chunk) + len(b) + 1 > MSG_LIMIT:
            tg_send(chunk)
            sent += 1
            chunk = ""
        chunk += b + "\n"
    if chunk.strip():
        tg_send(chunk)
        sent += 1

    print("context sent in %d message(s), %d games" % (sent, len(games)))


if __name__ == "__main__":
    main()
