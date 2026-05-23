"""Feedback-informed scoring adjustments from realized logs.

The scoring engine should learn from what actually happened after prior
decisions and signals. This module reads ASCR-H decision outcomes and ASCR
experience stats, then returns small bounded adjustments for live scoring.

It is intentionally not reinforcement learning. It is a conservative empirical
prior with shrinkage, sample-size floors, and hard caps.
"""
import os
import sqlite3
from functools import lru_cache

from src import config
from src.utils import get_logger

logger = get_logger("scoring_feedback")


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _confidence(sample_count: int, min_samples: int, full_confidence_samples: int) -> float:
    if sample_count < min_samples:
        return 0.0
    usable = max(full_confidence_samples - min_samples, 1)
    return min(1.0, (sample_count - min_samples + 1) / usable)


def _bounded_alpha(alpha_return: float, quality_score: float, sample_count: int,
                   cfg: dict, alpha_weight: float, quality_weight: float) -> float:
    min_samples = int(cfg.get("min_samples", 5))
    full_samples = int(cfg.get("full_confidence_samples", 30))
    cap = float(cfg.get("max_adjustment", 12))
    conf = _confidence(sample_count, min_samples, full_samples)
    if conf <= 0:
        return 0.0

    alpha_component = _safe_float(alpha_return) * float(alpha_weight)
    quality_component = (_safe_float(quality_score, 50) - 50.0) / 10.0 * float(quality_weight)
    raw = (alpha_component + quality_component) * conf
    return max(-cap, min(cap, raw))


def _paper_db_path(cfg: dict) -> str:
    path = cfg.get("paper_trader_db") or os.environ.get("ASCR_H_DB_PATH") or os.environ.get("PAPER_TRADER_DB")
    if not path:
        path = os.path.join(config.BASE_DIR, "..", "ASCR-H", "data", "ascr_h.sqlite")
    if not os.path.isabs(path):
        path = os.path.abspath(os.path.join(config.BASE_DIR, path))
    return path


def _read_rows(path: str, query: str, params=()) -> list:
    if not path or not os.path.exists(path):
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _as_of_filter(as_of_date: str = None) -> tuple:
    if not as_of_date:
        return "", ()
    return " AND o.evaluation_date <= ? AND d.decision_date < ? ", (as_of_date, as_of_date)


@lru_cache(maxsize=256)
def load_feedback_state(horizon_days: int = 20, as_of_date: str = None) -> dict:
    """Load aggregate feedback from local decision and experience logs.

    When `as_of_date` is provided, only outcomes whose evaluation_date was known
    on or before that date are included. This is required for walk-forward
    validation; otherwise feedback would explain the past with future outcomes.
    """
    cfg = config.scoring().get("feedback_alpha", {})
    paper_path = _paper_db_path(cfg)
    as_of_sql, as_of_params = _as_of_filter(as_of_date)

    rating_rows = _read_rows(paper_path, """
        SELECT
            d.rating,
            COUNT(*) AS sample_count,
            AVG(o.forward_return) AS avg_return,
            AVG(o.alpha_return) AS avg_alpha,
            AVG(q.quality_score) AS avg_quality
        FROM decisions d
        JOIN decision_outcomes o ON d.decision_id = o.decision_id
        LEFT JOIN decision_quality_scores q
          ON q.decision_id = d.decision_id AND q.horizon_days = o.horizon_days
        WHERE o.horizon_days = ?
          AND d.rating IS NOT NULL
          {as_of_sql}
        GROUP BY d.rating
    """.format(as_of_sql=as_of_sql), (horizon_days, *as_of_params))

    ticker_rows = _read_rows(paper_path, """
        SELECT
            d.ticker,
            COUNT(*) AS sample_count,
            AVG(o.forward_return) AS avg_return,
            AVG(o.alpha_return) AS avg_alpha,
            AVG(q.quality_score) AS avg_quality
        FROM decisions d
        JOIN decision_outcomes o ON d.decision_id = o.decision_id
        LEFT JOIN decision_quality_scores q
          ON q.decision_id = d.decision_id AND q.horizon_days = o.horizon_days
        WHERE o.horizon_days = ?
          AND d.ticker IS NOT NULL
          {as_of_sql}
        GROUP BY d.ticker
    """.format(as_of_sql=as_of_sql), (horizon_days, *as_of_params))

    type_rows = _read_rows(paper_path, """
        SELECT
            d.decision_type,
            COUNT(*) AS sample_count,
            AVG(o.forward_return) AS avg_return,
            AVG(o.alpha_return) AS avg_alpha,
            AVG(q.quality_score) AS avg_quality
        FROM decisions d
        JOIN decision_outcomes o ON d.decision_id = o.decision_id
        LEFT JOIN decision_quality_scores q
          ON q.decision_id = d.decision_id AND q.horizon_days = o.horizon_days
        WHERE o.horizon_days = ?
          AND d.decision_type IS NOT NULL
          {as_of_sql}
        GROUP BY d.decision_type
    """.format(as_of_sql=as_of_sql), (horizon_days, *as_of_params))

    experience_rows = []
    exp_path = os.path.join(config.DATA_DIR, "experience.sqlite")
    if os.path.exists(exp_path) and as_of_date is None:
        # signal_type_stats is a current aggregate without historical snapshots,
        # so it is valid for live scoring but not for strict as-of backtests.
        experience_rows = _read_rows(exp_path, """
            SELECT
                event_type,
                sample_count,
                avg_return_20d AS avg_return,
                avg_alpha_20d AS avg_alpha,
                prediction_accuracy
            FROM signal_type_stats
            WHERE sample_count IS NOT NULL
        """)

    state = {
        "horizon_days": horizon_days,
        "as_of_date": as_of_date,
        "paper_trader_db": paper_path,
        "by_rating": {row["rating"]: row for row in rating_rows if row.get("rating")},
        "by_ticker": {row["ticker"]: row for row in ticker_rows if row.get("ticker")},
        "by_decision_type": {row["decision_type"]: row for row in type_rows if row.get("decision_type")},
        "by_event_type": {row["event_type"]: row for row in experience_rows if row.get("event_type")},
    }
    logger.info(
        "Loaded feedback state%s: tickers=%d ratings=%d decision_types=%d event_types=%d",
        f" as_of={as_of_date}" if as_of_date else "",
        len(state["by_ticker"]), len(state["by_rating"]),
        len(state["by_decision_type"]), len(state["by_event_type"])
    )
    return state


