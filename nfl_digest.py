#!/usr/bin/env python3
"""
NFL сводка на тур. Кредиты НЕ тратит — читает последний снимок,
который уже собрал odds-монитор в data/nfl/ГГГГ-ММ-ДД.csv.

Порядок для каждого рынка:
  1) выбирается ОДНА линия — Pinnacle, а если её нет, то та,
     на которой стоит больше книг; для тотала .5 бьёт целое число.
     Линия годится в опоры только если на ней стоит не меньше
     MIN_BOOKS разных книг: цифра одной конторы — это не рынок.
     Если такой линии нет, рынок пропускается с пометкой.
  2) на этой линии берётся лучшая цена по каждой стороне
  3) fair — цена Pinnacle с убранной маржой, эталон вероятности.
     Сначала ищется цена Pinnacle НА ТОЙ ЖЕ линии, что и ставка;
     только если её там нет, берётся основная линия Pinnacle,
     и тогда в строке стоит пометка о расхождении номеров.
Хозяева всегда первыми.
"""

import csv
import os
import sys
import datetime as dt
from collections import defaultdict, Counter

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "120"))
SNAPSHOTS = os.environ.get("SNAPSHOTS", "data/nfl")
ANCHOR = "pinnacle"

# Сколько разных книг должно стоять на линии, чтобы она годилась в опоры.
MIN_BOOKS = 2

NEED = {"ts", "event_id", "commence_time", "home", "away",
        "book", "market", "outcome", "point", "price"}

ALIASES = {
    "snapshot_utc": "ts",
    "snapshot": "ts",
    "timestamp": "ts",
    "commence_utc": "commence_time",
    "commence": "commence_time",
    "start_time": "commence_time",
    "home_team": "home",
    "away_team": "away",
    "bookmaker": "book",
    "name": "outcome",
}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize(row):
    out = {}
    for k, v in row.items():
        out[ALIASES.get(k, k)] = v
    return out


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = {ALIASES.get(c, c) for c in (rd.fieldnames or [])}
        missing = NEED - cols
        if missing:
            print(f"{path}: не хватает колонок {sorted(missing)}")
            print(f"  есть: {sorted(rd.fieldnames or [])}")
            return []
        rows = [normalize(r) for r in rd]
        modes = {(r.get("mode") or "").lower() for r in rows}
        if modes - {""}:
            live = {"live", "inplay", "in_play"}
            rows = [r for r in rows if (r.get("mode") or "").lower() not in live]
            print(f"  {os.path.basename(path)}: режимы {sorted(modes)}, "
                  f"после отсева лайва {len(rows)} строк")
        return rows


def load():
    """SNAPSHOTS — либо конкретный файл, либо папка с файлами по дням."""
    if os.path.isdir(SNAPSHOTS):
        files = sorted(f for f in os.listdir(SNAPSHOTS) if f.endswith(".csv"))
        if not files:
            print("в папке нет csv:", SNAPSHOTS)
            return []
        rows = []
        for name in files[-2:]:
            rows += read_csv(os.path.join(SNAPSHOTS, name))
        print("прочитано:", files[-2:], "->", len(rows), "строк")
        return rows
    if not os.path.exists(SNAPSHOTS):
        print("нет пути", SNAPSHOTS)
        return []
    return read_csv(SNAPSHOTS)


def devig(p1, p2):
    if not p1 or not p2:
        return None, None
    i1, i2 = 1 / p1, 1 / p2
    s = i1 + i2
    return i1 / s, i2 / s


def line_books(rows, market, home=None):
    """{номер линии: множество книг, стоящих на ней}.

    Для гандикапа считаются только строки СО СТОРОНЫ ХОЗЯЕВ, чтобы
    сохранить знак; для тотала обе стороны стоят на одном номере.
    """
    out = {}
    for r in rows:
        pt = num(r["point"])
        if pt is None:
            continue
        if market == "spreads" and r["outcome"] != home:
            continue
        out.setdefault(pt, set()).add(r["book"])
    return out


def pin_points(rows, market, home=None):
    """Номера линий, на которых стоит сам Pinnacle."""
    out = set()
    for r in rows:
        if r["book"] != ANCHOR:
            continue
        if market == "spreads" and r["outcome"] != home:
            continue
        pt = num(r["point"])
        if pt is not None:
            out.add(pt)
    return out


def pick_line(rows, market, home=None):
    """
    Опорная линия: Pinnacle, иначе самая многокнижная.
    Только линии минимум с MIN_BOOKS книгами. None — опоры нет.
    Для тотала .5 бьёт целое число.
    """
    books = line_books(rows, market, home)
    solid = {p: b for p, b in books.items() if len(b) >= MIN_BOOKS}
    if not solid:
        return None

    def widest(cands):
        return max(cands, key=lambda p: (len(solid[p]), -abs(p)))

    pin = pin_points(rows, market, home)

    if market == "totals":
        half = {p for p in solid if abs(p % 1 - 0.5) < 1e-6}
        pin_half = half & pin
        if pin_half:
            return widest(pin_half)
        if half:
            return widest(half)
        # половинной линии ни у кого нет — остаётся целая, о ней предупредим ниже

    pin_solid = set(solid) & pin
    if pin_solid:
        return widest(pin_solid)
    return widest(set(solid))


