"""Ablation and walk-forward validation for scoring changes.

This module answers a narrow question: did the proposed scoring variants improve
rank quality on historical data? It uses existing backtest and ASCR-H logs
and writes reports only. It does not change production scoring.
"""
import json
import os
from collections import defaultdict
from datetime import datetime

from src import config
from src.ranking_quality import _spearman_corr
from src.scoring_calibration import collect_backtest_pairs
from src.scoring_feedback import load_feedback_state, _bounded_alpha
from src.utils import get_logger

logger = get_logger("scoring_ablation")


BASELINE_WEIGHTS = {"evidence": 0.30, "asymmetry": 0.30, "momentum": 0.25, "risk": -0.15}
CALIBRATED_WEIGHTS = {"evidence": 0.35, "asymmetry": 0.30, "momentum": 0.25, "risk": -0.10}


def _score(pair: dict, weights: dict) -> float:
    return (
        pair.get("evidence_score", 0) * weights["evidence"]
        + pair.get("asymmetry_score", 0) * weights["asymmetry"]
        + pair.get("momentum_score", 0) * weights["momentum"]
        + pair.get("risk_score", 0) * weights["risk"]
    )


def _rating(score: float, evidence: float, asymmetry: float, risk: float) -> str:
    """Offline rating approximation for distribution checks."""
    if score >= 75 and evidence >= 60 and asymmetry >= 50 and risk <= 30:
        return "S"
    if score >= 60 and evidence >= 50 and risk <= 35:
        return "A"
    if score >= 45:
        return "B"
    if score >= 25:
        return "C"
    return "D"


def _date_splits(pairs: list, folds: int = 4) -> list:
    dates = sorted({p["date"] for p in pairs})
    if not dates:
        return []
    fold_size = max(len(dates) // folds, 1)
    splits = []
    for i in range(folds):
        start = i * fold_size
        end = len(dates) if i == folds - 1 else (i + 1) * fold_size
        fold_dates = set(dates[start:end])
        if fold_dates:
            splits.append({
                "name": f"fold_{i+1}",
                "start": min(fold_dates),
                "end": max(fold_dates),
                "dates": fold_dates,
            })
    return splits


def _top_bottom_spread(rows: list, score_key: str, n: int = 5) -> dict:
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)

    top = []
    bottom = []
    for date_rows in by_date.values():
        if len(date_rows) < n * 2:
            continue
        ranked = sorted(date_rows, key=lambda r: r[score_key], reverse=True)
        top.extend(r["forward_return"] for r in ranked[:n])
        bottom.extend(r["forward_return"] for r in ranked[-n:])

    if not top or not bottom:
        return {"top_avg": None, "bottom_avg": None, "spread": None, "observations": 0}
    top_avg = sum(top) / len(top)
    bottom_avg = sum(bottom) / len(bottom)
    return {
        "top_avg": round(top_avg, 4),
        "bottom_avg": round(bottom_avg, 4),
        "spread": round(top_avg - bottom_avg, 4),
        "top_hit_rate": round(sum(1 for r in top if r > 0) / len(top) * 100, 2),
        "bottom_hit_rate": round(sum(1 for r in bottom if r > 0) / len(bottom) * 100, 2),
        "observations": len(top),
    }


def _evaluate_rows(rows: list, score_key: str) -> dict:
    if len(rows) < 20:
        return {"sample_size": len(rows), "ic": None, "top5": _top_bottom_spread(rows, score_key)}

    scores = [row[score_key] for row in rows]
    returns = [row["forward_return"] for row in rows]
    ic = _spearman_corr(scores, returns)
    by_rating = defaultdict(list)
    for row in rows:
        by_rating[row[f"{score_key}_rating"]].append(row["forward_return"])

    rating_summary = {}
    for rating in ["S", "A", "B", "C", "D"]:
        vals = by_rating.get(rating, [])
        rating_summary[rating] = {
            "count": len(vals),
            "avg_return": round(sum(vals) / len(vals), 4) if vals else None,
            "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 2) if vals else None,
        }

    return {
        "sample_size": len(rows),
        "ic": round(ic, 4),
        "top5": _top_bottom_spread(rows, score_key),
        "rating_distribution": {r: rating_summary[r]["count"] for r in rating_summary},
        "rating_returns": rating_summary,
    }


def _feedback_delta(pair: dict, feedback_state: dict, feedback_cfg: dict) -> float:
    ticker_stats = feedback_state.get("by_ticker", {}).get(pair["ticker"])
    rating_stats = feedback_state.get("by_rating", {}).get(pair.get("baseline_rating"))
    components = feedback_cfg.get("components", {})
    quality_weight = float(components.get("quality", 0.15))
    total = 0.0

    if ticker_stats:
        total += _bounded_alpha(
            ticker_stats.get("avg_alpha"),
            ticker_stats.get("avg_quality"),
            int(ticker_stats.get("sample_count") or 0),
            feedback_cfg,
            float(components.get("ticker_alpha", 0.50)),
            quality_weight,
        )
    if rating_stats:
        total += _bounded_alpha(
            rating_stats.get("avg_alpha"),
            rating_stats.get("avg_quality"),
            int(rating_stats.get("sample_count") or 0),
            feedback_cfg,
            float(components.get("rating_alpha", 0.25)),
            quality_weight,
        )
    cap = float(feedback_cfg.get("max_adjustment", 12))
    return max(-cap, min(cap, total))