def _event_type_adjustment(events: list, state: dict, cfg: dict) -> tuple:
    component_cfg = cfg.get("components", {})
    event_weight = float(component_cfg.get("event_type_alpha", 0.25))
    quality_weight = float(component_cfg.get("quality", 0.15))

    total = 0.0
    details = []
    seen = set()
    for event in events or []:
        event_type = event.get("event_type")
        if not event_type or event_type in seen:
            continue
        seen.add(event_type)
        stats = state.get("by_event_type", {}).get(event_type)
        if not stats:
            continue
        sample_count = int(stats.get("sample_count") or 0)
        alpha = _safe_float(stats.get("avg_alpha"))
        pred_acc = _safe_float(stats.get("prediction_accuracy"))
        adjustment = _bounded_alpha(alpha, 50 + pred_acc * 25, sample_count, cfg, event_weight, quality_weight)
        if adjustment:
            total += adjustment
            details.append({
                "event_type": event_type,
                "samples": sample_count,
                "avg_alpha": round(alpha, 2),
                "prediction_accuracy": round(pred_acc, 3),
                "adjustment": round(adjustment, 2),
            })
    return total, details


def feedback_adjustment(ticker: str, rating: str, recent_events: list,
                        cfg: dict = None, as_of_date: str = None) -> tuple:
    """Return bounded score adjustments and detail payload for one ticker."""
    cfg = cfg or config.scoring().get("feedback_alpha", {})
    if not cfg.get("enabled", True):
        return {"evidence": 0.0, "asymmetry": 0.0, "risk": 0.0}, {"enabled": False}

    horizon = int(cfg.get("horizon_days", 20))
    state = load_feedback_state(horizon, as_of_date)
    component_cfg = cfg.get("components", {})
    quality_weight = float(component_cfg.get("quality", 0.15))

    pieces = []
    evidence_adj = 0.0
    risk_adj = 0.0

    ticker_stats = state.get("by_ticker", {}).get(ticker)
    if ticker_stats:
        adj = _bounded_alpha(
            ticker_stats.get("avg_alpha"),
            ticker_stats.get("avg_quality"),
            int(ticker_stats.get("sample_count") or 0),
            cfg,
            float(component_cfg.get("ticker_alpha", 0.50)),
            quality_weight,
        )
        if adj:
            evidence_adj += adj
            pieces.append({"scope": "ticker", "key": ticker, "adjustment": round(adj, 2),
                           "samples": int(ticker_stats.get("sample_count") or 0),
                           "avg_alpha": round(_safe_float(ticker_stats.get("avg_alpha")), 2)})

    rating_stats = state.get("by_rating", {}).get(rating)
    if rating_stats:
        adj = _bounded_alpha(
            rating_stats.get("avg_alpha"),
            rating_stats.get("avg_quality"),
            int(rating_stats.get("sample_count") or 0),
            cfg,
            float(component_cfg.get("rating_alpha", 0.25)),
            quality_weight,
        )
        if adj:
            evidence_adj += adj
            pieces.append({"scope": "rating", "key": rating, "adjustment": round(adj, 2),
                           "samples": int(rating_stats.get("sample_count") or 0),
                           "avg_alpha": round(_safe_float(rating_stats.get("avg_alpha")), 2)})

    event_adj, event_details = _event_type_adjustment(recent_events, state, cfg)
    if event_adj:
        evidence_adj += event_adj
        pieces.extend({"scope": "event_type", **item} for item in event_details)

    cap = float(cfg.get("max_adjustment", 12))
    evidence_adj = max(-cap, min(cap, evidence_adj))
    if evidence_adj < 0:
        risk_adj = min(cap / 2, abs(evidence_adj) * 0.5)

    adjustments = {
        "evidence": evidence_adj,
        "asymmetry": max(-cap / 2, min(cap / 2, evidence_adj * 0.35)),
        "risk": risk_adj,
    }
    details = {
        "enabled": True,
        "horizon_days": horizon,
        "as_of_date": as_of_date,
        "adjustments": {k: round(v, 2) for k, v in adjustments.items()},
        "pieces": pieces[:8],
    }
    return adjustments, details
