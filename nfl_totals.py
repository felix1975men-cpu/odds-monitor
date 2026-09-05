#!/usr/bin/env python3
"""
NFL тотал-метод: три строки + / − на каждый матч слейта.
Знак ставится против СЕГОДНЯШНЕЙ линии, применённой ко всем прошлым матчам.
Источник — nflverse games.csv (вся история с 1999). Ключи не нужны.

Строки:
  дома      — домашние матчи хозяев, глубина до DEPTH
  в гостях  — гостевые матчи гостей, глубина до DEPTH
  очные     — очные ТОЛЬКО на стадионе сегодняшних хозяев, потолок DEPTH
"""

import csv
import io
import os
import sys
import datetime as dt

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "120"))
DEPTH = int(os.environ.get("DEPTH", "30"))
PER_LINE = int(os.environ.get("PER_LINE", "15"))
TIMEOUT = 40

GAMES_CSV = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "schedules/games.csv")

NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills",
    "CAR": "Panthers", "CHI": "Bears", "CIN": "Bengals", "CLE": "Browns",
    "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
    "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs",
    "LV": "Raiders", "LAC": "Chargers", "LA": "Rams", "MIA": "Dolphins",
    "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
    "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SF": "49ers",
    "SEA": "Seahawks", "TB": "Buccaneers", "TEN": "Titans", "WAS": "Commanders",
}
ALIAS = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "WSH": "WAS"}


def canon(ab):
    return ALIAS.get(ab, ab)


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


def played(games):
    out = []
    for g in games:
        hs, as_ = num(g.get("home_score")), num(g.get("away_score"))
        if hs is None or as_ is None:
            continue
        day = g.get("gameday") or ""
        if not day:
            continue
        out.append({
            "day": day,
            "home": canon(g.get("home_team", "")),
            "away": canon(g.get("away_team", "")),
            "total": hs + as_,
            "season": num(g.get("season")) or 0,
            "neutral": (g.get("location") or "Home") != "Home",
        })
    out.sort(key=lambda x: x["day"])
    return out


def mark(total, line):
    return "+" if total > line else "−"


def build_rows(hist, home, away, line, season):
    h_games = [g for g in hist if g["home"] == home and not g["neutral"]]
    a_games = [g for g in hist if g["away"] == away and not g["neutral"]]
    d_games = [g for g in hist
               if g["home"] == home and g["away"] == away and not g["neutral"]]

    def pack(rows, cap):
        rows = rows[-cap:]
        seq = "".join(mark(g["total"], line) for g in rows)
        cur = sum(1 for g in rows if g["season"] == season)
        return {"seq": seq, "cur": cur, "n": len(rows)}

    return {
        "home": pack(h_games, DEPTH),
        "away": pack(a_games, DEPTH),
        "h2h": pack(d_games, DEPTH),
    }


def render_seq(seq, cur, per_line=PER_LINE):
    if not seq:
        return ["нет данных"]
    lines, old = [], len(seq) - cur
    for start in range(0, len(seq), per_line):
        chunk = seq[start:start + per_line]
        parts, buf = [], ""
        for i, ch in enumerate(chunk):
            gi = start + i
            buf += ("▸" if gi == old and cur else "") + ch
            if (i + 1) % 5 == 0:
                parts.append(buf)
                buf = ""
        if buf:
            parts.append(buf)
        lines.append(" ".join(parts))
    return lines


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
        line = num(g.get("total_line"))
        if line is None:
            continue
        ko = kickoff(g)
        if ko and now <= ko <= horizon:
            upcoming.append((ko, g, line))
    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[0])

    hist = played(games)
    season = max(num(g.get("season")) or 0 for _, g, _ in upcoming)

    head = (f"📐 <b>NFL тотал-метод</b>  "
            f"<i>{dt.datetime.utcnow():%d.%m} · {len(upcoming)} матчей · глубина {DEPTH}</i>\n"
            f"<i>старые слева, новые справа · ▸ начало текущего сезона</i>")

    blocks = []
    for ko, g, line in upcoming:
        home, away = canon(g.get("home_team", "")), canon(g.get("away_team", ""))
        hn, an = NAMES.get(home, home), NAMES.get(away, away)
        rows = build_rows(hist, home, away, line, season)

        L = [f"<b>{hn} - {an}</b>  <i>нед.{g.get('week','?')} · "
             f"{ko:%d.%m %H:%M} · тотал {line:g}</i>"]
        for key, label in (("home", "дома"), ("away", "в гостях"), ("h2h", "очные")):
            r = rows[key]
            L.append(f"<code>{label} ({r['n']})</code>")
            for ln in render_seq(r["seq"], r["cur"]):
                L.append(f"<code>  {ln}</code>")
        blocks.append("\n".join(L))

    return head + "\n\n" + "\n\n".join(blocks)


def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram не настроен\n")
        print(text)
        return
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) > 3400:
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
