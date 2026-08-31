#!/usr/bin/env python3
"""
Line movement report — compares the price we saw in the morning digest
against the closing Pinnacle price, per event/market/outcome/point.

Reads the daily snapshot files already written by snapshots.py/closing.py:
  data/<league>/YYYY-MM-DD.csv

For every market where both sides exist:
  open  = best price across all books in the EARLIEST snapshot of the day
  close = pinnacle price in the LAST snapshot with mode=closing
  move  = open / close - 1

If the point (total or spread line) differs between the two snapshots the
pair is not comparable and is marked with a cross.

Env required: TG_TOKEN, TG_CHAT_ID, SPORT
"""

import os
import csv
import glob
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

TITLES = {"nfl": "\U0001F3C8 NFL", "nba": "\U0001F3C0 NBA",
          "nhl": "\U0001F3D2 NHL", "mlb": "\u26BE MLB"}

MK = {"h2h": "ML", "totals": "T", "spreads": "F"}


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


def key(r):
    return (r["market"], r["outcome"], r.get("point") or "")


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
        close_pin = {key(r): r for r in closing
                     if r["snapshot_utc"] == last and r["book"] == "pinnacle"
                     and r["market"] in MK}
        if not close_pin:
            continue

        opens = [r for r in ev if r.get("mode") == "scheduled"]
        if not opens:
            continue
        first = min(r["snapshot_utc"] for r in opens)
        open_rows = [r for r in opens if r["snapshot_utc"] == first
                     and r["market"] in MK]

        # best available price per market/outcome/point at digest time
        best = {}
        for r in open_rows:
            try:
                pr = float(r["price"])
            except (TypeError, ValueError):
                continue
            k = key(r)
            if k not in best or pr > best[k][0]:
                best[k] = (pr, r["book"])

        # also remember which points existed at open, to detect line moves
        open_points = defaultdict(set)
        for (m, o, p) in best:
            open_points[(m, o)].add(p)

        # one side per market is enough: the other is its mirror
        home = ev[0]["home"]
        lines = []
        for k, cr in sorted(close_pin.items()):
            m, o, p = k
            if m == "h2h" and o != home:
                continue
            if m == "spreads" and not str(p).startswith("-"):
                continue
            if m == "totals" and o != "Under":
                continue
            try:
                cp = float(cr["price"])
            except (TypeError, ValueError):
                continue
            label = "%s %s" % (MK[m], o[:14])
            if p:
                label += " %s" % p
            if k in best:
                op, _ = best[k]
                mv = (op / cp - 1) * 100
                lines.append("  %-22s %.2f>%.2f %+.1f%%" % (label, op, cp, mv))
            elif open_points.get((m, o)):
                had = ",".join(sorted(open_points[(m, o)]))
                lines.append("  %-22s line %s>%s x" % (label, had, p))

        if lines:
            g = ev[0]
            blocks.append("%s - %s\n%s" % (g["home"], g["away"], "\n".join(lines)))

    if not blocks:
        print("nothing comparable")
        return

    now = datetime.now(timezone.utc)
    head = ("%s \u2014 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u0435 \u043b\u0438\u043d\u0438\u0439\n"
            "%s UTC  |  \u0441\u043e\u0431\u044b\u0442\u0438\u0439: %d\n"
            "\u0441\u0432\u043e\u0434\u043a\u0430 -> \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435 pinnacle  |  x = \u043b\u0438\u043d\u0438\u044f \u0441\u0434\u0432\u0438\u043d\u0443\u043b\u0430\u0441\u044c\n\n"
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
