"""Performance feedback loop — evaluate scoring system using paper trading results.

Generates weekly report with:
- Best/worst performing signal types
- False positive analysis
- Underrated stocks
- Exit rule effectiveness
- B-tier tracking value assessment
- Recommended rule adjustments (human reviews, no auto-changes)
"""
import os
import sqlite3
from datetime import datetime
from src import config, db
from src.utils import get_logger

logger = get_logger("feedback")

PAPER_TRADER_DB = os.environ.get("ASCR_H_DB_PATH", os.path.join(os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H"), "data", "ascr_h.sqlite"))


def _read_paper_db(query: str, params=()):
    """Read from paper trader DB."""
    if not os.path.exists(PAPER_TRADER_DB):
        return []
    conn = sqlite3.connect(f"file:{PAPER_TRADER_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def analyze_rating_performance() -> dict:
    """Analyze P&L by rating at entry."""
    closed = _read_paper_db("SELECT * FROM paper_positions WHERE status='closed'")
    open_pos = _read_paper_db("SELECT * FROM paper_positions WHERE status='open'")

    by_rating = {}
    for p in closed + open_pos:
        r = p.get("rating_at_entry", "?")
        if r not in by_rating:
            by_rating[r] = {"count": 0, "total_pnl": 0, "wins": 0, "tickers": []}
        pnl = p.get("realized_pnl", 0) + p.get("unrealized_pnl", 0)
        by_rating[r]["count"] += 1
        by_rating[r]["total_pnl"] += pnl
        if pnl > 0:
            by_rating[r]["wins"] += 1
        by_rating[r]["tickers"].append(p["ticker"])

    return by_rating


def analyze_shadow_performance() -> dict:
    """Analyze B-tier shadow tracking results."""
    shadows = _read_paper_db("""
        SELECT * FROM shadow_tracks WHERE return_20d IS NOT NULL
    """)

    if not shadows:
        return {"total": 0, "message": "No shadow data yet"}

    returns_5d = [s["return_5d"] for s in shadows if s.get("return_5d") is not None]
    returns_20d = [s["return_20d"] for s in shadows if s.get("return_20d") is not None]
    returns_60d = [s["return_60d"] for s in shadows if s.get("return_60d") is not None]

    def stats(arr):
        if not arr:
            return {"avg": 0, "positive_pct": 0, "count": 0}
        return {
            "avg": sum(arr) / len(arr),
            "positive_pct": sum(1 for x in arr if x > 0) / len(arr) * 100,
            "count": len(arr),
            "best": max(arr),
            "worst": min(arr),
        }

    # Find big winners that were missed
    big_winners = [s for s in shadows if s.get("return_60d", 0) and s["return_60d"] > 30]

    return {
        "total": len(shadows),
        "5d": stats(returns_5d),
        "20d": stats(returns_20d),
        "60d": stats(returns_60d),
        "big_winners_missed": [{"ticker": s["ticker"], "date": s["signal_date"],
                                 "return_60d": s["return_60d"]} for s in big_winners],
        "should_consider_b_trades": len(big_winners) > len(shadows) * 0.15,
    }


def analyze_exit_effectiveness() -> dict:
    """Analyze which exit rules worked best."""
    orders = _read_paper_db("SELECT * FROM paper_orders WHERE side='SELL'")

    by_reason = {}
    for o in orders:
        reason = o.get("reason", "unknown")
        # Normalize reason
        base_reason = reason.split("_")[0] if "_" in reason else reason
        if base_reason not in by_reason:
            by_reason[base_reason] = {"count": 0, "tickers": []}
        by_reason[base_reason]["count"] += 1
        by_reason[base_reason]["tickers"].append(o["ticker"])

    return by_reason


def generate_feedback_report() -> str:
    """Generate weekly performance feedback report."""
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [f"# 🔄 Performance Feedback Report — {today}\n"]

    # Rating performance
    rating_perf = analyze_rating_performance()
    if rating_perf:
        lines.append("## 📊 Performance by Rating\n")
        lines.append("| Rating | Trades | Win Rate | Total P&L | Avg P&L |")
        lines.append("|--------|--------|----------|-----------|---------|")
        for r in sorted(rating_perf.keys()):
            d = rating_perf[r]
            wr = d["wins"] / max(d["count"], 1) * 100
            avg = d["total_pnl"] / max(d["count"], 1)
            lines.append(f"| {r} | {d['count']} | {wr:.0f}% | ${d['total_pnl']:+,.0f} | ${avg:+,.0f} |")
        lines.append("")

    # Shadow tracking
    shadow = analyze_shadow_performance()
    if shadow.get("total", 0) > 0:
        lines.append("## 👻 Shadow Tracking (B-rated, not bought)\n")
        for period in ["5d", "20d", "60d"]:
            s = shadow.get(period, {})
            if s.get("count", 0) > 0:
                lines.append(f"- **{period}:** avg {s['avg']:+.1f}%, positive {s['positive_pct']:.0f}% ({s['count']} tracked)")

        if shadow.get("big_winners_missed"):
            lines.append("\n**🏆 Missed Big Winners (>30% in 60d):**")
            for w in shadow["big_winners_missed"]:
                lines.append(f"- {w['ticker']} ({w['date']}): +{w['return_60d']:.0f}%")

        if shadow.get("should_consider_b_trades"):
            lines.append("\n⚠️ **RECOMMENDATION:** >15% of B-rated shadows gained >30%. Consider allowing small B-tier paper trades.")
        lines.append("")

    # Exit effectiveness
    exits = analyze_exit_effectiveness()
    if exits:
        lines.append("## 🚪 Exit Rule Usage\n")
        for reason, data in sorted(exits.items(), key=lambda x: x[1]["count"], reverse=True):
            lines.append(f"- **{reason}:** {data['count']} times")
        lines.append("")

    # Recommendations
    lines.append("## 💡 Recommended Adjustments\n")
    lines.append("_These are suggestions for human review. No automatic changes._\n")

    if rating_perf:
        s_data = rating_perf.get("S", {"count": 0, "wins": 0, "total_pnl": 0})
        a_data = rating_perf.get("A", {"count": 0, "wins": 0, "total_pnl": 0})
        if s_data["count"] > 0:
            s_wr = s_data["wins"] / s_data["count"] * 100
            if s_wr < 50:
                lines.append("- ⚠️ S-tier win rate below 50%. Consider tightening S criteria (higher evidence threshold).")
            elif s_wr > 70:
                lines.append("- ✅ S-tier performing well. Consider slightly relaxing criteria to catch more opportunities.")

        if shadow.get("should_consider_b_trades"):
            lines.append("- 📈 B-tier shadow tracking shows strong returns. Consider adding 0.5% sizing for B-High priority.")

    lines.append("- 🔄 Review exit rules quarterly using backtest_exit_rules.py output.")

    report = "\n".join(lines)

    # Save
    report_dir = os.path.join(config.REPORTS_DIR, "feedback")
    os.makedirs(report_dir, exist_ok=True)
    filepath = os.path.join(report_dir, f"{today}.md")
    with open(filepath, "w") as f:
        f.write(report)

    logger.info(f"Feedback report saved: {filepath}")
    return report


if __name__ == "__main__":
    print(generate_feedback_report())
