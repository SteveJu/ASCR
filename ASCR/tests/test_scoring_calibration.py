"""Tests for scoring calibration helpers."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scoring_calibration import evaluate_weights, optimize_weights


def _synthetic_pairs():
    pairs = []
    for date_idx, date in enumerate(["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]):
        for i in range(10):
            evidence = i * 10
            asymmetry = 50
            momentum = 100 - i * 10
            risk = 10
            pairs.append({
                "date": date,
                "ticker": f"T{date_idx}{i}",
                "evidence_score": evidence,
                "asymmetry_score": asymmetry,
                "momentum_score": momentum,
                "risk_score": risk,
                "forward_return": i - 4,
            })
    return pairs


def test_evaluate_weights_prefers_predictive_dimension():
    pairs = _synthetic_pairs()
    evidence_weights = {"evidence": 0.5, "asymmetry": 0.1, "momentum": 0.1, "risk": -0.1}
    momentum_weights = {"evidence": 0.1, "asymmetry": 0.1, "momentum": 0.5, "risk": -0.1}
    ev_result = evaluate_weights(pairs, evidence_weights)
    mo_result = evaluate_weights(pairs, momentum_weights)
    assert ev_result["ic"] > mo_result["ic"], f"evidence should win in synthetic data: {ev_result} vs {mo_result}"
    assert ev_result["top5_spread"] > mo_result["top5_spread"]


def test_optimize_weights_returns_best_candidate():
    report = optimize_weights(_synthetic_pairs(), top_n=3)
    assert report["best"] is not None
    assert report["best"]["objective"] > 0
    assert report["best"]["ic"] > 0
    assert len(report["top_candidates"]) == 3


if __name__ == "__main__":
    test_evaluate_weights_prefers_predictive_dimension()
    test_optimize_weights_returns_best_candidate()
    print("All scoring calibration tests passed!")
