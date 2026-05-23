"""Event classifier V2 — structured event extraction.

MVP: keyword-based classification.
Phase 2: LLM-powered with structured JSON output.

Target schema for LLM output:
{
    "ticker": "SNDK",
    "event_type": "earnings|sec_filing|partnership|contract|financing|guidance|product|customer|dilution|risk|other",
    "counterparty": "NVIDIA/Microsoft/etc or null",
    "is_ai_related": true,
    "ai_supply_chain_area": ["HBM", "NAND", "optical", "data_center", "power", "cooling", "networking"],
    "has_contract_value": true,
    "contract_value": "string or null",
    "has_warrant_or_equity_component": false,
    "revenue_impact": "high|medium|low|unclear",
    "time_horizon": "immediate|1-2 quarters|12-24 months|unclear",
    "evidence_impact": -10 to +10,
    "asymmetry_impact": -10 to +10,
    "risk_impact": -10 to +10,
    "summary": "short summary",
    "key_risks": ["risk1", "risk2"],
    "confidence": 0.0 to 1.0
}
"""
import json
from src import config
from src.utils import get_logger

logger = get_logger("event_classifier")

# Score adjustments per event type
EVENT_IMPACTS = {
    "guidance_raise":     {"evidence": 15, "risk": -5, "asymmetry": 0},
    "guidance_cut":       {"evidence": -10, "risk": 20, "asymmetry": 0},
    "earnings_beat":      {"evidence": 10, "momentum": 5, "asymmetry": -5},
    "earnings_miss":      {"evidence": -10, "risk": 10, "asymmetry": 5},
    "major_contract":     {"evidence": 20, "asymmetry": 5, "risk": -5},
    "partnership":        {"evidence": 10, "asymmetry": 5, "risk": -3},
    "acquisition":        {"evidence": 5, "risk": 5, "asymmetry": 0},
    "dilution":           {"risk": 25, "evidence": -5, "asymmetry": 5},
    "insider_buy":        {"evidence": 10, "risk": -3, "asymmetry": 0},
    "insider_sell":       {"risk": 5, "evidence": 0, "asymmetry": 0},
    "ai_catalyst":        {"evidence": 5, "asymmetry": 10, "risk": 0},
    "customer_loss":      {"evidence": -15, "risk": 20, "asymmetry": 0},
    "fraud_concern":      {"evidence": -20, "risk": 30, "asymmetry": 0},
    "management_change":  {"risk": 10, "evidence": -5, "asymmetry": 0},
    "revenue_acceleration": {"evidence": 20, "asymmetry": 5, "risk": -5},
}

AI_KEYWORDS = [
    "AI infrastructure", "data center", "HBM", "DRAM", "NAND", "enterprise SSD", "eSSD",
    "optical transceiver", "800G", "1.6T", "liquid cooling", "power management",
    "GPU cluster", "NVIDIA Blackwell", "NVL72", "hyperscaler",
]

COUNTERPARTIES = ["NVIDIA", "Microsoft", "Amazon", "Google", "Meta", "Oracle", "AMD", "Broadcom",
                  "Apple", "Tesla", "Alibaba", "ByteDance", "Samsung", "TSMC", "Intel"]

def classify_headline(headline: str, ticker: str = "") -> dict:
    """Classify a news headline into structured event (keyword-based MVP)."""
    h = headline.lower()
    event = {
        "ticker": ticker,
        "event_type": "other",
        "counterparty": None,
        "is_ai_related": False,
        "ai_supply_chain_area": [],
        "has_contract_value": False,
        "contract_value": None,
        "has_warrant_or_equity_component": False,
        "revenue_impact": "unclear",
        "time_horizon": "unclear",
        "evidence_impact": 0,
        "asymmetry_impact": 0,
        "risk_impact": 0,
        "summary": headline,
        "key_risks": [],
        "confidence": 0.5,
    }

    # Detect event type
    if any(w in h for w in ["raises guidance", "raises outlook", "raised guidance", "upward revision"]):
        event["event_type"] = "guidance"
        event["evidence_impact"] = 10
        event["risk_impact"] = -5
        event["revenue_impact"] = "high"
    elif any(w in h for w in ["cuts guidance", "lowers outlook", "lowered guidance", "warns"]):
        event["event_type"] = "guidance"
        event["evidence_impact"] = -10
        event["risk_impact"] = 15
    elif any(w in h for w in ["beats estimate", "beats expectation", "tops estimate", "beats consensus", "record revenue"]):
        event["event_type"] = "earnings"
        event["evidence_impact"] = 10
    elif any(w in h for w in ["misses estimate", "misses expectation", "falls short", "disappointing"]):
        event["event_type"] = "earnings"
        event["evidence_impact"] = -10
        event["risk_impact"] = 10
    elif any(w in h for w in ["multi-year", "multi-billion", "major contract", "awarded contract", "capacity reservation", "prepayment"]):
        event["event_type"] = "contract"
        event["evidence_impact"] = 15
        event["has_contract_value"] = any(c in h for c in ["$", "billion", "million"])
        event["revenue_impact"] = "high"
    elif any(w in h for w in ["partnership", "strategic alliance", "collaboration", "joint venture"]):
        event["event_type"] = "partnership"
        event["evidence_impact"] = 8
        event["asymmetry_impact"] = 5
    elif any(w in h for w in ["acquires", "acquisition", "to buy", "merger"]):
        event["event_type"] = "partnership"
        event["evidence_impact"] = 5
    elif any(w in h for w in ["offering", "dilution", "secondary offering", "shelf registration", "warrant"]):
        event["event_type"] = "financing"
        event["risk_impact"] = 20
        event["has_warrant_or_equity_component"] = "warrant" in h
        event["key_risks"].append("dilution")
    elif any(w in h for w in ["insider buy", "insider purchase", "director buys", "ceo buys", "officer buys"]):
        event["event_type"] = "sec_filing"
        event["evidence_impact"] = 8

    # Detect AI relevance
    for kw in AI_KEYWORDS:
        if kw.lower() in h:
            event["is_ai_related"] = True
            event["ai_supply_chain_area"].append(kw)

    # Detect counterparty
    for cp in COUNTERPARTIES:
        if cp.lower() in h:
            event["counterparty"] = cp
            break

    # Detect contract value
    import re
    value_match = re.search(r'\$[\d.]+\s*(billion|million|B|M|bn)', h)
    if value_match:
        event["has_contract_value"] = True
        event["contract_value"] = value_match.group(0)

    return event


def is_high_impact_event(event: dict, ticker: str, has_position: bool = False) -> bool:
    """Determine if an event qualifies as high_impact (triggers deep model)."""
    if has_position:
        # Any material event on a held position is high impact
        if event["event_type"] in ("earnings", "guidance", "financing", "sec_filing"):
            return True
        if abs(event.get("evidence_impact", 0)) >= 10:
            return True
        if event.get("risk_impact", 0) >= 15:
            return True

    # S-tier promotion
    if abs(event.get("evidence_impact", 0)) >= 15:
        return True

    # Major contract with value
    if event.get("has_contract_value") and event.get("event_type") == "contract":
        return True

    # Dilution/offering
    if event.get("event_type") == "financing":
        return True

    return False
