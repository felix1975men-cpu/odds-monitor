#!/usr/bin/env python3
"""
NFL Odds Monitor
Снимает линии через The Odds API, копит историю в CSV,
шлёт сводку в Telegram и раз в неделю — файл с базой.
"""

import os
import sys
import csv
import json
import datetime as dt
from pathlib import Path

import requests

# ---------- НАСТРОЙКИ ----------
API_KEY   = os.environ.get("ODDS_API_KEY", "")
TG_TOKEN  = os.environ.get("TG_TOKEN", "")
TG_CHAT   = os.environ.get("TG_CHAT_ID", "")

SPORT     = os.environ.get("SPORT", "americanfootball_nfl")
REGIONS   = "eu"                       # 1 регион = дешевле по кредитам
MARKETS   = "h2h,spreads,totals"       # 3 рынка = 3 кредита за вызов
BOOKMAKER = "stake"                    # какую контору выделять отдельно

DATA_DIR  = Path("data")
SNAPSHOTS = DATA_DIR / "snapshots.csv"

# порог, с которого считаем движение значимым
MOVE_TOTAL  = 1.0    # пункта по тоталу
MOVE_SPREAD = 1.0    # пункта по гандикапу

FIELDS = [
    "ts", "event_id", "commence_time", "home", "away",
    "book", "market", "outcome", "point", "price",
]


# ---------- API ----------
def fetch_odds():
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        print(f"API error {r.status_code}: {r.text[:300]}")
        sys.exit(1)

    remaining = r.headers.get("x-requests-remaining", "?")
    used = r.headers.get("x-requests-used", "?")
    print(f"Кредитов использовано: {used}, осталось: {remaining}")
    return r.json(), remaining


def flatten(events, ts):
    """Разворачиваем вложенный JSON в плоские строки."""
    rows = []
    for ev in events:
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for oc in mk.get("outcomes", []):
                    rows.append({
                        "ts": ts,
                        "event_id": ev["id"],
                        "commence_time": ev["commence_time"],
                        "home": ev["home_team"],
                        "away": ev["away_team"],
                        "book": bk["key"],
                        "market": mk["key"],
                        "outcome": oc["name"],
                        "point": oc.get("point", ""),
                        "price": oc.get("price", ""),
                    })
    return rows


# ---------- ХРАНЕНИЕ ----------
def append_rows(rows):
    DATA_DIR.mkdir(exist_ok=True)
    new_file = not SNAPSHOTS.exists()
    with SNAPSHOTS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    print(f"Записано строк: {len(rows)}")


def load_history():
    if not SNAPSHOTS.exists():
        return []
    with SNAPSHOTS.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- КОНСЕНСУС ----------
def consensus(rows, event_id, market, at_ts=None):
    """
    Средняя линия и средний коэффициент по всем конторам.
    Для totals возвращает (средний тотал, кф Over, кф Under).
    Для spreads — (гандикап фаворита, кф).
    """
    sel = [r for r in rows if r["event_id"] == event_id and r["market"] == market]
    if at_ts:
        sel = [r for r in sel if r["ts"] == at_ts]
    if not sel:
        return None

    if market == "totals":
        pts = [float(r["point"]) for r in sel if r["point"]]
        over  = [float(r["price"]) for r in sel if r["outcome"] == "Over"  and r["price"]]
        under = [float(r["price"]) for r in sel if r["outcome"] == "Under" and r["price"]]
        if not pts:
            return None
        return {
            "point": round(sum(pts) / len(pts), 2),
            "over":  round(sum(over) / len(over), 3) if over else None,
            "under": round(sum(under) / len(under), 3) if under else None,
            "books": len(set(r["book"] for r in sel)),
        }

    if market == "spreads":
        by_team = {}
        for r in sel:
            if not r["point"]:
                continue
            by_team.setdefault(r["outcome"], []).append(float(r["point"]))
        if not by_team:
            return None
        avg = {t: round(sum(v) / len(v), 2) for t, v in by_team.items()}
        fav = min(avg, key=lambda t: avg[t])
        return {"fav": fav, "point": avg[fav], "books": len(set(r["book"] for r in sel))}

    return None


def book_line(rows, event_id, market, book, at_ts):
    sel = [r for r in rows if r["event_id"] == event_id
           and r["market"] == market and r["book"] == book and r["ts"] == at_ts]
    if not sel:
        return None
    if market == "totals":
        pt = next((float(r["point"]) for r in sel if r["point"]), None)
        un = next((float(r["price"]) for r in sel if r["outcome"] == "Under"), None)
        ov = next((float(r["price"]) for r in sel if r["outcome"] == "Over"), None)
        if pt is None:
            return None
        margin = None
        if un and ov:
            margin = round((1 / un + 1 / ov - 1) * 100, 1)
        return {"point": pt, "under": un, "over": ov, "margin": margin}
    return None


