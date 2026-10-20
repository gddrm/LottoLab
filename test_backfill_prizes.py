import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT_PATH = ROOT / "scripts" / "backfill_prizes.py"
spec = importlib.util.spec_from_file_location("backfill_prizes", SCRIPT_PATH)
backfill_prizes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill_prizes)


class BackfillPrizeTests(unittest.TestCase):
    def test_backfills_all_prizes_and_keeps_multiple_three_digit_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draws.json"
            path.write_text(json.dumps({"draws": [["2020-01-16", "08"]]}), encoding="utf-8")
            with (
                patch.object(backfill_prizes, "DRAWS_PATH", path),
                patch.object(backfill_prizes, "fetch_draw", return_value=["2020-01-16", "08", "012345", "008,123", "007,890"]),
                patch.object(backfill_prizes.time, "sleep"),
            ):
                backfill_prizes.main()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["draws"][0], ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_old_draw_does_not_require_front_three_prize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draws.json"
            path.write_text(json.dumps({"draws": [["2010-01-16", "08", "012345", None, "007,890"]]}), encoding="utf-8")
            with (
                patch.object(backfill_prizes, "DRAWS_PATH", path),
                patch.object(backfill_prizes, "fetch_draw") as fetch,
                patch.object(backfill_prizes.time, "sleep"),
            ):
                backfill_prizes.main()
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
