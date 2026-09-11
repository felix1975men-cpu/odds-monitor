#!/usr/bin/env python3
"""
nhl_results.py — 08:00 UTC.
1) результаты вчерашнего дня с пометкой OT / SO
2) движение линий: последний scheduled снимок -> closing, Pinnacle, обе стороны

Источник счёта: api-web.nhle.com (без ключа, ТРЕБУЕТ браузерный User-Agent).
Источник линий: data/nhl/YYYY-MM-DD.csv из odds_monitor.py.

Env: TG_TOKEN, TG_CHAT_ID
"""

import os
import csv
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api-web.nhle.com/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TG_CHUNK = 3800
DONE = os.path.join("data", "nhl", "results_done.txt")

# как помечаем окончание в наших выводах
END = {"REG": "", "OT": " ОТ", "SO": " Б"}

MK_TITLE = {"h2h": "ML", "totals": "T", "spreads": "F"}


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


# ---------- nhl api ----------

def api(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("NHL API HTTP %s on %s" % (e.code, path))
    except Exception as e:
        print("NHL API failed %s: %s" % (path, e))
    return None


def done_ids():
    if not os.path.exists(DONE):
        return set()
    with open(DONE, encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())


def mark_done(ids):
    os.makedirs(os.path.dirname(DONE), exist_ok=True)
    with open(DONE, "a", encoding="utf-8") as f:
        for i in ids:
            f.write(str(i) + "\n")


def collect_results(now):
    """Игры NHL идут за полночь UTC, поэтому берём вчера и позавчера."""
    seen = done_ids()
    games, fresh = [], []
    for back in (1, 2):
        d = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        js = api("/score/%s" % d)
        for g in (js or {}).get("games", []):
            state = g.get("gameState", "")
            if state not in ("FINAL", "OFF"):
                continue
            gid = str(g.get("id"))
            if gid in seen:
                continue
            seen.add(gid)
            games.append(g)
            fresh.append(gid)
    games.sort(key=lambda x: x.get("startTimeUTC", ""))
    return games, fresh


def results_text(games, now):
    head = "\U0001F3D2 NHL — результаты\n%s UTC  |  матчей: %d\n\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(games))
    if not games:
        return head + "новых завершённых матчей нет"
    lines = []
    for g in games:
        h = g.get("homeTeam") or {}
        a = g.get("awayTeam") or {}
        end = END.get(((g.get("gameOutcome") or {}).get("lastPeriodType") or "REG"), "")
        lines.append("%s %s : %s %s%s" % (
            h.get("abbrev", "?"), h.get("score", "?"),
            a.get("score", "?"), a.get("abbrev", "?"), end))
    return head + "\n".join(lines)


# ---------- движение линий ----------

def read_snapshots(now):
    """Читаем CSV за вчера, позавчера и сегодня: закрытие поздней игры падает в новый день."""
    rows = []
    for back in (2, 1, 0):
        d = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        path = os.path.join("data", "nhl", "%s.csv" % d)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("mode") == "live":
                    continue
                rows.append(r)
    return rows


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def movement_text(rows, games, now):
    """Сравниваем последний scheduled снимок с closing по Pinnacle, обе стороны."""
    played = set()
    for g in games:
        h = (g.get("homeTeam") or {}).get("abbrev", "")
        a = (g.get("awayTeam") or {}).get("abbrev", "")
        played.add((h, a))

    ev = {}
    for r in rows:
        if r.get("book") != "pinnacle":
            continue
        if r.get("market") not in MK_TITLE:
            continue
        e = ev.setdefault(r["event_id"], {
            "home": r.get("home", ""), "away": r.get("away", ""),
            "start": r.get("commence_utc", ""), "sched": {}, "close": {}})
        key = (r["market"], r.get("outcome", ""))
        slot = e["close"] if r.get("mode") == "closing" else e["sched"]
        prev = slot.get(key)
        if prev is None or r["snapshot_utc"] > prev["ts"]:
            slot[key] = {"ts": r["snapshot_utc"],
                         "price": fnum(r.get("price")),
                         "point": fnum(r.get("point"))}

    # только те события, у которых есть закрытие и игра уже сыграна
    items = [e for e in ev.values() if e["close"]]
    items.sort(key=lambda e: e["start"])

    head = "\U0001F3D2 NHL — движение линий\n%s UTC  |  событий: %d\n" % (
        now.strftime("%Y-%m-%d %H:%M"), len(items))
    head += "сводка -> закрытие pinnacle  |  обе стороны, линия pinnacle  |  x = линия сдвинулась\n"
    if not items:
        return head + "\nзакрывающих снимков нет"

    blocks = []
    for e in items:
        out = ["\n%s - %s" % (e["home"], e["away"])]
        for mk in ("h2h", "totals", "spreads"):
            keys = [k for k in e["close"] if k[0] == mk]
            for k in sorted(keys, key=lambda x: x[1]):
                c = e["close"][k]
                s = e["sched"].get(k)
                label = "  %s %s" % (MK_TITLE[mk], k[1][:18])
                if c["point"] is not None:
                    label += " %+g" % c["point"] if mk == "spreads" else " %g" % c["point"]
                if not s or s["price"] is None or c["price"] is None:
                    out.append("%-28s —" % label)
                    continue
                moved = (s["point"] or 0) != (c["point"] or 0)
                if moved:
                    out.append("%-28s %.2f>%.2f x" % (label, s["price"], c["price"]))
                else:
                    pct = s["price"] / c["price"] - 1
                    out.append("%-28s %.2f>%.2f %+.1f%%" % (label, s["price"], c["price"], pct * 100))
        blocks.append("\n".join(out))
    return head + "\n".join(blocks)


def main():
    now = datetime.now(timezone.utc)
    games, fresh = collect_results(now)
    tg_send(results_text(games, now))

    rows = read_snapshots(now)
    tg_send(movement_text(rows, games, now))

    if fresh:
        mark_done(fresh)
    print("results: %d game(s), snapshot rows: %d" % (len(games), len(rows)))


if __name__ == "__main__":
    main()
