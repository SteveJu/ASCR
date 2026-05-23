"""Rating system V2 — S/A/B/C/D with tracking_priority and actionability."""
from src.utils import get_logger

logger = get_logger("rating")

RATING_DESCRIPTIONS = {
    "S": "🔴 S-Tier: Strong signal, small position probe NOW. High evidence + asymmetry, clear catalyst, not pure hype.",
    "A": "🟠 A-Tier: Worth buying or strong tracking. May need pullback or next catalyst to confirm.",
    "B": "🟡 B-Tier: Early tracking. Some signals but financials not yet delivered. Keep watching. (Sandisk H1 2025 was classic B)",
    "C": "🔵 C-Tier: High risk, observe only. Big story but bad financials, dilution risk, possible AI hype. Don't buy unless new evidence.",
    "D": "⚪ D-Tier: Exclude/blacklist. Fake AI, no real revenue, serial dilution, audit/delist risk, untrustworthy management.",
}

def compute_rating(opportunity_score, evidence, asymmetry, risk, momentum) -> str:
    """Compute rating based on opportunity score and dimension checks."""
    # S requires both high evidence AND high asymmetry, with manageable risk
    if opportunity_score >= 75 and evidence >= 60 and asymmetry >= 50 and risk <= 30:
        return "S"
    # S also possible with slightly lower opp but stellar evidence+asymmetry
    if opportunity_score >= 65 and evidence >= 70 and asymmetry >= 60 and risk <= 25:
        return "S"
    if opportunity_score >= 60 and evidence >= 50 and risk <= 35:
        return "A"
    if opportunity_score >= 45:
        return "B"
    if opportunity_score >= 25:
        return "C"
    return "D"

def compute_tracking_priority(rating, has_position=False, recent_events=None) -> str:
    """Compute tracking priority independent of current rating.
    A stock can be B-rated but High priority if it's close to breaking out."""
    if has_position:
        return "High"

    if rating == "S":
        return "High"
    elif rating == "A":
        return "High"
    elif rating == "B":
        # B-tier stocks with recent events should be tracked closely
        if recent_events and len(recent_events) > 0:
            return "High"
        return "Medium"
    elif rating == "C":
        return "Low"
    else:
        return "Exclude"

def get_rating_description(rating: str) -> str:
    return RATING_DESCRIPTIONS.get(rating, "Unknown")

def classify_action(rating: str, has_position: bool = False, position_status: str = None) -> str:
    """Determine recommended action."""
    if has_position:
        if position_status:
            return position_status
        if rating in ("S", "A"):
            return "Hold Strong / Add on dip"
        elif rating == "B":
            return "Hold but monitor"
        elif rating == "C":
            return "Consider reducing"
        else:
            return "EXIT — thesis likely broken"
    else:
        if rating == "S":
            return "BUY — small position probe"
        elif rating == "A":
            return "BUY CANDIDATE — strong setup, wait for entry"
        elif rating == "B":
            return "WATCHLIST — track for promotion"
        elif rating == "C":
            return "OBSERVE ONLY — don't buy yet"
        else:
            return "SKIP / EXCLUDE"
