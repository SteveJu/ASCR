"""Read signals from ascr database."""
from src import db, config
from src.utils import get_logger

logger = get_logger("signal_reader")


def get_todays_signals():
    """Read today's scores and identify actionable signals."""
    scores = db.read_radar_scores()
    exit_alerts = db.read_radar_exit_alerts(days=1)
    cfg = config.load()

    buy_signals = []
    shadow_signals = []
    exit_signals = []

    for s in scores:
        rating = s.get("rating", "D")
        sizing_pct = cfg["sizing"].get(rating, 0)

        if sizing_pct > 0:
            buy_signals.append({
                "ticker": s["ticker"],
                "rating": rating,
                "sizing_pct": sizing_pct,
                "opportunity_score": s.get("opportunity_score", 0),
                "evidence": s.get("evidence_score", 0),
                "asymmetry": s.get("asymmetry_score", 0),
                "momentum": s.get("momentum_score", 0),
                "risk": s.get("risk_score", 0),
                "tracking_priority": s.get("tracking_priority", ""),
                "date": s.get("date", ""),
            })
        elif rating == "B":
            shadow_signals.append({
                "ticker": s["ticker"],
                "rating": rating,
                "opportunity_score": s.get("opportunity_score", 0),
                "evidence": s.get("evidence_score", 0),
                "asymmetry": s.get("asymmetry_score", 0),
                "momentum": s.get("momentum_score", 0),
                "risk": s.get("risk_score", 0),
                "date": s.get("date", ""),
            })

    # Exit alerts from ascr
    for a in exit_alerts:
        exit_signals.append({
            "ticker": a.get("ticker", ""),
            "alert_type": a.get("alert_type", ""),
            "severity": a.get("severity", ""),
            "action": a.get("action_suggestion", ""),
            "reason": a.get("reason", ""),
        })

    logger.info(f"Signals: {len(buy_signals)} buy, {len(shadow_signals)} shadow, {len(exit_signals)} exit")
    return {
        "buy": buy_signals,
        "shadow": shadow_signals,
        "exit": exit_signals,
        "all_scores": scores,
    }
