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
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            error = update_draws.requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.payload


def glo_payload(last2="08"):
    """GLO legacy shape: first / last3f / last3b / last2 under response.result.data."""
    return {
        "status": True,
        "response": {"result": {"date": "2026-10-05", "data": {
            "first": {"number": [{"round": 1, "value": "012345"}]},
            "last3f": {"number": [{"round": 1, "value": "008"}, {"round": 2, "value": "123"}]},
            "last3b": {"number": [{"round": 1, "value": "007"}, {"round": 2, "value": "890"}]},
            "last2": {"number": [{"round": 1, "value": last2}]},
        }}},
    }


def array_payload():
    return {"prizes": [
        {"id": "prizeFirst", "name": "รางวัลที่ 1", "number": ["012345"]},
        {"id": "prizeFirstNear", "name": "รางวัลข้างเคียงรางวัลที่ 1", "number": ["012344", "012346"]},
        {"id": "prizeSecond", "name": "รางวัลที่ 2", "number": ["111111"]},
    ], "runningNumbers": [
        {"id": "runningNumberFrontThree", "name": "เลขหน้า 3 ตัว", "number": ["008", "123"]},
        {"id": "runningNumberBackThree", "name": "เลขท้าย 3 ตัว", "number": ["007", "890"]},
        {"id": "runningNumberBackTwo", "name": "เลขท้าย 2 ตัว", "number": ["08"]},
    ]}


