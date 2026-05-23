"""Tests for scoring engine."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from src.scoring import (
    _momentum_score,
    _asymmetry_score,
    _risk_score,
    _evidence_score_basic,
    _event_alpha_score,
)

def test_asymmetry_small_cap():
    info = {"market_cap": 5e9, "analyst_count": 3, "revenue_growth": 0.4}
    score, details = _asymmetry_score("TEST", info)
    assert score >= 40, f"Small cap + low coverage + high growth should score >= 40, got {score}"
    print(f"✅ Asymmetry small cap: {score} {details}")

def test_risk_high_debt():
    info = {"debt_to_equity": 250, "gross_margin": -0.05, "profit_margin": -0.1}
    score, details = _risk_score("TEST", info)
    assert score >= 50, f"High debt + negative margins should score >= 50, got {score}"
    print(f"✅ Risk high debt: {score} {details}")

def test_evidence_strong_growth():
    info = {"revenue_growth": 0.6, "gross_margin": 0.5, "profit_margin": 0.15, "forward_pe": 20, "pe_ratio": 35}
    score, details = _evidence_score_basic("TEST", info)
    assert score >= 50, f"Strong growth should score >= 50, got {score}"
    print(f"✅ Evidence strong: {score} {details}")

def test_event_alpha_rewards_fresh_high_confidence_contract():
    today = datetime(2026, 5, 23)
    events = [{
        "date": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
        "source": "sec",
        "event_type": "major_contract",
        "headline": "TEST signs multi-year AI infrastructure contract",
        "evidence_delta": 8,
        "asymmetry_delta": 4,
        "risk_delta": -2,
        "confidence": 0.9,
        "priced_in_pct": 20,
        "verdict": "BUY",
        "conviction": "HIGH",
        "hash": "contract-1",
    }]
    cfg = {
        "half_life_days": 14,
        "min_confidence": 0.25,
        "source_weights": {"sec": 1.2, "other": 0.75},
        "event_type_weights": {"major_contract": 1.35, "other": 0.7},
        "verdict_scores": {"BUY": 5},
        "conviction_weights": {"HIGH": 1.0},
        "max_evidence_adjustment": 45,
        "max_asymmetry_adjustment": 35,
        "max_risk_adjustment": 40,
    }
    adj, details = _event_alpha_score(events, cfg, as_of=today)
    assert adj["evidence"] > 8, f"contract + BUY verdict should add meaningful evidence, got {adj}"
    assert adj["asymmetry"] > 0, f"contract should add asymmetry, got {adj}"
    assert adj["risk"] < 0, f"negative risk_delta should reduce risk, got {adj}"
    assert details["used_events"] == 1
    print(f"✅ Event alpha contract: {adj} {details}")

def test_event_alpha_penalizes_priced_in_duplicate_news():
    today = datetime(2026, 5, 23)
    base = {
        "date": (today - timedelta(days=20)).strftime("%Y-%m-%d"),
        "source": "reddit",
        "event_type": "other",
        "headline": "old repeated hype",
        "evidence_delta": 8,
        "asymmetry_delta": 4,
        "risk_delta": 0,
        "confidence": 0.5,
        "priced_in_pct": 95,
        "verdict": "BUY",
        "conviction": "LOW",
        "hash": "same",
    }
    cfg = {
        "half_life_days": 10,
        "min_confidence": 0.25,
        "source_weights": {"reddit": 0.55, "other": 0.75},
        "event_type_weights": {"other": 0.7},
        "verdict_scores": {"BUY": 5},
        "conviction_weights": {"LOW": 0.4},
        "max_evidence_adjustment": 45,
        "max_asymmetry_adjustment": 35,
        "max_risk_adjustment": 40,
    }
    adj, details = _event_alpha_score([base, dict(base)], cfg, as_of=today)
    assert 0 < adj["evidence"] < 3, f"stale/priced-in duplicate hype should be heavily discounted, got {adj}"
    assert details["used_events"] == 2
    print(f"✅ Event alpha duplicate discount: {adj} {details}")

if __name__ == "__main__":
    test_asymmetry_small_cap()
    test_risk_high_debt()
    test_evidence_strong_growth()
    test_event_alpha_rewards_fresh_high_confidence_contract()
    test_event_alpha_penalizes_priced_in_duplicate_news()
    print("\nAll scoring tests passed! ✅")
