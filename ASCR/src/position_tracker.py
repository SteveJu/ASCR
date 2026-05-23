"""Position tracker — monitor open positions and P&L."""
from src import db
from src.utils import get_logger

logger = get_logger("position_tracker")

def get_position_status(positions: list = None, prices_cache: dict = None) -> list:
    """Get current status of all open positions."""
    if positions is None:
        positions = db.get_open_positions()

    results = []
    for pos in positions:
        ticker = pos["ticker"]
        entry_price = pos["entry_price"]
        shares = pos["shares"]

        # Get latest price
        prices = db.get_prices(ticker, days=5)
        if not prices:
            continue

        current_price = prices[0]["close"]  # Most recent
        cost_basis = entry_price * shares
        current_value = current_price * shares
        pnl = current_value - cost_basis
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # Get highest price since entry
        all_prices = db.get_prices(ticker, days=200)
        entry_date = pos["entry_date"]
        prices_since_entry = [p for p in all_prices if p["date"] >= entry_date]
        max_price = max((p["close"] for p in prices_since_entry), default=current_price)
        drawdown_from_high = (current_price - max_price) / max_price * 100 if max_price > 0 else 0

        results.append({
            "id": pos["id"],
            "ticker": ticker,
            "shares": shares,
            "entry_price": entry_price,
            "entry_date": entry_date,
            "current_price": current_price,
            "cost_basis": cost_basis,
            "current_value": current_value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "max_price_since_entry": max_price,
            "drawdown_from_high": drawdown_from_high,
            "stop_loss": pos.get("stop_loss"),
            "take_profit": pos.get("take_profit"),
            "trailing_stop_pct": pos.get("trailing_stop_pct", 0.15),
            "notes": pos.get("notes", ""),
        })

    return results
