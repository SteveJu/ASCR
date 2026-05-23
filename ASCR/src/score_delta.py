"""Score delta tracker — detect inflections and trend changes."""
from datetime import datetime, timedelta
from src import db
from src.utils import get_logger

logger = get_logger("score_delta")


def compute_deltas(tickers: list = None) -> list:
    """Compute 7d and 30d score deltas for all tickers."""
    if tickers is None:
        from src import config
        tickers = config.all_tickers()

    today = datetime.now().strftime("%Y-%m-%d")
    results = []

    for ticker in tickers:
        history = db.get_score_history(ticker, days=35)
        if len(history) < 2:
            continue

        latest = history[0]

        # Find 7-day-ago score
        target_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        target_30d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        score_7d_ago = None
        score_30d_ago = None
        for s in history:
            if s["date"] <= target_7d and score_7d_ago is None:
                score_7d_ago = s
            if s["date"] <= target_30d and score_30d_ago is None:
                score_30d_ago = s

        delta = {
            "ticker": ticker,
            "date": today,
            "current": {
                "evidence": latest["evidence_score"],
                "asymmetry": latest["asymmetry_score"],
                "momentum": latest["momentum_score"],
                "risk": latest["risk_score"],
                "opportunity": latest["opportunity_score"],
                "rating": latest.get("rating", "?"),
            },
            "delta_7d": {},
            "delta_30d": {},
            "inflections": [],
        }

        # 7d deltas
        if score_7d_ago:
            for dim in ["evidence_score", "asymmetry_score", "momentum_score", "risk_score", "opportunity_score"]:
                short = dim.replace("_score", "")
                delta["delta_7d"][short] = latest[dim] - score_7d_ago[dim]

        # 30d deltas
        if score_30d_ago:
            for dim in ["evidence_score", "asymmetry_score", "momentum_score", "risk_score", "opportunity_score"]:
                short = dim.replace("_score", "")
                delta["delta_30d"][short] = latest[dim] - score_30d_ago[dim]

            # Detect positive inflection
            ev_delta = delta["delta_30d"].get("evidence", 0)
            mo_delta = delta["delta_30d"].get("momentum", 0)
            ri_delta = delta["delta_30d"].get("risk", 0)

            if ev_delta >= 15 and ri_delta <= 5:
                delta["inflections"].append({
                    "type": "evidence_inflection",
                    "description": f"Evidence +{ev_delta:.0f} over 30d (risk stable)",
                    "significance": "high",
                })
            if mo_delta >= 10 and ri_delta <= 5:
                delta["inflections"].append({
                    "type": "momentum_inflection",
                    "description": f"Momentum +{mo_delta:.0f} over 30d",
                    "significance": "medium",
                })

            # Large opportunity delta → may need tracking priority upgrade
            opp_delta = delta["delta_30d"].get("opportunity", 0)
            if opp_delta >= 20:
                delta["inflections"].append({
                    "type": "opportunity_surge",
                    "description": f"Opportunity score +{opp_delta:.0f} over 30d",
                    "significance": "high",
                    "trigger_llm": True,
                })

        results.append(delta)

    # Sort by biggest positive opportunity delta
    results.sort(key=lambda d: d.get("delta_30d", {}).get("opportunity", 0), reverse=True)

    inflection_count = sum(1 for d in results if d["inflections"])
    logger.info(f"Score deltas computed for {len(results)} tickers, {inflection_count} with inflections")
    return results


def get_inflections(deltas: list) -> list:
    """Extract tickers with positive inflections."""
    return [d for d in deltas if d["inflections"]]


def should_upgrade_tracking(delta: dict) -> bool:
    """Check if a ticker's tracking priority should be upgraded based on deltas."""
    opp_30d = delta.get("delta_30d", {}).get("opportunity", 0)
    ev_30d = delta.get("delta_30d", {}).get("evidence", 0)

    # Big improvement but still low absolute score → upgrade tracking, not rating
    if opp_30d >= 15 and delta["current"]["opportunity"] < 60:
        return True
    if ev_30d >= 15:
        return True
    return False
