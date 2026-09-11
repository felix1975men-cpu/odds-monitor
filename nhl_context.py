#!/usr/bin/env python3
"""
nhl_context.py — 08:35 UTC.
Пары ближайшего окна + контекст: форма, GF/GA, дни отдыха, бэк-ту-бэк,
длина выезда, очные на площадке хозяев, вероятные вратари.
Раздельная статистика дома/в гостях (глубина 30, добор из прошлого сезона)
и базовая проекция тотала.
Цен НЕ печатает — прогноз даётся до цены.

api-web.nhle.com, без ключа, ТРЕБУЕТ браузерный User-Agent.
Env: TG_TOKEN, TG_CHAT_ID
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api-web.nhle.com/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

WINDOW_H = 30          # окно тура в часах
FORM_N = 15            # глубина формы
SPLIT_N = 30           # глубина раздельной статистики дома / в гостях
REG_TYPE = 2           # gameType регулярного чемпионата
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

def api(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("HTTP %s on %s" % (e.code, path))
    except Exception as e:
        print("failed %s: %s" % (path, e))
    return None


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def abbrev(d):
    v = d.get("abbrev")
    if isinstance(v, dict):
        return v.get("default", "?")
    return v or "?"


# ---------- сбор ----------

def upcoming(now):
    """Матчи в ближайшем окне. /schedule/{date} отдаёт неделю — берём нужные дни."""
    seen, games = set(), []
    hi = now + timedelta(hours=WINDOW_H)
    for back in (0, 1):
        d = (now + timedelta(days=back)).strftime("%Y-%m-%d")
        js = api("/schedule/%s" % d)
        for day in (js or {}).get("gameWeek", []):
            for g in day.get("games", []):
                st = g.get("startTimeUTC")
                if not st:
                    continue
                t = parse_iso(st)
                if not (now <= t <= hi):
                    continue
                gid = str(g.get("id"))
                if gid in seen:
                    continue
                seen.add(gid)
                games.append(g)
    games.sort(key=lambda g: g["startTimeUTC"])
    return games


def standings_map():
    js = api("/standings/now")
    out = {}
    for t in (js or {}).get("standings", []):
        out[abbrev(t.get("teamAbbrev", {}) if isinstance(t.get("teamAbbrev"), dict)
                   else {"abbrev": t.get("teamAbbrev")})] = t
    return out


_sched_cache = {}


def sched_json(team, season="now"):
    key = (team, season)
    if key not in _sched_cache:
        _sched_cache[key] = api("/club-schedule-season/%s/%s" % (team, season)) or {}
    return _sched_cache[key]


def season_games(team, season="now"):
    """Все игры команды в сезоне, отсортированы по дате."""
    js = sched_json(team, season)
    return sorted(js.get("games", []), key=lambda g: g.get("gameDate", ""))


def prev_season_id(team):
    """Номер прошлого сезона: из API, иначе арифметикой, иначе по календарю."""
    js = sched_json(team, "now")
    p = js.get("previousSeason")
    if p:
        return str(p)
    c = js.get("currentSeason")
    if c:
        try:
            return str(int(c) - 10001)
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    start = now.year if now.month >= 8 else now.year - 1
    return "%d%d" % (start - 1, start)


def finished(games):
    return [g for g in games if g.get("gameState") in ("FINAL", "OFF")]


def form(team, games, n=FORM_N):
    """W-L-OTL, забито и пропущено за последние n сыгранных."""
    w = l = otl = gf = ga = 0
    for g in finished(games)[-n:]:
        h, a = g.get("homeTeam", {}), g.get("awayTeam", {})
        us, them = (h, a) if abbrev(h) == team else (a, h)
        sf, sa = us.get("score"), them.get("score")
        if sf is None or sa is None:
            continue
        gf += sf
        ga += sa
        last = (g.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        if sf > sa:
            w += 1
        elif last in ("OT", "SO"):
            otl += 1
        else:
            l += 1
    return w, l, otl, gf, ga


# ---------- раздельная статистика дома / в гостях ----------

def venue_rows(team, games, at_home):
    """(забито, пропущено) по сыгранным матчам регулярки на нужной площадке."""
    rows = []
    for g in finished(games):
        if g.get("gameType") != REG_TYPE:
            continue
        h, a = g.get("homeTeam", {}), g.get("awayTeam", {})
        is_home = abbrev(h) == team
        if is_home != at_home:
            continue
        us, them = (h, a) if is_home else (a, h)
        sf, sa = us.get("score"), them.get("score")
        if sf is None or sa is None:
            continue
        rows.append((sf, sa))
    return rows


def split_stats(team, at_home, n=SPLIT_N):
    """Забито и пропущено за игру на своей/чужой площадке.
    Текущий сезон, добор из прошлого до глубины n.
    Возвращает (gf_per, ga_per, использовано, взято из прошлого сезона)."""
    rows = venue_rows(team, season_games(team, "now"), at_home)
    from_prev = 0
    if len(rows) < n:
        prev = venue_rows(team, season_games(team, prev_season_id(team)), at_home)
        need = n - len(rows)
        take = prev[-need:] if need > 0 else []
        from_prev = len(take)
        rows = take + rows
    rows = rows[-n:]
    if not rows:
        return None, None, 0, 0
    gf = sum(r[0] for r in rows) / float(len(rows))
    ga = sum(r[1] for r in rows) / float(len(rows))
    return gf, ga, len(rows), from_prev


def split_line(label, gf, ga, used, from_prev):
    if not used:
        return "    %s: нет данных" % label
    src = "%d матчей" % used
    if from_prev:
        src += ", из них %d из прошлого сезона" % from_prev
    return "    %s: забито %.2f, пропущено %.2f за игру (%s)" % (label, gf, ga, src)


def rest_info(team, games, kickoff):
    """Дни отдыха, бэк-ту-бэк, длина текущего выезда."""
    done = finished(games)
    if not done:
        return None, False, 0
    prev = done[-1]
    try:
        pd = datetime.strptime(prev["gameDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None, False, 0
    days = (kickoff.date() - pd.date()).days
    b2b = days <= 1
    trip = 0
    for g in reversed(done):
        if abbrev(g.get("awayTeam", {})) == team:
            trip += 1
        else:
            break
    return days, b2b, trip


def h2h(home, away):
    """Очные ТОЛЬКО на площадке сегодняшних хозяев, текущий сезон."""
    out = []
    for g in finished(season_games(home)):
        h, a = g.get("homeTeam", {}), g.get("awayTeam", {})
        if abbrev(h) == home and abbrev(a) == away:
            end = {"OT": " ОТ", "SO": " Б"}.get(
                (g.get("gameOutcome") or {}).get("lastPeriodType", "REG"), "")
            out.append("%s %s:%s %s%s" % (home, h.get("score"), a.get("score"), away, end))
    return out[-5:]


def goalies(gid):
    """Вероятные вратари. До объявления состава может не быть — тогда пусто."""
    land = api("/gamecenter/%s/landing" % gid)
    if not land:
        return None
    m = land.get("matchup") or {}
    for key in ("goalieComparison", "startingGoalies", "goalies"):
        blob = m.get(key)
        if blob:
            return json.dumps(blob, ensure_ascii=False)[:220]
    return None


def rec(t, pre=""):
    """Строка результатов из standings."""
    if not t:
        return "нет данных"
    return "%s-%s-%s, дома %s-%s-%s, в гостях %s-%s-%s, посл.10 %s-%s-%s, серия %s%s  GF/GA %s/%s" % (
        t.get("wins"), t.get("losses"), t.get("otLosses"),
        t.get("homeWins"), t.get("homeLosses"), t.get("homeOtLosses"),
        t.get("roadWins"), t.get("roadLosses"), t.get("roadOtLosses"),
        t.get("l10Wins"), t.get("l10Losses"), t.get("l10OtLosses"),
        t.get("streakCode", ""), t.get("streakCount", ""),
        t.get("goalFor"), t.get("goalAgainst"))


# ---------- вывод ----------

def main():
    now = datetime.now(timezone.utc)
    games = upcoming(now)

    head = "\U0001F3D2 NHL — расписание тура\n%s UTC  |  матчей: %d  |  окно: %dч\nбез коэффициентов — оценка до цены\n\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(games), WINDOW_H)
    if not games:
        tg_send(head + "игр в окне нет")
        print("no games in window")
        return

    lines = []
    for i, g in enumerate(games, 1):
        t = parse_iso(g["startTimeUTC"])
        lines.append("%2d. %s - %s  (%s UTC)" % (
            i, abbrev(g.get("homeTeam", {})), abbrev(g.get("awayTeam", {})),
            t.strftime("%d.%m %H:%M")))
    tg_send(head + "\n".join(lines))

    st = standings_map()
    blocks = []
    for g in games:
        home = abbrev(g.get("homeTeam", {}))
        away = abbrev(g.get("awayTeam", {}))
        kick = parse_iso(g["startTimeUTC"])
        hs, as_ = season_games(home), season_games(away)

        b = ["%s - %s  (%s UTC)" % (home, away, kick.strftime("%d.%m %H:%M"))]
        b.append("  хозяева: " + rec(st.get(home)))
        w, l, otl, gf, ga = form(home, hs)
        b.append("    посл.%d: %d-%d-%d, забито %d, пропущено %d" % (FORM_N, w, l, otl, gf, ga))
        d, b2b, trip = rest_info(home, hs, kick)
        b.append("    отдых: %s дн%s" % (d if d is not None else "?", ", БЭК-ТУ-БЭК" if b2b else ""))

        b.append("  гости: " + rec(st.get(away)))
        w, l, otl, gf, ga = form(away, as_)
        b.append("    посл.%d: %d-%d-%d, забито %d, пропущено %d" % (FORM_N, w, l, otl, gf, ga))
        d, b2b, trip = rest_info(away, as_, kick)
        b.append("    отдых: %s дн%s%s" % (
            d if d is not None else "?", ", БЭК-ТУ-БЭК" if b2b else "",
            ", выезд %d-я игра подряд" % trip if trip > 1 else ""))

        h_gf, h_ga, h_used, h_prev = split_stats(home, True)
        a_gf, a_ga, a_used, a_prev = split_stats(away, False)
        b.append("  Формула (глубина %d, только регулярка):" % SPLIT_N)
        b.append(split_line("%s дома" % home, h_gf, h_ga, h_used, h_prev))
        b.append(split_line("%s в гостях" % away, a_gf, a_ga, a_used, a_prev))
        if h_used and a_used:
            exp_h = (h_gf + a_ga) / 2.0
            exp_a = (a_gf + h_ga) / 2.0
            b.append("    база: хозяева %.2f + гости %.2f = %.2f (без поправки)" % (
                exp_h, exp_a, exp_h + exp_a))
        else:
            b.append("    база: не считается — нет данных")

        hh = h2h(home, away)
        b.append("  Очные (у хозяев): " + (" | ".join(hh) if hh else "в этом сезоне не было"))

        gk = goalies(g.get("id"))
        b.append("  Вратари: " + (gk if gk else "состав не объявлен"))
        blocks.append("\n".join(b))

    ctx = "\U0001F3D2 NHL — контекст тура\n%s UTC  |  матчей: %d\nбез коэффициентов  |  фактор площадки внутри раздельной статистики, погоды нет\n\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(games))
    tg_send(ctx + "\n\n".join(blocks))
    print("context sent for %d game(s)" % len(games))


if __name__ == "__main__":
    main()
