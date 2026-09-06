#!/usr/bin/env python3
"""
NFL: результаты вчерашних матчей + закрытие Pinnacle для CLV.
Кредиты Odds API не тратит.
  результаты — nflverse games.csv (счета, линия)
  закрытие   — data/nfl/*.csv, снимки odds-монитора
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
    for name in files[-3:]:
        rows += read_csv(os.path.join(SNAPSHOTS, name))
    print("снимки:", files[-3:], "->", len(rows), "строк")
    return rows


def devig(a, b):
    if not a or not b:
        return None, None
    ia, ib = 1/a, 1/b
    s = ia + ib
    return ia/s, ib/s


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


def pin_close(snaps, home, away):
    """Закрывающие цены Pinnacle по последнему снимку перед стартом."""
    rows = [r for r in snaps if r["home"] == home and r["away"] == away
            and r["book"] == ANCHOR]
    if not rows:
        return None
    last = max(r["ts"] for r in rows)
    rows = [r for r in rows if r["ts"] == last]
    out = {"ts": last}
    for mkt in ("h2h", "spreads", "totals"):
        mr = [r for r in rows if r["market"] == mkt]
        if not mr:
            continue
        if mkt == "totals":
            pt = next((num(r["point"]) for r in mr if r["point"]), None)
            o = next((num(r["price"]) for r in mr if r["outcome"].lower()=="over"), None)
            u = next((num(r["price"]) for r in mr if r["outcome"].lower()=="under"), None)
            fo, fu = devig(o, u)
            out[mkt] = {"line": pt, "a": o, "b": u, "fa": fo, "fb": fu}
        else:
            h = next((num(r["price"]) for r in mr if r["outcome"] == home), None)
            a = next((num(r["price"]) for r in mr if r["outcome"] == away), None)
            pt = next((num(r["point"]) for r in mr
                       if r["outcome"] == home and r["point"]), None)
            fh, fa = devig(h, a)
            out[mkt] = {"line": pt, "a": h, "b": a, "fa": fh, "fb": fa}
    return out


def build():
    r = requests.get(GAMES_CSV, timeout=TIMEOUT)
    if r.status_code != 200:
        print("games.csv HTTP", r.status_code)
        return None
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
        return None
    done.sort(key=lambda x: x[0])

    snaps = load_snaps()

    head = (f"🏁 <b>NFL — результаты</b>  "
            f"<i>{dt.datetime.utcnow():%d.%m} · {len(done)} матчей</i>")
    blocks = []

    for ko, g, hs, as_ in done:
        ha, aa = g.get("home_team"), g.get("away_team")
        hn, an = NAMES.get(ha, ha), NAMES.get(aa, aa)
        sl, tl = num(g.get("spread_line")), num(g.get("total_line"))
        total, margin = hs + as_, hs - as_

        L = [f"<b>{hn} {hs:.0f}:{as_:.0f} {an}</b>  "
             f"<i>нед.{g.get('week','?')} · {ko:%d.%m}</i>"]

        res = []
        if sl is not None:
            cov = margin - sl
            side = hn if cov > 0 else (an if cov < 0 else "возврат")
            res.append(f"гандикап {sl:+g}: <b>{side}</b>")
        if tl is not None:
            d = total - tl
            res.append(f"тотал {tl:g}: <b>{'выше' if d>0 else 'ниже' if d<0 else 'возврат'}</b> ({total:.0f})")
        if res:
            L.append("   " + " · ".join(res))

        cl = pin_close(snaps, hn, an) or pin_close(snaps, ha, aa)
        if cl:
            L.append(f"   <i>закрытие Pinnacle, снимок {cl['ts']}</i>")
            for mkt, label in (("h2h","ML"),("spreads","HC"),("totals","Тотал")):
                c = cl.get(mkt)
                if not c or not c["a"] or not c["b"]:
                    continue
                if mkt == "totals":
                    n1, n2 = f"Over {c['line']:g}", f"Under {c['line']:g}"
                elif mkt == "spreads":
                    n1 = f"{hn} {c['line']:+g}" if c["line"] is not None else hn
                    n2 = f"{an} {-c['line']:+g}" if c["line"] is not None else an
                else:
                    n1, n2 = hn, an
                L.append(f"   {label}: {n1} <b>{c['a']:.2f}</b> "
                         f"({c['fa']*100:.0f}%) · {n2} <b>{c['b']:.2f}</b> "
                         f"({c['fb']*100:.0f}%)")
        else:
            L.append("   <i>закрытия Pinnacle в снимках нет</i>")

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
    msg = build()
    if not msg:
        print("Сыгранных матчей в окне нет")
        return
    tg_send(msg)


if __name__ == "__main__":
    main()