# ---------- СВОДКА ----------
def build_summary(rows, ts_now):
    all_ts = sorted(set(r["ts"] for r in rows))
    ts_prev = all_ts[-2] if len(all_ts) >= 2 else None

    events = {}
    for r in rows:
        if r["ts"] == ts_now:
            events[r["event_id"]] = (r["away"], r["home"], r["commence_time"])

    if not events:
        return "Матчей в линии нет."

    lines = [f"🏈 <b>NFL — линии на {dt.datetime.utcnow():%d.%m %H:%M} UTC</b>", ""]
    alerts = []

    for eid, (away, home, start) in sorted(events.items(), key=lambda x: x[1][2]):
        tot_now = consensus(rows, eid, "totals", ts_now)
        spr_now = consensus(rows, eid, "spreads", ts_now)
        if not tot_now:
            continue

        when = start[5:16].replace("T", " ")
        head = f"<b>{away} @ {home}</b>  <i>{when}</i>"

        parts = [f"тотал {tot_now['point']}"]
        if tot_now["under"]:
            parts.append(f"U {tot_now['under']}")
        if spr_now:
            parts.append(f"{spr_now['fav']} {spr_now['point']}")

        # движение
        move_txt = ""
        if ts_prev:
            tot_prev = consensus(rows, eid, "totals", ts_prev)
            if tot_prev:
                d = round(tot_now["point"] - tot_prev["point"], 2)
                if abs(d) >= 0.01:
                    move_txt = f"  ({'+' if d > 0 else ''}{d})"
                if abs(d) >= MOVE_TOTAL:
                    alerts.append(f"⚠️ {away} @ {home}: тотал {'+' if d>0 else ''}{d} → {tot_now['point']}")

            if spr_now:
                spr_prev = consensus(rows, eid, "spreads", ts_prev)
                if spr_prev and spr_prev["fav"] == spr_now["fav"]:
                    ds = round(spr_now["point"] - spr_prev["point"], 2)
                    if abs(ds) >= MOVE_SPREAD:
                        alerts.append(f"⚠️ {away} @ {home}: гандикап {spr_now['fav']} {ds:+} → {spr_now['point']}")

        lines.append(head)
        lines.append("   " + " · ".join(parts) + move_txt)

        # отдельно выделенная контора
        bl = book_line(rows, eid, "totals", BOOKMAKER, ts_now)
        if bl:
            diff = round(bl["point"] - tot_now["point"], 2)
            flag = " ⚠️" if abs(diff) >= 1.0 else ""
            m = f", маржа {bl['margin']}%" if bl["margin"] else ""
            lines.append(f"   <i>{BOOKMAKER}: {bl['point']} (U {bl['under']}{m}){flag}</i>")
        lines.append("")

    if alerts:
        lines.append("<b>Движение линий:</b>")
        lines += alerts

    return "\n".join(lines)


# ---------- TELEGRAM ----------
def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram не настроен, пропускаю отправку")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": TG_CHAT, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }, timeout=30)
    print("TG message:", r.status_code, r.text[:200])


def tg_send_file(path, caption=""):
    if not TG_TOKEN or not TG_CHAT:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": TG_CHAT, "caption": caption},
                          files={"document": f}, timeout=60)
    print("TG file:", r.status_code, r.text[:200])


# ---------- MAIN ----------
def main():
    if not API_KEY:
        print("Нет ODDS_API_KEY")
        sys.exit(1)

    ts = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M")
    events, remaining = fetch_odds()
    rows = flatten(events, ts)

    if not rows:
        print("Пустой ответ — матчей нет")
        return

    append_rows(rows)
    history = load_history()

    summary = build_summary(history, ts)
    summary += f"\n\n<i>кредитов осталось: {remaining}</i>"
    tg_send(summary)

    # по понедельникам — база файлом
    if dt.datetime.utcnow().weekday() == 0 and dt.datetime.utcnow().hour < 12:
        n_rows = len(history)
        n_games = len(set(r["event_id"] for r in history))
        tg_send_file(SNAPSHOTS,
                     f"📊 База линий: {n_games} матчей, {n_rows} строк.\n"
                     f"Перешли этот файл Claude для пересчёта статистики.")


if __name__ == "__main__":
    main()
