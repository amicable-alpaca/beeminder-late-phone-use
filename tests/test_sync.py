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


def test_validate_and_clean_removes_unauthorized_datapoints(monkeypatch):
    """Test that unauthorized datapoints are identified and deleted."""
    db = {
        "datapoints": [
            {"date": "2025-08-22", "value": 1, "comment": "Authorized"}
        ]
    }
    beeminder_map = {
        "2025-08-22": {"id": "auth1", "value": 1, "comment": "Authorized"},
        "2025-08-23": {"id": "unauth1", "value": 1, "comment": "Unauthorized"},
        "2025-08-24": {"id": "unauth2", "value": 1, "comment": "Unauthorized"}
    }
    deleted_ids = []

    def fake_delete(dp_id):
        deleted_ids.append(dp_id)
        return True

    monkeypatch.setattr(tpu, "delete_beeminder_datapoint", fake_delete)

    deleted_count, failures = tpu.validate_and_clean_beeminder_data(db, beeminder_map)

    assert deleted_count == 2
    assert len(failures) == 0
    assert set(deleted_ids) == {"unauth1", "unauth2"}


def test_validate_and_clean_handles_delete_failures(monkeypatch):
    """Test that delete failures are properly tracked."""
    db = {"datapoints": []}
    beeminder_map = {
        "2025-08-23": {"id": "unauth1", "value": 1, "comment": "Unauthorized"},
        "2025-08-24": {"id": "unauth2", "value": 1, "comment": "Unauthorized"}
    }

    def fake_delete(dp_id):
        return dp_id != "unauth2"  # Fail to delete unauth2

    monkeypatch.setattr(tpu, "delete_beeminder_datapoint", fake_delete)

    deleted_count, failures = tpu.validate_and_clean_beeminder_data(db, beeminder_map)

    assert deleted_count == 1
    assert failures == ["2025-08-24"]


def test_validate_and_clean_no_unauthorized_datapoints(monkeypatch):
    """Test when all Beeminder datapoints are authorized."""
    db = {
        "datapoints": [
            {"date": "2025-08-22", "value": 1, "comment": "Authorized"},
            {"date": "2025-08-23", "value": 1, "comment": "Authorized"}
        ]
    }
    beeminder_map = {
        "2025-08-22": {"id": "auth1", "value": 1, "comment": "Authorized"},
        "2025-08-23": {"id": "auth2", "value": 1, "comment": "Authorized"}
    }
    deleted_ids = []

    def fake_delete(dp_id):
        deleted_ids.append(dp_id)
        return True

    monkeypatch.setattr(tpu, "delete_beeminder_datapoint", fake_delete)

    deleted_count, failures = tpu.validate_and_clean_beeminder_data(db, beeminder_map)

    assert deleted_count == 0
    assert len(failures) == 0
    assert len(deleted_ids) == 0


def test_delete_beeminder_datapoint_success(monkeypatch):
    """Test successful deletion of a Beeminder datapoint."""
    class MockResponse:
        def raise_for_status(self):
            pass

    def fake_delete(url, params):
        assert "auth_token" in params
        assert "datapoints/test123.json" in url
        return MockResponse()

    monkeypatch.setattr(tpu.requests, "delete", fake_delete)

    result = tpu.delete_beeminder_datapoint("test123")
    assert result is True


def test_delete_beeminder_datapoint_failure(monkeypatch):
    """Test handling of deletion failure."""
    class MockResponse:
        status_code = 404

        def raise_for_status(self):
            raise tpu.requests.exceptions.HTTPError()

    def fake_delete(url, params):
        response = MockResponse()
        error = tpu.requests.exceptions.HTTPError()
        error.response = response
        raise error

    monkeypatch.setattr(tpu.requests, "delete", fake_delete)

    result = tpu.delete_beeminder_datapoint("nonexistent")
    assert result is False


