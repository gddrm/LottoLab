#!/usr/bin/env python3
"""Check the official GLO API for recent lottery results and append any
missing draws to draws.json. Safe to run every day: does nothing on
non-draw days, and re-runs are harmless.

NOTE: the request/response shape here is based on GLO's documented
example (curl + a published Google Apps Script snippet). It has been
working in the daily GitHub Action; this repo's dev environment can't
reach glo.or.th, so test changes with workflow_dispatch.
"""
import json
import sys
from datetime import datetime, timezone, timedelta

import requests

DRAWS_PATH = "draws.json"
GLO_URL = "https://www.glo.or.th/api/checking/getLotteryResult"
RECENT_DAYS = 7

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def bangkok_now():
    return datetime.now(timezone.utc) + timedelta(hours=7)


def thai_display(dt):
    return f"{dt.day} {THAI_MONTHS[dt.month]} {dt.year + 543}"


def fetch_last2(dt):
    payload = {
        "date": dt.strftime("%d"),
        "month": dt.strftime("%m"),
        "year": dt.strftime("%Y"),
    }
    resp = requests.post(GLO_URL, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # Best-effort unwrap -- adjust this block if GLO's actual response
    # shape turns out to differ once tested for real.
    result = data
    for key in ("response", "result", "data"):
        if isinstance(result, dict) and key in result:
            result = result[key]

    last2_block = result.get("last2") if isinstance(result, dict) else None
    if not last2_block:
        return None
    numbers = last2_block.get("number") if isinstance(last2_block, dict) else last2_block
    if not numbers:
        return None
    first = numbers[0]
    value = first.get("value") if isinstance(first, dict) else first
    return str(value).zfill(2) if value is not None else None


def recent_dates(today):
    return [today - timedelta(days=offset) for offset in range(RECENT_DAYS + 1)]


def main():
    today = bangkok_now()

    with open(DRAWS_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    draws = payload["draws"]
    recorded_dates = {d for d, _ in draws}
    new_draws = []
    errors = []

    for draw_date in recent_dates(today):
        draw_iso = draw_date.strftime("%Y-%m-%d")
        if draw_iso in recorded_dates:
            continue

        try:
            last2 = fetch_last2(draw_date)
        except Exception as exc:
            errors.append(f"{draw_iso}: {exc}")
            continue

        if last2 is not None:
            new_draws.append([draw_iso, last2])

    if errors:
        for error in errors:
            print(f"Could not reach GLO API for {error}")
        sys.exit(1)

    if not new_draws:
        print("No new draws posted in the recent date window.")
        return

    draws.extend(new_draws)
    draws.sort(key=lambda x: x[0])
    payload["draws"] = draws
    latest_new_iso = max(draw_iso for draw_iso, _ in new_draws)
    latest_new_date = datetime.strptime(latest_new_iso, "%Y-%m-%d")
    payload["updatedDisplay"] = thai_display(latest_new_date)

    with open(DRAWS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    for draw_iso, last2 in new_draws:
        print(f"Added {draw_iso} -> {last2}")


if __name__ == "__main__":
    main()
