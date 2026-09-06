#!/usr/bin/env python3
"""
NFL, 08:00 UTC. Два сообщения подряд:
  1) результаты — счета сыгранных матчей (nflverse games.csv)
  2) движение линий — сводка -> закрытие Pinnacle, CLV по обеим сторонам
Кредиты Odds API не тратит: цены берутся из снимков odds-монитора.
Хозяева первыми, счёт хозяева:гости.
"""

import csv
import io
import os
import sys
import datetime as dt

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
SNAPSHOTS = os.environ.get("SNAPSHOTS", "data/nfl")
BACK_HOURS = int(os.environ.get("BACK_HOURS", "36"))
TIMEOUT = 60
ANCHOR = "pinnacle"

GAMES_CSV = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "schedules/games.csv")

NAMES = {
    "ARI":"Cardinals","ATL":"Falcons","BAL":"Ravens","BUF":"Bills","CAR":"Panthers",
    "CHI":"Bears","CIN":"Bengals","CLE":"Browns","DAL":"Cowboys","DEN":"Broncos",
    "DET":"Lions","GB":"Packers","HOU":"Texans","IND":"Colts","JAX":"Jaguars",
    "KC":"Chiefs","LV":"Raiders","LAC":"Chargers","LA":"Rams","MIA":"Dolphins",
    "MIN":"Vikings","NE":"Patriots","NO":"Saints","NYG":"Giants","NYJ":"Jets",
    "PHI":"Eagles","PIT":"Steelers","SF":"49ers","SEA":"Seahawks","TB":"Buccaneers",
    "TEN":"Titans","WAS":"Commanders",
}

NEED = {"ts","event_id","commence_time","home","away","book","market",
        "outcome","point","price"}
ALIASES = {"snapshot_utc":"ts","snapshot":"ts","timestamp":"ts",
           "commence_utc":"commence_time","commence":"commence_time",
           "start_time":"commence_time","home_team":"home","away_team":"away",
           "bookmaker":"book","name":"outcome"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = {ALIASES.get(c, c) for c in (rd.fieldnames or [])}
        if NEED - cols:
            print(f"{path}: нет колонок {sorted(NEED - cols)}")
            return []
        rows = [{ALIASES.get(k, k): v for k, v in r.items()} for r in rd]
        live = {"live","inplay","in_play"}
        return [r for r in rows if (r.get("mode") or "").lower() not in live]


def load_snaps():
    if not os.path.isdir(SNAPSHOTS):
        print("нет папки", SNAPSHOTS)
        return []
    files = sorted(f for f in os.listdir(SNAPSHOTS) if f.endswith(".csv"))
    rows = []
    for name in files[-4:]:
        rows += read_csv(os.path.join(SNAPSHOTS, name))
    print("снимки:", files[-4:], "->", len(rows), "строк")
    return rows


def kickoff(g):
    day, tm = g.get("gameday",""), g.get("gametime","")
    if not day or not tm:
        return None
    try:
        loc = dt.datetime.strptime(f"{day} {tm}", "%Y-%m-%d %H:%M")
        off = 4 if 3 <= loc.month <= 10 else 5
        return loc.replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=off)
    except Exception:
        return None


def side_of(r, home, away):
    n = r["outcome"]
    if r["market"] == "totals":
        return n.lower()
    return "home" if n == home else ("away" if n == away else None)


def snap_state(rows, home, away):
    """Линия Pinnacle, лучшая цена по стороне на этой линии и цена Pinnacle."""
    out = {}
    for mkt in ("h2h", "spreads", "totals"):
        mr = [r for r in rows if r["market"] == mkt]
        if not mr:
            continue
        pin = [r for r in mr if r["book"] == ANCHOR]
        if not pin:
            continue
        if mkt == "spreads":
            line = next((num(r["point"]) for r in pin
                         if r["outcome"] == home and r["point"]), None)
        else:
            line = next((num(r["point"]) for r in pin if r["point"]), None)

        def on_line(r):
            if line is None:
                return True
            pt = num(r["point"])
            if pt is None:
                return False
            return abs(abs(pt) - abs(line)) < 1e-6 if mkt == "spreads" \
                else abs(pt - line) < 1e-6

        best, pinp = {}, {}
        for r in mr:
            if not on_line(r):
                continue
            pr = num(r["price"])
            s = side_of(r, home, away)
            if not pr or not s:
                continue
            if s not in best or pr > best[s]:
                best[s] = pr
            if r["book"] == ANCHOR:
                pinp[s] = pr
        out[mkt] = {"line": line, "best": best, "pin": pinp}
    return out


