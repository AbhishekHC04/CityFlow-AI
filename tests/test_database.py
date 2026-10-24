import sqlite3
import pytest
import src.database as database


@pytest.fixture
def temporary_database(tmp_path, monkeypatch):
    """Use a temporary database instead of the project's real database."""
    test_db = tmp_path / "test_cityflow.db"

    monkeypatch.setattr(database, "DB_PATH", test_db)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)

    database.init_db()
    return test_db


def sample_record(**overrides):
    record = {
        "name": "Test Road",
        "lat": 12.9716,
        "lon": 77.5946,
        "current_speed_kmh": 25.0,
        "free_flow_speed_kmh": 60.0,
        "travel_time_seconds": 180.0,
        "free_flow_time_seconds": 75.0,
        "score": 65.0,
        "status": "Heavy",
        "confidence": 0.95,
        "road_closed": False,
        "source": "test",
        "simulated": False,
        "retrieved_at_utc": "2026-10-24T10:00:00+00:00",
    }
    record.update(overrides)
    return record


def test_init_db_creates_table(temporary_database):
    with sqlite3.connect(temporary_database) as connection:
        result = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='traffic_history'"
        ).fetchone()

    assert result is not None


def test_save_one_observation(temporary_database):
    inserted = database.save_observations([sample_record()])

    assert inserted == 1
    assert database.get_history_count() == 1


def test_save_multiple_observations(temporary_database):
    records = [
        sample_record(name="Road A"),
        sample_record(name="Road B"),
        sample_record(name="Road C"),
    ]

    assert database.save_observations(records) == 3
    assert database.get_history_count() == 3


def test_empty_records_do_not_insert_rows(temporary_database):
    assert database.save_observations([]) == 0
    assert database.get_history_count() == 0


def test_history_contains_expected_columns(temporary_database):
    database.save_observations([sample_record()])

    history = database.get_history()

    expected_columns = {
        "id",
        "location_name",
        "latitude",
        "longitude",
        "current_speed_kmh",
        "free_flow_speed_kmh",
        "congestion_score",
        "congestion_status",
        "source",
        "simulated",
        "observed_at_utc",
    }

    assert expected_columns.issubset(set(history.columns))
    assert len(history) == 1
    assert history.iloc[0]["location_name"] == "Test Road"


def test_simulated_records_are_excluded_by_default(temporary_database):
    records = [
        sample_record(name="Live Road", simulated=False),
        sample_record(name="Demo Road", simulated=True),
    ]

    database.save_observations(records)

    assert database.get_history_count() == 1
    assert database.get_history_count(include_simulated=True) == 2

    live_history = database.get_history()
    all_history = database.get_history(include_simulated=True)

    assert "Demo Road" not in live_history["location_name"].tolist()
    assert "Demo Road" in all_history["location_name"].tolist()


def test_history_limit_is_respected(temporary_database):
    records = [
        sample_record(
            name=f"Road {index}",
            retrieved_at_utc=f"2026-10-24T10:{index:02d}:00+00:00",
        )
        for index in range(5)
    ]

    database.save_observations(records)

    assert len(database.get_history(limit=2)) == 2


def test_saved_values_are_preserved(temporary_database):
    record = sample_record()
    database.save_observations([record])

    history = database.get_history()
    saved = history.iloc[0]

    assert saved["location_name"] == "Test Road"
    assert saved["latitude"] == pytest.approx(12.9716)
    assert saved["longitude"] == pytest.approx(77.5946)
    assert saved["current_speed_kmh"] == pytest.approx(25.0)
    assert saved["free_flow_speed_kmh"] == pytest.approx(60.0)
    assert saved["congestion_score"] == pytest.approx(65.0)
    assert saved["source"] == "test"


def test_history_count_increases_after_insert(temporary_database):
    assert database.get_history_count() == 0

    database.save_observations([sample_record()])
    assert database.get_history_count() == 1

    database.save_observations([sample_record(name="Second Road")])
    assert database.get_history_count() == 2


def test_invalid_record_does_not_insert_partial_batch(temporary_database):
    records = [
        sample_record(name="Valid Road"),
        {"name": "Invalid Road"},
    ]

    with pytest.raises((KeyError, TypeError, ValueError)):
        database.save_observations(records)

    assert database.get_history_count(include_simulated=True) == 0
