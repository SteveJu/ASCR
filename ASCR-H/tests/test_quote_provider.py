"""Tests for ASCR-H quote-aware position display."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_position_status_rows_use_quote_previous_close():
    from src.main import _position_status_rows

    positions = [{
        "ticker": "VRT",
        "quantity": 10,
        "avg_entry_price": 50,
        "cost_basis": 500,
        "current_value": 500,
    }]

    def fake_quote(ticker):
        assert ticker == "VRT"
        return {"price": 110.0, "previous_close": 100.0}

    rows, total_value, total_prev_value = _position_status_rows(positions, quote_func=fake_quote)

    assert total_value == 1100
    assert total_prev_value == 1000
    assert "+10.0%" in rows[0]
    assert "+120.0%" in rows[0]


if __name__ == "__main__":
    test_position_status_rows_use_quote_previous_close()
    print("Quote provider tests passed")
