"""Market shock post-review.

This module evaluates whether a broad selloff should have been treated as
warning, risk-off, or a controlled dip-buying setup after forward returns are
known. It is report-only and does not mutate production thresholds.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime

from src import config, db
from src.bubble_detector import LEVELS
from src.utils import get_logger

logger = get_logger("shock_review")

DEFAULT_WINDOWS = (5, 10)
DEFAULT_DIP_THRESHOLD_PCT = -8.0
DEFAULT_HIGH_RISK_THRESHOLD = 60.0


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _plain_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}%"


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _positive_pct(values: list[float]) -> float | None:
    return sum(1 for v in values if v > 0) / len(values) * 100 if values else None


def _price_dates(conn, tickers: list[str], min_coverage: float) -> list[str]:
    min_count = max(1, int(len(tickers) * min_coverage))
    rows = conn.execute(
        """
        SELECT date, COUNT(DISTINCT ticker) AS ticker_count
        FROM prices
        GROUP BY date
        HAVING ticker_count >= ?
        ORDER BY date ASC
        """,
        (min_count,),
    ).fetchall()
    return [r["date"] for r in rows]


def _resolve_shock_date(
    conn,
    requested_date: str | None,
    tickers: list[str],
    min_coverage: float,
) -> str | None:
    dates = _price_dates(conn, tickers, min_coverage)
    if not dates:
        return None
    if not requested_date:
        return dates[-1]
    eligible = [d for d in dates if d <= requested_date]
    return eligible[-1] if eligible else None


def _score_map(conn, as_of_date: str) -> dict[str, dict]:
    try:
        rows = conn.execute(
            """
            SELECT s.*
            FROM scores s
            INNER JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM scores
                WHERE date <= ?
                GROUP BY ticker
            ) latest
              ON s.ticker = latest.ticker AND s.date = latest.max_date
            """,
            (as_of_date,),
        ).fetchall()
    except Exception:
        return {}
    return {r["ticker"]: dict(r) for r in rows}


def _quality_bucket(score: dict | None) -> str:
    if not score:
        return "unknown"
    rating = str(score.get("rating") or "").upper()
    evidence = float(score.get("evidence_score") or 0)
    opportunity = float(score.get("opportunity_score") or 0)
    risk = float(score.get("risk_score") or 0)
    if risk >= DEFAULT_HIGH_RISK_THRESHOLD:
        return "high_risk"
    if rating in {"S", "A"} or (evidence >= 75 and opportunity >= 60):
        return "quality"
    return "speculative"


def _level_for_changes(changes: list[dict]) -> tuple[str | None, str]:
    if not changes:
        return None, "none"
    pct_declining = sum(1 for c in changes if c["change_pct"] < 0) / len(changes)
    for level_name in ("MELTDOWN", "DANGER", "WARNING"):
        level = LEVELS[level_name]
        pct_crash = (
            sum(1 for c in changes if c["change_pct"] <= level["crash_threshold"] * 100)
            / len(changes)
        )
        if pct_declining >= level["pct_declining"] and pct_crash >= level["pct_crash"]:
            return level_name, level["action"]
    return None, "none"


def shock_snapshot(
    shock_date: str | None = None,
    lookback_days: int = 1,
    tickers: list[str] | None = None,
    min_coverage: float = 0.5,
) -> dict:
    """Build breadth stats for the shock date."""
    tickers = tickers or config.all_tickers(include_benchmarks=False)
    with db.get_conn() as conn:
        resolved_date = _resolve_shock_date(conn, shock_date, tickers, min_coverage)
        if not resolved_date:
            return {
                "requested_date": shock_date,
                "shock_date": None,
                "level": None,
                "action": "none",
                "stats": {},
                "changes": [],
            }
        scores = _score_map(conn, resolved_date)
        changes = []
        for ticker in tickers:
            rows = conn.execute(
                """
                SELECT date, close
                FROM prices
                WHERE ticker = ? AND date <= ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (ticker, resolved_date, lookback_days + 1),
            ).fetchall()
            if len(rows) <= lookback_days:
                continue
            current = float(rows[0]["close"] or 0)
            previous = float(rows[lookback_days]["close"] or 0)
            if current <= 0 or previous <= 0:
                continue
            change_pct = (current - previous) / previous * 100
            score = scores.get(ticker)
            changes.append(
                {
                    "ticker": ticker,
                    "date": rows[0]["date"],
                    "price": current,
                    "previous_price": previous,
                    "change_pct": change_pct,
                    "quality_bucket": _quality_bucket(score),
                    "rating": (score or {}).get("rating"),
                    "opportunity": (score or {}).get("opportunity_score"),
                    "risk": (score or {}).get("risk_score"),
                }
            )

    level, action = _level_for_changes(changes)
    values = [c["change_pct"] for c in changes]
    declining = sum(1 for c in changes if c["change_pct"] < 0)
    drop_5 = sum(1 for c in changes if c["change_pct"] <= -5)
    drop_8 = sum(1 for c in changes if c["change_pct"] <= -8)
    drop_10 = sum(1 for c in changes if c["change_pct"] <= -10)
    worst = min(changes, key=lambda c: c["change_pct"], default=None)
    best = max(changes, key=lambda c: c["change_pct"], default=None)
    stats = {
        "total": len(changes),
        "declining": declining,
        "pct_declining": declining / len(changes) * 100 if changes else 0,
        "drop_5pct": drop_5,
        "pct_drop_5pct": drop_5 / len(changes) * 100 if changes else 0,
        "drop_8pct": drop_8,
        "pct_drop_8pct": drop_8 / len(changes) * 100 if changes else 0,
        "drop_10pct": drop_10,
        "pct_drop_10pct": drop_10 / len(changes) * 100 if changes else 0,
        "avg_change_pct": _avg(values),
        "median_change_pct": _median(values),
        "worst_ticker": worst["ticker"] if worst else None,
        "worst_change_pct": worst["change_pct"] if worst else None,
        "best_ticker": best["ticker"] if best else None,
        "best_change_pct": best["change_pct"] if best else None,
    }
    return {
        "requested_date": shock_date,
        "shock_date": resolved_date,
        "lookback_days": lookback_days,
        "level": level,
        "action": action,
        "stats": stats,
        "changes": sorted(changes, key=lambda c: c["change_pct"]),
    }