def test_delete_beeminder_datapoint_network_error(monkeypatch):
    """Test handling of network errors during deletion."""
    def fake_delete(url, params):
        raise tpu.requests.exceptions.ConnectionError("Network error")

    monkeypatch.setattr(tpu.requests, "delete", fake_delete)

    result = tpu.delete_beeminder_datapoint("test123")
    assert result is False


def test_validate_and_clean_empty_beeminder_map(monkeypatch):
    """Test validation with empty Beeminder data."""
    db = {
        "datapoints": [
            {"date": "2025-08-22", "value": 1, "comment": "Local only"}
        ]
    }
    beeminder_map = {}

    deleted_count, failures = tpu.validate_and_clean_beeminder_data(db, beeminder_map)

    assert deleted_count == 0
    assert len(failures) == 0


def test_validate_and_clean_empty_database(monkeypatch):
    """Test validation with empty local database."""
    db = {"datapoints": []}
    beeminder_map = {
        "2025-08-22": {"id": "unauth1", "value": 1, "comment": "Should be deleted"},
        "2025-08-23": {"id": "unauth2", "value": 1, "comment": "Should be deleted"}
    }
    deleted_ids = []

    def fake_delete(dp_id):
        deleted_ids.append(dp_id)
        return True

    monkeypatch.setattr(tpu, "delete_beeminder_datapoint", fake_delete)

    deleted_count, failures = tpu.validate_and_clean_beeminder_data(db, beeminder_map)

    assert deleted_count == 2
    assert len(failures) == 0
    assert set(deleted_ids) == {"unauth1", "unauth2"}


def test_get_beeminder_datapoints_success(monkeypatch):
    """Test successful fetching of Beeminder datapoints."""
    mock_data = [
        {"id": "1", "timestamp": 1692748800, "value": 1, "comment": "test"},
        {"id": "2", "timestamp": 1692835200, "value": 1, "comment": "test2"}
    ]

    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return mock_data

    def fake_get(url, params):
        assert "auth_token" in params
        assert f"{tpu.BEEMINDER_USERNAME}/goals/{tpu.BEEMINDER_GOAL_SLUG}" in url
        return MockResponse()

    monkeypatch.setattr(tpu.requests, "get", fake_get)

    result = tpu.get_beeminder_datapoints()
    assert result == mock_data


def test_get_beeminder_datapoints_failure(monkeypatch):
    """Test handling of API failure when fetching datapoints."""
    class MockResponse:
        status_code = 403

        def raise_for_status(self):
            raise tpu.requests.exceptions.HTTPError()

    def fake_get(url, params):
        response = MockResponse()
        error = tpu.requests.exceptions.HTTPError()
        error.response = response
        raise error

    monkeypatch.setattr(tpu.requests, "get", fake_get)

    result = tpu.get_beeminder_datapoints()
    assert result == []


def test_get_beeminder_date_map():
    """Test conversion of Beeminder datapoints to date map."""
    tpu.TIMEZONE = "America/New_York"
    datapoints = [
        {"id": "1", "timestamp": 1692748800, "value": 1, "comment": "test1"},  # 2023-08-22
        {"id": "2", "timestamp": 1692835200, "value": 1, "comment": "test2"}   # 2023-08-23
    ]

    date_map = tpu.get_beeminder_date_map(datapoints)

    assert "2023-08-22" in date_map
    assert "2023-08-23" in date_map
    assert date_map["2023-08-22"]["id"] == "1"
    assert date_map["2023-08-23"]["id"] == "2"


def test_load_database_existing_file(monkeypatch, tmp_path):
    """Test loading existing database file."""
    db_file = tmp_path / "test_db.json"
    test_data = {
        "datapoints": [{"date": "2025-08-22", "value": 1}],
        "metadata": {"created": "2025-08-22T10:00:00"}
    }
    db_file.write_text(json.dumps(test_data))

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))

    result = tpu.load_database()
    assert result == test_data


def test_load_database_nonexistent_file(monkeypatch, tmp_path):
    """Test loading database when file doesn't exist."""
    nonexistent_file = tmp_path / "nonexistent.json"
    monkeypatch.setattr(tpu, "DB_FILE", str(nonexistent_file))

    result = tpu.load_database()
    assert "datapoints" in result
    assert "metadata" in result
    assert result["datapoints"] == []


