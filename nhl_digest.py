#!/usr/bin/env python3
"""
nhl_digest.py — 09:00 UTC.
Сводка с ценами: лучшая цена рынка против fair (Pinnacle без маржи).
Якорь линии — минимум ДВЕ книги, иначе рынок пропускается.
Трёхисходный h2h (market=h2h_3way) не участвует.

Читает data/nhl/YYYY-MM-DD.csv от odds_monitor.py. Кредитов не тратит.
Env: TG_TOKEN, TG_CHAT_ID
"""

import os
import csv
from datetime import datetime, timezone, timedelta

WINDOW_H = 30
MIN_BOOKS = 2          # якорь линии
EDGE = 3.0             # перевес, при котором ставим <<
TG_CHUNK = 3800

import urllib.request
import urllib.parse


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


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def latest_rows(now):
    """Последний снимок по каждому ключу (событие, книга, рынок, исход, линия)."""
    best = {}
    for back in (1, 0):
        path = os.path.join("data", "nhl", "%s.csv" % (now - timedelta(days=back)).strftime("%Y-%m-%d"))
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("mode") in ("live", "closing"):
                    continue
                if r.get("market") not in ("h2h", "totals", "spreads"):
                    continue
                k = (r["event_id"], r["book"], r["market"], r.get("outcome", ""), r.get("point", ""))
                prev = best.get(k)
                if prev is None or r["snapshot_utc"] > prev["snapshot_utc"]:
                    best[k] = r
    return list(best.values())


def anchor_line(rows, market):
    """Линия-якорь: самая распространённая, при минимум MIN_BOOKS книгах.
    Возвращает (линия, число книг) или (None, сколько было у лучшей)."""
    books = {}
    for r in rows:
        if r["market"] != market:
            continue
        p = fnum(r.get("point"))
        if p is None:
            continue
        key = abs(p) if market == "spreads" else p
        books.setdefault(key, set()).add(r["book"])
    if not books:
        return None, 0
    line, bs = max(books.items(), key=lambda kv: (len(kv[1]), -abs(kv[0])))
    if len(bs) < MIN_BOOKS:
        return None, len(bs)
    return line, len(bs)


def devig(pin):
    """Две стороны Pinnacle -> честные цены."""
    if len(pin) != 2:
        return {}
    inv = sum(1.0 / p for p in pin.values())
    return {k: (1.0 / p) / inv for k, p in pin.items()}


def market_block(rows, market, title, home, away):
    line, nb = anchor_line(rows, market)
    if line is None:
        if market == "totals":
            return ["  T — пропуск: линия только у %d кн, нужно %d" % (nb, MIN_BOOKS)]
        return ["  %s — пропуск: линия только у %d кн, нужно %d" % (title, nb, MIN_BOOKS)]

    sel = [r for r in rows if r["market"] == market and
           (abs(fnum(r.get("point")) or 0) if market == "spreads" else fnum(r.get("point"))) == line]
    if market == "h2h":
        sel = [r for r in rows if r["market"] == "h2h"]

    best, pin, nbooks = {}, {}, set()
    for r in sel:
        oc, pr = r.get("outcome", ""), fnum(r.get("price"))
        if pr is None:
            continue
        nbooks.add(r["book"])
        if pr > best.get(oc, (0, ""))[0]:
            best[oc] = (pr, r["book"])
        if r["book"] == "pinnacle":
            pin[oc] = pr

    fair = devig(pin)
    out = []
    for oc in sorted(best, key=lambda x: (x != home, x)):
        pr, bk = best[oc]
        label = oc if market == "h2h" else "%s %s %g" % (title, oc, line)
        if oc in fair:
            f = 1.0 / fair[oc]
            diff = (pr / f - 1) * 100
            mark = " <<" if diff >= EDGE else ""
            out.append("  %s: %.2f (%s, %dкн) | fair %.2f  %+.1f%%%s" % (
                label[:34], pr, bk, len(nbooks), f, diff, mark))
        else:
            out.append("  %s: %.2f (%s, %dкн) | pin -" % (label[:34], pr, bk, len(nbooks)))
    return out


def main():
    now = datetime.now(timezone.utc)
    rows = latest_rows(now)

    ev = {}
    hi = now + timedelta(hours=WINDOW_H)
    for r in rows:
        try:
            t = parse_iso(r["commence_utc"])
        except Exception:
            continue
        if not (now <= t <= hi):
            continue
        ev.setdefault(r["event_id"], {"home": r.get("home", ""), "away": r.get("away", ""),
                                      "start": t, "rows": []})["rows"].append(r)

    items = sorted(ev.values(), key=lambda e: e["start"])
    head = "\U0001F3D2 NHL — сводка на тур\n%s UTC  |  матчей: %d  |  окно: %dч\nfair = Pinnacle без маржи  |  якорь линии от %d книг  |  << = перевес от %.0f%%\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(items), WINDOW_H, MIN_BOOKS, EDGE)
    if not items:
        tg_send(head + "\nсобытий в окне нет")
        return

    blocks = []
    for e in items:
        b = ["\n%s — %s  (%s UTC)" % (e["home"], e["away"], e["start"].strftime("%d.%m %H:%M"))]
        b += market_block(e["rows"], "h2h", "ML", e["home"], e["away"])
        b += market_block(e["rows"], "totals", "T", e["home"], e["away"])
        b += market_block(e["rows"], "spreads", "F", e["home"], e["away"])
        blocks.append("\n".join(b))

    tg_send(head + "\n".join(blocks))
    print("digest: %d event(s)" % len(items))


if __name__ == "__main__":
    main()
