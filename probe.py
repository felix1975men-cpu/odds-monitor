#!/usr/bin/env python3
"""Что доступно в релизах nflverse: EPA, стартовые QB, снэпы."""
import csv
import io
import requests

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
T = 60

TESTS = [
    ("stats_team 2025",       f"{BASE}/stats_team/stats_team_reg_2025.csv"),
    ("stats_team 2026",       f"{BASE}/stats_team/stats_team_reg_2026.csv"),
    ("stats_player 2025",     f"{BASE}/stats_player/stats_player_reg_2025.csv"),
    ("depth_charts 2025",     f"{BASE}/depth_charts/depth_charts_2025.csv"),
    ("depth_charts 2026",     f"{BASE}/depth_charts/depth_charts_2026.csv"),
    ("snap_counts 2025",      f"{BASE}/snap_counts/snap_counts_2025.csv"),
    ("rosters 2026",          f"{BASE}/rosters/roster_2026.csv"),
    ("injuries 2026",         f"{BASE}/injuries/injuries_2026.csv"),
    ("pbp 2025 (тяжёлый)",    f"{BASE}/pbp/play_by_play_2025.csv"),
]

for name, url in TESTS:
    print("=" * 55)
    print(name)
    try:
        # только заголовок: тянем первые 200 КБ
        r = requests.get(url, timeout=T, stream=True,
                         headers={"Range": "bytes=0-200000"})
        print(f"  HTTP {r.status_code}")
        if r.status_code not in (200, 206):
            continue
        text = r.content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            print("  пусто")
            continue
        cols = next(csv.reader([lines[0]]))
        print(f"  колонок: {len(cols)}")
        print(f"  {cols}")
        # ищем ключевые поля
        low = [c.lower() for c in cols]
        marks = []
        for key in ("epa", "success", "cpoe", "dakota", "wpa",
                    "passing_epa", "rushing_epa", "position",
                    "depth_team", "offense_snaps", "player_name", "team"):
            hit = [c for c in cols if key == c.lower()]
            if hit:
                marks.append(hit[0])
        if marks:
            print(f"  ЕСТЬ: {marks}")
        if len(lines) > 1:
            row = next(csv.reader([lines[1]]))
            pairs = [f"{c}={v}" for c, v in zip(cols, row)][:14]
            print(f"  пример: {pairs}")
    except Exception as e:
        print(f"  ОШИБКА: {e}")
    print()
