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
                    "last2": {"number": [{"round": 1, "value": value}]},
                    "first": {"number": [{"round": 1, "value": "012345"}]},
                    "first3": {"number": [{"round": 1, "value": "008"}, {"round": 2, "value": "123"}]},
                    "last3": {"number": [{"round": 1, "value": "007"}, {"round": 2, "value": "890"}]},
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

            request_years = []
            def post(_url, json, timeout):
                request_years.append(json["year"])
                if json["date"] == "05" and json["month"] == "10":
                    return FakeResponse(result_payload("08"))
                return FakeResponse({"status": True, "response": None})

            fake_now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
            with (
                patch.object(update_draws, "DRAWS_PATH", str(draws_path)),
                patch.object(update_draws, "bangkok_now", return_value=fake_now),
                patch.object(update_draws.requests, "post", side_effect=post),
                patch.object(update_draws.requests, "get", return_value=FakeResponse({"status": "error", "response": None})),
            ):
                update_draws.main()

            payload = json.loads(draws_path.read_text(encoding="utf-8"))
            self.assertIn(["2026-10-05", "08", "012345", "008,123", "007,890"], payload["draws"])
            self.assertEqual(payload["updatedDisplay"], "5 ตุลาคม 2569")
            self.assertTrue(request_years)
            self.assertIn("2569", request_years)
            self.assertIn("2026", request_years)

    def test_extracts_all_three_digit_prizes_and_preserves_zeroes(self):
        payload = result_payload("08")["response"]["result"]["data"]
        self.assertEqual(update_draws.prize_value(payload, "first3", 3, multiple=True), "008,123")
        self.assertEqual(update_draws.prize_value(payload, "last3", 3, multiple=True), "007,890")

    def test_parses_glo_prizes_and_running_numbers_arrays(self):
        payload = {
            "status": True,
            "response": {"result": {"data": {
                "prizes": [{"id": "prizeFirst", "name": "รางวัลที่ 1", "number": [{"value": "012345"}]}],
                "runningNumbers": [
                    {"id": "runningNumberFrontThree", "name": "เลขหน้า 3 ตัว", "number": [{"value": "008"}, {"value": "123"}]},
                    {"id": "runningNumberBackThree", "name": "เลขท้าย 3 ตัว", "number": [{"value": "007"}, {"value": "890"}]},
                    {"id": "runningNumberBackTwo", "name": "เลขท้าย 2 ตัว", "number": [{"value": "08"}]},
                ],
            }}}
        }
        result = update_draws._parse_response(payload, datetime(2020, 1, 16))
        self.assertEqual(result, ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_front_three_is_not_required_before_september_2015(self):
        payload = {
            "response": {"result": {"data": {
                "prizes": [{"id": "prizeFirst", "number": [{"value": "012345"}]}],
                "runningNumbers": [
                    {"id": "runningNumberBackThree", "number": [{"value": "007"}, {"value": "890"}]},
                    {"id": "runningNumberBackTwo", "number": [{"value": "08"}]},
                ],
            }}}
        }
        result = update_draws._parse_response(payload, datetime(2010, 1, 16))
        self.assertEqual(result, ["2010-01-16", "08", "012345", None, "007,890"])

    def test_parses_archive_api_response(self):
        payload = {
            "status": "success",
            "response": {
                "prizes": [{"id": "prizeFirst", "number": ["012345"]}],
                "runningNumbers": [
                    {"id": "runningNumberFrontThree", "number": ["008", "123"]},
                    {"id": "runningNumberBackThree", "number": ["007", "890"]},
                    {"id": "runningNumberBackTwo", "number": ["08"]},
                ],
            },
        }
        result = update_draws._parse_archive_response(payload, datetime(2020, 1, 16))
        self.assertEqual(result, ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_archive_draw_id_uses_buddhist_year(self):
        with patch.object(update_draws.requests, "get", return_value=FakeResponse({"status": "success", "response": {"prizes": [], "runningNumbers": []}})) as mocked_get:
            update_draws.fetch_archive_draw(datetime(2026, 10, 16))
        self.assertIn("/lotto/16102569", mocked_get.call_args.args[0])

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
                patch.object(
                    update_draws.requests,
                    "get",
                    side_effect=update_draws.requests.RequestException("offline"),
                ),
            ):
                with self.assertRaises(SystemExit) as error:
                    update_draws.main()

            self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