def _future_return(conn, ticker: str, shock_date: str, window: int) -> dict | None:
    rows = conn.execute(
        """
        SELECT date, close
        FROM prices
        WHERE ticker = ? AND date >= ?
        ORDER BY date ASC
        """,
        (ticker, shock_date),
    ).fetchall()
    if len(rows) <= window:
        return None
    entry = float(rows[0]["close"] or 0)
    future = float(rows[window]["close"] or 0)
    if entry <= 0 or future <= 0:
        return None
    return {
        "date": rows[window]["date"],
        "return_pct": (future - entry) / entry * 100,
        "entry_price": entry,
        "future_price": future,
    }


def _summarize_group(name: str, rows: list[dict]) -> dict:
    values = [r["return_pct"] for r in rows]
    worst = min(rows, key=lambda r: r["return_pct"], default=None)
    best = max(rows, key=lambda r: r["return_pct"], default=None)
    return {
        "name": name,
        "count": len(rows),
        "avg_return_pct": _avg(values),
        "median_return_pct": _median(values),
        "positive_pct": _positive_pct(values),
        "best_ticker": best["ticker"] if best else None,
        "best_return_pct": best["return_pct"] if best else None,
        "worst_ticker": worst["ticker"] if worst else None,
        "worst_return_pct": worst["return_pct"] if worst else None,
    }


