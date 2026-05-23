"""Degradation detector — fires warnings based on exact conditions.

Warning triggers:
1. overall_dqs declining 3 consecutive weeks
2. buy_dqs last 4wk < past 12wk mean by 15+
3. sell_dqs < 50
4. S-tier forward return no longer > A/B
5. B-High missed opportunities increasing
6. false_positive_rate rising
7. false_negative_rate rising
8. stability_score < 50
"""
from datetime import datetime, timedelta
from src import db
from src.utils import get_logger

logger = get_logger("degradation")


def detect_degradation(mode: str = "live_paper") -> dict:
    """Run all degradation checks."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM strategy_health WHERE mode=?
            ORDER BY date DESC LIMIT 20
        """, (mode,)).fetchall()

    if not rows:
        return {"alerts": [], "status": "no_data"}

    history = [dict(r) for r in rows]
    latest = history[0]
    alerts = []

    # --- 1. overall_dqs declining 3 consecutive weeks ---
    if len(history) >= 3:
        last3 = [h.get("overall_dqs", 0) for h in history[:3]]
        if all(last3[i] is not None and last3[i+1] is not None and last3[i] < last3[i+1]
               for i in range(2)):
            alerts.append({
                "level": "warning", "type": "dqs_3wk_decline",
                "message": f"Overall DQS declining 3 consecutive weeks: "
                           f"{last3[2]:.1f} → {last3[1]:.1f} → {last3[0]:.1f}",
                "action": "Review scoring model; check if market regime changed",
            })

    # --- 2. buy_dqs last 4wk < past 12wk mean by 15+ ---
    if len(history) >= 4:
        recent_4 = [h.get("buy_dqs", 0) for h in history[:4] if h.get("buy_dqs") is not None]
        older = [h.get("buy_dqs", 0) for h in history[4:16] if h.get("buy_dqs") is not None]
        if recent_4 and older:
            recent_avg = sum(recent_4) / len(recent_4)
            older_avg = sum(older) / len(older)
            if recent_avg < older_avg - 15:
                alerts.append({
                    "level": "warning", "type": "buy_dqs_drop",
                    "message": f"Buy DQS dropped: recent 4wk avg {recent_avg:.1f} vs "
                               f"prior avg {older_avg:.1f} (delta {recent_avg - older_avg:+.1f})",
                    "action": "Buy signals may be losing quality; review evidence scoring",
                })

    # --- 3. sell_dqs < 50 ---
    sell_dqs = latest.get("sell_dqs", 0) or 0
    if sell_dqs < 50 and sell_dqs > 0:
        alerts.append({
            "level": "warning", "type": "sell_dqs_low",
            "message": f"Sell DQS below threshold: {sell_dqs:.1f}/100",
            "action": "Exit rules may be suboptimal; review backtest_exit_rules output",
        })

    # --- 4. S-tier no longer outperforming A/B ---
    # (checked in strategy_health rating_quality_score)
    rq = latest.get("rating_quality_score", 50) or 50
    if rq < 40:
        alerts.append({
            "level": "critical", "type": "rating_quality_broken",
            "message": f"Rating quality score: {rq:.1f}/100 — S/A not outperforming B/C/D",
            "action": "Scoring formula needs recalibration; tier ordering violated",
        })

    # --- 5. B-High missed opportunities increasing ---
    if len(history) >= 2:
        current_mo = latest.get("missed_opportunity_score", 100) or 100
        prev_mo = history[1].get("missed_opportunity_score", 100) or 100
        if current_mo < prev_mo - 15:
            alerts.append({
                "level": "warning", "type": "missed_opps_increasing",
                "message": f"Missed opportunity score dropped: {prev_mo:.1f} → {current_mo:.1f}",
                "action": "Consider adding small position sizing for B-High stocks",
            })

    # --- 6. false_positive_rate rising ---
    fp = latest.get("false_positive_rate", 0) or 0
    if fp > 25:
        alerts.append({
            "level": "critical", "type": "high_false_positive",
            "message": f"False positive rate: {fp:.1f}% (too many bad buys)",
            "action": "Tighten buy criteria; raise evidence threshold",
        })
    elif fp > 15:
        alerts.append({
            "level": "warning", "type": "elevated_false_positive",
            "message": f"False positive rate elevated: {fp:.1f}%",
            "action": "Monitor buy decision quality closely",
        })

    # --- 7. false_negative_rate rising ---
    fn = latest.get("false_negative_rate", 0) or 0
    if fn > 25:
        alerts.append({
            "level": "warning", "type": "high_false_negative",
            "message": f"False negative rate: {fn:.1f}% (missing too many good stocks)",
            "action": "Scoring may be too conservative; review B-High tracking criteria",
        })

    # --- 8. stability_score < 50 ---
    stability = latest.get("stability_score", 50) or 50
    if stability < 50:
        alerts.append({
            "level": "warning", "type": "low_stability",
            "message": f"Stability score: {stability:.1f}/100 (high DQS variance)",
            "action": "Strategy performance is inconsistent; may be regime-dependent",
        })

    # --- Overall DQS thresholds ---
    overall = latest.get("overall_dqs", 0) or 0
    if overall < 50:
        alerts.append({
            "level": "critical", "type": "strategy_broken",
            "message": f"Overall DQS: {overall:.1f}/100 — strategy may be broken",
            "action": "Pause real-money usage; run full recalibration",
        })

    # Status
    critical = sum(1 for a in alerts if a["level"] == "critical")
    warning = sum(1 for a in alerts if a["level"] == "warning")
    if critical > 0:
        status = "critical"
    elif warning >= 2:
        status = "degraded"
    elif warning > 0:
        status = "caution"
    else:
        status = "healthy"

    logger.info(f"Degradation [{mode}]: {status}, {len(alerts)} alerts")
    return {"status": status, "alerts": alerts, "latest_health": latest}
