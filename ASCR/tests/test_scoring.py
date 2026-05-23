"""Tests for scoring engine."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scoring import _momentum_score, _asymmetry_score, _risk_score, _evidence_score_basic

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

if __name__ == "__main__":
    test_asymmetry_small_cap()
    test_risk_high_debt()
    test_evidence_strong_growth()
    print("\nAll scoring tests passed! ✅")
