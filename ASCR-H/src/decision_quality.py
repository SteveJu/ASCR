"""Decision Quality Score (DQS) — scores every decision type on multiple dimensions.

BUY DQS = 30% forward_return + 25% benchmark_outperformance + 20% drawdown_control
          + 15% thesis_confirmation + 10% timing
SELL DQS = 35% avoided_drawdown + 25% opportunity_cost + 20% rule_consistency + 20% thesis_accuracy
TRIM DQS = similar to SELL but partial penalty for missed upside
HOLD DQS = post_hold_return + benchmark_outperformance + profit_protection - risk_ignored_penalty
NO_BUY DQS = avoided_loss + benchmark_underperformance_of_stock + tracking_value_adjustment
"""
from collections import defaultdict
from src import db
from src.utils import get_logger

logger = get_logger("decision_quality")


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _score_buy(outcome: dict, decision: dict) -> dict:
    """Score a BUY decision."""
    fwd = outcome.get("forward_return", 0) or 0
    alpha = outcome.get("alpha_return", 0) or 0
    max_dd = outcome.get("max_drawdown", 0) or 0
    max_gain = outcome.get("max_gain", 0) or 0
    thesis = outcome.get("thesis_confirmed", 0) or 0

    # Forward return score: +20% → 100, 0% → 50, -20% → 0
    return_score = _clamp(50 + fwd * 2.5)

    # Benchmark outperformance: +10% alpha → 100, 0% → 50, -10% → 0
    benchmark_score = _clamp(50 + alpha * 5)

    # Drawdown control: 0% dd → 100, -15% → 50, -30% → 0
    drawdown_score = _clamp(100 + max_dd * 3.33)

    # Thesis confirmation: binary 0 or 100
    thesis_score = 100 if thesis else 0

    # Timing: if max_gain >> fwd_return, timing was bad (bought too early or held wrong)
    if max_gain > 0 and fwd > 0:
        capture_ratio = fwd / max_gain
        timing_score = _clamp(capture_ratio * 100)
    elif fwd > 0:
        timing_score = 80
    else:
        timing_score = _clamp(50 + fwd * 2)

    dqs = (return_score * 0.30 + benchmark_score * 0.25 + drawdown_score * 0.20
           + thesis_score * 0.15 + timing_score * 0.10)

    explanation = (f"Return={fwd:+.1f}%→{return_score:.0f}, Alpha={alpha:+.1f}%→{benchmark_score:.0f}, "
                   f"DD={max_dd:.1f}%→{drawdown_score:.0f}, Thesis={'✓' if thesis else '✗'}→{thesis_score:.0f}, "
                   f"Timing→{timing_score:.0f}")

    return {
        "quality_score": round(dqs, 1),
        "return_score": round(return_score, 1),
        "benchmark_score": round(benchmark_score, 1),
        "drawdown_score": round(drawdown_score, 1),
        "timing_score": round(timing_score, 1),
        "thesis_score": round(thesis_score, 1),
        "opportunity_cost_score": None,
        "rule_consistency_score": None,
        "explanation": explanation,
    }


