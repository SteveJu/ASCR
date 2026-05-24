"""Contract tests for event daemon storage behavior."""
from datetime import datetime
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_store_events_inserts_actionable_brain_signal_once():
    from src import db, event_daemon

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        db.init_db()

        event = {
            "ticker": "MU",
            "source": "unit",
            "headline": "Micron signs new HBM supply agreement",
            "event_type": "contract",
            "is_ai_related": 1,
            "supply_chain_area": "memory",
            "evidence_delta": 7,
            "asymmetry_delta": 3,
            "risk_delta": 0,
            "summary": "Specific AI memory contract signal",
            "confidence": 0.9,
            "verdict": "BUY",
            "conviction": "HIGH",
            "investment_thesis": "HBM demand visibility improves",
            "upside_potential": "20-50%",
            "moat": "HBM supply",
            "catalyst": "Contract announcement",
        }

        first = event_daemon._store_events([event])
        second = event_daemon._store_events([event])

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT ticker, headline, evidence_delta, verdict, investment_thesis FROM events"
            ).fetchall()
        finally:
            conn.close()
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)

    assert len(first) == 1
    assert second == []
    assert len(rows) == 1
    assert rows[0]["ticker"] == "MU"
    assert rows[0]["headline"] == "Micron signs new HBM supply agreement"
    assert rows[0]["evidence_delta"] == 7
    assert rows[0]["verdict"] == "BUY"
    assert rows[0]["investment_thesis"] == "HBM demand visibility improves"


def test_get_seen_hashes_only_reads_today():
    from src import db, event_daemon

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        db.init_db()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (hash, date, ticker, headline) VALUES (?, ?, ?, ?)",
                ("today-hash", "2026-05-23", "MU", "today"),
            )
            conn.execute(
                "INSERT INTO events (hash, date, ticker, headline) VALUES (?, ?, ?, ?)",
                ("old-hash", "2026-05-22", "MU", "old"),
            )

        with db.get_conn() as conn:
            seen = event_daemon._get_seen_hashes(conn, "2026-05-23")
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)

    assert seen == {"today-hash"}


def test_market_hours_use_dynamic_us_market_holidays():
    from src import event_daemon
    from src.market_calendar import us_market_holidays

    assert event_daemon._is_active_hours(datetime(2026, 5, 22, 9, 0))
    assert not event_daemon._is_active_hours(datetime(2026, 5, 22, 8, 59))
    assert not event_daemon._is_active_hours(datetime(2026, 5, 23, 12, 0))
    assert not event_daemon._is_active_hours(datetime(2027, 3, 26, 12, 0))

    holidays_2027 = {day.isoformat() for day in us_market_holidays(2027)}
    assert "2027-03-26" in holidays_2027  # Good Friday, not a federal holiday.
    assert "2027-10-11" not in holidays_2027  # Columbus Day is open for US equities.


if __name__ == "__main__":
    test_store_events_inserts_actionable_brain_signal_once()
    test_get_seen_hashes_only_reads_today()
    test_market_hours_use_dynamic_us_market_holidays()
    print("Event daemon contract tests passed")
