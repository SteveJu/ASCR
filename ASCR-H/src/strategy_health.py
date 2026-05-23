"""Strategy health — compute weekly health scores with exact DQS formula.

Overall DQS = 25% Buy + 20% Sell + 10% Trim + 15% Hold + 15% NoBuy + 10% RankingQuality + 5% Stability

Interpretation:
  80-100: healthy
  65-80:  usable but needs monitoring
  50-65:  unstable / needs review
  <50:    strategy may be broken, pause real-money usage

Stability = f(mean, stddev) of last 8 weeks of DQS.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from src import db
from src.utils import get_logger

logger = get_logger("strategy_health")

HORIZON_PRIMARY = 20


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _compute_stability(mode: str) -> float:
    """Stability from last 8 weekly DQS snapshots. High mean + low stddev = stable."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT overall_dqs FROM strategy_health
            WHERE mode=? AND overall_dqs IS NOT NULL
            ORDER BY date DESC LIMIT 8
        """, (mode,)).fetchall()

    values = [r[0] for r in rows if r[0] is not None]
    if len(values) < 2:
        return 50.0  # not enough history

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5

    # High mean (>60) and low std (<10) → stable (100)
    # Low mean or high std → unstable
    mean_component = _clamp((mean - 30) * 1.43)  # 30→0, 100→100
    std_component = _clamp(100 - std * 5)         # 0→100, 20→0
    return round(mean_component * 0.6 + std_component * 0.4, 1)


def _compute_ranking_quality(rows: list) -> float:
    """Check if higher ratings produce better returns (S > A > B > C > D)."""
    # Only look at BUY and NO_BUY decisions
    by_rating = defaultdict(list)
    for r in rows:
        fwd = r.get("forward_return")
        if fwd is None:
            continue
        rating = r.get("rating", "")
        if r["decision_type"] == "BUY":
            by_rating[rating].append(fwd)
        elif r["decision_type"] == "NO_BUY":
            by_rating[f"skip_{rating}"].append(fwd)

    # Rating tier monotonicity for bought stocks
    order = ["S", "A", "B"]
    avgs = []
    for rt in order:
        rets = by_rating.get(rt, [])
        if rets:
            avgs.append((rt, sum(rets) / len(rets)))

    if len(avgs) < 2:
        return 50.0

    # Check monotonicity
    violations = 0
    for i in range(len(avgs) - 1):
        if avgs[i][1] < avgs[i + 1][1]:
            violations += 1

    # Also: bought stocks should outperform skipped stocks at same rating
    for rt in order:
        bought = by_rating.get(rt, [])
        skipped = by_rating.get(f"skip_{rt}", [])
        if bought and skipped:
            if sum(bought) / len(bought) < sum(skipped) / len(skipped):
                violations += 1

    if violations == 0:
        return 90.0
    elif violations == 1:
        return 60.0
    elif violations == 2:
        return 35.0
    else:
        return 15.0


def compute_health(mode: str = "live_paper", lookback_days: int = None) -> dict:
    """Compute strategy health metrics."""
    if lookback_days is None:
        lookback_days = 365 if mode == "historical_backtest" else 90
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT q.*, d.decision_type, d.rating, d.tracking_priority, d.ticker,
                   d.decision_date, d.mode, d.reason,
                   o.forward_return, o.alpha_return, o.max_gain, o.max_drawdown
            FROM decision_quality_scores q
            JOIN decisions d ON q.decision_id = d.decision_id
            JOIN decision_outcomes o ON o.decision_id = q.decision_id AND o.horizon_days = q.horizon_days
            WHERE d.mode = ? AND d.decision_date >= ? AND q.horizon_days = ?
            ORDER BY d.decision_date
        """, (mode, cutoff, HORIZON_PRIMARY)).fetchall()

    if not rows:
        return {"error": "no data", "mode": mode}

    rows = [dict(r) for r in rows]

    # --- DQS by type ---
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["decision_type"]].append(r["quality_score"])

    type_dqs = {}
    for dtype in ["BUY", "SELL", "TRIM", "HOLD", "NO_BUY"]:
        scores = by_type.get(dtype, [])
        type_dqs[dtype] = round(sum(scores) / len(scores), 1) if scores else 0

    # --- Ranking quality ---
    ranking_quality = _compute_ranking_quality(rows)

    # --- Stability ---
    stability = _compute_stability(mode)

    # --- Overall DQS (exact formula) ---
    overall_dqs = (
        type_dqs.get("BUY", 0) * 0.25 +
        type_dqs.get("SELL", 0) * 0.20 +
        type_dqs.get("TRIM", 0) * 0.10 +
        type_dqs.get("HOLD", 0) * 0.15 +
        type_dqs.get("NO_BUY", 0) * 0.15 +
        ranking_quality * 0.10 +
        stability * 0.05
    )
    overall_dqs = round(overall_dqs, 1)

    # --- Warning level ---
    if overall_dqs >= 80:
        warning = "healthy"
    elif overall_dqs >= 65:
        warning = "monitoring"
    elif overall_dqs >= 50:
        warning = "unstable"
    else:
        warning = "broken"

    # --- False positive: BUY that underperformed QQQ >10%, or DD >-30%, or stopped out ---
    buys = [r for r in rows if r["decision_type"] == "BUY"]
    false_positives = [b for b in buys if (
        (b.get("alpha_return") is not None and b["alpha_return"] < -10)
        or (b.get("max_drawdown") is not None and b["max_drawdown"] < -30)
        or (b.get("forward_return") is not None and b["forward_return"] < -25)
    )]
    fp_rate = round(len(false_positives) / max(len(buys), 1) * 100, 1)

    # --- False negative: NO_BUY that outperformed QQQ >15% or fwd >35% ---
    no_buys = [r for r in rows if r["decision_type"] == "NO_BUY"]
    false_negatives = [n for n in no_buys if (
        (n.get("alpha_return") is not None and n["alpha_return"] > 15)
        or (n.get("forward_return") is not None and n["forward_return"] > 35)
    )]
    fn_rate = round(len(false_negatives) / max(len(no_buys), 1) * 100, 1)

    # --- Missed opportunities: NO_BUY or B-track with max_gain>50% and dd>-20%, or fwd>35% ---
    missed = [r for r in rows if r["decision_type"] == "NO_BUY" and (
        (r.get("max_gain") is not None and r["max_gain"] > 50
         and r.get("max_drawdown") is not None and r["max_drawdown"] > -20)
        or (r.get("forward_return") is not None and r["forward_return"] > 35)
    )]
    missed_score = round(max(0, 100 - len(missed) * 15), 1)

    # --- Exit quality: % of sells where stock fell afterward ---
    sell_outcomes = [r for r in rows if r["decision_type"] in ("SELL", "TRIM")]
    exit_quality = 50.0
    if sell_outcomes:
        good_sells = sum(1 for s in sell_outcomes if (s.get("forward_return") or 0) < 0)
        exit_quality = round(good_sells / len(sell_outcomes) * 100, 1)

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "mode": mode,
        "overall_dqs": overall_dqs,
        "buy_dqs": type_dqs.get("BUY", 0),
        "sell_dqs": type_dqs.get("SELL", 0),
        "trim_dqs": type_dqs.get("TRIM", 0),
        "hold_dqs": type_dqs.get("HOLD", 0),
        "no_buy_dqs": type_dqs.get("NO_BUY", 0),
        "rating_quality_score": round(ranking_quality, 1),
        "exit_quality_score": exit_quality,
        "missed_opportunity_score": missed_score,
        "false_positive_rate": fp_rate,
        "false_negative_rate": fn_rate,
        "stability_score": stability,
        "warning_level": warning,
        "sample_size": len(rows),
        "by_type_counts": {k: len(v) for k, v in by_type.items()},
        "missed_opportunities": [
            {"ticker": m["ticker"], "max_gain": m.get("max_gain", 0),
             "forward_return": m.get("forward_return", 0), "rating": m.get("rating", "")}
            for m in missed
        ],
        "false_positives_list": [
            {"ticker": f["ticker"], "return": f.get("forward_return", 0),
             "alpha": f.get("alpha_return", 0)}
            for f in false_positives
        ],
        "false_negatives_list": [
            {"ticker": f["ticker"], "return": f.get("forward_return", 0),
             "alpha": f.get("alpha_return", 0), "rating": f.get("rating", "")}
            for f in false_negatives
        ],
    }

    # Save
    with db.get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO strategy_health (date, mode, overall_dqs, buy_dqs, sell_dqs,
                trim_dqs, hold_dqs, no_buy_dqs, rating_quality_score, exit_quality_score,
                missed_opportunity_score, false_positive_rate, false_negative_rate,
                stability_score, warning_level, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (result["date"], mode, result["overall_dqs"], result["buy_dqs"],
              result["sell_dqs"], result["trim_dqs"], result["hold_dqs"],
              result["no_buy_dqs"], result["rating_quality_score"],
              result["exit_quality_score"], result["missed_opportunity_score"],
              result["false_positive_rate"], result["false_negative_rate"],
              result["stability_score"], result["warning_level"], ""))

    logger.info(f"Health [{mode}]: DQS={overall_dqs:.1f} ({warning}), "
                f"Buy={type_dqs.get('BUY',0):.1f} Sell={type_dqs.get('SELL',0):.1f} "
                f"Hold={type_dqs.get('HOLD',0):.1f} NoBuy={type_dqs.get('NO_BUY',0):.1f} "
                f"Ranking={ranking_quality:.1f} Stability={stability:.1f}")
    return result
