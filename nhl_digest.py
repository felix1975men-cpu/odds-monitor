#!/usr/bin/env python3
"""
nhl_digest.py — 09:00 UTC.
Сводка с ценами: лучшая цена рынка против fair (Pinnacle без маржи).
Якорь линии — минимум ДВЕ книги, иначе рынок пропускается.
Трёхисходный h2h (market=h2h_3way) не участвует.

ML якоря не имеет и через проверку книг не проходит: у него нет номера
линии, счётчик книг всегда выходил нулём и рынок отбрасывался целиком.

У форы сторона привязана к ЗНАКУ. Раньше +1.5 и -1.5 складывались в одну
корзину по модулю, и лучшая цена на -1.5 подставлялась к fair от +1.5 —
29 сентября это дало Торонто с перевесом +129.7%, которого не было.

Читает data/nhl/YYYY-MM-DD.csv от odds_monitor.py. Кредитов не тратит.
Env: TG_TOKEN, TG_CHAT_ID
"""

import os
import csv
from collections import Counter
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
    Возвращает (линия, число книг) или (None, сколько было у лучшей).
    Для форы линии группируются по модулю: +1.5 и -1.5 — одна линия."""
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


def signed_points(cand, home, away, line):
    """Кому какой знак форы. Pinnacle называет фаворита сам; без него —
    большинство книг по стороне хозяев, и зеркало для гостей."""
    pin = {}
    for r in cand:
        if r["book"] == "pinnacle":
            p = fnum(r.get("point"))
            if p is not None:
                pin[r.get("outcome", "")] = p
    if len(pin) >= 2:
        return pin
    home_pts = [fnum(r.get("point")) for r in cand
                if r.get("outcome") == home and fnum(r.get("point")) is not None]
    hp = Counter(home_pts).most_common(1)[0][0] if home_pts else line
    return {home: hp, away: -hp}


def devig(pin):
    """Две стороны Pinnacle -> честные вероятности."""
    if len(pin) != 2:
        return {}
    inv = sum(1.0 / p for p in pin.values())
    return {k: (1.0 / p) / inv for k, p in pin.items()}


def market_block(rows, market, title, home, away):
    want = {}
    line = None

    if market == "h2h":
        # Победитель: линии нет, якорить нечего.
        sel = [r for r in rows if r["market"] == "h2h"]
        if not sel:
            return ["  %s — котировок нет" % title]
    else:
        line, nb = anchor_line(rows, market)
        if line is None:
            return ["  %s — пропуск: линия только у %d кн, нужно %d" % (title, nb, MIN_BOOKS)]
        cand = []
        for r in rows:
            if r["market"] != market:
                continue
            p = fnum(r.get("point"))
            if p is None:
                continue
            if (abs(p) if market == "spreads" else p) == line:
                cand.append(r)
        if market == "spreads":
            want = signed_points(cand, home, away, line)
            sel = [r for r in cand if fnum(r.get("point")) == want.get(r.get("outcome", ""))]
            if not sel:
                return ["  %s %g — нет цен на этой стороне линии" % (title, line)]
        else:
            sel = cand

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
        if market == "h2h":
            label = oc
        elif market == "spreads":
            label = "%s %s %+g" % (title, oc, want.get(oc, line))
        else:
            label = "%s %s %g" % (title, oc, line)
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
