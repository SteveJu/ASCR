"""Tests for the shared events table schema."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _with_temp_db(fn):
    from src import db

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        return fn(path)
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)


def _event_columns(path):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    finally:
        conn.close()


def test_events_schema_is_compatible_when_db_initializes_first():
    from src import db, event_pipeline

    def run(path):
        db.init_db()
        event_pipeline.init_events_db()
        return _event_columns(path)

    columns = _with_temp_db(run)

    assert {"date", "event_date", "headline", "title"} <= columns
    assert {"evidence_delta", "verdict", "investment_thesis", "hash"} <= columns
    assert {"raw_llm_json", "llm_json"} <= columns


def test_events_schema_is_compatible_when_pipeline_initializes_first():
    from src import db, event_pipeline

    def run(path):
        event_pipeline.init_events_db()
        db.init_db()
        return _event_columns(path)

    columns = _with_temp_db(run)

    assert {"date", "event_date", "headline", "title"} <= columns
    assert {"evidence_delta", "verdict", "investment_thesis", "hash"} <= columns
    assert {"raw_llm_json", "llm_json"} <= columns


def test_legacy_add_event_populates_new_and_legacy_title_fields():
    from src import db

    def run(path):
        db.init_db()
        db.add_event("MU", "2026-05-23", "unit", "HBM headline", event_type="news")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT date, event_date, headline, title FROM events WHERE ticker='MU'"
            ).fetchone()
        finally:
            conn.close()

    row = _with_temp_db(run)

    assert row["date"] == "2026-05-23"
    assert row["event_date"] == "2026-05-23"
    assert row["headline"] == "HBM headline"
    assert row["title"] == "HBM headline"


if __name__ == "__main__":
    test_events_schema_is_compatible_when_db_initializes_first()
    test_events_schema_is_compatible_when_pipeline_initializes_first()
    test_legacy_add_event_populates_new_and_legacy_title_fields()
    print("Event schema tests passed")
