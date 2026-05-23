"""High-value LLM router — routes LLM calls to maximize performance impact.

NOT about saving tokens. About spending tokens where they improve:
- Signal quality (fewer false positives)
- Earlier discovery
- Better exit timing
- Higher confidence on held positions

Trigger conditions (any one triggers LLM):
1. Held position: major news, 8-K, 6-K, earnings, guidance, offering, dilution
2. Rating upgrade candidate: B/C → A/S
3. Score delta 30d > 20
4. Major counterparty event (NVIDIA, Microsoft, Amazon, Google, Meta, Oracle, Broadcom, AMD)
5. Material keywords: warrant, prepayment, capacity reservation, multi-year agreement,
   material definitive agreement, customer loss, guidance cut, offering, ATM, convertible, going concern
6. B-tier High tracking with new confirming evidence
7. Paper trader shows this signal type historically performs well
"""
import json
from src import db, config
from src.utils import get_logger

logger = get_logger("llm_router")

MAJOR_COUNTERPARTIES = {
    "nvidia", "microsoft", "amazon", "google", "meta", "oracle", "broadcom", "amd",
    "apple", "tsmc", "samsung", "intel", "alibaba", "bytedance",
}

MATERIAL_KEYWORDS = [
    "warrant", "prepayment", "capacity reservation", "multi-year agreement",
    "material definitive agreement", "customer loss", "guidance cut", "guidance raise",
    "offering", "ATM", "at-the-market", "convertible", "going concern",
    "dilution", "secondary offering", "shelf registration",
    "backlog", "record revenue", "record bookings",
]

SEC_HIGH_IMPACT_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A", "10-Q", "10-K"}


class HighValueRouter:
    def __init__(self):
        self._held_tickers = None

    def _get_held_tickers(self) -> set:
        if self._held_tickers is None:
            positions = db.get_open_positions()
            self._held_tickers = {p["ticker"] for p in positions}
        return self._held_tickers

    def should_call_llm(self, ticker: str, event: dict, score_delta: dict = None) -> dict:
        """Determine if this event warrants an LLM call.

        Returns:
            {
                "call": bool,
                "tier": "cheap"|"standard"|"premium",
                "reason": str,
                "priority": int (1=highest),
            }
        """
        title = (event.get("title", "") or event.get("headline", "")).lower()
        event_type = event.get("event_type", "")
        form = event.get("form", "")
        held = ticker in self._get_held_tickers()

        # --- Priority 1: Held position + material event ---
        if held:
            if form in SEC_HIGH_IMPACT_FORMS:
                return {"call": True, "tier": "premium", "reason": "held_position_sec_filing", "priority": 1}
            if event_type in ("earnings", "guidance", "financing"):
                return {"call": True, "tier": "premium", "reason": f"held_position_{event_type}", "priority": 1}
            if any(kw in title for kw in ["dilution", "offering", "guidance cut", "customer loss", "going concern"]):
                return {"call": True, "tier": "premium", "reason": "held_position_risk_event", "priority": 1}
            # Any news about a held position is at least standard
            if event_type != "other":
                return {"call": True, "tier": "standard", "reason": "held_position_event", "priority": 2}

        # --- Priority 2: Rating upgrade candidate ---
        if score_delta:
            opp_delta = score_delta.get("delta_30d", {}).get("opportunity", 0)
            current_rating = score_delta.get("current", {}).get("rating", "D")
            if opp_delta >= 20:
                return {"call": True, "tier": "standard", "reason": f"opp_delta_30d_{opp_delta:.0f}", "priority": 2}
            if current_rating in ("B", "C") and opp_delta >= 10:
                return {"call": True, "tier": "standard", "reason": f"upgrade_candidate_{current_rating}_delta_{opp_delta:.0f}", "priority": 2}

        # --- Priority 3: Major counterparty ---
        for cp in MAJOR_COUNTERPARTIES:
            if cp in title:
                return {"call": True, "tier": "standard", "reason": f"counterparty_{cp}", "priority": 3}

        # --- Priority 4: Material keywords ---
        for kw in MATERIAL_KEYWORDS:
            if kw.lower() in title:
                tier = "standard" if held else "cheap"
                return {"call": True, "tier": tier, "reason": f"material_keyword_{kw}", "priority": 4}

        # --- Priority 5: B-tier High tracking + new evidence ---
        scores = db.get_score_history(ticker, days=1)
        if scores:
            latest = scores[0]
            if latest.get("rating") == "B" and latest.get("tracking_priority") == "High":
                if event_type not in ("", "other", "news"):
                    return {"call": True, "tier": "cheap", "reason": "b_high_tracking_new_evidence", "priority": 5}

        # --- Default: no LLM ---
        return {"call": False, "tier": None, "reason": "below_threshold", "priority": 99}

    def get_llm_prompt(self, ticker: str, event: dict, context: str = "") -> str:
        """Generate LLM prompt for event analysis. Output must be JSON."""
        title = event.get("title", "") or event.get("headline", "")
        event_type = event.get("event_type", "")

        return f"""Analyze this event for stock {ticker}. Return ONLY valid JSON.

Event: {title}
Type: {event_type}
Context: {context}

Return JSON with these fields:
{{
    "event_type": "earnings|sec_filing|partnership|contract|financing|guidance|product|customer|dilution|risk|other",
    "is_incremental_information": true/false,
    "evidence_impact": -10 to +10,
    "asymmetry_impact": -10 to +10,
    "risk_impact": -10 to +10,
    "confidence": 0.0 to 1.0,
    "thesis_change": "strengthened|unchanged|weakened|broken",
    "suggested_next_trigger": "what to watch for next",
    "reason": "1-2 sentence explanation"
}}

Rules:
- Be conservative with impact scores
- Only rate evidence_impact high if there's real financial proof (revenue, backlog, contract value)
- AI buzzwords without revenue = low evidence_impact
- Dilution/offering = positive risk_impact (higher risk)
- Do NOT recommend buy/sell. Only analyze the event.
"""


# Singleton
_router = None

def get_router() -> HighValueRouter:
    global _router
    if _router is None:
        _router = HighValueRouter()
    return _router
