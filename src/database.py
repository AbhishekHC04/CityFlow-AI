
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

# Project root: the folder containing app.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "cityflow.db"


def get_connection():
    """Open a SQLite connection and enable named-column access."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    """Create the traffic history table and its indexes."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS traffic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                current_speed_kmh REAL,
                free_flow_speed_kmh REAL,
                travel_time_seconds REAL,
                free_flow_time_seconds REAL,
                congestion_score REAL,
                congestion_status TEXT,
                confidence REAL,
                road_closed INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                simulated INTEGER NOT NULL DEFAULT 0,
                observed_at_utc TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_traffic_history_location_time
            ON traffic_history (location_name, observed_at_utc)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_traffic_history_time
            ON traffic_history (observed_at_utc)
        """)


def save_observations(records):
    """
    Save a batch of traffic readings.
    Returns the number of rows inserted.
    """
    if not records:
        return 0

    rows = []

    for item in records:
        observed_at = (
            item.get("retrieved_at_utc")
            or datetime.now(timezone.utc).isoformat()
        )

        rows.append((
            item["name"],
            float(item["lat"]),
            float(item["lon"]),
            item.get("current_speed_kmh"),
            item.get("free_flow_speed_kmh"),
            item.get("travel_time_seconds"),
            item.get("free_flow_time_seconds"),
            item.get("score"),
            item.get("status", "Unknown"),
            item.get("confidence"),
            int(bool(item.get("road_closed", False))),
            item.get("source", "Unknown"),
            int(bool(item.get("simulated", False))),
            observed_at,
        ))

    with get_connection() as connection:
        connection.executemany("""
            INSERT INTO traffic_history (
                location_name,
                latitude,
                longitude,
                current_speed_kmh,
                free_flow_speed_kmh,
                travel_time_seconds,
                free_flow_time_seconds,
                congestion_score,
                congestion_status,
                confidence,
                road_closed,
                source,
                simulated,
                observed_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    return len(rows)


def get_history(limit=5000, include_simulated=False):
    """Return recent observations as a pandas DataFrame."""
    limit = max(1, min(int(limit), 100000))

    query = """
        SELECT
            id,
            location_name,
            latitude,
            longitude,
            current_speed_kmh,
            free_flow_speed_kmh,
            travel_time_seconds,
            free_flow_time_seconds,
            congestion_score,
            congestion_status,
            confidence,
            road_closed,
            source,
            simulated,
            observed_at_utc
        FROM traffic_history
    """

    if not include_simulated:
        query += " WHERE simulated = 0"

    query += " ORDER BY observed_at_utc DESC, id DESC LIMIT ?"

    with get_connection() as connection:
        return pd.read_sql_query(
            sql=query,
            con=connection,
            params=(limit,)
    )

def get_history_count(include_simulated=False):
    """Count saved traffic observations."""
    query = "SELECT COUNT(*) FROM traffic_history"

    if not include_simulated:
        query += " WHERE simulated = 0"

    with get_connection() as connection:
        return connection.execute(query).fetchone()[0]


def clear_history():
    """Delete saved observations. Use only when explicitly needed."""
    with get_connection() as connection:
        connection.execute("DELETE FROM traffic_history")


# Create the database and table when this module is imported.
init_db()
