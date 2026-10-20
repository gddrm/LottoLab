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


def extract_numbers(result, key, width=None):
    """Return every number for a prize category, preserving leading zeroes."""
    block = result.get(key) if isinstance(result, dict) else None
    if not block:
        return []
    numbers = block.get("number") if isinstance(block, dict) else block
    if numbers is None:
        return []
    if not isinstance(numbers, list):
        numbers = [numbers]
    values = []
    for item in numbers:
        value = item.get("value") if isinstance(item, dict) else item
        if value is None:
            continue
        value = str(value).strip()
        if value:
            values.append(value.zfill(width) if width else value)
    return values


def prize_value(result, key, width=None, multiple=False):
    values = extract_numbers(result, key, width)
    if not values:
        return None
    return ",".join(values) if multiple else values[0]


def fetch_draw(dt):
    payload = {
        "date": dt.strftime("%d"),
        "month": dt.strftime("%m"),
        "year": dt.strftime("%Y"),
    }
    resp = requests.post(GLO_URL, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # GLO has returned the prize object at different nesting levels over time.
    # Walk known wrappers first, then recursively locate the object containing
    # prize categories rather than assuming one fixed response shape.
    def find_prize_object(node, depth=0):
        if depth > 8:
            return None
        if isinstance(node, dict):
            if any(key in node for key in ("last2", "first", "first3", "front3", "last3")):
                return node
            for key in ("response", "result", "data", "lotteryResult"):
                if key in node:
                    found = find_prize_object(node[key], depth + 1)
                    if found is not None:
                        return found
            for value in node.values():
                if isinstance(value, (dict, list)):
                    found = find_prize_object(value, depth + 1)
                    if found is not None:
                        return found
        elif isinstance(node, list):
            for value in node:
                found = find_prize_object(value, depth + 1)
                if found is not None:
                    return found
        return None

    result = find_prize_object(data)
    if not isinstance(result, dict):
        print(f"No prize categories returned for {dt:%Y-%m-%d}")
        return None
    last2 = prize_value(result, "last2", 2)
    if last2 is None:
        return None
    first = prize_value(result, "first", 6)
    front3 = prize_value(result, "first3", 3, multiple=True)
    if front3 is None:
        front3 = prize_value(result, "front3", 3, multiple=True)
    last3 = prize_value(result, "last3", 3, multiple=True)
    return [dt.strftime("%Y-%m-%d"), last2, first, front3, last3]


def recent_dates(today):
    return [today - timedelta(days=offset) for offset in range(RECENT_DAYS + 1)]


def main():
    today = bangkok_now()

    with open(DRAWS_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    draws = payload["draws"]
    new_draws = []
    changed_existing = False
    errors = []

    for draw_date in recent_dates(today):
        draw_iso = draw_date.strftime("%Y-%m-%d")
        existing = next((row for row in draws if row[0] == draw_iso), None)
        if existing is not None and len(existing) >= 5 and all(existing[2:5]):
            continue

        try:
            draw = fetch_draw(draw_date)
        except Exception as exc:
            errors.append(f"{draw_iso}: {exc}")
            continue

        if draw is not None:
            if existing is not None:
                existing[:] = draw
                changed_existing = True
            else:
                new_draws.append(draw)

    if errors:
        for error in errors:
            print(f"Could not reach GLO API for {error}")
        sys.exit(1)

    if not new_draws and not changed_existing:
        print("No new draws posted in the recent date window.")
        return

    draws.extend(new_draws)
    draws.sort(key=lambda row: row[0])
    payload["draws"] = draws
    latest_iso = max(row[0] for row in new_draws) if new_draws else max(row[0] for row in draws)
    payload["updatedDisplay"] = thai_display(datetime.strptime(latest_iso, "%Y-%m-%d"))

    with open(DRAWS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    for row in new_draws:
        print(f"Added {row[0]} -> {row[1]}")
    if changed_existing:
        print("Updated missing prize fields for existing draws.")


if __name__ == "__main__":
    main()
