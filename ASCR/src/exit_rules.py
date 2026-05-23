"""Exit rules engine V2 — comprehensive position exit management.

Exit rule categories:
1. Thesis Broken → Sell All
2. Catalyst Played Out → Partial Trim
3. Profit Taking → Graduated selling
4. Trailing Stop → Dynamic based on profit level
5. Stop Loss → Hard review / sell
6. Time Stop → No catalyst materialized
"""
from datetime import datetime, timedelta
from src import db
from src.utils import get_logger

logger = get_logger("exit_rules")

# Position status labels
STATUS_HOLD_STRONG = "Hold Strong"
STATUS_HOLD_TRIM = "Hold but Trim"
STATUS_WATCH_EXIT = "Watch Exit"
STATUS_SELL_PARTIAL = "Sell Partial"
STATUS_SELL_ALL = "Sell All"
STATUS_THESIS_BROKEN = "Thesis Broken"


def evaluate_exits(position_statuses: list) -> list:
    """Evaluate all exit rules for all open positions. Returns list of exit signal groups."""
    all_signals = []

    for pos in position_statuses:
        signals = _evaluate_single(pos)
        if signals:
            # Determine overall position_status based on worst signal
            position_status = _determine_position_status(signals)
            all_signals.append({
                "ticker": pos["ticker"],
                "position_id": pos["id"],
                "current_price": pos["current_price"],
                "entry_price": pos["entry_price"],
                "pnl_pct": pos["pnl_pct"],
                "position_status": position_status,
                "signals": signals,
            })

            # Update position status in DB
            db.update_position(pos["id"], position_status=position_status)

            # Log exit alerts
            today = datetime.now().strftime("%Y-%m-%d")
            for s in signals:
                db.add_exit_alert(
                    pos["ticker"], pos["id"], today,
                    s["type"], s["severity"],
                    s["action"], s["message"]
                )
    return all_signals


def _evaluate_single(pos: dict) -> list:
    """Evaluate exit rules for a single position."""
    signals = []
    current = pos["current_price"]
    entry = pos["entry_price"]
    pnl_pct = pos["pnl_pct"]
    max_price = pos.get("max_price_since_entry", current)
    drawdown = pos.get("drawdown_from_high", 0)
    trailing_pct = pos.get("trailing_stop_pct", 15) / 100.0
    max_loss_pct = pos.get("max_loss_pct", 25)

    # --- 1. Stop Loss ---
    if pnl_pct <= -max_loss_pct:
        signals.append({
            "type": "HARD_STOP_LOSS",
            "severity": "CRITICAL",
            "message": f"Position down {pnl_pct:.1f}% — exceeded max loss of {max_loss_pct}%",
            "action": "SELL ALL — hard stop breached. Review thesis immediately.",
            "category": "stop_loss",
        })
    elif pnl_pct <= -15:
        signals.append({
            "type": "SOFT_STOP_REVIEW",
            "severity": "HIGH",
            "message": f"Position down {pnl_pct:.1f}% — approaching max loss",
            "action": "REVIEW — set hard stop if not set. Consider selling if thesis weakened.",
            "category": "stop_loss",
        })

    # --- 2. Profit Taking (graduated) ---
    if pnl_pct >= 100:
        signals.append({
            "type": "PROFIT_100PCT",
            "severity": "HIGH",
            "message": f"Position doubled! Up {pnl_pct:.1f}% — at least recover cost basis",
            "action": "SELL 50% minimum — take back your initial investment. Let rest run with trailing stop.",
            "category": "profit_taking",
        })
    elif pnl_pct >= 50:
        signals.append({
            "type": "PROFIT_50PCT",
            "severity": "HIGH",
            "message": f"Strong gain: +{pnl_pct:.1f}%",
            "action": "SELL 30-50% — lock in significant gains. Tighten trailing stop on remainder.",
            "category": "profit_taking",
        })
    elif pnl_pct >= 25:
        signals.append({
            "type": "PROFIT_25PCT",
            "severity": "MEDIUM",
            "message": f"Solid gain: +{pnl_pct:.1f}%",
            "action": "SELL 20-30% — take some off the table. Set trailing stop for rest.",
            "category": "profit_taking",
        })

    # --- 3. Trailing Stop (activated when profit > 30%) ---
    if pnl_pct > 30 and max_price > 0:
        trailing_stop_price = max_price * (1 - trailing_pct)

        # Tighter trailing for doubled positions
        if pnl_pct > 100:
            tight_trailing = max_price * 0.80  # 20% from high
            if current < tight_trailing:
                signals.append({
                    "type": "TRAILING_STOP_TIGHT",
                    "severity": "CRITICAL",
                    "message": f"Doubled position fell {abs(drawdown):.1f}% from peak ${max_price:.2f}. Now ${current:.2f}",
                    "action": "SELL AT LEAST 50% — protecting gains on doubled position.",
                    "category": "trailing_stop",
                })

        if current < trailing_stop_price:
            signals.append({
                "type": "TRAILING_STOP",
                "severity": "HIGH",
                "message": f"Price ${current:.2f} fell {abs(drawdown):.1f}% from peak ${max_price:.2f} (trailing stop: ${trailing_stop_price:.2f})",
                "action": f"REDUCE — momentum breaking down. Trailing stop ({trailing_pct*100:.0f}%) triggered.",
                "category": "trailing_stop",
            })

    # --- 4. Time Stop ---
    entry_date = pos.get("entry_date", "")
    if entry_date:
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            days_held = (datetime.now() - entry_dt).days

            # 6 months with no meaningful gain
            if days_held > 180 and -5 < pnl_pct < 15:
                signals.append({
                    "type": "TIME_STOP",
                    "severity": "MEDIUM",
                    "message": f"Held {days_held} days with only {pnl_pct:+.1f}% return. Opportunity cost.",
                    "action": "REVIEW — has the thesis played out? Consider reallocating capital.",
                    "category": "time_stop",
                })
            # 3 months flat
            elif days_held > 90 and -5 < pnl_pct < 5:
                signals.append({
                    "type": "TIME_STOP_EARLY",
                    "severity": "LOW",
                    "message": f"Held {days_held} days, essentially flat ({pnl_pct:+.1f}%). Check if catalysts are still coming.",
                    "action": "MONITOR — re-evaluate thesis and expected catalysts.",
                    "category": "time_stop",
                })
        except ValueError:
            pass

    return signals


