#!/usr/bin/env python3
"""Fetch Thai Government Lottery results and safely update draws.json.

Row format in draws.json:  [date, last2, first, front3, last3]
  - last2  : เลขท้าย 2 ตัว            ("08")
  - first  : รางวัลที่ 1                ("012345")
  - front3 : เลขหน้า 3 ตัว (comma list) ("008,123")  -- only draws >= 2015-09-01
  - last3  : เลขท้าย 3 ตัว (comma list) ("007,890")

Sources (tried in order, all official-data mirrors):
  1. GLO  https://www.glo.or.th/api/checking/getLotteryResult
  2. Archive  https://lotto.api.rayriffy.com/lotto/{DDMMYYYY(BE)}
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

DRAWS_PATH = "draws.json"
GLO_URL = "https://www.glo.or.th/api/checking/getLotteryResult"
ARCHIVE_URL = "https://lotto.api.rayriffy.com/lotto/{draw_id}"
RECENT_DAYS = 7
FRONT3_START = datetime(2015, 9, 1)  # first draw that has เลขหน้า 3 ตัว
MAX_CONSECUTIVE_FAILS = 5            # stop hammering a source that is clearly down

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
}
GLO_HEADERS = dict(HEADERS, **{
    "Content-Type": "application/json",
    "Origin": "https://www.glo.or.th",
    "Referer": "https://www.glo.or.th/",
})

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

# Per-run health of each source (consecutive failures).
SOURCE_FAILS = {"GLO": 0, "archive": 0}


def reset_source_health():
    for key in SOURCE_FAILS:
        SOURCE_FAILS[key] = 0


def source_available(name):
    return SOURCE_FAILS[name] < MAX_CONSECUTIVE_FAILS


def all_sources_down():
    return not any(source_available(name) for name in SOURCE_FAILS)


def bangkok_now():
    return datetime.now(timezone.utc) + timedelta(hours=7)


def thai_display(dt):
    return f"{dt.day} {THAI_MONTHS[dt.month]} {dt.year + 543}"


def has_front3(dt):
    return dt.replace(tzinfo=None) >= FRONT3_START


# ---------------------------------------------------------------- parsing --

def _items(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _value_list(block, width=None):
    """Return all numbers in a prize block as zero-padded strings."""
    if isinstance(block, dict):
        numbers = block.get("number", block.get("numbers", block.get("value")))
    else:
        numbers = block
    values = []
    for item in _items(numbers):
        if isinstance(item, dict):
            value = item.get("value", item.get("number", item.get("digits")))
        else:
            value = item
        if value is None:
            continue
        value = str(value).strip()
        if value and value.lower() not in {"none", "null"}:
            values.append(value.zfill(width) if width else value)
    return values


# Legacy GLO shape:  data = {"first": {...}, "last2": {...}, "last3f": {...}, "last3b": {...}}
# (older/other mirrors used first3 / front3 / last3, so accept those too)
LEGACY_KEYS = {
    "first": ("first",),
    "front3": ("last3f", "first3", "front3"),
    "last3": ("last3b", "last3", "back3"),
    "last2": ("last2", "back2"),
}
_ALL_LEGACY_KEYS = {k for names in LEGACY_KEYS.values() for k in names}


def extract_numbers(result, key, width=None):
    """Read one legacy category (key may be any alias of the category)."""
    if not isinstance(result, dict):
        return []
    for name in LEGACY_KEYS.get(key, (key,)):
        values = _value_list(result.get(name), width)
        if values:
            return values
    return []


def prize_value(result, key, width=None, multiple=False):
    values = extract_numbers(result, key, width)
    if not values:
        return None
    return ",".join(values) if multiple else values[0]


def _find_legacy_prize_object(node, depth=0):
    if depth > 10:
        return None
    if isinstance(node, dict):
        if "last2" in node or "last3f" in node or "last3b" in node:
            return node
        if any(key in node for key in _ALL_LEGACY_KEYS) and isinstance(node.get("first"), (dict, list)):
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


def _norm(text):
    return "".join(str(text or "").lower().split())


# Category arrays:  prizes[] / runningNumbers[]  (exact id or Thai name match;
# deliberately NOT substring matching, so "prizeFirstNear" is not "prizeFirst").
CATEGORY_IDS = {
    "prizefirst": "first",
    "runningnumberfrontthree": "front3",
    "runningnumberbackthree": "last3",
    "runningnumberbacktwo": "last2",
}
CATEGORY_NAMES = {
    "รางวัลที่1": "first",
    "เลขหน้า3ตัว": "front3",
    "เลขท้าย3ตัว": "last3",
    "เลขท้าย2ตัว": "last2",
}


def _category_of(item):
    ident = _norm(item.get("id"))
    if ident in CATEGORY_IDS:
        return CATEGORY_IDS[ident]
    for field in ("name", "title", "prizeName"):
        name = _norm(item.get(field))
        if name in CATEGORY_NAMES:
            return CATEGORY_NAMES[name]
    return None


def _find_category_lists(node, depth=0):
    if depth > 10:
        return []
    found = []
    if isinstance(node, dict):
        for key in ("prizes", "runningNumbers", "running_numbers", "prizeResults", "results"):
            value = node.get(key)
            if isinstance(value, list) and any(isinstance(i, dict) and "id" in i for i in value):
                found.extend(value)
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(_find_category_lists(value, depth + 1))
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                found.extend(_find_category_lists(value, depth + 1))
    unique, seen = [], set()
    for item in found:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else repr(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def _build_row(dt, first, front3, last3, last2):
    if not last2:
        return None
    first = [v.zfill(6) for v in first]
    front3 = [v.zfill(3) for v in front3]
    last3 = [v.zfill(3) for v in last3]
    last2 = [v.zfill(2) for v in last2]
    return [
        dt.strftime("%Y-%m-%d"),
        last2[0],
        first[0] if first else None,
        ",".join(front3) if front3 and has_front3(dt) else None,
        ",".join(last3) if last3 else None,
    ]


def _from_category_arrays(data, dt):
    found = {"first": [], "front3": [], "last3": [], "last2": []}
    for item in _find_category_lists(data):
        if not isinstance(item, dict):
            continue
        category = _category_of(item)
        if category:
            found[category].extend(_value_list(item, None))
    return _build_row(dt, found["first"], found["front3"], found["last3"], found["last2"])


def _parse_response(data, dt):
    """Parse a GLO response (legacy keys first, then category arrays)."""
    result = _find_legacy_prize_object(data)
    if isinstance(result, dict):
        last2 = extract_numbers(result, "last2", 2)
        if last2:
            return _build_row(
                dt,
                extract_numbers(result, "first", 6),
                extract_numbers(result, "front3", 3),
                extract_numbers(result, "last3", 3),
                last2,
            )
    return _from_category_arrays(data, dt)


def _parse_archive_response(data, dt):
    if not isinstance(data, dict) or data.get("status") not in ("success", True):
        return None
    response = data.get("response")
    if not isinstance(response, dict):
        return None
    return _from_category_arrays(response, dt)


# ------------------------------------------------------------- networking --

def fetch_archive_draw(dt):
    draw_id = dt.strftime("%d%m") + str(dt.year + 543)
    response = requests.get(ARCHIVE_URL.format(draw_id=draw_id), headers=HEADERS, timeout=15)
    response.raise_for_status()
    return _parse_response_archive(response.json(), dt)


def _parse_response_archive(data, dt):
    return _parse_archive_response(data, dt) or _parse_response(data, dt)


def _record_http_error(source, exc, diagnostics, **extra):
    """404/400 means "no such draw date" (a clean answer), anything else is a real failure."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (400, 404):
        diagnostics.append(dict(source=source, status=status, **extra))
    else:
        diagnostics.append(dict(source=source, error=str(exc)[:180], **extra))