def test_save_database(monkeypatch, tmp_path):
    """Test saving database to file."""
    db_file = tmp_path / "test_save.json"
    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))

    test_data = {
        "datapoints": [{"date": "2025-08-22", "value": 1}],
        "metadata": {"created": "2025-08-22T10:00:00"}
    }

    tpu.save_database(test_data)

    assert db_file.exists()
    saved_data = json.loads(db_file.read_text())
    assert saved_data == test_data


def test_update_last_run(monkeypatch, tmp_path):
    """Test updating last run file."""
    last_run_file = tmp_path / "test_last_run.json"
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))

    tpu.update_last_run("2025-08-22")

    assert last_run_file.exists()
    data = json.loads(last_run_file.read_text())
    assert data["last_processed_date"] == "2025-08-22"
    assert "last_run" in data


def test_ensure_directories(monkeypatch):
    """Test directory creation."""
    created_dirs = []

    def mock_mkdir(exist_ok=False):
        created_dirs.append("data")

    class MockPath:
        def __init__(self, path):
            self.path = path

        def mkdir(self, exist_ok=False):
            mock_mkdir(exist_ok)

    monkeypatch.setattr(tpu, "Path", MockPath)

    tpu.ensure_directories()

    assert "data" in created_dirs


def test_main_adds_new_datapoint(monkeypatch, tmp_path):
    """Test main workflow when adding a new datapoint."""
    # Setup mocks
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")

    # Mock database operations
    saved_data = {}
    def mock_save_database(db):
        saved_data.update(db)
    monkeypatch.setattr(tpu, "save_database", mock_save_database)

    # Mock API calls
    def mock_get_datapoints():
        return []
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", mock_get_datapoints)

    def mock_add_datapoint(date):
        return {"id": "new123", "value": 1}
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", mock_add_datapoint)

    # Mock other functions
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)
    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    # Run main
    tpu.main("2025-08-25")

    # Verify datapoint was added
    assert "datapoints" in saved_data
    assert len(saved_data["datapoints"]) == 1
    assert saved_data["datapoints"][0]["date"] == "2025-08-25"


def test_main_skips_existing_datapoint(monkeypatch, tmp_path):
    """Test main workflow when datapoint already exists."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    # Pre-populate database
    existing_db = {
        "datapoints": [{"date": "2025-08-25", "value": 1, "comment": "existing"}],
        "metadata": {"created": "2025-08-25T10:00:00"}
    }
    db_file.write_text(json.dumps(existing_db))

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")

    save_called = False
    def mock_save_database(db):
        nonlocal save_called
        save_called = True
    monkeypatch.setattr(tpu, "save_database", mock_save_database)

    # Mock API calls
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", lambda: [])
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", lambda date: {"id": "test"})
    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    tpu.main("2025-08-25")

    # Verify save was not called (no new datapoint)
    assert not save_called


def test_main_handles_api_failure(monkeypatch, tmp_path):
    """Test main workflow when API add fails."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")

    monkeypatch.setattr(tpu, "save_database", lambda db: None)
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", lambda: [])
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)

    # Mock failed API call
    def mock_add_datapoint(date):
        return None  # Simulate failure
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", mock_add_datapoint)

    last_run_called = False
    def mock_update_last_run(date):
        nonlocal last_run_called
        last_run_called = True
    monkeypatch.setattr(tpu, "update_last_run", mock_update_last_run)

    tpu.main("2025-08-25")

    # Verify last_run was not updated due to early return
    assert not last_run_called


