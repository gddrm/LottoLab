import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("backfill_prizes", ROOT / "scripts" / "backfill_prizes.py")
backfill_prizes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill_prizes)

FULL = ["2020-01-16", "08", "012345", "008,123", "007,890"]


def run(draws, fetch, argv=None, now=datetime(2026, 10, 24, tzinfo=timezone.utc)):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "draws.json"
        path.write_text(json.dumps({"draws": draws}), encoding="utf-8")
        with patch.object(backfill_prizes, "DRAWS_PATH", path), \
                patch.object(backfill_prizes, "fetch_draw", side_effect=fetch) as mocked, \
                patch.object(backfill_prizes.update_draws, "bangkok_now", return_value=now), \
                patch.object(backfill_prizes.time, "sleep"):
            error = None
            try:
                backfill_prizes.main(argv or [])
            except SystemExit as exc:
                error = exc
            return json.loads(path.read_text(encoding="utf-8"))["draws"], mocked, error


class BackfillTests(unittest.TestCase):
    def test_backfills_all_prizes_and_keeps_multiple_three_digit_numbers(self):
        draws, _, error = run([["2020-01-16", "08"]], lambda d, prefer_archive=False: FULL)
        self.assertIsNone(error)
        self.assertEqual(draws[0], FULL)

    def test_old_draw_does_not_need_front_three(self):
        row = ["2010-01-16", "08", "012345", None, "007,890"]
        draws, mocked, _ = run([row], lambda d, prefer_archive=False: None)
        mocked.assert_not_called()
        self.assertEqual(draws[0], row)

    def test_old_draw_gets_first_and_last3_but_no_front3(self):
        fetched = ["2010-01-16", "08", "012345", None, "007,890"]
        draws, _, _ = run([["2010-01-16", "08"]], lambda d, prefer_archive=False: fetched)
        self.assertEqual(draws[0], fetched)

    def test_zero_enriched_fails_instead_of_reporting_success(self):
        _, _, error = run([["2020-01-16", "08"]], lambda d, prefer_archive=False: None)
        self.assertIsNotNone(error)

    def test_existing_values_are_never_erased(self):
        row = ["2020-01-16", "08", "012345", None, None]
        fetched = ["2020-01-16", "08", None, "008,123", "007,890"]
        draws, _, _ = run([row], lambda d, prefer_archive=False: fetched)
        self.assertEqual(draws[0], FULL)

    def test_same_day_draws_are_fetched_first_and_limit_applies(self):
        draws = [["2024-11-01", "01"], ["2024-11-16", "02"], ["2025-11-01", "03"], ["2025-11-16", "04"]]
        seen = []

        def fetch(date, prefer_archive=False):
            seen.append(date.strftime("%Y-%m-%d"))
            return [date.strftime("%Y-%m-%d"), "00", "111111", "222,333", "444,555"]

        run(draws, fetch, ["--limit", "2"])  # next draw is 1 Nov 2026
        self.assertEqual(seen, ["2025-11-01", "2024-11-01"])

    def test_next_draw_date(self):
        self.assertEqual(backfill_prizes.next_draw_date(datetime(2026, 10, 24)), datetime(2026, 11, 1))
        self.assertEqual(backfill_prizes.next_draw_date(datetime(2026, 10, 16)), datetime(2026, 10, 16))
        self.assertEqual(backfill_prizes.next_draw_date(datetime(2026, 12, 20)), datetime(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
