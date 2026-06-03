"""Telegram alert gating for unusually material events.

The event pipeline can keep many actionable events for scoring and research.
This module decides which of them are important enough to actively push.
"""
from __future__ import annotations

from src import config


DEFAULT_ALERT_POLICY = {
    "enabled": True,
    "min_alert_score": 18,
    "max_events_per_push": 3,
    "min_confidence": 0.75,
    "min_abs_evidence_delta": 8,
    "max_priced_in_pct": 65,
    "suppress_sources": ["price"],
    "high_impact_sources": ["sec", "sec_8k", "sec_filing", "earnings"],
    "high_impact_event_types": [
        "major_contract",
        "contract",
        "guidance_raise",
        "guidance_lower",
        "earnings_beat",
        "earnings_miss",
        "dilution",
        "financing",
        "fraud_concern",
        "regulation",
        "insider_buy",
        "acquisition",
        "bankruptcy",
        "delisting",
    ],
    "explosive_keywords": [
        "billion",
        "multi-billion",
        "material definitive agreement",
        "signed contract",
        "supply agreement",
        "purchase agreement",
        "acquisition",
        "acquire",
        "merger",
        "guidance raise",
        "guidance cut",
        "raises guidance",
        "lowers guidance",
        "sec investigation",
        "doj investigation",
        "short report",
        "fraud",
        "bankruptcy",
        "delist",
        "halt",
        "nvidia invests",
        "nvidia partnership",
        "hyperscaler contract",
    ],
}

VERDICT_BONUS = {
    "STRONG_BUY": 4,
    "SELL": 4,
    "BUY": 1,
    "AVOID": 1,
}

CONVICTION_BONUS = {
    "HIGH": 3,
    "MEDIUM": 1,
}


def _merge_policy(overrides: dict | None) -> dict:
    policy = dict(DEFAULT_ALERT_POLICY)
    if overrides:
        for key, value in overrides.items():
            policy[key] = value
    return policy


def load_alert_policy() -> dict:
    """Load alert policy from scoring config with safe defaults."""
    try:
        return _merge_policy(config.scoring().get("alert_policy", {}))
    except Exception:
        return dict(DEFAULT_ALERT_POLICY)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _lower_set(values) -> set[str]:
    return {str(v).lower() for v in (values or [])}


def _event_text(event: dict) -> str:
    return " ".join(
        str(event.get(key, "") or "")
        for key in ("headline", "summary", "investment_thesis", "catalyst")
    ).lower()


def headline_is_explosive(headline: str, policy: dict | None = None) -> bool:
    """Return True for headline-only fast-scan alerts worth interrupting on."""
    policy = _merge_policy(policy or load_alert_policy())
    text = (headline or "").lower()
    return any(keyword.lower() in text for keyword in policy.get("explosive_keywords", []))


def event_alert_score(event: dict, policy: dict | None = None) -> float:
    """Score push-worthiness. Higher means more unusual and material."""
    policy = _merge_policy(policy or load_alert_policy())
    source = str(event.get("source", "") or "").lower()
    event_type = str(event.get("event_type", "") or "").lower()
    verdict = str(event.get("verdict", "") or "").upper()
    conviction = str(event.get("conviction", "") or "").upper()

    evidence = _safe_float(event.get("evidence_delta"))
    risk = _safe_float(event.get("risk_delta"))
    confidence = _safe_float(event.get("confidence"), 0.5)
    priced_in = _safe_float(event.get("priced_in_pct"))

    high_sources = _lower_set(policy.get("high_impact_sources"))
    high_types = _lower_set(policy.get("high_impact_event_types"))
    text = _event_text(event)

    score = min(abs(evidence), 20)
    if event_type in high_types:
        score += 4
    if source in high_sources:
        score += 3
    score += VERDICT_BONUS.get(verdict, 0)
    score += CONVICTION_BONUS.get(conviction, 0)
    if abs(risk) >= 8:
        score += min(abs(risk) / 2, 6)

    keyword_hits = sum(
        1 for keyword in policy.get("explosive_keywords", [])
        if keyword.lower() in text
    )
    score += min(keyword_hits * 3, 6)

    if confidence < float(policy.get("min_confidence", 0.75)):
        score -= 5
    if evidence > 0 and priced_in > float(policy.get("max_priced_in_pct", 65)):
        score -= 4

    return round(score, 1)


def is_explosive_event(event: dict, policy: dict | None = None) -> bool:
    """Return True if an event should be actively pushed to Telegram."""
    policy = _merge_policy(policy or load_alert_policy())
    if not policy.get("enabled", True):
        return True

    source = str(event.get("source", "") or "").lower()
    if source in _lower_set(policy.get("suppress_sources")):
        return False

    evidence = _safe_float(event.get("evidence_delta"))
    confidence = _safe_float(event.get("confidence"), 0.5)
    risk = _safe_float(event.get("risk_delta"))
    event_type = str(event.get("event_type", "") or "").lower()
    source_is_high = source in _lower_set(policy.get("high_impact_sources"))
    type_is_high = event_type in _lower_set(policy.get("high_impact_event_types"))
    keyword_hit = headline_is_explosive(_event_text(event), policy)

    severe_negative = evidence <= -8 or risk >= 10
    material_enough = (
        abs(evidence) >= float(policy.get("min_abs_evidence_delta", 8))
        or severe_negative
    )
    context_enough = source_is_high or type_is_high or keyword_hit or severe_negative

    if confidence < float(policy.get("min_confidence", 0.75)) and not source_is_high:
        return False
    if not material_enough or not context_enough:
        return False
    return event_alert_score(event, policy) >= float(policy.get("min_alert_score", 18))


def filter_explosive_events(events: list[dict], policy: dict | None = None) -> list[dict]:
    """Filter and sort events for active push notifications."""
    policy = _merge_policy(policy or load_alert_policy())
    max_events = int(policy.get("max_events_per_push", 3))
    scored = []
    for event in events or []:
        if is_explosive_event(event, policy):
            enriched = dict(event)
            enriched["_alert_score"] = event_alert_score(event, policy)
            scored.append(enriched)
    scored.sort(
        key=lambda e: (
            e.get("_alert_score", 0),
            abs(_safe_float(e.get("evidence_delta"))),
            _safe_float(e.get("confidence")),
        ),
        reverse=True,
    )
    return scored[:max_events]
