#!/usr/bin/env python3
"""
Line movement report — compares the price we saw in the morning digest
against the closing Pinnacle price, per event/market/outcome.

Reads the daily snapshot files already written by snapshots.py/closing.py:
  data/<league>/YYYY-MM-DD.csv

For every market BOTH sides are reported, anchored to a single line:
  anchor = Pinnacle's point for that market, or the most common point
           across books if Pinnacle has none
  open   = best price across all books in the EARLIEST snapshot of the day,
           at the open anchor
  close  = pinnacle price in the LAST snapshot with mode=closing,
           at the close anchor
  move   = open / close - 1

Prices are always printed, because both sides at one line are what the
reverse column needs. When the open and close anchors differ the pair is
not comparable for CLV and is marked with a cross.

Env required: TG_TOKEN, TG_CHAT_ID, SPORT
"""

import os
import csv
import urllib.request
import urllib.parse
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

TITLES = {"nfl": "\U0001F3C8 NFL", "nba": "\U0001F3C0 NBA",
          "nhl": "\U0001F3D2 NHL", "mlb": "\u26BE MLB"}

MK = {"h2h": "ML", "totals": "T", "spreads": "F"}
TWO_SIDED = ("totals", "spreads")


def tg_send(text):
    url = "https://api.telegram.org/bot%s/sendMessage" % os.environ["TG_TOKEN"]
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()
    except Exception as e:
        print("Telegram send failed: %s" % e)


def load_rows(sport):
    """Rows from yesterday and today — games can cross midnight UTC."""
    now = datetime.now(timezone.utc)
    rows = []
    for d in (now - timedelta(days=1), now):
        p = os.path.join("data", sport, "%s.csv" % d.strftime("%Y-%m-%d"))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rows.extend(list(csv.DictReader(f)))
    return rows


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def point_of(r):
    return num(r.get("point"))


def anchor(rows, market):
    """One line per market: Pinnacle's, else the most common across books.

    Spreads are anchored on the absolute value, since the two sides carry
    mirrored points (-1.5 / +1.5).
    """
    def norm(r):
        p = point_of(r)
        if p is None:
            return None
        return abs(p) if market == "spreads" else p

    pin = [norm(r) for r in rows if r["market"] == market and r["book"] == "pinnacle"]
    pin = [p for p in pin if p is not None]
    if pin:
        return Counter(pin).most_common(1)[0][0]
    allp = [norm(r) for r in rows if r["market"] == market]
    allp = [p for p in allp if p is not None]
    if not allp:
        return None
    return Counter(allp).most_common(1)[0][0]


def at_anchor(index, market, a):
    """All (outcome, point, price) in index for this market at anchor a."""
    out = []
    for (m, o, p), price in index.items():
        if m != market or p is None or a is None:
            continue
        if (abs(p) if market == "spreads" else p) == a:
            out.append((o, p, price))
    return out


def fmt_point(market, p):
    if p is None:
        return ""
    s = ("%+.1f" if market == "spreads" else "%.1f") % p
    return s.rstrip("0").rstrip(".") if s.endswith("0") and "." in s else s


def main():
    sport = os.environ["SPORT"].lower()
    rows = load_rows(sport)
    if not rows:
        print("no snapshot files")
        return

    by_event = defaultdict(list)
    for r in rows:
        by_event[r["event_id"]].append(r)

    blocks = []
    for eid, ev in by_event.items():
        closing = [r for r in ev if r.get("mode") == "closing"]
        if not closing:
            continue
        last = max(r["snapshot_utc"] for r in closing)
        close_rows = [r for r in closing if r["snapshot_utc"] == last
                      and r["book"] == "pinnacle" and r["market"] in MK]
        if not close_rows:
            continue

        opens = [r for r in ev if r.get("mode") == "scheduled"]
        if not opens:
            continue
        first = min(r["snapshot_utc"] for r in opens)
        open_rows = [r for r in opens if r["snapshot_utc"] == first
                     and r["market"] in MK]
        if not open_rows:
            continue

        # best available price per market/outcome/point at digest time
        best = {}
        for r in open_rows:
            pr = num(r["price"])
            if pr is None:
                continue
            k = (r["market"], r["outcome"], point_of(r))
            if k not in best or pr > best[k]:
                best[k] = pr

        close_px = {}
        for r in close_rows:
            pr = num(r["price"])
            if pr is not None:
                close_px[(r["market"], r["outcome"], point_of(r))] = pr

        home, away = ev[0]["home"], ev[0]["away"]
        lines = []

        # --- moneyline: both sides ---
        for o in (home, away):
            op, cp = best.get(("h2h", o, None)), close_px.get(("h2h", o, None))
            if op is None or cp is None:
                continue
            lines.append("  %-24s %.2f>%.2f %+.1f%%"
                         % ("ML " + o[:20], op, cp, (op / cp - 1) * 100))

        # --- totals and spreads: both sides, one anchored line ---
        for m in TWO_SIDED:
            a_open, a_close = anchor(open_rows, m), anchor(close_rows, m)
            o_side = {o: (p, px) for o, p, px in at_anchor(best, m, a_open)}
            c_side = {o: (p, px) for o, p, px in at_anchor(close_px, m, a_close)}
            same = a_open is not None and a_open == a_close
            for o in sorted(set(o_side) | set(c_side)):
                op = o_side.get(o)
                cp = c_side.get(o)
                if op is None and cp is None:
                    continue
                if same:
                    tag = fmt_point(m, op[0] if op else cp[0])
                else:
                    tag = "%s>%s" % (fmt_point(m, op[0]) if op else "-",
                                     fmt_point(m, cp[0]) if cp else "-")
                label = "%s %s %s" % (MK[m], o[:12], tag)
                if op and cp:
                    move = "%+.1f%%" % ((op[1] / cp[1] - 1) * 100) if same else "x"
                    lines.append("  %-24s %.2f>%.2f %s" % (label, op[1], cp[1], move))
                elif op:
                    lines.append("  %-24s %.2f>-    x" % (label, op[1]))
                else:
                    lines.append("  %-24s -   >%.2f x" % (label, cp[1]))

        if lines:
            blocks.append("%s - %s\n%s" % (home, away, "\n".join(lines)))

    if not blocks:
        print("nothing comparable")
        return

    now = datetime.now(timezone.utc)
    head = ("%s \u2014 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u0435 \u043b\u0438\u043d\u0438\u0439\n"
            "%s UTC  |  \u0441\u043e\u0431\u044b\u0442\u0438\u0439: %d\n"
            "\u0441\u0432\u043e\u0434\u043a\u0430 -> \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435 pinnacle  |  "
            "\u043e\u0431\u0435 \u0441\u0442\u043e\u0440\u043e\u043d\u044b, \u043b\u0438\u043d\u0438\u044f pinnacle  |  "
            "x = \u043b\u0438\u043d\u0438\u044f \u0441\u0434\u0432\u0438\u043d\u0443\u043b\u0430\u0441\u044c\n\n"
            % (TITLES[sport], now.strftime("%Y-%m-%d %H:%M"), len(blocks)))

    chunk, sent = head, 0
    for b in blocks:
        if len(chunk) + len(b) + 2 > 3800:
            tg_send(chunk)
            sent += 1
            chunk = ""
        chunk += ("\n\n" if chunk and chunk != head else "") + b
    if chunk.strip():
        tg_send(chunk)
        sent += 1
    print("movement report sent for %d events in %d message(s)" % (len(blocks), sent))


if __name__ == "__main__":
    main()
