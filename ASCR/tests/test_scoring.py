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
    _valuation_overlay,
    _business_quality_overlay,
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

def test_valuation_overlay_penalizes_extreme_multiples():
    info = {
        "forward_pe": 85,
        "price_to_sales": 18,
        "ev_to_sales": 22,
        "ev_to_ebitda": 48,
        "market_cap": 100e9,
        "free_cashflow": 1e9,
    }
    adj, details = _valuation_overlay("TEST", info)
    assert adj["asymmetry"] < 0, f"Extreme multiples should reduce asymmetry, got {adj}"
    assert adj["risk"] > 0, f"Extreme multiples should increase risk, got {adj}"
    assert details["label"] in ("premium", "extreme_premium")
    print(f"✅ Valuation premium penalty: {adj} {details}")

def test_valuation_overlay_rewards_reasonable_cash_flow():
    info = {
        "forward_pe": 18,
        "price_to_sales": 3,
        "ev_to_sales": 3.5,
        "ev_to_ebitda": 12,
        "market_cap": 20e9,
        "free_cashflow": 1.2e9,
    }
    adj, details = _valuation_overlay("TEST", info)
    assert adj["asymmetry"] > 0, f"Reasonable valuation should support asymmetry, got {adj}"
    assert adj["risk"] <= 0, f"Reasonable valuation should not add risk, got {adj}"
    assert details["label"] == "discount"
    print(f"✅ Valuation support: {adj} {details}")

def test_valuation_overlay_does_not_over_penalize_high_pe_alone():
    info = {"forward_pe": 95}
    adj, details = _valuation_overlay("TEST", info)
    assert adj["asymmetry"] >= -4, f"High P/E alone should be weak signal, got {adj}"
    assert adj["risk"] <= 4, f"High P/E alone should not dominate risk, got {adj}"
    assert details["pe_deemphasized"] is True
    assert details["label"] == "reasonable"
    print(f"✅ P/E de-emphasized: {adj} {details}")

def test_business_quality_penalizes_weak_levered_company():
    info = {
        "gross_margin": 0.12,
        "operating_margin": -0.08,
        "profit_margin": -0.10,
        "revenue": 1e9,
        "free_cashflow": -100e6,
        "total_debt": 1.5e9,
        "total_cash": 100e6,
        "ebitda": 100e6,
        "return_on_equity": -0.20,
        "revenue_growth": -0.05,
    }
    adj, details = _business_quality_overlay("TEST", info)
    assert details["quality_score"] < 35, f"Weak business should score poor, got {details}"
    assert adj["evidence"] < 0, f"Weak quality should reduce evidence, got {adj}"
    assert adj["risk"] > 0, f"Weak quality should increase risk, got {adj}"
    assert details["label"] == "poor"
    print(f"✅ Business quality penalty: {adj} {details}")

def test_business_quality_rewards_cash_generative_company():
    info = {
        "gross_margin": 0.55,
        "operating_margin": 0.27,
        "profit_margin": 0.18,
        "revenue": 2e9,
        "free_cashflow": 500e6,
        "total_debt": 500e6,
        "total_cash": 1e9,
        "ebitda": 700e6,
        "return_on_equity": 0.28,
        "revenue_growth": 0.30,
    }
    adj, details = _business_quality_overlay("TEST", info)
    assert details["quality_score"] >= 80, f"Strong business should score excellent, got {details}"
    assert adj["evidence"] > 0, f"Strong quality should support evidence, got {adj}"
    assert adj["risk"] < 0, f"Strong quality should reduce risk, got {adj}"
    assert details["label"] == "excellent"
    print(f"✅ Business quality support: {adj} {details}")

if __name__ == "__main__":
    test_asymmetry_small_cap()
    test_risk_high_debt()
    test_evidence_strong_growth()
    test_event_alpha_rewards_fresh_high_confidence_contract()
    test_event_alpha_penalizes_priced_in_duplicate_news()
    test_valuation_overlay_penalizes_extreme_multiples()
    test_valuation_overlay_rewards_reasonable_cash_flow()
    test_valuation_overlay_does_not_over_penalize_high_pe_alone()
    test_business_quality_penalizes_weak_levered_company()
    test_business_quality_rewards_cash_generative_company()
    print("\nAll scoring tests passed! ✅")
