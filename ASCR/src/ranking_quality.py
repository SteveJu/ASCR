"""Ranking quality evaluator — does our scoring system actually predict returns?

Core question: Do high-scored stocks outperform low-scored stocks?
If not, the scoring system is noise and needs recalibration.

Metrics:
  1. Tier forward returns: S/A/B/C/D group avg returns at 5/10/20/60d
  2. Top-N forward returns: top 5/10/20 vs bottom 5/10/20
  3. Information coefficient: Spearman rank correlation(score, forward_return)
  4. Tracking priority value: High vs Medium vs Low forward returns
  5. Hit rate: % of each tier that has positive forward returns
  6. Tier monotonicity: is S > A > B > C > D in returns?
  7. Alert generation: if high-score group doesn't outperform, flag it

Uses score history + price data. No lookahead — only uses data available at signal time.
"""
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
from src import db, config
from src.utils import get_logger

logger = get_logger("ranking_quality")


def _spearman_corr(x: list, y: list) -> float:
    """Compute Spearman rank correlation coefficient."""
    if len(x) != len(y) or len(x) < 3:
        return 0.0

    n = len(x)

    def _rank(arr):
        indexed = sorted(enumerate(arr), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(x)
    ry = _rank(y)

    d_sq = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - (6 * d_sq) / (n * (n * n - 1))


def _collect_score_return_pairs(lookback_days: int = 90, return_window: int = 20) -> list:
    """Collect (score, rating, tracking_priority, forward_return) pairs.

    For each ticker on each scored date, look up the forward return.
    Only includes pairs where forward return is known (no lookahead).
    """
    pairs = []
    cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_date = (datetime.now() - timedelta(days=return_window)).strftime("%Y-%m-%d")

    tickers = config.all_tickers()

    for ticker in tickers:
        score_history = db.get_score_history(ticker, days=lookback_days)
        if not score_history:
            continue

        prices_raw = db.get_prices(ticker, days=lookback_days + return_window + 10)
        if len(prices_raw) < return_window + 5:
            continue

        # Build date->close lookup
        price_map = {p["date"]: p["close"] for p in prices_raw if p["close"] and p["close"] > 0}
        price_dates = sorted(price_map.keys())

        for score in score_history:
            score_date = score["date"]
            if score_date < cutoff_date or score_date > end_date:
                continue

            # Find the closest price on/after score_date
            entry_price = price_map.get(score_date)
            if entry_price is None:
                for pd in price_dates:
                    if pd >= score_date:
                        entry_price = price_map[pd]
                        break
            if entry_price is None or entry_price <= 0:
                continue

            # Find forward price (return_window trading days later)
            forward_candidates = [pd for pd in price_dates if pd > score_date]
            if len(forward_candidates) < return_window:
                continue
            forward_date = forward_candidates[min(return_window - 1, len(forward_candidates) - 1)]
            forward_price = price_map.get(forward_date)
            if forward_price is None or forward_price <= 0:
                continue

            fwd_return = (forward_price - entry_price) / entry_price * 100

            pairs.append({
                "ticker": ticker,
                "date": score_date,
                "opportunity_score": score["opportunity_score"],
                "evidence_score": score["evidence_score"],
                "asymmetry_score": score["asymmetry_score"],
                "momentum_score": score["momentum_score"],
                "risk_score": score["risk_score"],
                "rating": score.get("rating", "?"),
                "tracking_priority": score.get("tracking_priority", "Medium"),
                "forward_return": fwd_return,
            })

    logger.info(f"Collected {len(pairs)} score-return pairs (lookback={lookback_days}d, window={return_window}d)")
    return pairs


def tier_forward_returns(pairs: list) -> dict:
    """Group forward returns by S/A/B/C/D rating tier."""
    by_tier = defaultdict(list)
    for p in pairs:
        by_tier[p["rating"]].append(p["forward_return"])

    result = {}
    for tier in ["S", "A", "B", "C", "D"]:
        returns = by_tier.get(tier, [])
        if not returns:
            result[tier] = {"count": 0, "avg": None, "median": None, "hit_rate": None}
            continue
        returns_sorted = sorted(returns)
        median = returns_sorted[len(returns_sorted) // 2]
        result[tier] = {
            "count": len(returns),
            "avg": sum(returns) / len(returns),
            "median": median,
            "hit_rate": sum(1 for r in returns if r > 0) / len(returns) * 100,
            "best": max(returns),
            "worst": min(returns),
        }
    return result


def top_n_returns(pairs: list, n_values: list = None) -> dict:
    """Compare forward returns of top-N scored stocks vs bottom-N."""
    if n_values is None:
        n_values = [5, 10, 20]

    by_date = defaultdict(list)
    for p in pairs:
        by_date[p["date"]].append(p)

    results = {}
    for n in n_values:
        top_returns = []
        bottom_returns = []
        for date, date_pairs in by_date.items():
            if len(date_pairs) < n * 2:
                continue
            ranked = sorted(date_pairs, key=lambda x: x["opportunity_score"], reverse=True)
            top_returns.extend(r["forward_return"] for r in ranked[:n])
            bottom_returns.extend(r["forward_return"] for r in ranked[-n:])

        if not top_returns or not bottom_returns:
            results[f"top_{n}"] = {"error": "insufficient data"}
            continue

        results[f"top_{n}"] = {
            "top_avg": sum(top_returns) / len(top_returns),
            "bottom_avg": sum(bottom_returns) / len(bottom_returns),
            "spread": sum(top_returns) / len(top_returns) - sum(bottom_returns) / len(bottom_returns),
            "top_hit_rate": sum(1 for r in top_returns if r > 0) / len(top_returns) * 100,
            "bottom_hit_rate": sum(1 for r in bottom_returns if r > 0) / len(bottom_returns) * 100,
            "observations": len(top_returns),
        }
    return results


def information_coefficient(pairs: list) -> dict:
    """Compute information coefficient (Spearman rank correlation) between scores and forward returns."""
    if len(pairs) < 10:
        return {"ic": None, "message": "insufficient data (<10 pairs)"}

    scores = [p["opportunity_score"] for p in pairs]
    returns = [p["forward_return"] for p in pairs]

    ic = _spearman_corr(scores, returns)

    dim_ics = {}
    for dim in ["evidence_score", "asymmetry_score", "momentum_score", "risk_score"]:
        dim_values = [p[dim] for p in pairs]
        dim_ic = _spearman_corr(dim_values, returns)
        dim_ics[dim.replace("_score", "")] = round(dim_ic, 4)

    if abs(ic) >= 0.10:
        quality = "good"
    elif abs(ic) >= 0.05:
        quality = "marginal"
    else:
        quality = "weak"

    return {
        "ic": round(ic, 4),
        "quality": quality,
        "interpretation": f"{'Positive' if ic > 0 else 'Negative'} IC of {ic:.4f} ({quality}). "
                          f"{'Score correlates with future returns.' if ic > 0.05 else 'Score may not predict returns well.'}",
        "dimension_ics": dim_ics,
        "sample_size": len(pairs),
    }


def tracking_priority_analysis(pairs: list) -> dict:
    """Compare forward returns by tracking priority."""
    by_priority = defaultdict(list)
    for p in pairs:
        by_priority[p["tracking_priority"]].append(p["forward_return"])

    result = {}
    for priority in ["High", "Medium", "Low", "Exclude"]:
        returns = by_priority.get(priority, [])
        if not returns:
            result[priority] = {"count": 0, "avg": None, "hit_rate": None}
            continue
        result[priority] = {
            "count": len(returns),
            "avg": sum(returns) / len(returns),
            "hit_rate": sum(1 for r in returns if r > 0) / len(returns) * 100,
        }
    return result


def check_tier_monotonicity(tier_returns: dict) -> dict:
    """Check if higher-rated tiers produce better returns (S > A > B > C > D)."""
    order = ["S", "A", "B", "C", "D"]
    avgs = []
    for t in order:
        data = tier_returns.get(t, {})
        avg = data.get("avg")
        if avg is not None:
            avgs.append((t, avg))

    if len(avgs) < 2:
        return {"monotonic": None, "message": "insufficient tiers with data"}

    violations = []
    for i in range(len(avgs) - 1):
        if avgs[i][1] < avgs[i + 1][1]:
            violations.append(f"{avgs[i][0]}({avgs[i][1]:+.1f}%) < {avgs[i+1][0]}({avgs[i+1][1]:+.1f}%)")

    is_monotonic = len(violations) == 0
    return {
        "monotonic": is_monotonic,
        "violations": violations,
        "message": "Tier returns are properly ordered" if is_monotonic
                   else f"{len(violations)} tier ordering violations: {'; '.join(violations)}"
    }


def generate_ranking_report(lookback_days: int = 90, return_windows: list = None) -> str:
    """Generate comprehensive ranking quality report."""
    if return_windows is None:
        return_windows = [5, 10, 20, 60]

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 📊 Ranking Quality Report — {today}\n"]

    for window in return_windows:
        lines.append(f"## {window}-Day Forward Returns\n")

        pairs = _collect_score_return_pairs(lookback_days, window)
        if len(pairs) < 5:
            lines.append(f"_Insufficient data ({len(pairs)} pairs). Need more scoring history._\n")
            continue

        # 1. Tier returns
        tier_ret = tier_forward_returns(pairs)
        lines.append("### By Rating Tier\n")
        lines.append("| Tier | Count | Avg Return | Median | Hit Rate | Best | Worst |")
        lines.append("|------|-------|-----------|--------|----------|------|-------|")
        for tier in ["S", "A", "B", "C", "D"]:
            d = tier_ret.get(tier, {})
            if d.get("count", 0) == 0:
                lines.append(f"| {tier} | 0 | — | — | — | — | — |")
            else:
                lines.append(
                    f"| {tier} | {d['count']} | {d['avg']:+.1f}% | {d['median']:+.1f}% | "
                    f"{d['hit_rate']:.0f}% | {d['best']:+.1f}% | {d['worst']:+.1f}% |"
                )
        lines.append("")

        # Monotonicity check
        mono = check_tier_monotonicity(tier_ret)
        if mono["monotonic"] is not None:
            emoji = "✅" if mono["monotonic"] else "⚠️"
            lines.append(f"{emoji} **Tier Monotonicity:** {mono['message']}\n")

        # 2. Top-N analysis
        top_n = top_n_returns(pairs)
        has_top_n = any("error" not in v for v in top_n.values())
        if has_top_n:
            lines.append("### Top-N vs Bottom-N\n")
            lines.append("| Group | Top Avg | Bottom Avg | Spread | Top Hit% | Bottom Hit% | N |")
            lines.append("|-------|---------|-----------|--------|----------|-------------|---|")
            for key, d in top_n.items():
                if "error" in d:
                    continue
                spread_emoji = "🟢" if d["spread"] > 0 else "🔴"
                lines.append(
                    f"| {key} | {d['top_avg']:+.1f}% | {d['bottom_avg']:+.1f}% | "
                    f"{spread_emoji} {d['spread']:+.1f}% | {d['top_hit_rate']:.0f}% | "
                    f"{d['bottom_hit_rate']:.0f}% | {d['observations']} |"
                )
            lines.append("")

        # 3. Information coefficient
        ic = information_coefficient(pairs)
        lines.append("### Information Coefficient (Spearman)\n")
        if ic.get("ic") is not None:
            ic_emoji = "🟢" if ic["quality"] == "good" else ("🟡" if ic["quality"] == "marginal" else "🔴")
            lines.append(f"- {ic_emoji} **Overall IC:** {ic['ic']:.4f} ({ic['quality']})")
            lines.append(f"- {ic['interpretation']}")
            lines.append(f"- Sample size: {ic['sample_size']}")
            lines.append("\n**Per-dimension ICs:**")
            for dim, val in ic.get("dimension_ics", {}).items():
                d_emoji = "🟢" if val > 0.05 else ("🟡" if val > 0 else "🔴")
                lines.append(f"  - {d_emoji} {dim}: {val:.4f}")
        else:
            lines.append(f"- {ic.get('message', 'No data')}")
        lines.append("")

        # 4. Tracking priority
        tp = tracking_priority_analysis(pairs)
        has_tp = any(d.get("count", 0) > 0 for d in tp.values())
        if has_tp:
            lines.append("### By Tracking Priority\n")
            lines.append("| Priority | Count | Avg Return | Hit Rate |")
            lines.append("|----------|-------|-----------|----------|")
            for prio in ["High", "Medium", "Low", "Exclude"]:
                d = tp.get(prio, {})
                if d.get("count", 0) == 0:
                    continue
                lines.append(f"| {prio} | {d['count']} | {d['avg']:+.1f}% | {d['hit_rate']:.0f}% |")
            lines.append("")

    # Alerts section
    lines.append("## 🚨 Scoring System Health\n")

    pairs_20d = _collect_score_return_pairs(lookback_days, 20)
    alerts = []

    if len(pairs_20d) >= 10:
        ic_20d = information_coefficient(pairs_20d)
        if ic_20d.get("ic") is not None and ic_20d["ic"] < 0.03:
            alerts.append(f"🔴 **IC too low ({ic_20d['ic']:.4f}):** Scores may not predict 20d returns. Consider recalibrating weights.")

        tier_20d = tier_forward_returns(pairs_20d)
        mono_20d = check_tier_monotonicity(tier_20d)
        if mono_20d.get("monotonic") is False:
            alerts.append(f"⚠️ **Tier violation:** {mono_20d['message']}")

        # Check if B-High outperforms
        b_high = [p["forward_return"] for p in pairs_20d if p["rating"] == "B" and p["tracking_priority"] == "High"]
        b_other = [p["forward_return"] for p in pairs_20d if p["rating"] == "B" and p["tracking_priority"] != "High"]
        if b_high and b_other:
            b_high_avg = sum(b_high) / len(b_high)
            b_other_avg = sum(b_other) / len(b_other)
            if b_high_avg > b_other_avg + 3:
                alerts.append(f"📈 **B-High outperforms B-other** by {b_high_avg - b_other_avg:.1f}%. Tracking priority adds value.")
            elif b_high_avg < b_other_avg:
                alerts.append(f"⚠️ **B-High underperforms B-other** by {b_other_avg - b_high_avg:.1f}%. Tracking priority criteria may need review.")
    else:
        alerts.append("ℹ️ Insufficient data for scoring system health assessment. Need more daily runs.")

    if not alerts:
        lines.append("✅ No scoring anomalies detected.\n")
    else:
        for a in alerts:
            lines.append(f"- {a}")
        lines.append("")

    report = "\n".join(lines)

    report_dir = os.path.join(config.REPORTS_DIR, "ranking")
    os.makedirs(report_dir, exist_ok=True)
    filepath = os.path.join(report_dir, f"{today}.md")
    with open(filepath, "w") as f:
        f.write(report)

    logger.info(f"Ranking quality report saved: {filepath}")
    return report


if __name__ == "__main__":
    print(generate_ranking_report())
