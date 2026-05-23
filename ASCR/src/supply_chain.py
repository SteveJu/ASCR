"""Supply Chain Relationship Graph.

Maps upstream/downstream relationships in AI supply chain.
When an event fires on a major player, propagate attenuated signals to related tickers.
"""
from src.utils import get_logger

logger = get_logger("supply_chain")

# Relationship graph: ticker → {relationship_type: [related_tickers]}
# Attenuation: how much of the original evidence_delta propagates
GRAPH = {
    "NVDA": {
        "suppliers": ["GLW", "COHR", "LITE", "AAOI", "CIEN", "CRDO", "MXL"],  # optical
        "memory": ["MU", "SNDK", "WDC", "STX", "SMCI"],
        "equipment": ["ASML", "AMAT", "LRCX", "KLAC", "TER", "ACMR", "UCTT"],
        "power": ["VRT", "ETN", "CEG", "VST", "IDA", "VICR", "ON"],
        "networking": ["ANET", "MRVL", "AVGO"],
        "foundry": ["TSM"],
    },
    "AMD": {
        "foundry": ["TSM"],
        "memory": ["MU", "SMCI"],
        "networking": ["MRVL"],
    },
    "INTC": {
        "equipment": ["ASML", "AMAT", "LRCX"],
    },
    "TSM": {
        "equipment": ["ASML", "AMAT", "LRCX", "KLAC", "TER"],
        "customers": ["NVDA", "AMD", "AVGO", "MRVL"],
    },
    "AVGO": {
        "networking": ["ANET", "MRVL"],
        "optical": ["LITE", "COHR", "GLW"],
    },
    # Hyperscalers (not in universe but trigger supply chain events)
    "MSFT": {
        "infra": ["NVDA", "AMD", "AVGO", "VRT", "CEG", "VST", "EQIX"],
    },
    "META": {
        "infra": ["NVDA", "AMD", "AVGO", "VRT", "ANET", "IREN", "CRWV"],
    },
    "GOOG": {
        "infra": ["NVDA", "AVGO", "TSM", "ANET", "VRT"],
    },
    "AMZN": {
        "infra": ["NVDA", "AMD", "AVGO", "ANET", "VRT", "CEG"],
    },
}

# Attenuation by relationship type
ATTENUATION = {
    "suppliers": 0.5,    # Direct supplier gets 50% of signal
    "memory": 0.4,
    "equipment": 0.35,   # Equipment is more indirect
    "power": 0.3,
    "networking": 0.4,
    "foundry": 0.45,
    "customers": 0.35,
    "optical": 0.45,
    "infra": 0.4,
}


def get_related_tickers(ticker: str) -> dict:
    """Get all tickers related to a given ticker.
    Returns {related_ticker: (relationship, attenuation)}
    """
    relations = GRAPH.get(ticker.upper(), {})
    result = {}
    for rel_type, tickers in relations.items():
        att = ATTENUATION.get(rel_type, 0.3)
        for t in tickers:
            if t != ticker:
                # Take the highest attenuation if multiple paths
                if t not in result or att > result[t][1]:
                    result[t] = (rel_type, att)
    return result


def propagate_event(event: dict, universe: set) -> list:
    """Given an event, generate derived events for related tickers.

    Args:
        event: {ticker, evidence_delta, event_type, headline, ...}
        universe: set of tickers in current universe

    Returns:
        list of derived event dicts ready for insertion
    """
    ticker = event.get("ticker", "")
    ev_delta = event.get("evidence_delta", 0) or 0

    # Only propagate significant events
    if abs(ev_delta) < 5:
        return []

    related = get_related_tickers(ticker)
    derived = []

    for rel_ticker, (rel_type, attenuation) in related.items():
        if rel_ticker not in universe:
            continue

        derived_delta = round(ev_delta * attenuation, 1)
        if abs(derived_delta) < 2:
            continue

        derived.append({
            "ticker": rel_ticker,
            "event_type": "supply_chain_propagation",
            "evidence_delta": derived_delta,
            "headline": "[{}→{}] {}".format(ticker, rel_ticker, event.get("headline", "")),
            "source": "supply_chain",
            "counterparty": ticker,
            "summary": "{} event on {} propagated to {} ({}, {:.0f}% signal)".format(
                event.get("event_type", "?"), ticker, rel_ticker, rel_type, attenuation * 100),
            "confidence": (event.get("confidence", 0.5) or 0.5) * attenuation,
            "verdict": event.get("verdict", ""),
            "conviction": "LOW",  # Derived signals get lower conviction
            "is_ai_related": 1,
            "supply_chain_area": rel_type,
        })

    if derived:
        logger.info(f"Propagated {ticker} event to {len(derived)} related tickers")

    return derived
