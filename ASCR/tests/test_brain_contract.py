"""Contract tests for ASCR as the decision brain."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_portfolio_instructions_contract_for_executor():
    """The brain returns intent; the executor decides sizing and order mechanics."""
    from src import recommender
    import src.bubble_detector as bubble_detector

    old_rankings = recommender.get_rankings
    old_sell_signals = recommender.check_sell_signals
    old_bubble = bubble_detector.check_bubble_burst
    try:
        bubble_detector.check_bubble_burst = lambda **kwargs: {
            "level": "NORMAL",
            "action": "none",
            "stats": {},
        }
        recommender.check_sell_signals = lambda ticker: {
            "should_sell": False,
            "reason": "",
            "total_neg_ev": 0,
            "total_risk": 0,
        }
        recommender.get_rankings = lambda **kwargs: [
            {
                "ticker": "MU",
                "ev_score": 8,
                "verdict_score": 6,
                "top_summary": "HBM demand improving",
            },
            {
                "ticker": "OLD",
                "ev_score": 6,
                "verdict_score": 2,
                "top_summary": "Existing position",
            },
        ]

        result = recommender.get_portfolio_instructions(
            {
                "OLD": {
                    "quantity": 10,
                    "avg_entry_price": 100,
                    "peak_price": 110,
                    "current_price": 105,
                }
            },
            max_pos=2,
        )
    finally:
        recommender.get_rankings = old_rankings
        recommender.check_sell_signals = old_sell_signals
        bubble_detector.check_bubble_burst = old_bubble

    assert set(result) == {
        "sells", "buys", "holds", "add_capital_suggestions", "rankings", "bubble",
    }
    assert result["sells"] == []
    assert result["buys"][0]["ticker"] == "MU"
    assert result["buys"][0]["reason"].startswith("rank#1_ev")
    assert "ev_score" in result["buys"][0]
    assert "verdict_score" in result["buys"][0]
    assert "thesis" in result["buys"][0]
    assert "shares" not in result["buys"][0]
    assert "order_type" not in result["buys"][0]
    assert isinstance(result["add_capital_suggestions"], list)
    assert result["holds"][0]["ticker"] == "OLD"


def test_meltdown_contract_sells_everything_and_buys_nothing():
    from src import recommender
    import src.bubble_detector as bubble_detector

    old_bubble = bubble_detector.check_bubble_burst
    try:
        bubble_detector.check_bubble_burst = lambda **kwargs: {
            "level": "MELTDOWN",
            "action": "liquidate",
            "stats": {"pct_declining": 90},
        }

        result = recommender.get_portfolio_instructions(
            {
                "A": {"avg_entry_price": 10, "current_price": 8},
                "B": {"avg_entry_price": 20, "current_price": 30},
            },
            max_pos=10,
        )
    finally:
        bubble_detector.check_bubble_burst = old_bubble

    assert {s["ticker"] for s in result["sells"]} == {"A", "B"}
    assert all(s["urgency"] == "critical" for s in result["sells"])
    assert result["buys"] == []
    assert result["holds"] == []
    assert result["add_capital_suggestions"] == []
    assert result["rankings"] == []


if __name__ == "__main__":
    test_portfolio_instructions_contract_for_executor()
    test_meltdown_contract_sells_everything_and_buys_nothing()
    print("Brain contract tests passed")
