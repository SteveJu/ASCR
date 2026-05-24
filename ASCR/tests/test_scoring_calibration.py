"""Tests for scoring calibration helpers."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scoring_calibration import (
    evaluate_event_alpha_weights,
    evaluate_weights,
    optimize_event_alpha_weights,
    optimize_weights,
)


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
    assert report["grid_best"] is not None
    assert report["best"]["objective"] > 0
    assert report["best"]["ic"] > 0
    assert len(report["top_candidates"]) == 3


def test_optimize_weights_falls_back_when_surface_is_flat():
    pairs = _synthetic_pairs()
    for pair in pairs:
        pair["forward_return"] = 1.0

    report = optimize_weights(pairs, top_n=10)
    assert report["status"] == "baseline_fallback_unstable"
    assert report["stability"]["fallback_to_baseline"]
    assert report["best"] == report["baseline"]
    assert report["grid_best"] is not None


def _synthetic_event_alpha_pairs():
    pairs = []
    for date_idx, date in enumerate(["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]):
        for i in range(10):
            strong = i >= 5
            pairs.append({
                "date": date,
                "ticker": f"E{date_idx}{i}",
                "forward_return": i - 4,
                "events": [{
                    "date": date,
                    "source": "sec" if strong else "reddit",
                    "event_type": "major_contract" if strong else "other",
                    "evidence_delta": 8 if strong else 1,
                    "asymmetry_delta": 2 if strong else 0,
                    "risk_delta": 0 if strong else 3,
                    "confidence": 0.9,
                    "priced_in_pct": 10 if strong else 80,
                    "verdict": "BUY" if strong else "HOLD",
                    "conviction": "HIGH" if strong else "LOW",
                    "hash": f"{date_idx}-{i}",
                }],
            })
    return pairs


def test_event_alpha_weights_can_be_evaluated_and_optimized():
    pairs = _synthetic_event_alpha_pairs()
    event_cfg = {
        "half_life_days": 14,
        "source_weights": {"sec": 1.2, "reddit": 0.55, "other": 0.75},
        "event_type_weights": {"major_contract": 1.35, "other": 0.70},
        "verdict_scores": {"BUY": 5, "HOLD": 0},
        "conviction_weights": {"HIGH": 1.0, "LOW": 0.4},
    }

    result = evaluate_event_alpha_weights(
        pairs,
        event_cfg["source_weights"],
        event_cfg["event_type_weights"],
        event_cfg,
    )
    report = optimize_event_alpha_weights(pairs, top_n=3, event_cfg=event_cfg)

    assert result["ic"] > 0
    assert report["best"] is not None
    assert report["grid_best"] is not None
    assert len(report["top_candidates"]) == 3


if __name__ == "__main__":
    test_evaluate_weights_prefers_predictive_dimension()
    test_optimize_weights_returns_best_candidate()
    test_optimize_weights_falls_back_when_surface_is_flat()
    test_event_alpha_weights_can_be_evaluated_and_optimized()
    print("All scoring calibration tests passed!")
