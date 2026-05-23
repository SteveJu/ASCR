"""Report generator for paper trader."""
import os
from datetime import datetime
from src import db, config
from src.performance import compute_stats
from src.utils import get_logger

logger = get_logger("report")


def generate_daily_report() -> str:
    """Generate daily paper trading report."""
    today = datetime.now().strftime("%Y-%m-%d")
    stats = compute_stats()

    report_dir = os.path.join(config.REPORTS_DIR, "daily")
    os.makedirs(report_dir, exist_ok=True)

    lines = [f"# 📈 ASCR-H Daily Report — {today}\n"]

    # Account summary
    lines.append("## Account Summary\n")
    lines.append(f"- **Total Equity:** ${stats['total_equity']:,.0f}")
    lines.append(f"- **Cash:** ${stats['cash']:,.0f}")
    lines.append(f"- **Total Return:** {stats['total_return_pct']:+.2f}%")
    lines.append(f"- **Max Drawdown:** {stats['max_drawdown_pct']:.2f}%")
    lines.append(f"- **Peak Equity:** ${stats['peak_equity']:,.0f}")
    lines.append(f"- **Open Positions:** {stats['num_open']}")
    lines.append(f"- **Closed Trades:** {stats['num_closed']}")
    lines.append("")

    # Open positions
    positions = db.get_all_positions("open")
    if positions:
        lines.append("## 💼 Open Positions\n")
        lines.append("| Ticker | Entry | Qty | Cost | Value | P&L % | Rating |")
        lines.append("|--------|-------|-----|------|-------|-------|--------|")
        for p in positions:
            avg = p["avg_entry_price"]
            qty = p["quantity"]
            cost = p["cost_basis"]
            value = p.get("current_value", 0)
            pnl_pct = (value - cost) / cost * 100 if cost > 0 else 0
            emoji = "🟢" if pnl_pct >= 0 else "🔴"
            lines.append(f"| {emoji} {p['ticker']} | ${avg:.2f} | {qty:.1f} | ${cost:,.0f} | ${value:,.0f} | {pnl_pct:+.1f}% | {p.get('rating_at_entry', '')} |")
        lines.append("")

    # Today's orders
    orders = db.get_orders()
    today_orders = [o for o in orders if o["date"] == today]
    if today_orders:
        lines.append("## 📋 Today's Orders\n")
        for o in today_orders:
            emoji = "🟢" if o["side"] == "BUY" else "🔴"
            lines.append(f"- {emoji} **{o['side']}** {o['ticker']}: {o['quantity']:.2f} shares @ ${o['price']:.2f} — {o['reason']}")
        lines.append("")

    # Performance by rating
    if stats["by_rating"]:
        lines.append("## 📊 Performance by Rating\n")
        lines.append("| Rating | Trades | Win Rate | Total P&L |")
        lines.append("|--------|--------|----------|-----------|")
        for r, data in sorted(stats["by_rating"].items()):
            wr = data["wins"] / max(data["count"], 1) * 100
            lines.append(f"| {r} | {data['count']} | {wr:.0f}% | ${data['total_pnl']:+,.0f} |")
        lines.append("")

    # Shadow tracking summary
    if stats["shadow_total"] > 0:
        lines.append("## 👻 Shadow Tracking (B-rated, not bought)\n")
        lines.append(f"- Total tracked: {stats['shadow_total']}")
        lines.append(f"- Avg 20-day return: {stats['shadow_avg_20d']:+.1f}%")
        lines.append(f"- Positive 20d: {stats['shadow_positive_20d_pct']:.0f}%")
        lines.append("")

    # Win/Loss stats
    if stats["num_closed"] > 0:
        lines.append("## 📈 Trading Stats\n")
        lines.append(f"- **Win Rate:** {stats['win_rate']:.0f}%")
        lines.append(f"- **Avg Win:** ${stats['avg_win']:+,.0f}")
        lines.append(f"- **Avg Loss:** ${stats['avg_loss']:+,.0f}")
        lines.append(f"- **Profit Factor:** {stats['profit_factor']:.2f}")
        lines.append("")

    report = "\n".join(lines)
    filepath = os.path.join(report_dir, f"{today}.md")
    with open(filepath, "w") as f:
        f.write(report)

    logger.info(f"Report saved: {filepath}")
    return report


def generate_weekly_report() -> str:
    """Generate weekly summary."""
    stats = compute_stats()
    today = datetime.now().strftime("%Y-%m-%d")

    report_dir = os.path.join(config.REPORTS_DIR, "weekly")
    os.makedirs(report_dir, exist_ok=True)

    lines = [f"# 📊 ASCR-H Weekly Summary — {today}\n"]
    lines.append(f"- Equity: ${stats['total_equity']:,.0f} ({stats['total_return_pct']:+.2f}%)")
    lines.append(f"- Max DD: {stats['max_drawdown_pct']:.2f}%")
    lines.append(f"- Win Rate: {stats['win_rate']:.0f}% | PF: {stats['profit_factor']:.2f}")
    lines.append(f"- Open: {stats['num_open']} | Closed: {stats['num_closed']}")

    if stats["top_winners"]:
        lines.append("\n## 🏆 Top Winners")
        for p in stats["top_winners"][:5]:
            lines.append(f"- {p['ticker']}: ${p.get('realized_pnl', 0):+,.0f}")

    if stats["top_losers"]:
        lines.append("\n## 💀 Top Losers")
        for p in stats["top_losers"][:5]:
            lines.append(f"- {p['ticker']}: ${p.get('realized_pnl', 0):+,.0f}")

    report = "\n".join(lines)
    filepath = os.path.join(report_dir, f"{today}.md")
    with open(filepath, "w") as f:
        f.write(report)

    return report
