"""Tests for universe pruning guardrails."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, universe_pruner


def test_negative_sentiment_requires_count_and_ratio():
    conn = sqlite3.connect(":memory:")
    try:
        db.ensure_events_table(conn)
        today = datetime.now().strftime("%Y-%m-%d")

        for _ in range(44):
            conn.execute(
                "INSERT INTO events (ticker, date, verdict) VALUES (?, ?, ?)",
                ("NOISY", today, "BUY"),
            )
        for _ in range(3):
            conn.execute(
                "INSERT INTO events (ticker, date, verdict) VALUES (?, ?, ?)",
                ("NOISY", today, "SELL"),
            )

        for _ in range(10):
            conn.execute(
                "INSERT INTO events (ticker, date, verdict) VALUES (?, ?, ?)",
                ("BAD", today, "HOLD"),
            )
        for _ in range(3):
            conn.execute(
                "INSERT INTO events (ticker, date, verdict) VALUES (?, ?, ?)",
                ("BAD", today, "AVOID"),
            )

        assert universe_pruner.check_negative_sentiment("NOISY", conn) is None

        trigger = universe_pruner.check_negative_sentiment("BAD", conn)
        assert trigger["trigger"] == "negative_sentiment"
        assert "(23%)" in trigger["detail"]
    finally:
        conn.close()


def test_all_negative_still_flags_small_samples():
    conn = sqlite3.connect(":memory:")
    try:
        db.ensure_events_table(conn)
        today = datetime.now().strftime("%Y-%m-%d")
        for _ in range(2):
            conn.execute(
                "INSERT INTO events (ticker, date, verdict) VALUES (?, ?, ?)",
                ("ALLBAD", today, "SELL"),
            )

        trigger = universe_pruner.check_negative_sentiment("ALLBAD", conn)
        assert trigger["trigger"] == "all_negative"
    finally:
        conn.close()


def test_d_tier_needs_enough_scoring_days_and_marks_removal_eligibility():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE scores (ticker TEXT, date TEXT, rating TEXT)")
        start = datetime(2026, 1, 1)

        for idx in range(14):
            date = (start + timedelta(days=idx)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO scores (ticker, date, rating) VALUES (?, ?, ?)",
                ("TOO_SOON", date, "D"),
            )

        for idx in range(15):
            date = (start + timedelta(days=idx)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO scores (ticker, date, rating) VALUES (?, ?, ?)",
                ("WATCH", date, "D"),
            )

        for idx in range(30):
            date = (start + timedelta(days=idx)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO scores (ticker, date, rating) VALUES (?, ?, ?)",
                ("REMOVE_ELIGIBLE", date, "D"),
            )

        assert universe_pruner.check_d_tier("TOO_SOON", conn) is None

        watch_trigger = universe_pruner.check_d_tier("WATCH", conn)
        assert watch_trigger["trigger"] == "d_tier"
        assert watch_trigger["severity"] == "low"
        assert watch_trigger["removal_eligible"] is False

        removal_trigger = universe_pruner.check_d_tier("REMOVE_ELIGIBLE", conn)
        assert removal_trigger["trigger"] == "d_tier"
        assert removal_trigger["severity"] == "medium"
        assert removal_trigger["removal_eligible"] is True
    finally:
        conn.close()


def test_evaluate_skips_excluded_from_trading_tickers():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    old_db_path = db._DB_PATH
    old_all_tickers = universe_pruner.config.all_tickers
    old_universe = universe_pruner.config.universe
    old_open_positions = universe_pruner._get_open_positions
    old_check_d_tier = universe_pruner.check_d_tier
    old_check_no_events = universe_pruner.check_no_events
    old_check_negative = universe_pruner.check_negative_sentiment
    old_check_delisted = universe_pruner.check_delisted
    try:
        db._DB_PATH = path
        db.init_db()
        universe_pruner.config.all_tickers = lambda: ["NVDA", "TEST"]
        universe_pruner.config.universe = lambda: {
            "excluded_from_trading": {"tickers": ["NVDA"]},
            "sectors": {"compute": {"tickers": ["NVDA", "TEST"]}},
        }
        universe_pruner._get_open_positions = lambda: set()
        universe_pruner.check_d_tier = lambda ticker, conn: {
            "trigger": "d_tier",
            "detail": "forced",
            "severity": "medium",
        }
        universe_pruner.check_no_events = lambda ticker, conn: None
        universe_pruner.check_negative_sentiment = lambda ticker, conn: None
        universe_pruner.check_delisted = lambda ticker, conn=None: None

        result = universe_pruner.evaluate_all()
    finally:
        db._DB_PATH = old_db_path
        universe_pruner.config.all_tickers = old_all_tickers
        universe_pruner.config.universe = old_universe
        universe_pruner._get_open_positions = old_open_positions
        universe_pruner.check_d_tier = old_check_d_tier
        universe_pruner.check_no_events = old_check_no_events
        universe_pruner.check_negative_sentiment = old_check_negative
        universe_pruner.check_delisted = old_check_delisted
        os.remove(path)

    assert "NVDA" in result["exempt_tickers"]
    assert [w["ticker"] for w in result["watch"]] == ["TEST"]
    assert result["remove"] == []


def test_ineligible_d_tier_does_not_count_toward_removal():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    old_db_path = db._DB_PATH
    old_all_tickers = universe_pruner.config.all_tickers
    old_universe = universe_pruner.config.universe
    old_open_positions = universe_pruner._get_open_positions
    old_check_d_tier = universe_pruner.check_d_tier
    old_check_no_events = universe_pruner.check_no_events
    old_check_negative = universe_pruner.check_negative_sentiment
    old_check_delisted = universe_pruner.check_delisted
    try:
        db._DB_PATH = path
        db.init_db()
        universe_pruner.config.all_tickers = lambda: ["TEST"]
        universe_pruner.config.universe = lambda: {
            "excluded_from_trading": {"tickers": []},
            "sectors": {"new": {"tickers": ["TEST"]}},
        }
        universe_pruner._get_open_positions = lambda: set()
        universe_pruner.check_d_tier = lambda ticker, conn: {
            "trigger": "d_tier",
            "detail": "young D-tier sample",
            "severity": "low",
            "removal_eligible": False,
        }
        universe_pruner.check_no_events = lambda ticker, conn: {
            "trigger": "no_events",
            "detail": "no events",
            "severity": "low",
        }
        universe_pruner.check_negative_sentiment = lambda ticker, conn: None
        universe_pruner.check_delisted = lambda ticker, conn=None: None

        result = universe_pruner.evaluate_all()
    finally:
        db._DB_PATH = old_db_path
        universe_pruner.config.all_tickers = old_all_tickers
        universe_pruner.config.universe = old_universe
        universe_pruner._get_open_positions = old_open_positions
        universe_pruner.check_d_tier = old_check_d_tier
        universe_pruner.check_no_events = old_check_no_events
        universe_pruner.check_negative_sentiment = old_check_negative
        universe_pruner.check_delisted = old_check_delisted
        os.remove(path)

    assert result["remove"] == []
    assert result["watch"][0]["ticker"] == "TEST"
    assert result["watch"][0]["trigger_count"] == 2
    assert result["watch"][0]["removal_trigger_count"] == 1
