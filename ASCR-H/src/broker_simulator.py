"""Simulated broker — executes paper trades at market price."""
from datetime import datetime
from src import db, config
from src.utils import get_logger

logger = get_logger("broker")


def get_current_price(ticker: str) -> float:
    """Get latest price from ascr DB."""
    prices = db.read_radar_prices(ticker, days=5)
    if prices:
        return prices[0]["close"]
    return 0.0


def buy(ticker: str, dollar_amount: float, reason: str = "", rating: str = "") -> dict:
    """Execute a paper buy order."""
    price = get_current_price(ticker)
    if price <= 0:
        logger.warning(f"No price for {ticker}, skipping buy")
        return {"success": False, "reason": "no_price"}

    account = db.get_account()
    if not account:
        logger.error("No account initialized")
        return {"success": False, "reason": "no_account"}

    cash = account["cash"]
    cfg = config.load()

    # Check cash
    if dollar_amount > cash:
        dollar_amount = cash * 0.95  # Use 95% of remaining cash max
        if dollar_amount < 100:
            return {"success": False, "reason": "insufficient_cash"}

    # Check max position size
    total_equity = _total_equity()
    max_pos = total_equity * cfg["limits"]["max_position_pct"]
    existing = db.get_position(ticker)
    current_pos_value = existing["current_value"] if existing else 0

    if current_pos_value + dollar_amount > max_pos:
        dollar_amount = max(0, max_pos - current_pos_value)
        if dollar_amount < 100:
            return {"success": False, "reason": "max_position_exceeded"}

    # Check max positions count
    if not existing:
        open_positions = db.get_all_positions()
        if len(open_positions) >= cfg["limits"]["max_positions"]:
            return {"success": False, "reason": "max_positions_reached"}

    # Check no duplicate buy
    if existing and cfg["limits"]["no_duplicate_buy"]:
        # Allow only if this is a rating upgrade
        if not reason.startswith("upgrade"):
            return {"success": False, "reason": "duplicate_position"}

    # Execute
    quantity = dollar_amount / price
    date = datetime.now().strftime("%Y-%m-%d")

    db.add_order(date, ticker, "BUY", quantity, price, reason, "", rating)
    db.increase_position(ticker, date, price, quantity, rating, existing.get("sector", "") if existing else "")

    # Update cash
    db.update_cash(cash - dollar_amount)

    logger.info(f"BUY {ticker}: {quantity:.2f} shares @ ${price:.2f} = ${dollar_amount:,.0f} ({reason})")
    return {"success": True, "ticker": ticker, "quantity": quantity, "price": price, "amount": dollar_amount}


def sell(ticker: str, sell_pct: float = 1.0, reason: str = "") -> dict:
    """Sell a percentage of a position."""
    pos = db.get_position(ticker)
    if not pos or pos["quantity"] <= 0:
        return {"success": False, "reason": "no_position"}

    price = get_current_price(ticker)
    if price <= 0:
        return {"success": False, "reason": "no_price"}

    sell_qty = pos["quantity"] * sell_pct
    sell_amount = sell_qty * price
    date = datetime.now().strftime("%Y-%m-%d")

    realized = db.reduce_position(ticker, sell_qty, price)
    db.add_order(date, ticker, "SELL", sell_qty, price, reason, "", "")

    # Update cash
    account = db.get_account()
    db.update_cash(account["cash"] + sell_amount)

    logger.info(f"SELL {ticker}: {sell_qty:.2f} shares @ ${price:.2f} = ${sell_amount:,.0f} (P&L: ${realized:+,.0f}) [{reason}]")
    return {"success": True, "ticker": ticker, "quantity": sell_qty, "price": price,
            "amount": sell_amount, "realized_pnl": realized}


def _total_equity() -> float:
    account = db.get_account()
    if not account:
        return 0
    cash = account["cash"]
    positions = db.get_all_positions()
    pos_value = sum(p.get("current_value", 0) for p in positions)
    return cash + pos_value