def _score_sell(outcome: dict, decision: dict) -> dict:
    """Score a SELL decision."""
    fwd = outcome.get("forward_return", 0) or 0  # return AFTER selling
    alpha = outcome.get("alpha_return")
    signal_return = alpha if alpha is not None else fwd
    max_dd = outcome.get("max_drawdown", 0) or 0
    max_gain = outcome.get("max_gain", 0) or 0

    # Avoided drawdown/opportunity cost is market-adjusted when alpha exists.
    # In a strong market, a sold stock rising with QQQ is less damning than a
    # sold stock generating large positive alpha after the exit.
    avoided_dd_score = _clamp(50 - signal_return * 2.5)

    if signal_return > 0:
        opp_cost_score = _clamp(100 - signal_return * 3)
    else:
        opp_cost_score = _clamp(80 - signal_return)

    # Rule consistency: was the sell triggered by a defined rule?
    reason = decision.get("reason", "")
    known_rules = ["hard_stop", "profit_", "trailing_stop", "time_stop", "thesis_broken",
                   "thesis_break", "pruner_", "rotation_for_", "catalyst_played",
                   "critical_alert", "meltdown"]
    rule_match = any(r in reason.lower() for r in known_rules)
    rule_score = 80 if rule_match else 40

    # Thesis accuracy: did the sell reason prove correct?
    if "stop" in reason.lower() and (fwd < -5 or signal_return < -5):
        thesis_score = 90  # correct to stop out
    elif "profit" in reason.lower() and signal_return < 10:
        thesis_score = 80  # took profit, stock didn't keep running much
    elif "profit" in reason.lower() and signal_return > 20:
        thesis_score = 30  # took profit too early
    elif signal_return < 0:
        thesis_score = 80
    elif alpha is not None and signal_return <= 5:
        thesis_score = 65
    else:
        thesis_score = 50

    dqs = (avoided_dd_score * 0.35 + opp_cost_score * 0.25
           + rule_score * 0.20 + thesis_score * 0.20)

    alpha_text = f", Alpha={alpha:+.1f}%" if alpha is not None else ""
    explanation = (f"PostSellReturn={fwd:+.1f}%{alpha_text}→Avoided={avoided_dd_score:.0f}, "
                   f"OppCost→{opp_cost_score:.0f}, Rule={'✓' if rule_match else '✗'}→{rule_score:.0f}, "
                   f"Thesis→{thesis_score:.0f}")

    return {
        "quality_score": round(dqs, 1),
        "return_score": None,
        "benchmark_score": None,
        "drawdown_score": round(avoided_dd_score, 1),
        "timing_score": None,
        "thesis_score": round(thesis_score, 1),
        "opportunity_cost_score": round(opp_cost_score, 1),
        "rule_consistency_score": round(rule_score, 1),
        "explanation": explanation,
    }


def _score_trim(outcome: dict, decision: dict) -> dict:
    """Score a TRIM decision — like SELL but softer penalty for missed upside."""
    result = _score_sell(outcome, decision)

    # TRIM is risk management — reduce penalty for subsequent gains
    fwd = outcome.get("forward_return", 0) or 0
    if fwd > 0:
        # Only half penalty for missed upside (trim is partial, not full exit)
        opp_penalty = result["opportunity_cost_score"]
        result["opportunity_cost_score"] = round((opp_penalty + 80) / 2, 1)

        # Recalculate
        result["quality_score"] = round(
            result["drawdown_score"] * 0.35 +
            result["opportunity_cost_score"] * 0.25 +
            result["rule_consistency_score"] * 0.20 +
            result["thesis_score"] * 0.20, 1
        )
        result["explanation"] += " [TRIM: reduced opp_cost penalty]"

    return result


def _score_hold(outcome: dict, decision: dict) -> dict:
    """Score a HOLD decision."""
    fwd = outcome.get("forward_return", 0) or 0
    alpha = outcome.get("alpha_return", 0) or 0
    max_dd = outcome.get("max_drawdown", 0) or 0

    # Post-hold return: good if positive
    return_score = _clamp(50 + fwd * 2.5)

    # Benchmark outperformance
    benchmark_score = _clamp(50 + alpha * 5)

    # Profit protection: bad if big drawdown during hold period
    protection_score = _clamp(100 + max_dd * 3.33)

    # Risk ignored penalty: if there was a thesis_broken alert but system held
    # (detected by checking if reason mentions any warning)
    reason = decision.get("reason", "").lower()
    risk_penalty = 0
    if "warning" in reason or "thesis" in reason:
        if fwd < -10:
            risk_penalty = 30  # held through warning and lost big

    dqs = (return_score * 0.35 + benchmark_score * 0.25
           + protection_score * 0.25 - risk_penalty * 0.15)
    dqs = _clamp(dqs)

    explanation = (f"HoldReturn={fwd:+.1f}%→{return_score:.0f}, Alpha={alpha:+.1f}%→{benchmark_score:.0f}, "
                   f"Protection→{protection_score:.0f}, RiskPenalty={risk_penalty:.0f}")

    return {
        "quality_score": round(dqs, 1),
        "return_score": round(return_score, 1),
        "benchmark_score": round(benchmark_score, 1),
        "drawdown_score": round(protection_score, 1),
        "timing_score": None,
        "thesis_score": None,
        "opportunity_cost_score": None,
        "rule_consistency_score": None,
        "explanation": explanation,
    }