class ParsingTests(unittest.TestCase):
    def test_glo_legacy_shape_with_last3f_last3b(self):
        row = update_draws._parse_response(glo_payload(), datetime(2020, 1, 16))
        self.assertEqual(row, ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_old_alias_names_still_work(self):
        data = glo_payload()["response"]["result"]["data"]
        data["first3"], data["last3"] = data.pop("last3f"), data.pop("last3b")
        row = update_draws._parse_response({"response": {"data": data}}, datetime(2020, 1, 16))
        self.assertEqual(row, ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_category_arrays_ignore_near_and_second_prizes(self):
        row = update_draws._parse_response({"response": array_payload()}, datetime(2020, 1, 16))
        self.assertEqual(row, ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_leading_zeroes_are_preserved(self):
        row = update_draws._parse_response({"response": array_payload()}, datetime(2020, 1, 16))
        self.assertEqual(row[2], "012345")
        self.assertTrue(row[3].startswith("008"))

    def test_front_three_is_dropped_before_september_2015(self):
        row = update_draws._parse_response(glo_payload(), datetime(2015, 8, 16))
        self.assertEqual(row, ["2015-08-16", "08", "012345", None, "007,890"])
        row = update_draws._parse_response(glo_payload(), datetime(2015, 9, 1))
        self.assertEqual(row[3], "008,123")

    def test_archive_response(self):
        data = {"status": "success", "response": array_payload()}
        row = update_draws._parse_archive_response(data, datetime(2020, 1, 16))
        self.assertEqual(row, ["2020-01-16", "08", "012345", "008,123", "007,890"])

    def test_unknown_payload_returns_none(self):
        self.assertIsNone(update_draws._parse_response({"status": True, "response": None}, datetime(2020, 1, 16)))


class FetchTests(unittest.TestCase):
    def setUp(self):
        update_draws.reset_source_health()

    def test_archive_draw_id_uses_buddhist_year(self):
        empty = FakeResponse({"status": "success", "response": {"prizes": [], "runningNumbers": []}})
        with patch.object(update_draws.requests, "get", return_value=empty) as mocked_get:
            update_draws.fetch_archive_draw(datetime(2026, 10, 16))
        self.assertIn("/lotto/16102569", mocked_get.call_args.args[0])

    def test_requests_send_browser_like_headers(self):
        with patch.object(update_draws.requests, "post", return_value=FakeResponse(glo_payload())) as post:
            update_draws.fetch_draw(datetime(2026, 10, 5))
        self.assertIn("Mozilla", post.call_args.kwargs["headers"]["User-Agent"])

    def test_glo_calendar_year_is_tried_first(self):
        years = []

        def post(_url, json, headers, timeout):
            years.append(json["year"])
            return FakeResponse({"status": True, "response": None})

        with patch.object(update_draws.requests, "post", side_effect=post), \
                patch.object(update_draws.requests, "get", return_value=FakeResponse({}, 404)):
            update_draws.fetch_draw(datetime(2026, 10, 5))
        self.assertEqual(years, ["2026", "2569"])

    def test_falls_back_to_archive_when_glo_has_no_prize_data(self):
        with patch.object(update_draws.requests, "post", return_value=FakeResponse({"status": True, "response": None})), \
                patch.object(update_draws.requests, "get",
                             return_value=FakeResponse({"status": "success", "response": array_payload()})):
            row = update_draws.fetch_draw(datetime(2026, 10, 5))
        self.assertEqual(row, ["2026-10-05", "08", "012345", "008,123", "007,890"])

    def test_combines_sources_when_one_is_incomplete(self):
        only_last2 = {"response": {"result": {"data": {"last2": {"number": [{"value": "08"}]}}}}}
        with patch.object(update_draws.requests, "post", return_value=FakeResponse(only_last2)), \
                patch.object(update_draws.requests, "get",
                             return_value=FakeResponse({"status": "success", "response": array_payload()})):
            row = update_draws.fetch_draw(datetime(2026, 10, 5))
        self.assertEqual(row, ["2026-10-05", "08", "012345", "008,123", "007,890"])

    def test_404_is_not_a_source_failure(self):
        with patch.object(update_draws.requests, "post", return_value=FakeResponse({}, 404)), \
                patch.object(update_draws.requests, "get", return_value=FakeResponse({}, 404)):
            for day in range(1, 10):
                self.assertIsNone(update_draws.fetch_draw(datetime(2026, 10, day)))
        self.assertFalse(update_draws.all_sources_down())

    def test_source_is_disabled_after_repeated_network_errors(self):
        boom = update_draws.requests.ConnectionError("down")
        with patch.object(update_draws.requests, "post", side_effect=boom), \
                patch.object(update_draws.requests, "get", side_effect=boom):
            for day in range(1, 8):
                try:
                    update_draws.fetch_draw(datetime(2026, 10, day), prefer_archive=True)
                except update_draws.requests.RequestException:
                    pass
        self.assertTrue(update_draws.all_sources_down())


class MainTests(unittest.TestCase):
    def setUp(self):
        update_draws.reset_source_health()

    def test_main_adds_recent_draw_with_all_prizes(self):
        with tempfile.TemporaryDirectory() as directory:
            draws_path = Path(directory) / "draws.json"
            draws_path.write_text(json.dumps({"draws": [["2026-10-04", "12"]]}), encoding="utf-8")

            def post(_url, json, headers, timeout):
                if json["date"] == "05" and json["month"] == "10":
                    return FakeResponse(glo_payload("08"))
                return FakeResponse({"status": True, "response": None})

            fake_now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
            with patch.object(update_draws, "DRAWS_PATH", str(draws_path)), \
                    patch.object(update_draws, "bangkok_now", return_value=fake_now), \
                    patch.object(update_draws.requests, "post", side_effect=post), \
                    patch.object(update_draws.requests, "get", return_value=FakeResponse({}, 404)):
                update_draws.main()

            payload = json.loads(draws_path.read_text(encoding="utf-8"))
            self.assertIn(["2026-10-05", "08", "012345", "008,123", "007,890"], payload["draws"])
            self.assertEqual(payload["updatedDisplay"], "5 ตุลาคม 2569")

    def test_network_error_fails_the_update(self):
        with tempfile.TemporaryDirectory() as directory:
            draws_path = Path(directory) / "draws.json"
            draws_path.write_text(json.dumps({"draws": []}), encoding="utf-8")
            fake_now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
            boom = update_draws.requests.RequestException("offline")
            with patch.object(update_draws, "DRAWS_PATH", str(draws_path)), \
                    patch.object(update_draws, "bangkok_now", return_value=fake_now), \
                    patch.object(update_draws.requests, "post", side_effect=boom), \
                    patch.object(update_draws.requests, "get", side_effect=boom):
                with self.assertRaises(SystemExit) as error:
                    update_draws.main()
            self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