def test_main_with_cleanup_and_sync(monkeypatch, tmp_path):
    """Test main workflow with validation cleanup and sync operations."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(tpu, "save_database", lambda db: None)

    # Track API calls
    get_datapoints_calls = []
    def mock_get_datapoints():
        get_datapoints_calls.append(1)
        return [{"id": "existing", "timestamp": 1692748800}]
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", mock_get_datapoints)

    # Mock validation that finds and deletes unauthorized data
    def mock_validate_and_clean(db, beeminder_map):
        return (2, [])  # Deleted 2 unauthorized datapoints
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", mock_validate_and_clean)

    # Mock sync that syncs historical data
    def mock_sync_datapoints(db, beeminder_map):
        return (3, ["failed_date"])  # Synced 3, failed 1
    monkeypatch.setattr(tpu, "sync_datapoints", mock_sync_datapoints)

    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", lambda date: {"id": "new"})
    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    tpu.main("2025-08-25")

    # Verify API was called 3 times (initial, after cleanup, after sync)
    assert len(get_datapoints_calls) == 3


def test_main_already_processed(monkeypatch, tmp_path):
    """Test main workflow when date already processed."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(tpu, "save_database", lambda db: None)
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", lambda: [])
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))

    # Mock already processed
    def mock_check_already_processed(date, beeminder_map, db):
        return True
    monkeypatch.setattr(tpu, "check_already_processed_date", mock_check_already_processed)

    add_called = False
    def mock_add_datapoint(date):
        nonlocal add_called
        add_called = True
        return {"id": "test"}
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", mock_add_datapoint)

    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    tpu.main("2025-08-25")

    # Verify add was not called since already processed
    assert not add_called


def test_main_datapoint_exists_in_beeminder(monkeypatch, tmp_path):
    """Test main workflow when datapoint already exists in Beeminder."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")
    monkeypatch.setattr(tpu, "save_database", lambda db: None)

    # Mock Beeminder data with existing datapoint
    def mock_get_datapoints():
        return [{"id": "existing", "timestamp": 1692748800}]
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", mock_get_datapoints)

    def mock_get_date_map(datapoints):
        return {"2025-08-25": {"id": "existing", "value": 1}}
    monkeypatch.setattr(tpu, "get_beeminder_date_map", mock_get_date_map)

    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)

    add_called = False
    def mock_add_datapoint(date):
        nonlocal add_called
        add_called = True
        return {"id": "test"}
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", mock_add_datapoint)

    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    tpu.main("2025-08-25")

    # Verify add was not called since datapoint exists in Beeminder
    assert not add_called


def test_add_beeminder_datapoint_network_error(monkeypatch):
    """Test add_beeminder_datapoint with network error."""
    def fake_post(url, json):
        raise tpu.requests.exceptions.ConnectionError("Network error")

    monkeypatch.setattr(tpu.requests, "post", fake_post)

    result = tpu.add_beeminder_datapoint("2025-08-25")
    assert result is None


def test_update_beeminder_datapoint_success(monkeypatch):
    """Test successful update of a Beeminder datapoint."""
    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "test123", "value": 2, "comment": "updated"}

    def fake_put(url, json):
        assert "auth_token" in json
        assert json["value"] == 2
        assert json["comment"] == "updated"
        return MockResponse()

    monkeypatch.setattr(tpu.requests, "put", fake_put)

    result = tpu.update_beeminder_datapoint("test123", 2, "updated")
    assert result["id"] == "test123"
    assert result["value"] == 2


def test_update_beeminder_datapoint_failure(monkeypatch):
    """Test handling of update failure."""
    class MockResponse:
        status_code = 500

        def raise_for_status(self):
            raise tpu.requests.exceptions.HTTPError()

    def fake_put(url, json):
        response = MockResponse()
        error = tpu.requests.exceptions.HTTPError()
        error.response = response
        raise error

    monkeypatch.setattr(tpu.requests, "put", fake_put)

    result = tpu.update_beeminder_datapoint("test123", 2, "updated")
    assert result is None


def test_check_already_processed_date_no_last_run_file(monkeypatch, tmp_path):
    """Test check_already_processed_date when last run file doesn't exist."""
    nonexistent_file = tmp_path / "nonexistent.json"
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(nonexistent_file))

    db = {"datapoints": [{"date": "2025-08-25", "value": 1}]}
    beeminder_map = {"2025-08-25": {"value": 1, "comment": ""}}

    result = tpu.check_already_processed_date("2025-08-25", beeminder_map, db)
    assert result is False


