"""Report generator V2 — daily markdown reports with ratings and tracking priority."""
import os
import json
from datetime import datetime
from src import config, db
from src.rating import get_rating_description, classify_action
from src.utils import get_logger

logger = get_logger("report_generator")


def generate_daily_report(scores: list, exit_signals: list = None, positions: list = None, news: list = None, regime: dict = None, inflections: list = None) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join(config.REPORTS_DIR, "daily")
    os.makedirs(report_dir, exist_ok=True)

    lines = [f"# 📡 ASCR Daily Report — {today}\n"]

    # Summary counts
    counts = {}
    for r in ["S", "A", "B", "C", "D"]:
        counts[r] = sum(1 for s in scores if s["rating"] == r)

    lines.append("## Summary")
    lines.append(f"🔴 S: {counts['S']} | 🟠 A: {counts['A']} | 🟡 B: {counts['B']} | 🔵 C: {counts['C']} | ⚪ D: {counts['D']}")
    lines.append(f"Total scored: {len(scores)}\n")

    # Market regime
    if regime:
        r_emoji = {"risk_on": "🟢", "neutral": "🟡", "risk_off": "🔴"}.get(regime.get("regime", ""), "⚪")
        lines.append(f"## {r_emoji} Market Regime: {regime.get('regime', 'unknown').upper()}")
        lines.append(f"_{regime.get('reason', '')}_\n")

    # Score inflections
    if inflections:
        lines.append("## 📈 Score Inflections\n")
        for d in inflections[:10]:
            for inf in d["inflections"]:
                lines.append(f"- **${d['ticker']}**: {inf['description']} [{inf['significance']}]")
        lines.append("")

    # S-Tier (immediate attention)
    s_tier = [s for s in scores if s["rating"] == "S"]
    if s_tier:
        lines.append("## 🔴 S-Tier — Small Position Probe NOW\n")
        for s in s_tier:
            lines.append(f"### ${s['ticker']} — {s.get('name', '')} (Opp: {s['opportunity']:.1f})")
            lines.append(f"- Evidence: {s['evidence']:.0f} | Asymmetry: {s['asymmetry']:.0f} | Momentum: {s['momentum']:.0f} | Risk: {s['risk']:.0f}")
            lines.append(f"- Tracking: **{s.get('tracking_priority', 'High')}**")
            lines.append(f"- Reason: {s.get('reason', '')}")
            lines.append(f"- Next trigger: {s.get('next_trigger', '')}")
            lines.append(f"- Action: **{classify_action(s['rating'])}**\n")

    # A-Tier
    a_tier = [s for s in scores if s["rating"] == "A"]
    if a_tier:
        lines.append("## 🟠 A-Tier — Buy Candidates\n")
        for s in a_tier:
            lines.append(f"### ${s['ticker']} — {s.get('name', '')} (Opp: {s['opportunity']:.1f})")
            lines.append(f"- E:{s['evidence']:.0f} A:{s['asymmetry']:.0f} M:{s['momentum']:.0f} R:{s['risk']:.0f}")
            lines.append(f"- Tracking: **{s.get('tracking_priority', 'High')}**")
            lines.append(f"- {s.get('reason', '')}")
            lines.append(f"- Next: {s.get('next_trigger', '')}\n")

    # B-Tier table
    b_tier = [s for s in scores if s["rating"] == "B"]
    if b_tier:
        lines.append("## 🟡 B-Tier — Early Tracking\n")
        lines.append("| Ticker | Opp | E | A | M | R | Priority | Reason |")
        lines.append("|--------|-----|---|---|---|---|----------|--------|")
        for s in b_tier:
            lines.append(f"| **{s['ticker']}** | {s['opportunity']:.1f} | {s['evidence']:.0f} | {s['asymmetry']:.0f} | {s['momentum']:.0f} | {s['risk']:.0f} | {s.get('tracking_priority','')} | {s.get('reason','')} |")
        lines.append("")

    # Position alerts (CRITICAL)
    if exit_signals:
        lines.append("## ⚠️ Position Alerts\n")
        for sig in exit_signals:
            status_emoji = {
                "Sell All": "🚨", "Thesis Broken": "🚨", "Sell Partial": "⚠️",
                "Watch Exit": "👀", "Hold but Trim": "✂️",
            }.get(sig.get("position_status", ""), "📌")
            lines.append(f"### {status_emoji} ${sig['ticker']} ({sig['pnl_pct']:+.1f}%) — {sig.get('position_status', '')}")
            for s in sig["signals"]:
                sev = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "⚡", "LOW": "ℹ️"}.get(s["severity"], "")
                lines.append(f"- {sev} **{s['type']}**: {s['message']}")
                lines.append(f"  → {s['action']}")
            lines.append("")

    # Positions
    if positions:
        lines.append("## 💼 Open Positions\n")
        lines.append("| Ticker | Shares | Entry | Current | P&L | Status |")
        lines.append("|--------|--------|-------|---------|-----|--------|")
        total_pnl = 0
        for p in positions:
            emoji = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            status = p.get("position_status", "Hold Strong")
            lines.append(f"| {emoji} {p['ticker']} | {p['shares']:.2f} | ${p['entry_price']:.2f} | ${p['current_price']:.2f} | {p['pnl_pct']:+.1f}% | {status} |")
            total_pnl += p["pnl"]
        lines.append(f"\nTotal P&L: **${total_pnl:+,.0f}**\n")

    # News highlights
    if news:
        high_rel = [n for n in news if n.get("relevance_score", 0) >= 20][:15]
        if high_rel:
            lines.append("## 📰 Key News\n")
            for n in high_rel:
                tag = f"[{n['ticker']}] " if n.get('ticker') else ""
                lines.append(f"- {tag}**{n['headline']}** (rel: {n.get('relevance_score', 0)})")
            lines.append("")

    # C/D tier (collapsed)
    lower = [s for s in scores if s["rating"] in ("C", "D")]
    if lower:
        lines.append("## ⚪ Lower Tier (C/D)\n")
        lines.append("| Ticker | Rating | Opp | E | A | M | R | Priority |")
        lines.append("|--------|--------|-----|---|---|---|---|----------|")
        for s in lower:
            lines.append(f"| {s['ticker']} | {s['rating']} | {s['opportunity']:.1f} | {s['evidence']:.0f} | {s['asymmetry']:.0f} | {s['momentum']:.0f} | {s['risk']:.0f} | {s.get('tracking_priority','')} |")
        lines.append("")

    report = "\n".join(lines)

    filepath = os.path.join(report_dir, f"{today}.md")
    with open(filepath, "w") as f:
        f.write(report)

    # Save to DB
    db.save_report(today, "daily", report)

    logger.info(f"Daily report saved to {filepath}")
    return report
