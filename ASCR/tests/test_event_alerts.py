"""Tests for active event alert gating."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.event_alerts import (
    filter_explosive_events,
    headline_is_explosive,
    is_explosive_event,
)


TEST_POLICY = {
    "enabled": True,
    "min_alert_score": 18,
    "max_events_per_push": 3,
    "min_confidence": 0.75,
    "min_abs_evidence_delta": 8,
    "max_priced_in_pct": 65,
    "suppress_sources": ["price"],
    "high_impact_sources": ["sec_8k", "earnings"],
    "high_impact_event_types": [
        "contract",
        "guidance_raise",
        "guidance_lower",
        "fraud_concern",
        "bankruptcy",
    ],
    "explosive_keywords": [
        "billion",
        "signed contract",
        "sec investigation",
        "fraud",
        "bankruptcy",
        "nvidia invests",
    ],
}


def test_routine_actionable_event_is_not_pushed():
    event = {
        "ticker": "ABC",
        "headline": "ABC announces AI infrastructure partnership",
        "source": "news",
        "event_type": "partnership",
        "evidence_delta": 6,
        "confidence": 0.70,
        "verdict": "BUY",
        "conviction": "MEDIUM",
    }

    assert not is_explosive_event(event, TEST_POLICY)
    assert filter_explosive_events([event], TEST_POLICY) == []


def test_major_sec_contract_is_pushed():
    event = {
        "ticker": "ABC",
        "headline": "ABC signs billion dollar AI data center supply agreement",
        "source": "sec_8k",
        "event_type": "contract",
        "evidence_delta": 11,
        "confidence": 0.82,
        "verdict": "BUY",
        "conviction": "HIGH",
        "priced_in_pct": 30,
    }

    filtered = filter_explosive_events([event], TEST_POLICY)
    assert len(filtered) == 1
    assert filtered[0]["ticker"] == "ABC"
    assert filtered[0]["_alert_score"] >= 18


def test_severe_negative_event_is_pushed():
    event = {
        "ticker": "XYZ",
        "headline": "XYZ faces SEC investigation over fraud allegations",
        "source": "news",
        "event_type": "fraud_concern",
        "evidence_delta": -9,
        "risk_delta": 12,
        "confidence": 0.80,
        "verdict": "SELL",
        "conviction": "HIGH",
    }

    assert is_explosive_event(event, TEST_POLICY)


def test_price_only_event_is_suppressed_by_default():
    event = {
        "ticker": "XYZ",
        "headline": "XYZ price surges 20 percent",
        "source": "price",
        "event_type": "price_surge",
        "evidence_delta": 20,
        "confidence": 1.0,
        "verdict": "BUY",
        "conviction": "HIGH",
    }

    assert not is_explosive_event(event, TEST_POLICY)


def test_headline_explosive_keywords():
    assert headline_is_explosive("NVIDIA invests $2 billion in new AI supplier", TEST_POLICY)
    assert not headline_is_explosive("NVIDIA AI stocks rise as market optimism improves", TEST_POLICY)


if __name__ == "__main__":
    test_routine_actionable_event_is_not_pushed()
    test_major_sec_contract_is_pushed()
    test_severe_negative_event_is_pushed()
    test_price_only_event_is_suppressed_by_default()
    test_headline_explosive_keywords()
    print("Event alert tests passed")
