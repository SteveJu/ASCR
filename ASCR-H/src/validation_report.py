"""Weekly validation report — 12 sections as specified.

1. Strategy Health Summary
2. Overall DQS trend
3. Buy/Sell/Hold/NoBuy DQS trend
4. Rating quality by S/A/B/C/D
5. Top successful decisions
6. Worst decisions
7. Missed opportunities
8. False positives
9. False negatives
10. Exit rule effectiveness
11. Improving / degrading / unstable assessment
12. Recommended rule adjustments
"""
import os
from datetime import datetime
from collections import defaultdict
from src import db, config
from src.strategy_health import compute_health, HORIZON_PRIMARY
from src.degradation_detector import detect_degradation
from src.utils import get_logger

logger = get_logger("validation_report")


def generate_validation_report(mode: str = "live_paper") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 🔬 Strategy Validation Report — {today}\n"]
    lines.append(f"_Mode: {mode} | Horizon: {HORIZON_PRIMARY}d_\n")

    health = compute_health(mode)
    if "error" in health:
        lines.append(f"⚠️ {health['error']}\n")
        report = "\n".join(lines)
        _save(report, mode, today)
        return report

    degradation = detect_degradation(mode)

    # === 1. Strategy Health Summary ===
    w = health["warning_level"]
    emoji = {"healthy": "🟢", "monitoring": "🟡", "unstable": "🟠", "broken": "🔴"}.get(w, "⚪")
    lines.append(f"## 1. {emoji} Strategy Health: DQS {health['overall_dqs']:.1f}/100 ({w.upper()})\n")
    lines.append(f"| Metric | Score |")
    lines.append(f"|--------|-------|")
    for label, key in [("Buy DQS (25%)", "buy_dqs"), ("Sell DQS (20%)", "sell_dqs"),
                       ("Trim DQS (10%)", "trim_dqs"), ("Hold DQS (15%)", "hold_dqs"),
                       ("NoBuy DQS (15%)", "no_buy_dqs"), ("Ranking Quality (10%)", "rating_quality_score"),
                       ("Stability (5%)", "stability_score")]:
        v = health.get(key, 0) or 0
        count = health.get("by_type_counts", {}).get(label.split(" ")[0].upper(), "")
        n_str = f" (n={count})" if count else ""
        lines.append(f"| {label} | {v:.1f}{n_str} |")
    lines.append(f"| **Overall** | **{health['overall_dqs']:.1f}** |")
    lines.append("")

    lines.append(f"- FP Rate: {health['false_positive_rate']:.1f}% | "
                 f"FN Rate: {health['false_negative_rate']:.1f}% | "
                 f"Exit Quality: {health['exit_quality_score']:.1f}% | "
                 f"Sample: {health['sample_size']}\n")

    # === 2. Overall DQS Trend ===
    with db.get_conn() as conn:
        trend_rows = conn.execute("""
            SELECT date, overall_dqs, buy_dqs, sell_dqs, hold_dqs, no_buy_dqs,
                   stability_score, warning_level
            FROM strategy_health WHERE mode=? ORDER BY date DESC LIMIT 12
        """, (mode,)).fetchall()
    if len(trend_rows) > 1:
        lines.append("## 2. DQS Trend\n")
        lines.append("| Date | Overall | Buy | Sell | Hold | NoBuy | Stability | Status |")
        lines.append("|------|---------|-----|------|------|-------|-----------|--------|")
        for r in trend_rows:
            r = dict(r)
            s_emoji = {"healthy": "🟢", "monitoring": "🟡", "unstable": "🟠", "broken": "🔴"}.get(r.get("warning_level", ""), "")
            lines.append(f"| {r['date']} | {r.get('overall_dqs',0):.1f} | {r.get('buy_dqs',0):.1f} | "
                         f"{r.get('sell_dqs',0):.1f} | {r.get('hold_dqs',0):.1f} | "
                         f"{r.get('no_buy_dqs',0):.1f} | {r.get('stability_score',0):.1f} | {s_emoji} |")
        lines.append("")
    else:
        lines.append("## 2. DQS Trend\n_First week — no trend data yet._\n")

    # === 3. Buy/Sell/Hold/NoBuy DQS Detail (skip if covered in #2) ===
    lines.append("## 3. DQS by Decision Type\n")
    with db.get_conn() as conn:
        type_detail = conn.execute("""
            SELECT d.decision_type, q.horizon_days,
                   COUNT(*) as n, AVG(q.quality_score) as avg_dqs,
                   MIN(q.quality_score) as min_dqs, MAX(q.quality_score) as max_dqs
            FROM decision_quality_scores q
            JOIN decisions d ON q.decision_id = d.decision_id
            WHERE d.mode = ?
            GROUP BY d.decision_type, q.horizon_days
            ORDER BY d.decision_type, q.horizon_days
        """, (mode,)).fetchall()
    if type_detail:
        lines.append("| Type | Horizon | N | Avg DQS | Min | Max |")
        lines.append("|------|---------|---|---------|-----|-----|")
        for r in type_detail:
            r = dict(r)
            lines.append(f"| {r['decision_type']} | {r['horizon_days']}d | {r['n']} | "
                         f"{r['avg_dqs']:.1f} | {r['min_dqs']:.1f} | {r['max_dqs']:.1f} |")
        lines.append("")

    # === 4. Rating Quality by S/A/B/C/D ===
    lines.append("## 4. Rating Quality\n")
    with db.get_conn() as conn:
        rating_rows = conn.execute("""
            SELECT d.rating,
                   AVG(o.forward_return) as avg_fwd,
                   AVG(o.alpha_return) as avg_alpha,
                   COUNT(*) as n,
                   SUM(CASE WHEN o.forward_return > 0 THEN 1 ELSE 0 END) as positive
            FROM decisions d
            JOIN decision_outcomes o ON d.decision_id = o.decision_id
            WHERE d.mode = ? AND o.horizon_days = ? AND d.decision_type IN ('BUY', 'NO_BUY')
            GROUP BY d.rating
            ORDER BY d.rating
        """, (mode, HORIZON_PRIMARY)).fetchall()
    if rating_rows:
        lines.append("| Rating | N | Avg Return | Avg Alpha | Hit Rate |")
        lines.append("|--------|---|-----------|-----------|----------|")
        for r in rating_rows:
            r = dict(r)
            hr = r["positive"] / r["n"] * 100 if r["n"] > 0 else 0
            lines.append(f"| {r['rating']} | {r['n']} | {r['avg_fwd']:+.1f}% | "
                         f"{r['avg_alpha']:+.1f}% | {hr:.0f}% |")
        lines.append("")

    # === 5. Top Successful Decisions ===
    with db.get_conn() as conn:
        best = conn.execute("""
            SELECT d.ticker, d.decision_type, d.decision_date, d.rating,
                   q.quality_score, q.explanation,
                   o.forward_return, o.alpha_return
            FROM decision_quality_scores q
            JOIN decisions d ON q.decision_id = d.decision_id
            JOIN decision_outcomes o ON o.decision_id = q.decision_id AND o.horizon_days = q.horizon_days
            WHERE d.mode = ? AND q.horizon_days = ?
            ORDER BY q.quality_score DESC LIMIT 10
        """, (mode, HORIZON_PRIMARY)).fetchall()
    if best:
        lines.append("## 5. 🏆 Top Decisions\n")
        for r in [dict(r) for r in best]:
            lines.append(f"- **{r['decision_type']} {r['ticker']}** ({r['decision_date']}) "
                         f"[{r.get('rating','')}] DQS={r['quality_score']:.0f} "
                         f"Return={r.get('forward_return',0):+.1f}% Alpha={r.get('alpha_return',0):+.1f}%")
        lines.append("")

    # === 6. Worst Decisions ===
    with db.get_conn() as conn:
        worst = conn.execute("""
            SELECT d.ticker, d.decision_type, d.decision_date, d.rating,
                   q.quality_score, q.explanation,
                   o.forward_return, o.alpha_return
            FROM decision_quality_scores q
            JOIN decisions d ON q.decision_id = d.decision_id
            JOIN decision_outcomes o ON o.decision_id = q.decision_id AND o.horizon_days = q.horizon_days
            WHERE d.mode = ? AND q.horizon_days = ?
            ORDER BY q.quality_score ASC LIMIT 10
        """, (mode, HORIZON_PRIMARY)).fetchall()
    if worst:
        lines.append("## 6. 💀 Worst Decisions\n")
        for r in [dict(r) for r in worst]:
            lines.append(f"- **{r['decision_type']} {r['ticker']}** ({r['decision_date']}) "
                         f"[{r.get('rating','')}] DQS={r['quality_score']:.0f} "
                         f"Return={r.get('forward_return',0):+.1f}% Alpha={r.get('alpha_return',0):+.1f}%")
        lines.append("")

    # === 7. Missed Opportunities ===
    if health.get("missed_opportunities"):
        lines.append("## 7. 🎯 Missed Opportunities\n")
        lines.append("_NO_BUY or B-track with max_gain >50% (dd >-20%) or fwd >35%_\n")
        for m in health["missed_opportunities"]:
            lines.append(f"- **{m['ticker']}** [{m.get('rating','')}]: "
                         f"max gain +{m.get('max_gain',0):.0f}%, "
                         f"fwd {m.get('forward_return',0):+.1f}%")
        lines.append("")
    else:
        lines.append("## 7. 🎯 Missed Opportunities\n_None detected._\n")

    # === 8. False Positives ===
    if health.get("false_positives_list"):
        lines.append("## 8. 💥 False Positives (Bad Buys)\n")
        lines.append("_BUY that underperformed QQQ >10%, or DD >-30%, or fwd <-25%_\n")
        for f in health["false_positives_list"]:
            lines.append(f"- **{f['ticker']}**: return {f.get('return',0):+.1f}%, "
                         f"alpha {f.get('alpha',0):+.1f}%")
        lines.append("")
    else:
        lines.append("## 8. 💥 False Positives\n_None detected._\n")

    # === 9. False Negatives ===
    if health.get("false_negatives_list"):
        lines.append("## 9. 😱 False Negatives (Should Have Bought)\n")
        lines.append("_NO_BUY that outperformed QQQ >15% or fwd >35%_\n")
        for f in health["false_negatives_list"]:
            lines.append(f"- **{f['ticker']}** [{f.get('rating','')}]: "
                         f"return {f.get('return',0):+.1f}%, alpha {f.get('alpha',0):+.1f}%")
        lines.append("")
    else:
        lines.append("## 9. 😱 False Negatives\n_None detected._\n")

    # === 10. Exit Rule Effectiveness ===
    lines.append("## 10. 🚪 Exit Rule Effectiveness\n")
    with db.get_conn() as conn:
        exit_rows = conn.execute("""
            SELECT d.reason, COUNT(*) as n,
                   AVG(o.forward_return) as avg_post_exit,
                   AVG(q.quality_score) as avg_dqs
            FROM decisions d
            JOIN decision_outcomes o ON d.decision_id = o.decision_id AND o.horizon_days = ?
            JOIN decision_quality_scores q ON q.decision_id = d.decision_id AND q.horizon_days = ?
            WHERE d.mode = ? AND d.decision_type IN ('SELL', 'TRIM')
            GROUP BY d.reason
        """, (HORIZON_PRIMARY, HORIZON_PRIMARY, mode)).fetchall()
    if exit_rows:
        lines.append("| Exit Rule | Count | Post-Exit Return | DQS |")
        lines.append("|-----------|-------|-----------------|-----|")
        for r in exit_rows:
            r = dict(r)
            rule = r["reason"]
            lines.append(f"| {rule} | {r['n']} | {r['avg_post_exit']:+.1f}% | {r['avg_dqs']:.0f} |")
        lines.append("")
    else:
        lines.append("_No exit decisions to evaluate._\n")

    # === 11. Improving / Degrading / Unstable ===
    lines.append("## 11. 📈 Strategy Trajectory\n")
    status = degradation["status"]
    status_emoji = {"healthy": "🟢", "caution": "🟡", "degraded": "🟠", "critical": "🔴"}.get(status, "⚪")
    lines.append(f"**Status: {status_emoji} {status.upper()}**\n")
    if degradation["alerts"]:
        for a in degradation["alerts"]:
            a_emoji = "🚨" if a["level"] == "critical" else "⚠️"
            lines.append(f"- {a_emoji} **{a['type']}**: {a['message']}")
            lines.append(f"  → _{a['action']}_")
        lines.append("")
    else:
        lines.append("_No degradation alerts._\n")

    # === 12. Recommended Adjustments ===
    lines.append("## 12. 💡 Recommended Adjustments\n")
    lines.append("_These are suggestions for human review. No automatic changes._\n")
    recommendations = []

    overall = health["overall_dqs"]
    if overall < 50:
        recommendations.append("🔴 **PAUSE** real-money consideration until DQS > 65")
    if health["false_negative_rate"] > 20:
        recommendations.append("📈 Consider adding 0.5% sizing for B-High stocks (FN rate high)")
    if health["false_positive_rate"] > 15:
        recommendations.append("🔍 Tighten buy criteria: raise evidence threshold for A-rating")
    rq = health["rating_quality_score"]
    if rq < 40:
        recommendations.append("⚙️ Recalibrate scoring weights: rating tiers not predicting returns")
    if health["exit_quality_score"] < 50:
        recommendations.append("🚪 Review exit rules with backtest_exit_rules output")
    if health["stability_score"] < 50:
        recommendations.append("📊 Strategy is regime-dependent: consider dynamic weight regime")
    if health.get("missed_opportunities"):
        recommendations.append(f"🎯 {len(health['missed_opportunities'])} missed opportunities: "
                               f"review B-High tracking → position conversion criteria")

    if not recommendations:
        recommendations.append("✅ No urgent adjustments needed. Continue monitoring.")

    for r in recommendations:
        lines.append(f"- {r}")
    lines.append("")

    report = "\n".join(lines)
    _save(report, mode, today)
    return report


def _save(report: str, mode: str, today: str):
    report_dir = os.path.join(config.REPORTS_DIR, "validation")
    os.makedirs(report_dir, exist_ok=True)
    filepath = os.path.join(report_dir, f"{mode}_{today}.md")
    with open(filepath, "w") as f:
        f.write(report)
    logger.info(f"Report saved: {filepath}")