def build_ablation_rows(return_window: int = 20) -> list:
    pairs = collect_backtest_pairs(return_window=return_window)
    feedback_cfg = config.scoring().get("feedback_alpha", {})
    horizon = int(feedback_cfg.get("horizon_days", 20))
    rows = []

    for pair in pairs:
        row = dict(pair)
        baseline = _score(row, BASELINE_WEIGHTS)
        calibrated = _score(row, CALIBRATED_WEIGHTS)
        row["baseline_score"] = baseline
        row["baseline_rating"] = _rating(
            baseline, row["evidence_score"], row["asymmetry_score"], row["risk_score"]
        )

        feedback_state = load_feedback_state(horizon, row["date"])
        feedback_delta = _feedback_delta(row, feedback_state, feedback_cfg)
        feedback_evidence = max(0, min(100, row["evidence_score"] + feedback_delta))
        feedback_asymmetry = max(0, min(100, row["asymmetry_score"] + feedback_delta * 0.35))
        feedback_risk = row["risk_score"] + (abs(feedback_delta) * 0.5 if feedback_delta < 0 else 0)
        feedback_risk = max(0, min(100, feedback_risk))

        feedback_row = {
            **row,
            "evidence_score": feedback_evidence,
            "asymmetry_score": feedback_asymmetry,
            "risk_score": feedback_risk,
        }
        feedback_score = _score(feedback_row, BASELINE_WEIGHTS)
        combined_score = _score(feedback_row, CALIBRATED_WEIGHTS)

        row["calibrated_score"] = calibrated
        row["feedback_score"] = feedback_score
        row["combined_score"] = combined_score
        row["feedback_delta"] = feedback_delta

        for key in ["calibrated_score", "feedback_score", "combined_score"]:
            row[f"{key}_rating"] = _rating(key and row[key], feedback_evidence, feedback_asymmetry, feedback_risk)
        row["baseline_score_rating"] = row["baseline_rating"]

        rows.append(row)

    return rows


def run_ablation(return_window: int = 20, folds: int = 4, write_report: bool = True) -> dict:
    rows = build_ablation_rows(return_window=return_window)
    variants = {
        "baseline": "baseline_score",
        "calibrated_weights": "calibrated_score",
        "feedback_alpha": "feedback_score",
        "combined": "combined_score",
    }

    overall = {name: _evaluate_rows(rows, key) for name, key in variants.items()}

    fold_reports = []
    for split in _date_splits(rows, folds=folds):
        fold_rows = [row for row in rows if row["date"] in split["dates"]]
        fold_reports.append({
            "name": split["name"],
            "start": split["start"],
            "end": split["end"],
            "sample_size": len(fold_rows),
            "variants": {name: _evaluate_rows(fold_rows, key) for name, key in variants.items()},
        })

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "return_window": return_window,
        "sample_size": len(rows),
        "date_range": {
            "start": min((row["date"] for row in rows), default=None),
            "end": max((row["date"] for row in rows), default=None),
        },
        "feedback_mode": "strict_as_of_evaluation_date",
        "variants": overall,
        "walk_forward_folds": fold_reports,
        "interpretation": _interpret(overall, fold_reports),
    }

    if write_report:
        out_dir = os.path.join(config.REPORTS_DIR, "scoring_ablation")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{datetime.now().strftime('%Y-%m-%d')}.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        report["report_path"] = path
        logger.info(f"Scoring ablation report saved: {path}")

    return report


def _interpret(overall: dict, folds: list) -> dict:
    base = overall.get("baseline", {})
    base_ic = base.get("ic") or 0
    base_spread = (base.get("top5") or {}).get("spread") or 0
    rows = []
    for name, metrics in overall.items():
        ic = metrics.get("ic") or 0
        spread = (metrics.get("top5") or {}).get("spread") or 0
        positive_folds = 0
        for fold in folds:
            variant = fold["variants"].get(name, {})
            fold_ic = variant.get("ic") or 0
            fold_spread = (variant.get("top5") or {}).get("spread") or 0
            base_fold = fold["variants"].get("baseline", {})
            base_fold_ic = base_fold.get("ic") or 0
            base_fold_spread = (base_fold.get("top5") or {}).get("spread") or 0
            if fold_ic >= base_fold_ic and fold_spread >= base_fold_spread:
                positive_folds += 1
        rows.append({
            "variant": name,
            "ic_delta": round(ic - base_ic, 4),
            "spread_delta": round(spread - base_spread, 4),
            "objective_delta": round((ic - base_ic) + (spread - base_spread) / 100.0, 4),
            "folds_not_worse_than_baseline": positive_folds,
        })
    rows.sort(key=lambda r: (r["folds_not_worse_than_baseline"], r["objective_delta"]), reverse=True)
    return {
        "baseline_ic": base_ic,
        "baseline_top5_spread": base_spread,
        "ranking": rows,
        "promotion_rule": "Promote only if a variant improves overall IC/spread and is not worse than baseline in most folds.",
    }


if __name__ == "__main__":
    print(json.dumps(run_ablation(), indent=2))
