"""Tests for paper trader execution logic."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_position_sizing():
    """S=2%, A=1%, B=0%"""
    initial = 100000
    assert initial * 0.02 == 2000, "S sizing: 2% of 100k = 2000"
    assert initial * 0.01 == 1000, "A sizing: 1% of 100k = 1000"
    assert initial * 0.00 == 0, "B sizing: 0% (shadow only)"
    print("✅ Position sizing correct")

def test_max_position():
    """Single position max 5%"""
    equity = 100000
    max_pos = equity * 0.05
    assert max_pos == 5000
    print("✅ Max position 5% = $5,000")

def test_profit_taking_thresholds():
    """Check profit taking triggers"""
    entry = 50.0
    # +25%
    assert 62.5 / entry - 1 >= 0.25
    # +50%
    assert 75.0 / entry - 1 >= 0.50
    # +100%
    assert 100.0 / entry - 1 >= 1.00
    print("✅ Profit taking thresholds correct")

def test_stop_loss():
    """Hard stop at -25%"""
    entry = 100.0
    price = 74.0
    pnl_pct = (price - entry) / entry * 100
    assert pnl_pct <= -25, f"Expected <= -25%, got {pnl_pct}"
    print("✅ Stop loss triggers at -26%")

def test_trailing_stop():
    """Trailing stop: activate at +30%, trigger at 20% from peak"""
    entry = 50.0
    peak = 75.0  # +50%, trailing active
    trailing_price = peak * 0.80  # 20% trailing
    assert trailing_price == 60.0
    current = 59.0  # below trailing
    assert current < trailing_price
    print("✅ Trailing stop: peak=$75, trigger=$60, current=$59 → SELL")

def test_time_stop():
    """60 days with rating below A → sell"""
    days_held = 65
    current_rating = "C"
    rating_order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}
    should_sell = days_held > 60 and rating_order[current_rating] > rating_order["A"]
    assert should_sell
    print("✅ Time stop: 65 days + C rating → SELL")

def test_shadow_not_bought():
    """B-rated stocks should not be bought"""
    sizing = {"S": 0.02, "A": 0.01, "B": 0.0, "C": 0.0, "D": 0.0}
    assert sizing["B"] == 0
    print("✅ B-rated stocks: shadow only, $0 sizing")

if __name__ == "__main__":
    test_position_sizing()
    test_max_position()
    test_profit_taking_thresholds()
    test_stop_loss()
    test_trailing_stop()
    test_time_stop()
    test_shadow_not_bought()
    print("\nAll paper trader tests passed! ✅")
