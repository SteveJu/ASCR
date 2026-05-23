"""Shadow tracker — tracks B-rated stocks without buying them."""
from datetime import datetime, timedelta
from src import db
from src.broker_simulator import get_current_price
from src.utils import get_logger

logger = get_logger("shadow")


def record_shadows(shadow_signals: list):
    """Record new B-rated stocks for shadow tracking."""
    today = datetime.now().strftime("%Y-%m-%d")

    for sig in shadow_signals:
        ticker = sig["ticker"]
        price = get_current_price(ticker)
        if price <= 0:
            continue

        db.add_shadow(
            today, ticker, sig["rating"],
            sig.get("evidence", 0), sig.get("asymmetry", 0),
            sig.get("momentum", 0), sig.get("risk", 0),
            sig.get("opportunity_score", 0),
            price, sig.get("sector", "")
        )
    logger.info(f"Recorded {len(shadow_signals)} shadow tracks")


def update_shadows():
    """Update forward returns for pending shadow tracks."""
    pending = db.get_pending_shadows()
    today = datetime.now()

    for shadow in pending:
        ticker = shadow["ticker"]
        signal_date = shadow["signal_date"]
        entry_price = shadow["entry_reference_price"]

        if entry_price <= 0:
            continue

        try:
            sig_dt = datetime.strptime(signal_date, "%Y-%m-%d")
        except ValueError:
            continue

        days_elapsed = (today - sig_dt).days
        current_price = get_current_price(ticker)
        if current_price <= 0:
            continue

        # Get price history to compute max gain/drawdown
        prices = db.read_radar_prices(ticker, days=min(days_elapsed + 5, 65))
        prices_since = [p for p in prices if p["date"] >= signal_date]
        closes = [p["close"] for p in prices_since if p["close"]]

        updates = {}

        if closes:
            max_price = max(closes)
            min_price = min(closes)
            max_gain = (max_price - entry_price) / entry_price * 100
            max_dd = (min_price - entry_price) / entry_price * 100

            if days_elapsed >= 60:
                updates["max_gain_60d"] = max_gain
                updates["max_drawdown_60d"] = max_dd

        current_return = (current_price - entry_price) / entry_price * 100

        if days_elapsed >= 5 and shadow.get("return_5d") is None:
            updates["return_5d"] = current_return
        if days_elapsed >= 10 and shadow.get("return_10d") is None:
            updates["return_10d"] = current_return
        if days_elapsed >= 20 and shadow.get("return_20d") is None:
            updates["return_20d"] = current_return
        if days_elapsed >= 60 and shadow.get("return_60d") is None:
            updates["return_60d"] = current_return

        if updates:
            db.update_shadow_returns(shadow["id"], **updates)

    logger.info(f"Updated {len(pending)} shadow tracks")