def test_check_already_processed_date_different_date(monkeypatch, tmp_path):
    """Test check_already_processed_date with different processed date."""
    last_run_file = tmp_path / "last_run.json"
    last_run_file.write_text(json.dumps({
        "last_run": "2025-08-24T10:00:00",
        "last_processed_date": "2025-08-24"
    }))

    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))

    db = {"datapoints": [{"date": "2025-08-25", "value": 1}]}
    beeminder_map = {"2025-08-25": {"value": 1, "comment": ""}}

    result = tpu.check_already_processed_date("2025-08-25", beeminder_map, db)
    assert result is False


def test_check_already_processed_date_missing_expected_datapoint(monkeypatch, tmp_path):
    """Test check_already_processed_date when expected datapoint missing."""
    last_run_file = tmp_path / "last_run.json"
    last_run_file.write_text(json.dumps({
        "last_run": "2025-08-25T10:00:00",
        "last_processed_date": "2025-08-25"
    }))

    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))

    db = {"datapoints": []}  # No expected datapoint
    beeminder_map = {"2025-08-25": {"value": 1, "comment": ""}}

    result = tpu.check_already_processed_date("2025-08-25", beeminder_map, db)
    assert result is False


def test_check_already_processed_date_missing_beeminder_datapoint(monkeypatch, tmp_path):
    """Test check_already_processed_date when Beeminder datapoint missing."""
    last_run_file = tmp_path / "last_run.json"
    last_run_file.write_text(json.dumps({
        "last_run": "2025-08-25T10:00:00",
        "last_processed_date": "2025-08-25"
    }))

    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))

    db = {"datapoints": [{"date": "2025-08-25", "value": 1}]}
    beeminder_map = {}  # No Beeminder datapoint

    result = tpu.check_already_processed_date("2025-08-25", beeminder_map, db)
    assert result is False


def test_main_with_trigger_date_parsing(monkeypatch, tmp_path):
    """Test main function with custom trigger date parsing."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")

    # Track the calculated Beeminder date
    calculated_date = None
    saved_data = {}

    def mock_save_database(db):
        nonlocal calculated_date
        saved_data.update(db)
        if db["datapoints"]:
            calculated_date = db["datapoints"][-1]["date"]

    monkeypatch.setattr(tpu, "save_database", mock_save_database)

    # Mock other functions
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", lambda: [])
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", lambda date: {"id": "new"})
    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    # Test with a specific trigger date
    tpu.main("2025-12-25")

    # Verify the date was correctly processed
    assert calculated_date == "2025-12-25"


def test_main_without_trigger_date(monkeypatch, tmp_path):
    """Test main function without trigger date (uses current time)."""
    db_file = tmp_path / "db.json"
    last_run_file = tmp_path / "last_run.json"

    monkeypatch.setattr(tpu, "DB_FILE", str(db_file))
    monkeypatch.setattr(tpu, "LAST_RUN_FILE", str(last_run_file))
    monkeypatch.setattr(tpu, "TIMEZONE", "America/New_York")

    # Track if save was called
    save_called = False
    def mock_save_database(db):
        nonlocal save_called
        save_called = True

    monkeypatch.setattr(tpu, "save_database", mock_save_database)

    # Mock other functions
    monkeypatch.setattr(tpu, "get_beeminder_datapoints", lambda: [])
    monkeypatch.setattr(tpu, "validate_and_clean_beeminder_data", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "sync_datapoints", lambda db, bm: (0, []))
    monkeypatch.setattr(tpu, "check_already_processed_date", lambda date, bm, db: False)
    monkeypatch.setattr(tpu, "add_beeminder_datapoint", lambda date: {"id": "new"})
    monkeypatch.setattr(tpu, "update_last_run", lambda date: None)

    # Test without trigger date
    tpu.main()

    # Verify the workflow ran (save was called)
    assert save_called
