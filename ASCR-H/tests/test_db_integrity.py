"""Tests for ASCR-H DB accounting helpers.

These tests redirect src.config.DATA_DIR to a temporary directory before DB use.
They do not read or write the production SQLite file.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, db


def _with_temp_db(test_func):
    original_data_dir = config.DATA_DIR
    original_journal_state = db._journal_mode_initialized
    tmp = tempfile.TemporaryDirectory()
    try:
        config.DATA_DIR = tmp.name
        db._journal_mode_initialized = False
        db.init_db()
        db.init_account(10_000)
        test_func()
    finally:
        config.DATA_DIR = original_data_dir
        db._journal_mode_initialized = original_journal_state
        tmp.cleanup()


def test_reduce_position_partial_sale_keeps_position_open():
    def scenario():
        db.upsert_position(
            "VRT", "2026-05-26", 100, 10, 1_000, 1_000,
            max_price=100, peak_date="2026-05-26",
        )

        realized = db.reduce_position("VRT", 4, 125)
        pos = db.get_position("VRT")

        assert realized == 100
        assert pos is not None
        assert pos["quantity"] == 6
        assert pos["cost_basis"] == 600
        assert pos["realized_pnl"] == 100
        print("OK partial reduce")

    _with_temp_db(scenario)


def test_reduce_position_full_sale_closes_without_nested_connection():
    def scenario():
        db.upsert_position(
            "SMH", "2026-05-26", 50, 2, 100, 100,
            max_price=50, peak_date="2026-05-26",
        )

        realized = db.reduce_position("SMH", 2, 40)
        open_pos = db.get_position("SMH")
        closed = db.get_all_positions("closed")

        assert realized == -20
        assert open_pos is None
        assert len(closed) == 1
        assert closed[0]["ticker"] == "SMH"
        assert closed[0]["quantity"] == 0
        assert closed[0]["realized_pnl"] == -20
        print("OK full reduce close")

    _with_temp_db(scenario)


if __name__ == "__main__":
    test_reduce_position_partial_sale_keeps_position_open()
    test_reduce_position_full_sale_closes_without_nested_connection()
    print("\nAll DB integrity tests passed")