def forward_outcomes(
    snapshot: dict,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    dip_threshold_pct: float = DEFAULT_DIP_THRESHOLD_PCT,
) -> list[dict]:
    """Measure post-shock returns for each requested trading-day window."""
    shock_date = snapshot.get("shock_date")
    if not shock_date:
        return []
    changes = snapshot.get("changes", [])
    change_by_ticker = {c["ticker"]: c for c in changes}
    outcomes = []
    with db.get_conn() as conn:
        for window in windows:
            rows = []
            pending = []
            for ticker, change in change_by_ticker.items():
                fwd = _future_return(conn, ticker, shock_date, window)
                if not fwd:
                    pending.append(ticker)
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "shock_change_pct": change["change_pct"],
                        "quality_bucket": change["quality_bucket"],
                        "rating": change.get("rating"),
                        "risk": change.get("risk"),
                        **fwd,
                    }
                )

            drop_rows = [r for r in rows if r["shock_change_pct"] <= dip_threshold_pct]
            quality_dips = [r for r in drop_rows if r["quality_bucket"] == "quality"]
            risky_dips = [r for r in drop_rows if r["quality_bucket"] == "high_risk"]
            outcomes.append(
                {
                    "window": window,
                    "completed": bool(rows) and not pending,
                    "available": len(rows),
                    "pending": len(pending),
                    "pending_tickers": sorted(pending),
                    "future_date": rows[0]["date"] if rows else None,
                    "groups": {
                        "universe": _summarize_group("universe", rows),
                        "shock_drops": _summarize_group("shock_drops", drop_rows),
                        "quality_dips": _summarize_group("quality_dips", quality_dips),
                        "high_risk_dips": _summarize_group("high_risk_dips", risky_dips),
                    },
                }
            )
    return outcomes


def policy_read(snapshot: dict, outcomes: list[dict]) -> dict:
    """Turn completed forward outcomes into a policy recommendation."""
    completed = [o for o in outcomes if o.get("completed")]
    if not completed:
        return {
            "stance": "wait_for_forward_data",
            "threshold_recommendation": "do_not_change_thresholds_yet",
            "dip_buying": "disabled_until_forward_data_confirms_rebound",
            "reason": "Need at least one complete 5d/10d forward window before judging the warning.",
        }

    latest = completed[-1]
    universe = latest["groups"]["universe"]
    quality = latest["groups"]["quality_dips"]
    universe_avg = universe.get("avg_return_pct")
    universe_pos = universe.get("positive_pct")
    quality_avg = quality.get("avg_return_pct")
    quality_pos = quality.get("positive_pct")
    quality_count = quality.get("count", 0)

    if universe_avg is not None and (universe_avg <= -3 or (universe_pos or 0) < 40):
        return {
            "stance": "warning_should_block_buys",
            "threshold_recommendation": "keep_or_lower_warning_threshold",
            "dip_buying": "disabled",
            "reason": (
                f"{latest['window']}d follow-through was weak: universe avg "
                f"{universe_avg:+.1f}%, positive {universe_pos:.0f}%."
            ),
        }

    quality_outperformed = (
        quality_avg is not None
        and universe_avg is not None
        and quality_avg - universe_avg >= 2
    )
    if (
        quality_count >= 2
        and quality_avg is not None
        and quality_avg >= 3
        and (quality_pos or 0) >= 60
        and quality_outperformed
    ):
        return {
            "stance": "controlled_dip_buying_allowed",
            "threshold_recommendation": "keep_warning_threshold",
            "dip_buying": "allow_only_quality_dips_after_stabilization",
            "reason": (
                f"Quality dips recovered better than the universe over {latest['window']}d: "
                f"{quality_avg:+.1f}% vs {universe_avg:+.1f}%."
            ),
        }

    if universe_avg is not None and universe_avg >= 3 and (universe_pos or 0) >= 60:
        return {
            "stance": "warning_may_be_too_sensitive_for_this_event",
            "threshold_recommendation": "consider_raising_warning_threshold_after_more_samples",
            "dip_buying": "watchlist_only_unless_quality_dips_outperform",
            "reason": (
                f"The broad warning recovered over {latest['window']}d, but quality dips "
                "did not clearly outperform."
            ),
        }

    return {
        "stance": "warning_should_pause_new_risk",
        "threshold_recommendation": "keep_warning_threshold",
        "dip_buying": "disabled_or_manual_only",
        "reason": (
            f"{latest['window']}d outcome was mixed: universe avg "
            f"{_pct(universe_avg)}, positive {universe_pos:.0f}%."
        ),
    }


def build_review(
    shock_date: str | None = None,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    lookback_days: int = 1,
    write_report: bool = False,
) -> dict:
    snapshot = shock_snapshot(shock_date=shock_date, lookback_days=lookback_days)
    outcomes = forward_outcomes(snapshot, windows=windows)
    review = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot": snapshot,
        "outcomes": outcomes,
        "policy_read": policy_read(snapshot, outcomes),
    }
    if write_report:
        path = write_review_report(review)
        review["report_path"] = path
    return review


