"""Data-driven calibration tools for ASCR scoring weights.

This module does not auto-mutate live configuration. It evaluates candidate
weights against historical score/forward-return pairs and writes a report that
can be reviewed before changing `config/scoring.yaml`.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from itertools import product
from collections import defaultdict
from math import sqrt

from src import config, db
from src.event_deduper import canonical_news_title
from src.ranking_quality import _collect_score_return_pairs, _spearman_corr
from src.utils import get_logger

logger = get_logger("scoring_calibration")


DEFAULT_GRID = {
    "evidence": [0.20, 0.25, 0.30, 0.35, 0.40],
    "asymmetry": [0.15, 0.20, 0.25, 0.30, 0.35],
    "momentum": [0.10, 0.15, 0.20, 0.25, 0.30],
    "risk": [-0.10, -0.15, -0.20, -0.25],
}

STABILITY_TOP_N = 10
FLAT_OBJECTIVE_DELTA = 0.001
UNSTABLE_WEIGHT_STDDEV = 0.05

EVENT_SOURCE_GROUPS = {
    "authoritative": ["sec", "sec_filing", "form4", "press_release"],
    "news": ["news", "google_news"],
    "social": ["reddit"],
    "market": ["price_event"],
}

EVENT_TYPE_GROUPS = {
    "catalyst": ["major_contract", "contract", "guidance_raise", "partnership"],
    "fundamental": ["guidance", "earnings", "sec_filing", "financing"],
    "risk": ["dilution", "risk", "fraud_concern", "insider_sell"],
    "market": ["price_surge", "other"],
}

EVENT_SOURCE_SCALE_GRID = {
    "authoritative": [0.90, 1.00, 1.10],
    "news": [0.85, 1.00, 1.15],
    "social": [0.75, 1.00],
    "market": [0.75, 1.00, 1.25],
}

EVENT_TYPE_SCALE_GRID = {
    "catalyst": [0.90, 1.00, 1.10],
    "fundamental": [0.90, 1.00, 1.10],
    "risk": [0.90, 1.00, 1.10],
    "market": [0.80, 1.00, 1.20],
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


def _stddev(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _weight_stability(results: list, top_n: int = STABILITY_TOP_N) -> dict:
    top = results[:top_n]
    if len(top) < 2:
        return {
            "stable": True,
            "fallback_to_baseline": False,
            "objective_range_top_n": 0.0,
            "max_weight_stddev": 0.0,
            "weight_stddev": {},
            "reason": "fewer than two candidates",
        }

    objective_range = top[0]["objective"] - top[-1]["objective"]
    weight_keys = sorted({k for row in top for k in row.get("weights", {})})
    stddevs = {}
    for key in weight_keys:
        values = [float(row.get("weights", {}).get(key, 0.0)) for row in top]
        stddevs[key] = round(_stddev(values), 4)
    max_stddev = max(stddevs.values()) if stddevs else 0.0

    fallback = (
        objective_range <= FLAT_OBJECTIVE_DELTA
        and max_stddev >= UNSTABLE_WEIGHT_STDDEV
    )
    if fallback:
        reason = (
            f"top-{len(top)} objective range <= {FLAT_OBJECTIVE_DELTA} "
            f"and max weight stddev >= {UNSTABLE_WEIGHT_STDDEV}"
        )
    else:
        reason = "candidate surface has enough objective separation or low weight dispersion"

    return {
        "stable": not fallback,
        "fallback_to_baseline": fallback,
        "objective_range_top_n": round(objective_range, 4),
        "max_weight_stddev": round(max_stddev, 4),
        "weight_stddev": stddevs,
        "reason": reason,
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
    baseline_eval = evaluate_weights(pairs, baseline) if baseline else None
    stability = _weight_stability(results, top_n=min(top_n, STABILITY_TOP_N))
    selected = results[0] if results else None
    status = "ok"
    notes = [
        "Use this as calibration evidence, not automatic production truth.",
        "Re-run after material universe changes or regime shifts.",
        "Promote weights only if they improve out-of-sample IC and top/bottom spread.",
    ]
    if stability["fallback_to_baseline"] and baseline_eval:
        selected = baseline_eval
        status = "baseline_fallback_unstable"
        notes.append(
            "Top grid candidates are statistically indistinguishable and weight-dispersed; "
            "final selection falls back to baseline until the surface is sharper."
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_size": len(pairs),
        "baseline": baseline_eval,
        "grid_best": results[0] if results else None,
        "best": selected,
        "top_candidates": results[:top_n],
        "method": "grid_search_rank_ic_plus_top5_spread",
        "status": status,
        "stability": stability,
        "notes": notes,
    }


def _parse_date(raw):
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw)
        except Exception:
            return None
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
    return None


def _event_signal(event: dict, source_weights: dict, event_type_weights: dict,
                  event_cfg: dict, as_of: datetime, seen: set) -> float:
    headline = event.get("headline") or event.get("title") or ""
    event_key = canonical_news_title(headline) or event.get("hash") or event.get("url") or headline
    duplicate_penalty = 1.0
    if event_key:
        if event_key in seen:
            duplicate_penalty = 0.35
        seen.add(event_key)

    event_date = event.get("_event_date") or _parse_date(event.get("event_date") or event.get("date"))
    age_days = max((as_of - event_date).days, 0) if event_date else 0
    half_life = max(float(event_cfg.get("half_life_days", 14)), 1.0)
    decay = 0.5 ** (age_days / half_life)

    try:
        confidence = float(event.get("confidence") if event.get("confidence") is not None else 0.60)
    except (TypeError, ValueError):
        confidence = 0.60

    source = str(event.get("source") or "other").lower()
    event_type = str(event.get("event_type") or "other").lower()
    source_weight = float(source_weights.get(source, source_weights.get("other", 0.75)))
    type_weight = float(event_type_weights.get(event_type, event_type_weights.get("other", 0.70)))

    try:
        priced_in = float(event.get("priced_in_pct") if event.get("priced_in_pct") is not None else 25.0)
    except (TypeError, ValueError):
        priced_in = 25.0
    priced_in_multiplier = max(0.25, 1.0 - priced_in / 100.0)

    verdict_scores = event_cfg.get("verdict_scores", {})
    conviction_weights = event_cfg.get("conviction_weights", {})
    verdict = str(event.get("verdict") or "").upper()
    conviction = str(event.get("conviction") or "").upper()
    verdict_delta = float(verdict_scores.get(verdict, 0))
    conviction_weight = float(conviction_weights.get(conviction, 0.65 if verdict_delta else 1.0))

    def _num(name: str, default: float = 0.0) -> float:
        try:
            return float(event.get(name) if event.get(name) is not None else default)
        except (TypeError, ValueError):
            return default

    raw_signal = (
        _num("evidence_delta")
        + 0.50 * _num("asymmetry_delta")
        - _num("risk_delta")
        + verdict_delta * conviction_weight
    )
    if raw_signal > 0:
        raw_signal *= priced_in_multiplier

    return raw_signal * decay * confidence * source_weight * type_weight * duplicate_penalty


def _event_alpha_candidate_score(pair: dict, source_weights: dict,
                                 event_type_weights: dict, event_cfg: dict) -> float:
    as_of = _parse_date(pair.get("date")) or datetime.now()
    seen = set()
    return sum(
        _event_signal(event, source_weights, event_type_weights, event_cfg, as_of, seen)
        for event in pair.get("events", [])
    )


def _top_bottom_spread_event_alpha(pairs: list, source_weights: dict,
                                   event_type_weights: dict, event_cfg: dict, n: int = 5) -> float:
    by_date = defaultdict(list)
    for pair in pairs:
        by_date[pair["date"]].append(pair)

    top_returns = []
    bottom_returns = []
    for date_pairs in by_date.values():
        if len(date_pairs) < n * 2:
            continue
        ranked = sorted(
            date_pairs,
            key=lambda p: _event_alpha_candidate_score(p, source_weights, event_type_weights, event_cfg),
            reverse=True,
        )
        top_returns.extend(p["forward_return"] for p in ranked[:n])
        bottom_returns.extend(p["forward_return"] for p in ranked[-n:])

    if not top_returns or not bottom_returns:
        return 0.0
    return sum(top_returns) / len(top_returns) - sum(bottom_returns) / len(bottom_returns)


def _flatten_event_weights(source_weights: dict, event_type_weights: dict) -> dict:
    flattened = {}
    for key, value in source_weights.items():
        flattened[f"source.{key}"] = float(value)
    for key, value in event_type_weights.items():
        flattened[f"type.{key}"] = float(value)
    return flattened


def evaluate_event_alpha_weights(pairs: list, source_weights: dict,
                                 event_type_weights: dict, event_cfg: dict = None,
                                 scales: dict = None) -> dict:
    """Evaluate event-alpha source/type weights against forward returns."""
    event_cfg = event_cfg or {}
    if len(pairs) < 20:
        return {
            "source_weights": dict(source_weights),
            "event_type_weights": dict(event_type_weights),
            "weights": _flatten_event_weights(source_weights, event_type_weights),
            "scales": scales or {},
            "sample_size": len(pairs),
            "ic": 0.0,
            "top5_spread": 0.0,
            "objective": 0.0,
        }

    scores = [
        _event_alpha_candidate_score(pair, source_weights, event_type_weights, event_cfg)
        for pair in pairs
    ]
    returns = [pair["forward_return"] for pair in pairs]
    ic = _spearman_corr(scores, returns)
    spread = _top_bottom_spread_event_alpha(pairs, source_weights, event_type_weights, event_cfg, n=5)
    objective = ic + spread / 100.0
    return {
        "source_weights": dict(source_weights),
        "event_type_weights": dict(event_type_weights),
        "weights": _flatten_event_weights(source_weights, event_type_weights),
        "scales": scales or {},
        "sample_size": len(pairs),
        "ic": round(ic, 4),
        "top5_spread": round(spread, 4),
        "objective": round(objective, 4),
    }


def _apply_group_scales(base_weights: dict, groups: dict, scales: dict) -> dict:
    weights = {k: float(v) for k, v in base_weights.items()}
    for group_name, scale in scales.items():
        for key in groups.get(group_name, []):
            if key in weights:
                weights[key] = round(weights[key] * float(scale), 4)
    return weights


def generate_event_alpha_candidates(event_cfg: dict = None,
                                    source_scale_grid: dict = None,
                                    type_scale_grid: dict = None) -> list:
    """Generate source/type weight candidates from grouped multipliers."""
    event_cfg = event_cfg or config.scoring().get("event_alpha", {})
    source_weights = event_cfg.get("source_weights", {})
    event_type_weights = event_cfg.get("event_type_weights", {})
    source_scale_grid = source_scale_grid or EVENT_SOURCE_SCALE_GRID
    type_scale_grid = type_scale_grid or EVENT_TYPE_SCALE_GRID

    source_groups = list(source_scale_grid.keys())
    type_groups = list(type_scale_grid.keys())
    candidates = []
    for source_values in product(*(source_scale_grid[g] for g in source_groups)):
        source_scales = dict(zip(source_groups, source_values))
        scaled_sources = _apply_group_scales(source_weights, EVENT_SOURCE_GROUPS, source_scales)
        for type_values in product(*(type_scale_grid[g] for g in type_groups)):
            type_scales = dict(zip(type_groups, type_values))
            scaled_types = _apply_group_scales(event_type_weights, EVENT_TYPE_GROUPS, type_scales)
            candidates.append({
                "source_weights": scaled_sources,
                "event_type_weights": scaled_types,
                "scales": {
                    "source": source_scales,
                    "event_type": type_scales,
                },
            })
    return candidates


def optimize_event_alpha_weights(pairs: list, top_n: int = 10, event_cfg: dict = None) -> dict:
    event_cfg = event_cfg or config.scoring().get("event_alpha", {})
    source_weights = event_cfg.get("source_weights", {})
    event_type_weights = event_cfg.get("event_type_weights", {})
    baseline_eval = evaluate_event_alpha_weights(pairs, source_weights, event_type_weights, event_cfg)

    if len(pairs) < 20:
        return {
            "sample_size": len(pairs),
            "baseline": baseline_eval,
            "grid_best": None,
            "best": None,
            "top_candidates": [],
            "method": "grouped_grid_search_event_alpha_rank_ic_plus_top5_spread",
            "status": "insufficient_data",
            "notes": [
                "Need at least 20 event/forward-return pairs before event-alpha calibration is meaningful.",
                "This report does not mutate config/scoring.yaml.",
            ],
        }

    candidates = generate_event_alpha_candidates(event_cfg)
    results = [
        evaluate_event_alpha_weights(
            pairs,
            candidate["source_weights"],
            candidate["event_type_weights"],
            event_cfg,
            candidate["scales"],
        )
        for candidate in candidates
    ]
    results.sort(key=lambda row: row["objective"], reverse=True)
    stability = _weight_stability(results, top_n=min(top_n, STABILITY_TOP_N))
    selected = results[0] if results else None
    status = "ok"
    notes = [
        "Calibrates source_weights and event_type_weights with the same IC/spread objective as base scoring.",
        "Promote suggested weights only after out-of-sample validation; this report is evidence only.",
    ]
    if stability["fallback_to_baseline"]:
        selected = baseline_eval
        status = "baseline_fallback_unstable"
        notes.append(
            "Event-alpha grid is flat and weight-dispersed; final selection falls back to current config."
        )

    return {
        "sample_size": len(pairs),
        "baseline": baseline_eval,
        "grid_best": results[0] if results else None,
        "best": selected,
        "top_candidates": results[:top_n],
        "method": "grouped_grid_search_event_alpha_rank_ic_plus_top5_spread",
        "status": status,
        "stability": stability,
        "notes": notes,
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


def collect_event_alpha_pairs(score_pairs: list, event_lookback_days: int = 45) -> list:
    """Attach recent events to score/return pairs for event-alpha calibration."""
    dated_pairs = []
    for pair in score_pairs:
        pair_date = _parse_date(pair.get("date"))
        ticker = pair.get("ticker")
        if pair_date and ticker:
            dated_pairs.append((pair, pair_date, str(ticker)))
    if not dated_pairs:
        return []

    tickers = sorted({ticker for _, _, ticker in dated_pairs})
    min_pair_date = min(pair_date for _, pair_date, _ in dated_pairs)
    max_pair_date = max(pair_date for _, pair_date, _ in dated_pairs)
    start_date = (min_pair_date - timedelta(days=event_lookback_days)).strftime("%Y-%m-%d")
    end_date = max_pair_date.strftime("%Y-%m-%d")

    try:
        placeholders = ",".join("?" for _ in tickers)
        with db.get_conn() as conn:
            rows = conn.execute(f"""
                SELECT *
                FROM events
                WHERE ticker IN ({placeholders})
                  AND COALESCE(NULLIF(event_date, ''), date) >= ?
                  AND COALESCE(NULLIF(event_date, ''), date) <= ?
            """, (*tickers, start_date, end_date)).fetchall()
    except Exception as exc:
        logger.warning(f"Event-alpha pair collection failed: {exc}")
        return []

    events_by_ticker = defaultdict(list)
    for row in rows:
        event = dict(row)
        event_date = _parse_date(event.get("event_date") or event.get("date"))
        if not event_date:
            continue
        event["_event_date"] = event_date
        events_by_ticker[str(event.get("ticker"))].append(event)

    pairs = []
    for pair, pair_date, ticker in dated_pairs:
        window_start = pair_date - timedelta(days=event_lookback_days)
        recent_events = [
            event for event in events_by_ticker.get(ticker, [])
            if window_start <= event["_event_date"] <= pair_date
        ]
        if not recent_events:
            continue
        enriched = dict(pair)
        enriched["events"] = recent_events
        pairs.append(enriched)

    logger.info(
        f"Collected {len(pairs)} event-alpha calibration pairs "
        f"(event_lookback={event_lookback_days}d)"
    )
    return pairs


def run_calibration(lookback_days: int = 180, return_window: int = 20, write_report: bool = True) -> dict:
    pairs = _collect_score_return_pairs(lookback_days=lookback_days, return_window=return_window)
    live_pairs = list(pairs)
    source = "live_scores"
    if len(pairs) < 20:
        bt_pairs = collect_backtest_pairs(return_window=return_window)
        if len(bt_pairs) > len(pairs):
            pairs = bt_pairs
            source = "historical_backtest"

    report = optimize_weights(pairs)
    event_cfg = config.scoring().get("event_alpha", {})
    event_pairs = collect_event_alpha_pairs(live_pairs, event_lookback_days=int(event_cfg.get("lookback_days", 45)))
    report["event_alpha"] = optimize_event_alpha_weights(event_pairs, event_cfg=event_cfg)
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