def _score_no_buy(outcome: dict, decision: dict) -> dict:
    """Score a NO_BUY decision."""
    fwd = outcome.get("forward_return", 0) or 0
    alpha = outcome.get("alpha_return", 0) or 0
    max_gain = outcome.get("max_gain", 0) or 0
    max_dd = outcome.get("max_drawdown", 0) or 0
    rating = decision.get("rating", "")
    tracking = decision.get("tracking_priority", "")

    # Core: if stock went down or underperformed, NO_BUY was correct
    # fwd < 0 → good skip, fwd > 20 → bad skip
    base_score = _clamp(70 - fwd * 2)

    # Alpha-adjusted: if stock underperformed QQQ, extra good
    alpha_adj = _clamp(50 - alpha * 3)

    # Tracking value: penalize missing B-High that went up big
    tracking_penalty = 0
    if rating == "B" and tracking == "High":
        if max_gain > 50 and max_dd > -20:
            tracking_penalty = 40  # missed opportunity
        elif fwd > 30:
            tracking_penalty = 30
        elif fwd > 15:
            tracking_penalty = 15

    # For D/C: almost always correct to skip
    if rating in ("D", "C") and fwd < 5:
        base_score = max(base_score, 80)

    dqs = _clamp(base_score * 0.5 + alpha_adj * 0.30 - tracking_penalty * 0.20)

    explanation = (f"StockReturn={fwd:+.1f}%→{base_score:.0f}, Alpha={alpha:+.1f}%→{alpha_adj:.0f}, "
                   f"TrackingPenalty={tracking_penalty:.0f} [{rating}/{tracking}]")

    return {
        "quality_score": round(dqs, 1),
        "return_score": round(base_score, 1),
        "benchmark_score": round(alpha_adj, 1),
        "drawdown_score": None,
        "timing_score": None,
        "thesis_score": None,
        "opportunity_cost_score": round(tracking_penalty, 1) if tracking_penalty else None,
        "rule_consistency_score": None,
        "explanation": explanation,
    }


SCORERS = {
    "BUY": _score_buy,
    "SELL": _score_sell,
    "TRIM": _score_trim,
    "HOLD": _score_hold,
    "NO_BUY": _score_no_buy,
}


def score_decision(decision: dict, outcome: dict) -> dict:
    """Score a single decision+outcome pair."""
    dec_type = decision.get("decision_type", "")
    scorer = SCORERS.get(dec_type, _score_no_buy)
    return scorer(outcome, decision)


def score_all_pending():
    """Score all decisions that have outcomes but no quality scores yet."""
    with db.get_conn() as conn:
        # Find outcomes without quality scores
        rows = conn.execute("""
            SELECT o.*, d.* FROM decision_outcomes o
            JOIN decisions d ON o.decision_id = d.decision_id
            WHERE NOT EXISTS (
                SELECT 1 FROM decision_quality_scores q
                WHERE q.decision_id = o.decision_id AND q.horizon_days = o.horizon_days
            )
        """).fetchall()

    scored = 0
    for row in rows:
        row = dict(row)
        outcome = {
            "forward_return": row.get("forward_return"),
            "benchmark_return": row.get("benchmark_return"),
            "alpha_return": row.get("alpha_return"),
            "max_gain": row.get("max_gain"),
            "max_drawdown": row.get("max_drawdown"),
            "thesis_confirmed": row.get("thesis_confirmed"),
        }

        result = score_decision(row, outcome)

        with db.get_conn() as conn:
            conn.execute("""
                INSERT INTO decision_quality_scores (decision_id, evaluation_date,
                    horizon_days, decision_type, quality_score, return_score,
                    benchmark_score, drawdown_score, timing_score, thesis_score,
                    opportunity_cost_score, rule_consistency_score, explanation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (row["decision_id"], row.get("evaluation_date", ""),
                  row["horizon_days"], row["decision_type"],
                  result["quality_score"], result.get("return_score"),
                  result.get("benchmark_score"), result.get("drawdown_score"),
                  result.get("timing_score"), result.get("thesis_score"),
                  result.get("opportunity_cost_score"), result.get("rule_consistency_score"),
                  result["explanation"]))
        scored += 1

    logger.info(f"Scored {scored} decision outcomes")
    return scored
