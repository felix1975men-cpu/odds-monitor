#!/usr/bin/env python3
"""
nba_context.py — 08:33 UTC.
Пары ближайшего окна + контекст: форма, очки за/против, дни отдыха,
бэк-ту-бэк, длина выезда, очные на площадке хозяев, травмы.
Раздельная статистика дома / в гостях (глубина 30, добор из прошлого
сезона) и базовая проекция тотала.
Цен НЕ печатает — прогноз даётся до цены.

Правила те же, что в хоккее:
- предсезонка не идёт НИКУДА, где считается статистика (форма, очные,
  формула); исключение — отдых и длина выезда, там считается последняя
  сыгранная игра любого типа;
- плей-офф и плей-ин идут в статистику наравне с регуляркой;
- константы домашней площадки нет: фактор площадки сидит внутри
  раздельной статистики.

ESPN site.api.espn.com, без ключа. Проверено из Actions 17.10.2026:
отвечает и без User-Agent, и с ним (cdn.nba.com даёт 403, stats.nba.com
не отвечает). Расписание команды весит около 15 МБ, поэтому каждый
ответ сразу сворачивается в короткие строки и кэшируется.
Env: TG_TOKEN, TG_CHAT_ID. Необязательно: WINDOW_H.
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

WINDOW_H = int(os.environ.get("WINDOW_H", "30"))   # окно тура в часах
FORM_N = 15            # глубина формы
SPLIT_N = 30           # глубина раздельной статистики дома / в гостях
PRESEASON = 1
COUNT_TYPES = (2, 3, 5)  # регулярка, плей-офф, плей-ин
FRESH_DAYS = 14        # травма считается свежей в течение этих дней
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

def api(url, retries=2):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            print("HTTP %s on %s" % (e.code, url))
            if e.code in (400, 404):
                return None
        except Exception as e:
            print("failed %s (try %d): %s" % (url, attempt + 1, e))
    return None


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def num(v):
    """Счёт приходит то числом/строкой, то {"value": 115.0, "displayValue": "115"}."""
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def team_id(c):
    t = c.get("team") or {}
    return str(t.get("id") or c.get("id") or "")


def season_year(now):
    """2026-27 в ESPN — это season=2027."""
    return now.year + 1 if now.month >= 8 else now.year


# ---------- ближайшие матчи ----------

def upcoming(now):
    """Матчи в окне. День на табло у ESPN считается по Восточному времени,
    поэтому берём вчера, сегодня и завтра по UTC и режем по времени старта."""
    seen, games = set(), []
    hi = now + timedelta(hours=WINDOW_H)
    for back in (-1, 0, 1):
        d = (now + timedelta(days=back)).strftime("%Y%m%d")
        js = api("%s/scoreboard?dates=%s" % (ESPN, d))
        for ev in (js or {}).get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            st = (comp.get("status") or {}).get("type") or {}
            if st.get("completed") or st.get("state") == "in":
                continue
            try:
                t = parse_iso(comp.get("date") or ev.get("date"))
            except Exception:
                continue
            if not (now <= t <= hi):
                continue
            eid = str(ev.get("id"))
            if eid in seen:
                continue
            cs = comp.get("competitors") or []
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            seen.add(eid)
            games.append({
                "id": eid, "t": t,
                "home": team_id(home), "away": team_id(away),
                "home_ab": (home.get("team") or {}).get("abbreviation", "?"),
                "away_ab": (away.get("team") or {}).get("abbreviation", "?"),
            })
    games.sort(key=lambda g: g["t"])
    return games


# ---------- расписания команд ----------

_cache = {}


def rows_for(tid, season, stype):
    """Короткие строки по играм команды за сезон и тип. Сырой ответ не держим."""
    key = (tid, season, stype)
    if key in _cache:
        return _cache[key]
    js = api("%s/teams/%s/schedule?season=%d&seasontype=%d" % (ESPN, tid, season, stype)) or {}
    out = []
    for ev in js.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        us = next((c for c in cs if team_id(c) == tid), None)
        them = next((c for c in cs if c is not us), None)
        if not us or not them:
            continue
        try:
            when = parse_iso(comp.get("date") or ev.get("date"))
        except Exception:
            continue
        done = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
        pf, pa = num(us.get("score")), num(them.get("score"))
        if done and (pf is None or pa is None):
            done = False
        try:
            typ = int((ev.get("seasonType") or {}).get("id") or stype)
        except (TypeError, ValueError):
            typ = stype
        out.append({
            "id": str(ev.get("id")), "t": when, "type": typ,
            "home": us.get("homeAway") == "home",
            "opp": (them.get("team") or {}).get("abbreviation", "?"),
            "pf": pf, "pa": pa, "done": done,
        })
    out.sort(key=lambda r: r["t"])
    _cache[key] = out
    return out


def merged(tid, season, types):
    seen, out = set(), []
    for typ in types:
        for r in rows_for(tid, season, typ):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(r)
    out.sort(key=lambda r: r["t"])
    return out


def counted(rows):
    """Сыгранные игры, которые идут в статистику. Предсезонка не идёт."""
    return [r for r in rows if r["done"] and r["type"] in COUNT_TYPES]


def cur_counted(tid, now):
    types = (2,) if now.month in (8, 9, 10, 11, 12, 1, 2, 3) else (2, 3, 5)
    return counted(merged(tid, season_year(now), types))


def prev_counted(tid, now):
    return counted(merged(tid, season_year(now) - 1, (2, 3, 5)))


# ---------- форма, раздельная статистика ----------

def record(rows):
    """W-L, дома, в гостях, последние 10, серия."""
    if not rows:
        return None
    w = l = hw = hl = aw = al = 0
    seq = []
    for r in rows:
        win = r["pf"] > r["pa"]
        seq.append(win)
        if win:
            w += 1
            if r["home"]:
                hw += 1
            else:
                aw += 1
        else:
            l += 1
            if r["home"]:
                hl += 1
            else:
                al += 1
    last = seq[-1]
    n = 0
    for x in reversed(seq):
        if x == last:
            n += 1
        else:
            break
    l10 = seq[-10:]
    return {"w": w, "l": l, "hw": hw, "hl": hl, "aw": aw, "al": al,
            "l10w": sum(l10), "l10l": len(l10) - sum(l10),
            "streak": "%s%d" % ("W" if last else "L", n)}


def rec_line(rows):
    r = record(rows)
    if not r:
        return "матчей сезона ещё нет"
    pf = sum(x["pf"] for x in rows) / float(len(rows))
    pa = sum(x["pa"] for x in rows) / float(len(rows))
    return "%d-%d, дома %d-%d, в гостях %d-%d, посл.10 %d-%d, серия %s  очки за/против %.1f/%.1f" % (
        r["w"], r["l"], r["hw"], r["hl"], r["aw"], r["al"],
        r["l10w"], r["l10l"], r["streak"], pf, pa)


def form_line(rows, n=FORM_N):
    last = rows[-n:]
    if not last:
        return "    посл.%d: матчей сезона ещё нет" % n
    w = sum(1 for r in last if r["pf"] > r["pa"])
    pf = sum(r["pf"] for r in last)
    pa = sum(r["pa"] for r in last)
    return "    посл.%d (сыграно %d): %d-%d, набрано %d, пропущено %d (в среднем %.1f / %.1f)" % (
        n, len(last), w, len(last) - w, pf, pa, pf / float(len(last)), pa / float(len(last)))


def split_stats(tid, at_home, now, n=SPLIT_N):
    """Очки за и против за игру на своей/чужой площадке.
    Текущий сезон, добор из прошлого до глубины n.
    Возвращает (pf, pa, использовано, взято из прошлого сезона)."""
    rows = [(r["pf"], r["pa"]) for r in cur_counted(tid, now) if r["home"] == at_home]
    from_prev = 0
    if len(rows) < n:
        prev = [(r["pf"], r["pa"]) for r in prev_counted(tid, now) if r["home"] == at_home]
        take = prev[-(n - len(rows)):]
        from_prev = len(take)
        rows = take + rows
    rows = rows[-n:]
    if not rows:
        return None, None, 0, 0
    return (sum(r[0] for r in rows) / float(len(rows)),
            sum(r[1] for r in rows) / float(len(rows)), len(rows), from_prev)


def split_line(label, pf, pa, used, from_prev):
    if not used:
        return "    %s: нет данных" % label
    src = "%d матчей" % used
    if from_prev:
        src += ", из них %d из прошлого сезона" % from_prev
    return "    %s: набрано %.1f, пропущено %.1f за игру (%s)" % (label, pf, pa, src)


def rest_info(tid, kickoff, now):
    """Дни отдыха, бэк-ту-бэк, игры за последние 4 дня, длина выезда.

    Здесь предсезонка УЧИТЫВАЕТСЯ намеренно: это физический факт — когда
    команда последний раз играла. Иначе в первый день сезона у всех
    выходило бы «отдых: 130 дн»."""
    rows = merged(tid, season_year(now), (PRESEASON, 2))
    done = [r for r in rows if r["done"]]
    if not done:
        done = [r for r in merged(tid, season_year(now) - 1, (2, 3, 5)) if r["done"]]
    if not done:
        return None, False, 0, 0
    days = (kickoff.date() - done[-1]["t"].date()).days
    b2b = days <= 1
    in4 = 1 + sum(1 for r in done if 0 <= (kickoff.date() - r["t"].date()).days <= 3)
    trip = 0
    for r in reversed(done):
        if not r["home"]:
            trip += 1
        else:
            break
    return days, b2b, in4, trip


def h2h(home_id, home_ab, away_ab, now):
    """Очные ТОЛЬКО на площадке сегодняшних хозяев: этот сезон, иначе прошлый."""
    for season in (season_year(now), season_year(now) - 1):
        rows = [r for r in counted(merged(home_id, season, (2, 3, 5)))
                if r["home"] and r["opp"] == away_ab]
        if rows:
            tag = "" if season == season_year(now) else " (прошлый сезон)"
            return ["%s %d:%d %s" % (home_ab, r["pf"], r["pa"], away_ab) for r in rows[-5:]], tag
    return [], ""


# ---------- травмы ----------

def injuries_map(now):
    """{team_id: [(имя, статус, дата, позиция)]}. Один запрос на всю лигу."""
    js = api("%s/injuries" % ESPN) or {}
    out = {}
    for grp in js.get("injuries", []):
        tid = str(grp.get("id"))
        lst = []
        for it in grp.get("injuries", []):
            ath = it.get("athlete") or {}
            pos = ((ath.get("position") or {}).get("abbreviation")) or ""
            try:
                when = parse_iso(it["date"])
            except Exception:
                when = None
            lst.append((ath.get("displayName", "?"), it.get("status", "?"), when, pos))
        out[tid] = lst
    return out


def inj_lines(label, items, now):
    keep = [x for x in items if x[1] in ("Out", "Doubtful", "Questionable", "Day-To-Day")]
    if not keep:
        return ["  травмы %s: нет в списке" % label]
    fresh, old = [], []
    for name, status, when, pos in keep:
        bit = "%s (%s%s)" % (name, status, ", " + pos if pos else "")
        if when and (now - when).days <= FRESH_DAYS:
            fresh.append("%s %s" % (bit, when.strftime("%d.%m")))
        else:
            old.append(bit)
    out = []
    if fresh:
        out.append("  СВЕЖИЕ потери %s: %s" % (label, "; ".join(fresh)))
    if old:
        shown = "; ".join(old[:3]) + (" и ещё %d" % (len(old) - 3) if len(old) > 3 else "")
        out.append("  давно вне строя %s: %s" % (label, shown))
    return out


# ---------- вывод ----------

def main():
    now = datetime.now(timezone.utc)
    games = upcoming(now)

    head = "\U0001F3C0 NBA — расписание тура\n%s UTC  |  матчей: %d  |  окно: %dч\nбез коэффициентов — оценка до цены\n\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(games), WINDOW_H)
    if not games:
        tg_send(head + "игр в окне нет")
        print("no games in window")
        return

    lines = []
    for i, g in enumerate(games, 1):
        lines.append("%2d. %s - %s  (%s UTC)" % (
            i, g["home_ab"], g["away_ab"], g["t"].strftime("%d.%m %H:%M")))
    tg_send(head + "\n".join(lines))

    inj = injuries_map(now)
    blocks = []
    for g in games:
        h, a = g["home"], g["away"]
        hs, as_ = cur_counted(h, now), cur_counted(a, now)

        b = ["%s - %s  (%s UTC)" % (g["home_ab"], g["away_ab"], g["t"].strftime("%d.%m %H:%M"))]
        b.append("  хозяева: " + rec_line(hs))
        b.append(form_line(hs))
        d, b2b, in4, trip = rest_info(h, g["t"], now)
        b.append("    отдых: %s дн%s%s" % (
            d if d is not None else "?", ", БЭК-ТУ-БЭК" if b2b else "",
            ", %d-я игра за 4 дня" % in4 if in4 >= 3 else ""))

        b.append("  гости: " + rec_line(as_))
        b.append(form_line(as_))
        d, b2b, in4, trip = rest_info(a, g["t"], now)
        b.append("    отдых: %s дн%s%s%s" % (
            d if d is not None else "?", ", БЭК-ТУ-БЭК" if b2b else "",
            ", %d-я игра за 4 дня" % in4 if in4 >= 3 else "",
            ", выезд %d-я игра подряд" % trip if trip > 1 else ""))

        h_pf, h_pa, h_used, h_prev = split_stats(h, True, now)
        a_pf, a_pa, a_used, a_prev = split_stats(a, False, now)
        b.append("  Формула (глубина %d, регулярка и плей-офф):" % SPLIT_N)
        b.append(split_line("%s дома" % g["home_ab"], h_pf, h_pa, h_used, h_prev))
        b.append(split_line("%s в гостях" % g["away_ab"], a_pf, a_pa, a_used, a_prev))
        if h_used and a_used:
            exp_h = (h_pf + a_pa) / 2.0
            exp_a = (a_pf + h_pa) / 2.0
            b.append("    база: хозяева %.1f + гости %.1f = %.1f (без поправки)" % (
                exp_h, exp_a, exp_h + exp_a))
        else:
            b.append("    база: не считается — нет данных")

        hh, tag = h2h(h, g["home_ab"], g["away_ab"], now)
        b.append("  Очные (у хозяев)%s: %s" % (tag, " | ".join(hh) if hh else "не было"))

        b.extend(inj_lines(g["home_ab"], inj.get(h, []), now))
        b.extend(inj_lines(g["away_ab"], inj.get(a, []), now))
        blocks.append("\n".join(b))

    ctx = "\U0001F3C0 NBA — контекст тура\n%s UTC  |  матчей: %d\nбез коэффициентов  |  фактор площадки внутри раздельной статистики\n\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(games))
    tg_send(ctx + "\n\n".join(blocks))
    print("context sent for %d game(s)" % len(games))


if __name__ == "__main__":
    main()
