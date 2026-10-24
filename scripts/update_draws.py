#!/usr/bin/env python3
"""Fetch Thai Government Lottery Office results and safely update draws.json."""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

DRAWS_PATH = "draws.json"
GLO_URL = "https://www.glo.or.th/api/checking/getLotteryResult"
RECENT_DAYS = 7
FRONT3_START = datetime(2015, 9, 1)

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def bangkok_now():
    return datetime.now(timezone.utc) + timedelta(hours=7)


def thai_display(dt):
    return f"{dt.day} {THAI_MONTHS[dt.month]} {dt.year + 543}"


def _items(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _value_list(block, width=None):
    if isinstance(block, dict):
        numbers = block.get("number", block.get("numbers", block.get("value")))
    else:
        numbers = block
    values = []
    for item in _items(numbers):
        value = item.get("value", item.get("number", item.get("digits"))) if isinstance(item, dict) else item
        if value is None:
            continue
        value = str(value).strip()
        if value and value.lower() not in {"none", "null"}:
            values.append(value.zfill(width) if width else value)
    return values


def extract_numbers(result, key, width=None):
    """Read legacy category objects (last2/first/first3/front3/last3)."""
    if not isinstance(result, dict):
        return []
    return _value_list(result.get(key), width)


def prize_value(result, key, width=None, multiple=False):
    values = extract_numbers(result, key, width)
    if not values:
        return None
    return ",".join(values) if multiple else values[0]


def _category_id(item):
    if not isinstance(item, dict):
        return ""
    return " ".join(str(item.get(k, "")) for k in ("id", "key", "name", "type", "title", "prizeName")).lower()


def _find_category_lists(node, depth=0):
    """Collect GLO's newer prizes/runningNumbers arrays from all wrappers."""
    if depth > 10:
        return []
    found = []
    if isinstance(node, dict):
        for key in ("prizes", "runningNumbers", "running_numbers", "prizeResults", "results"):
            value = node.get(key)
            if isinstance(value, list) and value and any(
                isinstance(item, dict) and any(k in item for k in ("id", "name", "number", "value", "type"))
                for item in value
            ):
                found.extend(value)
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(_find_category_lists(value, depth + 1))
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                found.extend(_find_category_lists(value, depth + 1))
    # Same category array can be reachable through multiple wrapper paths.
    unique = []
    seen = set()
    for item in found:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else repr(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique

def _from_category_arrays(data, dt):
    categories = _find_category_lists(data)
    if not categories:
        return None
    found = {"first": [], "front3": [], "last3": [], "last2": []}
    for item in categories:
        if not isinstance(item, dict):
            continue
        ident = _category_id(item)
        vals = _value_list(item, None)
        if not vals:
            continue
        if any(token in ident for token in ("prizefirst", "first prize", "รางวัลที่ 1", "รางวัลที่หนึ่ง")):
            found["first"].extend(vals)
        elif any(token in ident for token in ("runningnumberfrontthree", "frontthree", "front3", "เลขหน้า 3", "เลขหน้า3")):
            found["front3"].extend(vals)
        elif any(token in ident for token in ("runningnumberbackthree", "backthree", "last3", "เลขท้าย 3", "เลขท้าย3")):
            found["last3"].extend(vals)
        elif any(token in ident for token in ("runningnumberbacktwo", "backtwo", "last2", "เลขท้าย 2", "เลขท้าย2")):
            found["last2"].extend(vals)
    first = [v.zfill(6) for v in found["first"]]
    front3 = [v.zfill(3) for v in found["front3"]]
    last3 = [v.zfill(3) for v in found["last3"]]
    last2 = [v.zfill(2) for v in found["last2"]]
    if not last2:
        return None
    # Before 1 Sep 2015, front-three was not an official category.
    return [dt.strftime("%Y-%m-%d"), last2[0], first[0] if first else None,
            ",".join(front3) if front3 and dt.replace(tzinfo=None) >= FRONT3_START else None,
            ",".join(last3) if last3 else None]


def _find_legacy_prize_object(node, depth=0):
    if depth > 10:
        return None
    legacy_keys = ("last2", "first", "first3", "front3", "last3")
    if isinstance(node, dict):
        if any(key in node for key in legacy_keys):
            return node
        for value in node.values():
            if isinstance(value, (dict, list)):
                found = _find_legacy_prize_object(value, depth + 1)
                if found is not None:
                    return found
    elif isinstance(node, list):
        for value in node:
            found = _find_legacy_prize_object(value, depth + 1)
            if found is not None:
                return found
    return None


def _parse_response(data, dt):
    result = _find_legacy_prize_object(data)
    if isinstance(result, dict):
        last2 = prize_value(result, "last2", 2)
        if last2:
            first = prize_value(result, "first", 6)
            front3 = prize_value(result, "first3", 3, multiple=True) or prize_value(result, "front3", 3, multiple=True)
            last3 = prize_value(result, "last3", 3, multiple=True)
            if dt.replace(tzinfo=None) < FRONT3_START:
                front3 = None
            return [dt.strftime("%Y-%m-%d"), last2, first, front3, last3]
    return _from_category_arrays(data, dt)


def fetch_draw(dt):
    """Fetch one date. Try BE year first, then CE if the response has no prizes."""
    last_diagnostic = None
    for year in (dt.year + 543, dt.year):
        request_payload = {"date": dt.strftime("%d"), "month": dt.strftime("%m"), "year": str(year)}
        response = requests.post(GLO_URL, json=request_payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        parsed = _parse_response(data, dt)
        if parsed and parsed[1]:
            return parsed
        last_diagnostic = {
            "year": year,
            "status": data.get("status") if isinstance(data, dict) else None,
            "message": data.get("message") if isinstance(data, dict) else None,
            "keys": list(data.keys()) if isinstance(data, dict) else [type(data).__name__],
        }
    print(f"No usable prize data for {dt:%Y-%m-%d}; tried BE/CE year. Diagnostic={last_diagnostic}")
    return None


def recent_dates(today):
    return [today - timedelta(days=offset) for offset in range(RECENT_DAYS + 1)]


def _merge_draw(existing, fetched):
    """Never replace existing non-empty values with missing values."""
    merged = list(existing) + [None] * max(0, 5 - len(existing))
    merged[0] = fetched[0]
    for i in range(1, 5):
        if fetched[i] not in (None, ""):
            merged[i] = fetched[i]
    return merged[:5]


def main():
    today = bangkok_now()
    with open(DRAWS_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    draws = payload["draws"]
    new_draws, changed_existing, errors = [], False, []

    for draw_date in recent_dates(today):
        draw_iso = draw_date.strftime("%Y-%m-%d")
        existing = next((row for row in draws if row[0] == draw_iso), None)
        if existing is not None and len(existing) >= 5 and all(existing[2:5]):
            continue
        try:
            fetched = fetch_draw(draw_date)
        except Exception as exc:
            errors.append(f"{draw_iso}: {exc}")
            continue
        if fetched and fetched[1]:
            if existing is not None:
                merged = _merge_draw(existing, fetched)
                if merged != (list(existing) + [None] * max(0, 5 - len(existing)))[:5]:
                    existing[:] = merged
                    changed_existing = True
            else:
                # Add only dates that the API identifies as an actual draw by returning a first-prize result.
                if fetched[2]:
                    new_draws.append(fetched)

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
