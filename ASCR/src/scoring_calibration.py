"""Data-driven calibration tools for ASCR scoring weights.

This module does not auto-mutate live configuration. It evaluates candidate
weights against historical score/forward-return pairs and writes a report that
can be reviewed before changing `config/scoring.yaml`.
"""
import json
import os
import sqlite3
from datetime import datetime
from itertools import product
from collections import defaultdict

from src import config
from src.ranking_quality import _collect_score_return_pairs, _spearman_corr
from src.utils import get_logger

logger = get_logger("scoring_calibration")


DEFAULT_GRID = {
    "evidence": [0.20, 0.25, 0.30, 0.35, 0.40],
    "asymmetry": [0.15, 0.20, 0.25, 0.30, 0.35],
    "momentum": [0.10, 0.15, 0.20, 0.25, 0.30],
    "risk": [-0.10, -0.15, -0.20, -0.25],
}


def _candidate_score(pair: dict, weights: dict) -> float:
    return (
        pair.get("evidence_score", 0) * weights["evidence"]
        + pair.get("asymmetry_score", 0) * weights["asymmetry"]
        + pair.get("momentum_score", 0) * weights["momentum"]
        + pair.get("risk_score", 0) * weights["risk"]
    )


def _top_bottom_spread(pairs: list, weights: dict, n: int = 5) -> float:
    by_date = defaultdict(list)
    for pair in pairs:
        by_date[pair["date"]].append(pair)

    top_returns = []
    bottom_returns = []
    for date_pairs in by_date.values():
        if len(date_pairs) < n * 2:
            continue
        ranked = sorted(date_pairs, key=lambda p: _candidate_score(p, weights), reverse=True)
        top_returns.extend(p["forward_return"] for p in ranked[:n])
        bottom_returns.extend(p["forward_return"] for p in ranked[-n:])

    if not top_returns or not bottom_returns:
        return 0.0
    return sum(top_returns) / len(top_returns) - sum(bottom_returns) / len(bottom_returns)


def evaluate_weights(pairs: list, weights: dict) -> dict:
    """Evaluate one weight set on rank IC plus top/bottom spread."""
    if len(pairs) < 20:
        return {
            "weights": weights,
            "sample_size": len(pairs),
            "ic": 0.0,
            "top5_spread": 0.0,
            "objective": 0.0,
        }

    scores = [_candidate_score(pair, weights) for pair in pairs]
    returns = [pair["forward_return"] for pair in pairs]
    ic = _spearman_corr(scores, returns)
    spread = _top_bottom_spread(pairs, weights, n=5)
    objective = ic + spread / 100.0
    return {
        "weights": dict(weights),
        "sample_size": len(pairs),
        "ic": round(ic, 4),
        "top5_spread": round(spread, 4),
        "objective": round(objective, 4),
    }


def generate_candidates(grid: dict = None) -> list:
    """Generate constrained weight candidates.

    Positive weights are constrained near 0.75-0.95 total so risk can remain a
    true penalty without letting the total exposure explode.
    """
    grid = grid or DEFAULT_GRID
    candidates = []
    keys = ["evidence", "asymmetry", "momentum", "risk"]
    for values in product(*(grid[k] for k in keys)):
        weights = dict(zip(keys, values))
        positive_sum = weights["evidence"] + weights["asymmetry"] + weights["momentum"]
        if 0.70 <= positive_sum <= 0.95:
            candidates.append(weights)
    return candidates


def optimize_weights(pairs: list, top_n: int = 10) -> dict:
    if len(pairs) < 20:
        baseline = config.scoring().get("opportunity_weights", {})
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sample_size": len(pairs),
            "baseline": evaluate_weights(pairs, baseline) if baseline else None,
            "best": None,
            "top_candidates": [],
            "method": "grid_search_rank_ic_plus_top5_spread",
            "status": "insufficient_data",
            "notes": [
                "Need at least 20 score/forward-return pairs before calibration is meaningful.",
                "Run historical backtests or wait until live score history has known forward returns.",
            ],
        }

    candidates = generate_candidates()
    results = [evaluate_weights(pairs, weights) for weights in candidates]
    results.sort(key=lambda row: row["objective"], reverse=True)
    baseline = config.scoring().get("opportunity_weights", {})
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_size": len(pairs),
        "baseline": evaluate_weights(pairs, baseline) if baseline else None,
        "best": results[0] if results else None,
        "top_candidates": results[:top_n],
        "method": "grid_search_rank_ic_plus_top5_spread",
        "status": "ok",
        "notes": [
            "Use this as calibration evidence, not automatic production truth.",
            "Re-run after material universe changes or regime shifts.",
            "Promote weights only if they improve out-of-sample IC and top/bottom spread.",
        ],
    }


def collect_backtest_pairs(return_window: int = 20) -> list:
    """Collect calibration pairs from `data/backtest.sqlite` if available."""
    db_path = os.path.join(config.DATA_DIR, "backtest.sqlite")
    if not os.path.exists(db_path):
        return []

    return_col = f"return_{int(return_window)}d"
    allowed_cols = {"return_5d", "return_10d", "return_20d", "return_60d"}
    if return_col not in allowed_cols:
        raise ValueError(f"Unsupported return window for backtest calibration: {return_window}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"""
            SELECT
                s.eval_date AS date,
                s.ticker,
                s.evidence AS evidence_score,
                s.asymmetry AS asymmetry_score,
                s.momentum AS momentum_score,
                s.risk AS risk_score,
                f.{return_col} AS forward_return
            FROM bt_scores s
            JOIN bt_forward_returns f
              ON s.eval_date = f.eval_date AND s.ticker = f.ticker
            WHERE f.{return_col} IS NOT NULL
        """).fetchall()
    finally:
        conn.close()

    pairs = [dict(row) for row in rows]
    logger.info(f"Collected {len(pairs)} backtest calibration pairs from {db_path} ({return_col})")
    return pairs


def run_calibration(lookback_days: int = 180, return_window: int = 20, write_report: bool = True) -> dict:
    pairs = _collect_score_return_pairs(lookback_days=lookback_days, return_window=return_window)
    source = "live_scores"
    if len(pairs) < 20:
        bt_pairs = collect_backtest_pairs(return_window=return_window)
        if len(bt_pairs) > len(pairs):
            pairs = bt_pairs
            source = "historical_backtest"

    report = optimize_weights(pairs)
    report["lookback_days"] = lookback_days
    report["return_window"] = return_window
    report["data_source"] = source

    if write_report:
        out_dir = os.path.join(config.REPORTS_DIR, "scoring_calibration")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{datetime.now().strftime('%Y-%m-%d')}.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = path
        logger.info(f"Scoring calibration report saved: {path}")

    return report


if __name__ == "__main__":
    result = run_calibration()
    print(json.dumps(result, indent=2))
