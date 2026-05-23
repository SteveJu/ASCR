"""Tests for exit rules — unit tests without DB dependency."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exit_rules import _evaluate_single, _determine_position_status

def _make_pos(**kwargs):
    base = {
        "id": 1, "ticker": "TEST", "current_price": 10, "entry_price": 10,
        "pnl_pct": 0, "drawdown_from_high": 0, "max_price_since_entry": 10,
        "trailing_stop_pct": 15, "max_loss_pct": 25, "entry_date": "2026-01-01",
    }
    base.update(kwargs)
    return base

def test_hard_stop_loss():
    pos = _make_pos(current_price=7, pnl_pct=-30)
    signals = _evaluate_single(pos)
    types = [s["type"] for s in signals]
    assert "HARD_STOP_LOSS" in types, f"Expected HARD_STOP_LOSS, got {types}"
    status = _determine_position_status(signals)
    assert status == "Sell All"
    print(f"✅ Hard stop loss: {types} → {status}")

def test_soft_stop():
    pos = _make_pos(current_price=8.2, pnl_pct=-18)
    signals = _evaluate_single(pos)
    types = [s["type"] for s in signals]
    assert "SOFT_STOP_REVIEW" in types
    print(f"✅ Soft stop review: {types}")

def test_profit_25():
    pos = _make_pos(current_price=13, entry_price=10, pnl_pct=30, max_price_since_entry=13)
    signals = _evaluate_single(pos)
    types = [s["type"] for s in signals]
    assert "PROFIT_25PCT" in types
    print(f"✅ Profit 25%: {types}")

def test_profit_100_recover_cost():
    pos = _make_pos(current_price=22, entry_price=10, pnl_pct=120, max_price_since_entry=22)
    signals = _evaluate_single(pos)
    types = [s["type"] for s in signals]
    assert "PROFIT_100PCT" in types
    print(f"✅ Profit 100% (recover cost): {types}")

def test_trailing_stop():
    pos = _make_pos(current_price=12, entry_price=8, pnl_pct=50,
                    max_price_since_entry=16, drawdown_from_high=-25)
    signals = _evaluate_single(pos)
    types = [s["type"] for s in signals]
    assert "TRAILING_STOP" in types
    print(f"✅ Trailing stop: {types}")

def test_time_stop():
    pos = _make_pos(pnl_pct=3, entry_date="2025-08-01")
    signals = _evaluate_single(pos)
    types = [s["type"] for s in signals]
    assert "TIME_STOP" in types
    print(f"✅ Time stop: {types}")

if __name__ == "__main__":
    test_hard_stop_loss()
    test_soft_stop()
    test_profit_25()
    test_profit_100_recover_cost()
    test_trailing_stop()
    test_time_stop()
    print("\nAll exit rule tests passed! ✅")