def render_report(review: dict) -> str:
    snapshot = review["snapshot"]
    stats = snapshot.get("stats", {})
    policy = review["policy_read"]
    lines = [
        f"# Market Shock Review - {snapshot.get('shock_date') or 'n/a'}",
        "",
        f"_Generated: {review['generated_at']}_",
        "",
        "## Shock Snapshot",
        "",
        f"- Level: {snapshot.get('level') or 'NORMAL'} ({snapshot.get('action', 'none')})",
        f"- Lookback: {snapshot.get('lookback_days', 1)} trading day(s)",
        (
            f"- Breadth: {stats.get('declining', 0)}/{stats.get('total', 0)} declining "
            f"({stats.get('pct_declining', 0):.1f}%)"
        ),
        (
            f"- Drops: >5% {stats.get('drop_5pct', 0)} "
            f"({stats.get('pct_drop_5pct', 0):.1f}%), "
            f">8% {stats.get('drop_8pct', 0)} ({stats.get('pct_drop_8pct', 0):.1f}%), "
            f">10% {stats.get('drop_10pct', 0)} ({stats.get('pct_drop_10pct', 0):.1f}%)"
        ),
        (
            f"- Avg/median change: {_pct(stats.get('avg_change_pct'))} / "
            f"{_pct(stats.get('median_change_pct'))}"
        ),
        (
            f"- Worst/best: {stats.get('worst_ticker') or 'n/a'} "
            f"{_pct(stats.get('worst_change_pct'))} / "
            f"{stats.get('best_ticker') or 'n/a'} {_pct(stats.get('best_change_pct'))}"
        ),
        "",
        "## Forward Outcomes",
        "",
        "| Window | Status | Universe Avg | Universe Positive | Shock Drops Avg | Quality Dips Avg | High-Risk Dips Avg |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for outcome in review.get("outcomes", []):
        groups = outcome["groups"]
        status = "complete" if outcome["completed"] else f"pending {outcome['pending']}"
        lines.append(
            f"| {outcome['window']}d | {status} | "
            f"{_pct(groups['universe']['avg_return_pct'])} | "
            f"{_plain_pct(groups['universe']['positive_pct'])} | "
            f"{_pct(groups['shock_drops']['avg_return_pct'])} | "
            f"{_pct(groups['quality_dips']['avg_return_pct'])} | "
            f"{_pct(groups['high_risk_dips']['avg_return_pct'])} |"
        )

    lines.extend(
        [
            "",
            "## Policy Read",
            "",
            f"- Stance: {policy['stance']}",
            f"- Thresholds: {policy['threshold_recommendation']}",
            f"- Dip buying: {policy['dip_buying']}",
            f"- Reason: {policy['reason']}",
            "",
            "## Interpretation Rules",
            "",
            "- Do not change production thresholds until at least one 5d/10d window is complete.",
            "- Raise WARNING only if repeated warnings recover quickly and do not lead to follow-through selling.",
            "- Allow dip buying only when high-quality drops rebound better than the universe, not merely because everything fell.",
            "- If broad follow-through is negative, WARNING should block new buys and add-capital suggestions.",
        ]
    )
    return "\n".join(lines)


def write_review_report(review: dict) -> str:
    shock_date = review["snapshot"].get("shock_date") or "unknown"
    report_dir = os.path.join(config.REPORTS_DIR, "shock_review")
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"{shock_date}.md")
    with open(path, "w") as f:
        f.write(render_report(review))
    logger.info(f"Shock review saved: {path}")
    return path


def _parse_windows(raw: str) -> tuple[int, ...]:
    windows = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            windows.append(int(part))
    return tuple(windows) or DEFAULT_WINDOWS


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Review a market shock after forward returns are known.")
    parser.add_argument("--date", help="Shock date. Non-trading dates resolve to latest prior price date.")
    parser.add_argument("--windows", default="5,10", help="Comma-separated forward trading-day windows.")
    parser.add_argument("--lookback-days", type=int, default=1)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    review = build_review(
        shock_date=args.date,
        windows=_parse_windows(args.windows),
        lookback_days=args.lookback_days,
        write_report=args.write_report,
    )
    if args.json:
        print(json.dumps(review, indent=2, sort_keys=True, default=str))
    else:
        print(render_report(review))


if __name__ == "__main__":
    main()
