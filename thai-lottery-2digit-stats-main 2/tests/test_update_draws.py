import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "update_draws.py"
spec = importlib.util.spec_from_file_location("update_draws", SCRIPT_PATH)
update_draws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_draws)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def result_payload(value):
    return {
        "statusCode": 200,
        "status": True,
        "response": {
            "result": {
                "date": "2026-10-05",
                "data": {
                    "last2": {
                        "number": [{"round": 1, "value": value}],
                    },
                },
            },
        },
    }


class UpdateDrawsTests(unittest.TestCase):
    def test_main_backfills_recent_draw(self):
        with tempfile.TemporaryDirectory() as directory:
            draws_path = Path(directory) / "draws.json"
            draws_path.write_text(
                json.dumps({"draws": [["2026-10-04", "12"]]}),
                encoding="utf-8",
            )

            def post(_url, json, timeout):
                if json["date"] == "05" and json["month"] == "10":
                    return FakeResponse(result_payload("08"))
                return FakeResponse({"status": True, "response": None})

            fake_now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
            with (
                patch.object(update_draws, "DRAWS_PATH", str(draws_path)),
                patch.object(update_draws, "bangkok_now", return_value=fake_now),
                patch.object(update_draws.requests, "post", side_effect=post),
            ):
                update_draws.main()

            payload = json.loads(draws_path.read_text(encoding="utf-8"))
            self.assertIn(["2026-10-05", "08"], payload["draws"])
            self.assertEqual(payload["updatedDisplay"], "5 ตุลาคม 2569")

    def test_network_error_fails_the_update(self):
        with tempfile.TemporaryDirectory() as directory:
            draws_path = Path(directory) / "draws.json"
            draws_path.write_text(json.dumps({"draws": []}), encoding="utf-8")
            fake_now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)

            with (
                patch.object(update_draws, "DRAWS_PATH", str(draws_path)),
                patch.object(update_draws, "bangkok_now", return_value=fake_now),
                patch.object(
                    update_draws.requests,
                    "post",
                    side_effect=update_draws.requests.RequestException("offline"),
                ),
            ):
                with self.assertRaises(SystemExit) as error:
                    update_draws.main()

            self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