def _determine_position_status(signals: list) -> str:
    """Determine overall position status from exit signals."""
    severities = [s["severity"] for s in signals]
    categories = [s["category"] for s in signals]

    if any(s["type"] == "HARD_STOP_LOSS" for s in signals):
        return STATUS_SELL_ALL
    if any(s["type"] == "TRAILING_STOP_TIGHT" for s in signals):
        return STATUS_SELL_PARTIAL
    if "CRITICAL" in severities:
        return STATUS_SELL_ALL
    if any(s["type"] in ("PROFIT_100PCT", "PROFIT_50PCT") for s in signals):
        return STATUS_SELL_PARTIAL
    if "HIGH" in severities:
        return STATUS_WATCH_EXIT
    if any(s["type"] == "PROFIT_25PCT" for s in signals):
        return STATUS_HOLD_TRIM
    if "MEDIUM" in severities:
        return STATUS_WATCH_EXIT
    return STATUS_HOLD_STRONG


def generate_trade_card(position: dict, current_price: float = None) -> str:
    """Generate a trade card for a position."""
    t = position
    ticker = t["ticker"]
    entry = t["entry_price"]
    shares = t["shares"]
    cost = entry * shares
    thesis = t.get("thesis", "N/A")
    expected = t.get("expected_holding_period", "N/A")
    max_loss = t.get("max_loss_pct", 25)
    trailing = t.get("trailing_stop_pct", 15)
    stop_price = entry * (1 - max_loss / 100)

    card = f"""
╔══════════════════════════════════════╗
║  📊 TRADE CARD: ${ticker}
╠══════════════════════════════════════╣
║  Entry:     ${entry:.2f} × {shares:.2f} = ${cost:,.2f}
║  Date:      {t.get('entry_date', 'N/A')}
║  Status:    {t.get('position_status', 'Hold Strong')}
╠══════════════════════════════════════╣
║  📝 Thesis: {thesis}
║  ⏱  Horizon: {expected}
╠══════════════════════════════════════╣
║  🛑 Stop Loss:     ${stop_price:.2f} (-{max_loss}%)
║  📉 Trailing Stop: {trailing}% from peak
║  🎯 Profit Targets:
║     +25% → sell 20-30%  (${entry*1.25:.2f})
║     +50% → sell 30-50%  (${entry*1.50:.2f})
║     +100% → recover cost (${entry*2.0:.2f})
╠══════════════════════════════════════╣"""

    if current_price:
        pnl = (current_price - entry) / entry * 100
        card += f"""
║  💰 Current: ${current_price:.2f} ({pnl:+.1f}%)
╚══════════════════════════════════════╝"""
    else:
        card += """
╚══════════════════════════════════════╝"""

    return card
