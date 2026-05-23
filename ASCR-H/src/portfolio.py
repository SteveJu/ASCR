"""Portfolio state management and equity curve recording."""
from datetime import datetime
from src import db
from src.broker_simulator import get_current_price
from src.utils import get_logger

logger = get_logger("portfolio")


def update_portfolio():
    """Update all position values and record equity curve."""
    account = db.get_account()
    if not account:
        return

    positions = db.get_all_positions()
    total_pos_value = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for pos in positions:
        price = get_current_price(pos["ticker"])
        if price > 0:
            value = pos["quantity"] * price
            old_peak = pos.get("max_price_since_entry") or pos["avg_entry_price"]
            peak = max(old_peak, price)
            peak_date = today if price > old_peak else pos.get("peak_date")
            db.upsert_position(
                pos["ticker"], pos["entry_date"], pos["avg_entry_price"],
                pos["quantity"], pos["cost_basis"], value,
                pos.get("realized_pnl", 0), value - pos["cost_basis"],
                peak, peak_date, pos.get("rating_at_entry", ""),
                pos.get("sector", ""), "open"
            )
            total_pos_value += value

    cash = account["cash"]
    total_equity = cash + total_pos_value
    peak = max(account.get("peak_equity", total_equity), total_equity)

    # Daily return
    prev_equity = peak  # simplified; in production, look up yesterday
    daily_return = 0
    drawdown = (total_equity - peak) / peak * 100 if peak > 0 else 0

    db.record_equity(today, cash, total_pos_value, total_equity, daily_return, drawdown, peak)
    db.update_peak_equity(peak)

    logger.info(f"Portfolio: cash=${cash:,.0f} positions=${total_pos_value:,.0f} total=${total_equity:,.0f} dd={drawdown:.1f}%")
    return {
        "cash": cash,
        "positions_value": total_pos_value,
        "total_equity": total_equity,
        "drawdown": drawdown,
        "num_positions": len(positions),
    }
