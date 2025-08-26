import json
import os
import sys

import pytest
from datetime import datetime
import pytz

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import scripts.track_phone_usage as tpu


def test_sync_adds_missing_datapoint(monkeypatch):
    db = {
        "datapoints": [
            {"date": "2025-08-22", "value": 1, "comment": "Late night"}
        ]
    }
    beeminder_map = {}
    added = []

    def fake_add(date_str, value=1, comment=""):
        added.append((date_str, value, comment))
        return {"id": "1", "timestamp": 0, "value": value, "comment": comment}

    monkeypatch.setattr(tpu, "add_beeminder_datapoint", fake_add)

    tpu.sync_datapoints(db, beeminder_map)

    assert added == [("2025-08-22", 1, "Late night")]


def test_sync_updates_mismatched_datapoint(monkeypatch):
    db = {
        "datapoints": [
            {"date": "2025-08-23", "value": 1, "comment": "Late night"}
        ]
    }
    beeminder_map = {
        "2025-08-23": {"id": "abc", "value": 0.58, "comment": "wrong"}
    }
    updated = []

    def fake_update(dp_id, value, comment):
        updated.append((dp_id, value, comment))
        return {"id": dp_id, "value": value, "comment": comment}

    monkeypatch.setattr(tpu, "update_beeminder_datapoint", fake_update)

    tpu.sync_datapoints(db, beeminder_map)

    assert updated == [("abc", 1, "Late night")]


def test_check_already_processed_requires_match(monkeypatch, tmp_path):
    last_run = tmp_path / "last_run.json"
    last_run.write_text(
        json.dumps(
            {"last_run": "2025-08-25T00:00:00", "last_processed_date": "2025-08-23"}
        )
    )

    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run))

    db = {
        "datapoints": [
            {"date": "2025-08-23", "value": 1, "comment": "Late"}
        ]
    }
    beeminder_map = {"2025-08-23": {"value": 1, "comment": "Late"}}

    assert tpu.check_already_processed_date("2025-08-23", beeminder_map, db)

    beeminder_map["2025-08-23"]["value"] = 0.5
    assert not tpu.check_already_processed_date("2025-08-23", beeminder_map, db)


def test_beeminder_date_cutoff():
    tz = pytz.timezone("America/New_York")
    early = tz.localize(datetime(2025, 8, 25, 1, 0))
    late = tz.localize(datetime(2025, 8, 25, 23, 30))

    assert tpu.calculate_beeminder_date(early) == "2025-08-24"
    assert tpu.calculate_beeminder_date(late) == "2025-08-25"


def test_add_beeminder_datapoint_uses_timezone(monkeypatch):
    """Ensure timestamps are generated in the configured timezone."""
    tpu.TIMEZONE = "America/Los_Angeles"
    payload = {}

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "1"}

    def fake_post(url, json):
        payload.update(json)
        return DummyResponse()

    monkeypatch.setattr(tpu.requests, "post", fake_post)

    tpu.add_beeminder_datapoint("2025-08-24")

    tz = pytz.timezone("America/Los_Angeles")
    expected = int(tz.localize(datetime(2025, 8, 24)).timestamp())
    assert payload["timestamp"] == expected
