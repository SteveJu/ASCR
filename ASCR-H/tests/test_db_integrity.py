"""Tests for ASCR-H DB accounting helpers.

These tests redirect src.config.DATA_DIR to a temporary directory before DB use.
They do not read or write the production SQLite file.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, db
from src.position_audit import audit_positions


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
        assert pos["current_value"] == 750
        assert pos["realized_pnl"] == 100
        assert pos["unrealized_pnl"] == 150
        print("OK partial reduce")

    _with_temp_db(scenario)


def test_increase_position_merges_buys_with_weighted_average():
    def scenario():
        db.increase_position("LITE", "2026-05-26", 100, 2)
        db.increase_position("LITE", "2026-05-27", 125, 2)

        pos = db.get_position("LITE")

        assert pos["quantity"] == 4
        assert pos["cost_basis"] == 450
        assert pos["avg_entry_price"] == 112.5
        assert pos["entry_date"] == "2026-05-26"
        assert pos["current_value"] == 500
        print("OK weighted average increase")

    _with_temp_db(scenario)


def test_position_audit_detects_snapshot_overwrite():
    def scenario():
        db.add_order("2026-05-26", "LITE", "BUY", 2, 100, "first")
        db.upsert_position(
            "LITE", "2026-05-26", 100, 2, 200, 200,
            max_price=100, peak_date="2026-05-26",
        )
        db.update_cash(9_800)

        db.add_order("2026-05-27", "LITE", "BUY", 2, 125, "second")
        db.upsert_position(
            "LITE", "2026-05-27", 125, 2, 250, 250,
            max_price=125, peak_date="2026-05-27",
        )
        db.update_cash(9_550)

        result = audit_positions(initial_cash=10_000)

        assert len(result["mismatches"]) == 1
        mismatch = result["mismatches"][0]
        assert mismatch["ticker"] == "LITE"
        assert mismatch["recorded_qty"] == 2
        assert mismatch["expected_qty"] == 4
        assert result["cash_diff"] == 0
        print("OK audit detects overwritten snapshot")

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
    test_increase_position_merges_buys_with_weighted_average()
    test_position_audit_detects_snapshot_overwrite()
    print("\nAll DB integrity tests passed")
