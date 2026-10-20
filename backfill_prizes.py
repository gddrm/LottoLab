#!/usr/bin/env python3
"""Enrich historical draw rows with first-prize and 3-digit results from GLO."""
import json
import time
from datetime import datetime
from pathlib import Path

from update_draws import fetch_draw

DRAWS_PATH = Path("draws.json")


def main():
    payload = json.loads(DRAWS_PATH.read_text(encoding="utf-8"))
    draws = payload.get("draws", [])
    updated = 0
    failed = 0
    for index, row in enumerate(draws):
        try:
            date = datetime.strptime(row[0], "%Y-%m-%d")
            # Front-three prizes were introduced in September 2015. Older
            # draws legitimately have no front3 value in the official archive.
            needs_front3 = date >= datetime(2015, 9, 1)
            has_required = len(row) >= 5 and bool(row[2]) and bool(row[4]) and (bool(row[3]) or not needs_front3)
            if has_required:
                continue
            full = fetch_draw(date)
            if full and full[2] and full[4] and (full[3] or not needs_front3):
                draws[index] = full
                updated += 1
            else:
                failed += 1
        except Exception as exc:
            print(f"Skipped {row[0]}: {exc}")
            failed += 1
        time.sleep(0.15)
        if (index + 1) % 50 == 0:
            print(f"Checked {index + 1}/{len(draws)}; enriched {updated}")
    payload["draws"] = draws
    if updated:
        DRAWS_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Finished. Enriched {updated} draws; unavailable/failed: {failed}.")
    if failed:
        print("Some draws could not be enriched. Check the log and rerun the workflow later.")


if __name__ == "__main__":
    main()
