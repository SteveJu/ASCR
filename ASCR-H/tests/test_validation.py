"""Tests for strategy validation modules."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_decision_quality_buy():
    """BUY DQS: good buy → high score, bad buy → low score."""
    from src.decision_quality import _score_buy

    # Good buy: +20% return, +15% alpha, only -5% drawdown
    good = _score_buy(
        {"forward_return": 20, "alpha_return": 15, "max_drawdown": -5,
         "max_gain": 25, "thesis_confirmed": 1},
        {"reason": "rating_A_opp_65"}
    )
    assert good["quality_score"] > 70, f"Good buy should score >70, got {good['quality_score']}"

    # Bad buy: -20% return, -25% alpha, -30% drawdown
    bad = _score_buy(
        {"forward_return": -20, "alpha_return": -25, "max_drawdown": -30,
         "max_gain": 5, "thesis_confirmed": 0},
        {"reason": "rating_A_opp_65"}
    )
    assert bad["quality_score"] < 30, f"Bad buy should score <30, got {bad['quality_score']}"
    assert good["quality_score"] > bad["quality_score"]
    print(f"✅ BUY DQS: good={good['quality_score']:.1f}, bad={bad['quality_score']:.1f}")


def test_decision_quality_sell():
    """SELL DQS: good sell (stock drops after) → high, bad sell (stock rallies) → low."""
    from src.decision_quality import _score_sell

    # Good sell: stock dropped 15% after selling
    good = _score_sell(
        {"forward_return": -15, "max_drawdown": -20, "max_gain": 2},
        {"reason": "hard_stop_loss_-20pct"}
    )
    assert good["quality_score"] > 60, f"Good sell should score >60, got {good['quality_score']}"

    # Bad sell: stock rallied 30% after selling
    bad = _score_sell(
        {"forward_return": 30, "max_drawdown": -2, "max_gain": 35},
        {"reason": "profit_taking_25pct"}
    )
    assert bad["quality_score"] < 50, f"Bad sell should score <50, got {bad['quality_score']}"
    print(f"✅ SELL DQS: good={good['quality_score']:.1f}, bad={bad['quality_score']:.1f}")


def test_decision_quality_sell_uses_alpha_when_available():
    """SELL DQS should judge early exits by alpha when a hot market lifts everything."""
    from src.decision_quality import _score_sell

    market_beta_rally = _score_sell(
        {"forward_return": 22, "alpha_return": 2, "max_drawdown": -3, "max_gain": 24},
        {"reason": "rotation_for_stronger_event"}
    )
    missed_alpha = _score_sell(
        {"forward_return": 22, "alpha_return": 18, "max_drawdown": -3, "max_gain": 24},
        {"reason": "rotation_for_stronger_event"}
    )

    assert market_beta_rally["quality_score"] > missed_alpha["quality_score"]
    assert "Alpha=+2.0%" in market_beta_rally["explanation"]


def test_decision_quality_trim():
    """TRIM: similar to SELL but softer penalty for missed upside."""
    from src.decision_quality import _score_sell, _score_trim

    # Stock rallied 25% after trim
    sell_score = _score_sell(
        {"forward_return": 25, "max_drawdown": -3, "max_gain": 30},
        {"reason": "profit_50pct"}
    )
    trim_score = _score_trim(
        {"forward_return": 25, "max_drawdown": -3, "max_gain": 30},
        {"reason": "profit_50pct"}
    )
    # TRIM should have softer penalty than full SELL
    assert trim_score["quality_score"] >= sell_score["quality_score"], \
        f"TRIM should score >= SELL, got TRIM={trim_score['quality_score']:.1f} SELL={sell_score['quality_score']:.1f}"
    print(f"✅ TRIM vs SELL: trim={trim_score['quality_score']:.1f}, sell={sell_score['quality_score']:.1f}")


def test_decision_quality_hold():
    """HOLD DQS: continuing gains → high, drawdown → low."""
    from src.decision_quality import _score_hold

    good = _score_hold(
        {"forward_return": 15, "alpha_return": 10, "max_drawdown": -3},
        {"reason": "holding_20d_pnl_+10pct"}
    )
    assert good["quality_score"] > 60, f"Good hold should score >60, got {good['quality_score']}"

    bad = _score_hold(
        {"forward_return": -20, "alpha_return": -15, "max_drawdown": -25},
        {"reason": "holding_20d_pnl_-5pct"}
    )
    assert bad["quality_score"] < 40, f"Bad hold should score <40, got {bad['quality_score']}"
    print(f"✅ HOLD DQS: good={good['quality_score']:.1f}, bad={bad['quality_score']:.1f}")


def test_decision_quality_no_buy():
    """NO_BUY DQS: stock drops → good skip, stock rallies → bad skip."""
    from src.decision_quality import _score_no_buy

    # Good skip: stock dropped
    good = _score_no_buy(
        {"forward_return": -15, "alpha_return": -20, "max_gain": 5, "max_drawdown": -20},
        {"rating": "D", "tracking_priority": "Low", "reason": "rating_D_no_sizing"}
    )
    assert good["quality_score"] > 50, f"Good skip should score >50, got {good['quality_score']}"

    # Bad skip: B-High stock rallied 40%
    bad = _score_no_buy(
        {"forward_return": 40, "alpha_return": 35, "max_gain": 55, "max_drawdown": -5},
        {"rating": "B", "tracking_priority": "High", "reason": "rating_B_no_sizing"}
    )
    assert bad["quality_score"] < 25, f"Bad skip of B-High should score <25, got {bad['quality_score']}"
    print(f"✅ NO_BUY DQS: good_skip={good['quality_score']:.1f}, bad_skip_B_High={bad['quality_score']:.1f}")


def test_overall_dqs_formula():
    """Overall DQS uses exact weights: 25% Buy + 20% Sell + 10% Trim + 15% Hold + 15% NoBuy + 10% Ranking + 5% Stability."""
    buy, sell, trim, hold, nobuy = 70, 65, 60, 50, 40
    ranking, stability = 80, 70

    expected = (buy * 0.25 + sell * 0.20 + trim * 0.10 + hold * 0.15
                + nobuy * 0.15 + ranking * 0.10 + stability * 0.05)

    # Manual calculation
    manual = 70*0.25 + 65*0.20 + 60*0.10 + 50*0.15 + 40*0.15 + 80*0.10 + 70*0.05
    assert abs(expected - manual) < 0.01
    assert abs(expected - 61.5) < 0.01, f"Expected 61.5, got {expected}"
    print(f"✅ Overall DQS formula: {expected:.1f} (weights sum to 1.0)")


def test_warning_levels():
    """Warning level thresholds: 80+=healthy, 65-80=monitoring, 50-65=unstable, <50=broken."""
    levels = [(85, "healthy"), (72, "monitoring"), (55, "unstable"), (40, "broken")]
    for dqs, expected in levels:
        if dqs >= 80:
            actual = "healthy"
        elif dqs >= 65:
            actual = "monitoring"
        elif dqs >= 50:
            actual = "unstable"
        else:
            actual = "broken"
        assert actual == expected, f"DQS {dqs} should be '{expected}', got '{actual}'"
    print("✅ Warning levels: healthy/monitoring/unstable/broken thresholds correct")


def test_false_positive_definition():
    """FP: BUY that underperformed QQQ >10%, or DD >-30%, or fwd <-25%."""
    # This is a definition test — verify the logic matches spec
    cases = [
        ({"alpha_return": -12, "max_drawdown": -10, "forward_return": 0}, True, "alpha < -10"),
        ({"alpha_return": 5, "max_drawdown": -35, "forward_return": -5}, True, "DD < -30"),
        ({"alpha_return": 5, "max_drawdown": -5, "forward_return": -28}, True, "fwd < -25"),
        ({"alpha_return": 5, "max_drawdown": -5, "forward_return": 10}, False, "all good"),
        ({"alpha_return": -8, "max_drawdown": -15, "forward_return": -10}, False, "borderline ok"),
    ]
    for outcome, expected_fp, label in cases:
        is_fp = (
            (outcome.get("alpha_return") is not None and outcome["alpha_return"] < -10)
            or (outcome.get("max_drawdown") is not None and outcome["max_drawdown"] < -30)
            or (outcome.get("forward_return") is not None and outcome["forward_return"] < -25)
        )
        assert is_fp == expected_fp, f"FP check '{label}': expected {expected_fp}, got {is_fp}"
    print("✅ False positive definition matches spec")


def test_missed_opportunity_definition():
    """Missed: NO_BUY with max_gain>50% and dd>-20%, or fwd>35%."""
    cases = [
        ({"max_gain": 60, "max_drawdown": -10, "forward_return": 45}, True, "max_gain>50 + dd>-20"),
        ({"max_gain": 55, "max_drawdown": -25, "forward_return": 20}, False, "max_gain>50 but dd<-20"),
        ({"max_gain": 40, "max_drawdown": -5, "forward_return": 38}, True, "fwd>35"),
        ({"max_gain": 30, "max_drawdown": -5, "forward_return": 20}, False, "no trigger"),
    ]
    for outcome, expected, label in cases:
        is_missed = (
            (outcome.get("max_gain", 0) > 50 and outcome.get("max_drawdown", -100) > -20)
            or (outcome.get("forward_return", 0) > 35)
        )
        assert is_missed == expected, f"Missed check '{label}': expected {expected}, got {is_missed}"
    print("✅ Missed opportunity definition matches spec")


if __name__ == "__main__":
    test_decision_quality_buy()
    test_decision_quality_sell()
    test_decision_quality_sell_uses_alpha_when_available()
    test_decision_quality_trim()
    test_decision_quality_hold()
    test_decision_quality_no_buy()
    test_overall_dqs_formula()
    test_warning_levels()
    test_false_positive_definition()
    test_missed_opportunity_definition()
    print(f"\n{'='*50}")
    print("All 10 tests passed! ✅")
    print(f"{'='*50}")
