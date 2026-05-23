"""Tests for rating system."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rating import compute_rating, compute_tracking_priority

def test_s_tier():
    r = compute_rating(80, evidence=75, asymmetry=60, risk=20, momentum=70)
    assert r == "S", f"Expected S, got {r}"
    print(f"✅ S-tier: {r}")

def test_a_tier():
    r = compute_rating(62, evidence=55, asymmetry=45, risk=30, momentum=50)
    assert r == "A", f"Expected A, got {r}"
    print(f"✅ A-tier: {r}")

def test_b_tier():
    r = compute_rating(50, evidence=40, asymmetry=40, risk=20, momentum=50)
    assert r == "B", f"Expected B, got {r}"
    print(f"✅ B-tier: {r}")

def test_high_opp_but_high_risk_not_s():
    r = compute_rating(76, evidence=70, asymmetry=50, risk=40, momentum=60)
    assert r != "S", f"High risk should prevent S, got {r}"
    print(f"✅ High risk blocks S: {r}")

def test_tracking_priority():
    assert compute_tracking_priority("S") == "High"
    assert compute_tracking_priority("B", has_position=True) == "High"
    assert compute_tracking_priority("B", has_position=False) == "Medium"
    assert compute_tracking_priority("D") == "Exclude"
    print("✅ Tracking priority correct")

if __name__ == "__main__":
    test_s_tier()
    test_a_tier()
    test_b_tier()
    test_high_opp_but_high_risk_not_s()
    test_tracking_priority()
    print("\nAll rating tests passed! ✅")