def _fetch_glo(dt, diagnostics):
    # Calendar (CE) year is what GLO accepts today; Buddhist year kept as fallback.
    for year in (dt.year, dt.year + 543):
        payload = {"date": dt.strftime("%d"), "month": dt.strftime("%m"), "year": str(year)}
        try:
            response = requests.post(GLO_URL, json=payload, headers=GLO_HEADERS, timeout=15)
            response.raise_for_status()
            data = response.json()
            parsed = _parse_response(data, dt)
            if parsed and parsed[1]:
                return parsed
            diagnostics.append({"source": "GLO", "year": year,
                                "keys": list(data.keys()) if isinstance(data, dict) else [type(data).__name__]})
        except requests.RequestException as exc:
            _record_http_error("GLO", exc, diagnostics, year=year)
        except ValueError as exc:
            diagnostics.append({"source": "GLO", "year": year, "error": str(exc)[:180]})
    return None


def _fetch_archive(dt, diagnostics):
    try:
        archived = fetch_archive_draw(dt)
        if archived and archived[1]:
            return archived
        diagnostics.append({"source": "archive", "keys": "no usable prize data"})
    except requests.RequestException as exc:
        _record_http_error("archive", exc, diagnostics)
    except ValueError as exc:
        diagnostics.append({"source": "archive", "error": str(exc)[:180]})
    return None


def fetch_draw(dt, prefer_archive=False):
    """Fetch one draw. Returns a row, None (not a draw day), or raises
    RequestException when every source failed at the network level."""
    order = [("archive", _fetch_archive), ("GLO", _fetch_glo)]
    if not prefer_archive:
        order.reverse()
    diagnostics = []
    best = None
    for name, fetcher in order:
        if not source_available(name):
            continue
        before = len(diagnostics)
        row = fetcher(dt, diagnostics)
        if row:
            SOURCE_FAILS[name] = 0
            if best is None:
                best = row
            # A source that returned everything we need ends the search.
            if row[2] and row[4] and (row[3] or not has_front3(dt)):
                print(f"Fetched {dt:%Y-%m-%d} from {name}.")
                return row
        else:
            # Only real network/parse errors count against a source; a clean
            # "no such draw" answer (non-draw day) does not.
            if any("error" in item for item in diagnostics[before:]):
                SOURCE_FAILS[name] += 1
            else:
                SOURCE_FAILS[name] = 0
    if best:
        return best
    print(f"No usable prize data for {dt:%Y-%m-%d}. Diagnostics={diagnostics}")
    if diagnostics and all("error" in item for item in diagnostics):
        raise requests.RequestException(f"All lottery sources failed for {dt:%Y-%m-%d}: {diagnostics}")
    return None


# -------------------------------------------------------------- main flow --

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
    reset_source_health()
    today = bangkok_now()
    with open(DRAWS_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    draws = payload["draws"]
    new_draws, changed_existing, errors = [], False, []

    for draw_date in recent_dates(today):
        draw_iso = draw_date.strftime("%Y-%m-%d")
        existing = next((row for row in draws if row[0] == draw_iso), None)
        needs_front3 = has_front3(draw_date)
        if existing is not None and len(existing) >= 5 and existing[2] and existing[4] and (existing[3] or not needs_front3):
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
            elif fetched[2]:  # only a real draw day returns a first prize
                new_draws.append(fetched)

    if errors:
        for error in errors:
            print(f"Could not reach lottery sources for {error}")
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
