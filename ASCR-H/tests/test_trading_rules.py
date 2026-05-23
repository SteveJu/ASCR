"""Tests for executor-side trading constraints.

These tests use a temporary SQLite database and do not touch production state.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_rules import (
    check_daily_turnover,
    check_duplicate_buy,
    check_pdt_rule,
    is_market_open,
    is_trading_day,
)


def _make_orders_db():
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "ascr_h.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE paper_orders (
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return tmp, path


def _insert_order(path, date, ticker, side, quantity=1, price=100):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO paper_orders (date, ticker, side, quantity, price) VALUES (?, ?, ?, ?, ?)",
        (date, ticker, side, quantity, price),
    )
    conn.commit()
    conn.close()


def test_market_calendar_rules():
    assert is_trading_day(datetime(2026, 5, 23, 10, 0))[0] is False
    assert is_trading_day(datetime(2026, 5, 25, 10, 0))[0] is False
    assert is_market_open(datetime(2026, 5, 26, 9, 29))[0] is False
    assert is_market_open(datetime(2026, 5, 26, 9, 30))[0] is True
    assert is_market_open(datetime(2026, 5, 26, 16, 0))[0] is False
    print("OK market calendar rules")


def test_duplicate_buy_blocks_same_day_rebuy():
    tmp, path = _make_orders_db()
    try:
        _insert_order(path, "2026-05-26", "NVDA", "BUY")
        ok, reason = check_duplicate_buy("NVDA", path, "2026-05-26")
        assert ok is False
        assert "already bought" in reason
        ok, _ = check_duplicate_buy("NVDA", path, "2026-05-27")
        assert ok is True
        print("OK duplicate buy rule")
    finally:
        tmp.cleanup()


def test_pdt_rule_blocks_normal_same_day_trade_but_allows_urgent_with_budget():
    tmp, path = _make_orders_db()
    try:
        _insert_order(path, "2026-05-26", "VRT", "BUY")

        ok, reason = check_pdt_rule(10_000, "VRT", "SELL", path, "2026-05-26")
        assert ok is False
        assert "would use day trade" in reason

        ok, reason = check_pdt_rule(10_000, "VRT", "SELL", path, "2026-05-26", urgency="urgent")
        assert ok is True
        assert "urgent" in reason

        ok, reason = check_pdt_rule(30_000, "VRT", "SELL", path, "2026-05-26")
        assert ok is True
        assert "PDT exempt" in reason
        print("OK PDT rule")
    finally:
        tmp.cleanup()


def test_daily_turnover_blocks_above_limit():
    tmp, path = _make_orders_db()
    try:
        _insert_order(path, "2026-05-26", "A", "BUY", quantity=10, price=100)
        _insert_order(path, "2026-05-26", "B", "BUY", quantity=10, price=100)

        ok, reason = check_daily_turnover("BUY", 900, 10_000, path, "2026-05-26")
        assert ok is True
        assert "29%" in reason

        ok, reason = check_daily_turnover("BUY", 1_100, 10_000, path, "2026-05-26")
        assert ok is False
        assert "turnover 31%" in reason
        print("OK daily turnover rule")
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    test_market_calendar_rules()
    test_duplicate_buy_blocks_same_day_rebuy()
    test_pdt_rule_blocks_normal_same_day_trade_but_allows_urgent_with_budget()
    test_daily_turnover_blocks_above_limit()
    print("\nAll trading rule tests passed")