def best_prices(rows, market, line, home, away):
    out = {}
    for r in rows:
        pt = num(r["point"])
        if line is not None:
            if pt is None:
                continue
            if market == "spreads":
                if abs(abs(pt) - abs(line)) > 1e-6:
                    continue
            elif abs(pt - line) > 1e-6:
                continue
        price = num(r["price"])
        if not price:
            continue
        name = r["outcome"]
        if market in ("h2h", "spreads"):
            side = "home" if name == home else ("away" if name == away else None)
        else:
            side = name.lower()
        if not side:
            continue
        if side not in out or price > out[side][0]:
            out[side] = (price, r["book"])
    return out


def pin_line(rows, market, home):
    pts = [num(r["point"]) for r in rows if r["book"] == ANCHOR
           and (r["outcome"] == home if market == "spreads" else True)
           and num(r["point"]) is not None]
    return pts[0] if pts else None


def pin_pair(rows, market, line, home, away):
    p = {}
    for r in rows:
        if r["book"] != ANCHOR:
            continue
        pt = num(r["point"])
        if line is not None:
            if pt is None:
                continue
            if market == "spreads":
                if abs(abs(pt) - abs(line)) > 1e-6:
                    continue
            elif abs(pt - line) > 1e-6:
                continue
        price = num(r["price"])
        if not price:
            continue
        name = r["outcome"]
        if market in ("h2h", "spreads"):
            side = "home" if name == home else ("away" if name == away else None)
        else:
            side = name.lower()
        if side:
            p[side] = price
    k1, k2 = ("home", "away") if market in ("h2h", "spreads") else ("over", "under")
    return p.get(k1), p.get(k2)


def build():
    rows = load()
    if not rows:
        return None

    last_ts = max(r["ts"] for r in rows)
    snap = [r for r in rows if r["ts"] == last_ts]

    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(hours=WINDOW_HOURS)

    by_ev = defaultdict(list)
    for r in snap:
        by_ev[r["event_id"]].append(r)

    events = []
    for eid, rs in by_ev.items():
        try:
            start = dt.datetime.fromisoformat(rs[0]["commence_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        if now <= start <= horizon:
            events.append((start, eid, rs))
    if not events:
        return None
    events.sort(key=lambda x: x[0])

    books = sorted({r["book"] for r in snap})
    head = (f"💹 <b>NFL сводка на тур</b>  <i>{len(events)} матчей · "
            f"снимок {last_ts} UTC</i>\n"
            f"<i>{len(books)} книг · fair = Pinnacle без маржи · "
            f"опора минимум на {MIN_BOOKS} книгах</i>")
    if ANCHOR not in books:
        head += "\n⚠️ <i>Pinnacle в снимке нет — опора по большинству книг, fair недоступен</i>"

    blocks = []
    for start, eid, rs in events:
        home, away = rs[0]["home"], rs[0]["away"]
        L = [f"<b>{home} - {away}</b>  <i>{start:%d.%m %H:%M} UTC</i>"]

        for market, label in (("h2h", "ML"), ("spreads", "HC"), ("totals", "Тотал")):
            mrows = [r for r in rs if r["market"] == market]
            if not mrows:
                continue
            line = pick_line(mrows, market, home)

            if market in ("spreads", "totals") and line is None:
                L.append(f"<b>{label}</b>")
                L.append(f"   ⚠️ <i>нет линии минимум на {MIN_BOOKS} книгах — пропуск</i>")
                continue

            best = best_prices(mrows, market, line, home, away)

            # fair сначала ищем на линии самой ставки, и только если
            # Pinnacle там не стоит — на его собственной линии, с пометкой
            note = ""
            f1, f2 = pin_pair(mrows, market, line, home, away)
            if not (f1 and f2) and market in ("spreads", "totals"):
                pl = pin_line(mrows, market, home)
                if pl is not None and (line is None or abs(pl - line) > 1e-6):
                    g1, g2 = pin_pair(mrows, market, pl, home, away)
                    if g1 and g2:
                        f1, f2 = g1, g2
                        note = (f" <i>(fair по линии Pinnacle {pl:g}, "
                                f"ставка на {line:g})</i>")
            fair1, fair2 = devig(f1, f2)

            nb = len(line_books(mrows, market, home).get(line, ())) if line is not None else 0

            if market == "totals":
                s1, s2 = "over", "under"
                n1 = f"Over {line:g}" if line is not None else "Over"
                n2 = f"Under {line:g}" if line is not None else "Under"
            else:
                s1, s2 = "home", "away"
                if market == "spreads" and line is not None:
                    n1 = f"{home} {line:+g}"
                    n2 = f"{away} {-line:+g}"
                else:
                    n1, n2 = home, away

            L.append(f"<b>{label}</b>" + (f" <i>{nb}кн на линии</i>" if nb else ""))
            for side, nm, fair in ((s1, n1, fair1), (s2, n2, fair2)):
                if side not in best:
                    continue
                price, book = best[side]
                ftxt = f" · fair {1/fair:.2f} ({fair*100:.0f}%)" if fair else " · fair —"
                L.append(f"   {nm}: <b>{price:.2f}</b> <i>{book}</i>{ftxt}")
            if note:
                L.append(f"  {note}")

            if market == "totals" and line is not None and abs(line % 1) < 1e-6:
                L.append("   ⚠️ <i>целая линия — возможен возврат</i>")

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
            parts.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
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
        print("Матчей в окне нет или снимок пуст")
        return
    tg_send(msg)


if __name__ == "__main__":
    main()
