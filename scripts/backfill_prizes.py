#!/usr/bin/env python3
"""Fill in รางวัลที่ 1 / เลขหน้า 3 ตัว / เลขท้าย 3 ตัว for historical draws.

Usage:  python scripts/backfill_prizes.py [--limit N]

Rows are processed by priority so the website is useful even if the run is
cut short:
  1. draws that share the day+month of the next draw (the table the site shows,
     newest first, up to KEEP_SAME_DAY rows)
  2. everything else, newest first
Progress is saved to draws.json every SAVE_EVERY enriched rows.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_draws  # noqa: E402
from update_draws import fetch_draw, has_front3  # noqa: E402

DRAWS_PATH = Path("draws.json")
KEEP_SAME_DAY = 20
SAVE_EVERY = 25
SLEEP_SECONDS = 0.2


def next_draw_date(today):
    """Next 1st/16th on or after today (matches index.html nextDraw())."""
    today = datetime(today.year, today.month, today.day)
    candidates = []
    for offset in range(4):
        month_index = today.month - 1 + offset
        year, month = today.year + month_index // 12, month_index % 12 + 1
        candidates += [datetime(year, month, 1), datetime(year, month, 16)]
    return min(d for d in candidates if d >= today)


def needs_update(row):
    date = datetime.strptime(row[0], "%Y-%m-%d")
    row = list(row) + [None] * max(0, 5 - len(row))
    return not (row[2] and row[4] and (row[3] or not has_front3(date)))


def prioritized_indexes(draws, today):
    md = next_draw_date(today).strftime("%m-%d")
    order = sorted(range(len(draws)), key=lambda i: draws[i][0], reverse=True)
    same_day = [i for i in order if draws[i][0][5:] == md][:KEEP_SAME_DAY]
    rest = [i for i in order if i not in set(same_day)]
    return same_day + rest


def save(payload):
    DRAWS_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="max rows to try this run (0 = all)")
    args = parser.parse_args(argv)

    update_draws.reset_source_health()
    payload = json.loads(DRAWS_PATH.read_text(encoding="utf-8"))
    draws = payload.get("draws", [])
    updated = failed = tried = 0
    dirty = False

    for index in prioritized_indexes(draws, update_draws.bangkok_now()):
        row = draws[index]
        if not needs_update(row):
            continue
        if args.limit and tried >= args.limit:
            break
        if update_draws.all_sources_down():
            print("All lottery sources are failing; stopping early. Re-run later.")
            break
        tried += 1
        try:
            date = datetime.strptime(row[0], "%Y-%m-%d")
            full = fetch_draw(date, prefer_archive=True)
            if full:
                merged = list(row) + [None] * max(0, 5 - len(row))
                merged[1] = full[1] or merged[1]
                for field_index in (2, 3, 4):
                    if full[field_index]:
                        merged[field_index] = full[field_index]
                merged = merged[:5]
                if merged != (list(row) + [None] * 5)[:5]:
                    draws[index] = merged
                    updated += 1
                    dirty = True
                else:
                    failed += 1
            else:
                failed += 1
        except Exception as exc:  # keep going; one bad date must not stop the run
            print(f"Skipped {row[0]}: {exc}")
            failed += 1
        time.sleep(SLEEP_SECONDS)
        if dirty and updated and updated % SAVE_EVERY == 0:
            payload["draws"] = draws
            save(payload)
            dirty = False
            print(f"Saved progress: enriched {updated}, unavailable/failed {failed}.")

    if dirty:
        payload["draws"] = draws
        save(payload)
    remaining = sum(1 for row in draws if needs_update(row))
    print(f"Finished. Tried {tried}; enriched {updated}; unavailable/failed {failed}; still incomplete {remaining}.")
    if tried and updated == 0:
        raise SystemExit(
            "Backfill enriched zero draws. Check the 'Diagnostics=' lines above: "
            "they show what each source (GLO / archive) returned."
        )


if __name__ == "__main__":
    main()