def movement(snaps, home, away):
    """Первый снимок (сводка) против последнего перед стартом (закрытие)."""
    rows = [r for r in snaps if r["home"] == home and r["away"] == away]
    if not rows:
        return None
    times = sorted({r["ts"] for r in rows})
    if len(times) < 2:
        return None
    first, last = times[0], times[-1]
    a = snap_state([r for r in rows if r["ts"] == first], home, away)
    b = snap_state([r for r in rows if r["ts"] == last], home, away)
    return {"open": a, "close": b}


def build_results(games_done):
    head = (f"🏈 <b>NFL — результаты</b>\n"
            f"{dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC  |  "
            f"матчей: {len(games_done)}")
    lines = []
    for ko, g, hs, as_ in games_done:
        hn = NAMES.get(g.get("home_team"), g.get("home_team"))
        an = NAMES.get(g.get("away_team"), g.get("away_team"))
        lines.append(f"{hn} {hs:.0f} : {as_:.0f} {an}")
    return head + "\n\n" + "\n".join(lines)


def build_movement(games_done, snaps):
    head = (f"📉 <b>NFL — движение линий</b>\n"
            f"{dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC  |  "
            f"событий: {len(games_done)}\n"
            f"<i>сводка -&gt; закрытие pinnacle  |  обе стороны, "
            f"линия pinnacle  |  x = линия сдвинулась</i>")
    blocks = []
    for ko, g, hs, as_ in games_done:
        ha, aa = g.get("home_team"), g.get("away_team")
        hn, an = NAMES.get(ha, ha), NAMES.get(aa, aa)
        mv = movement(snaps, hn, an) or movement(snaps, ha, aa)
        if not mv:
            blocks.append(f"<b>{hn} - {an}</b>\n  <i>снимков не хватает</i>")
            continue

        L = [f"<b>{hn} - {an}</b>"]
        for mkt, tag in (("h2h","ML"), ("totals","T"), ("spreads","F")):
            o, c = mv["open"].get(mkt), mv["close"].get(mkt)
            if not o or not c:
                continue
            moved = (o["line"] is not None and c["line"] is not None
                     and abs(o["line"] - c["line"]) > 1e-6)
            x = " x" if moved else ""
            if mkt == "totals":
                sides = [("over", f"Over {c['line']:g}"),
                         ("under", f"Under {c['line']:g}")]
            elif mkt == "spreads":
                sides = [("home", f"{hn} {c['line']:+g}"),
                         ("away", f"{an} {-c['line']:+g}")]
            else:
                sides = [("home", hn), ("away", an)]
            for key, label in sides:
                op, cp = o["best"].get(key), c["pin"].get(key)
                if not op or not cp:
                    continue
                clv = (op / cp - 1) * 100
                sign = "+" if clv >= 0 else ""
                L.append(f"  {tag} {label}   {op:.2f}&gt;{cp:.2f} "
                         f"{sign}{clv:.1f}%{x}")
        blocks.append("\n".join(L))
    return head + "\n\n" + "\n\n".join(blocks)


def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram не настроен\n")
        print(text)
        return
    parts, cur = [], ""
    for b in text.split("\n\n"):
        if len(cur) + len(b) > 3400:
            parts.append(cur); cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        parts.append(cur)
    for p in parts:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          data={"chat_id": TG_CHAT, "text": p, "parse_mode":"HTML",
                                "disable_web_page_preview": True}, timeout=30)
        print("TG:", r.status_code, r.text[:160])
        if r.status_code != 200:
            sys.exit(1)


def main():
    r = requests.get(GAMES_CSV, timeout=TIMEOUT)
    if r.status_code != 200:
        print("games.csv HTTP", r.status_code)
        return
    games = list(csv.DictReader(io.StringIO(r.text)))

    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=BACK_HOURS)

    done = []
    for g in games:
        hs, as_ = num(g.get("home_score")), num(g.get("away_score"))
        if hs is None or as_ is None:
            continue
        ko = kickoff(g)
        if ko and since <= ko <= now:
            done.append((ko, g, hs, as_))
    if not done:
        print("Сыгранных матчей в окне нет")
        return
    done.sort(key=lambda x: x[0])

    tg_send(build_results(done))
    tg_send(build_movement(done, load_snaps()))


if __name__ == "__main__":
    main()
