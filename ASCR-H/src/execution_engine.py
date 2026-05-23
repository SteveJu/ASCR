"""Execution engine — processes signals and generates orders."""
from datetime import datetime
from src import db, config
from src.broker_simulator import buy, sell, get_current_price
from src.utils import get_logger

logger = get_logger("execution")


def process_buy_signals(signals: list):
    """Process buy signals from ascr."""
    cfg = config.load()
    account = db.get_account()
    if not account:
        return

    total_equity = account["cash"]
    positions = db.get_all_positions()
    total_equity += sum(p.get("current_value", 0) for p in positions)
    held_tickers = {p["ticker"] for p in positions}

    for sig in signals:
        ticker = sig["ticker"]

        if ticker in held_tickers:
            logger.info(f"Skip {ticker}: already held")
            continue

        sizing_pct = sig["sizing_pct"]
        dollar_amount = total_equity * sizing_pct
        reason = f"rating_{sig['rating']}_opp_{sig['opportunity_score']:.0f}"

        result = buy(ticker, dollar_amount, reason, sig["rating"])
        if result["success"]:
            held_tickers.add(ticker)
        else:
            logger.info(f"Skip {ticker}: {result.get('reason', 'unknown')}")


def process_exit_signals(exit_signals: list):
    """Process exit alerts from ascr."""
    cfg = config.load()

    for sig in exit_signals:
        ticker = sig["ticker"]
        pos = db.get_position(ticker)
        if not pos:
            continue

        alert_type = sig.get("alert_type", "").upper()
        severity = sig.get("severity", "").upper()

        if "THESIS_BROKEN" in alert_type or "THESIS" in alert_type:
            sell(ticker, 1.0, "thesis_broken")
        elif "CATALYST_PLAYED" in alert_type:
            sell(ticker, cfg["exits"]["catalyst_played_sell_pct"], "catalyst_played_out")
        elif severity == "CRITICAL":
            sell(ticker, 1.0, f"critical_alert:{alert_type}")


def process_position_exits():
    """Check all positions against exit rules."""
    cfg = config.load()
    positions = db.get_all_positions()
    today = datetime.now().strftime("%Y-%m-%d")

    for pos in positions:
        ticker = pos["ticker"]
        price = get_current_price(ticker)
        if price <= 0:
            continue

        qty = pos["quantity"]
        avg_entry = pos["avg_entry_price"]
        pnl_pct = (price - avg_entry) / avg_entry * 100
        max_price = pos.get("max_price_since_entry", price)
        entry_date = pos.get("entry_date", today)

        # Update max price
        if price > max_price:
            max_price = price
            db.upsert_position(ticker, entry_date, avg_entry, qty, pos["cost_basis"],
                              qty * price, pos.get("realized_pnl", 0), (price - avg_entry) * qty,
                              max_price, today, pos.get("rating_at_entry", ""), pos.get("sector", ""))
        else:
            # Just update current value
            db.upsert_position(ticker, entry_date, avg_entry, qty, pos["cost_basis"],
                              qty * price, pos.get("realized_pnl", 0), (price - avg_entry) * qty,
                              max_price, pos.get("peak_date", ""), pos.get("rating_at_entry", ""),
                              pos.get("sector", ""))

        # --- Hard Stop Loss ---
        if pnl_pct <= cfg["exits"]["hard_stop_loss_pct"]:
            sell(ticker, 1.0, f"hard_stop_loss_{pnl_pct:.1f}pct")
            continue

        # --- Profit Taking: +100% recover cost ---
        if pnl_pct >= 100 and cfg["exits"]["profit_100_recover_cost"]:
            # Sell enough to recover cost basis
            cost = pos["cost_basis"]
            sell_qty = cost / price
            if sell_qty > 0 and sell_qty < qty:
                sell_pct = sell_qty / qty
                sell(ticker, sell_pct, f"profit_100_recover_cost_{pnl_pct:.0f}pct")
                continue

        # --- Profit Taking: +50% ---
        if pnl_pct >= 50:
            sell(ticker, cfg["exits"]["profit_50_sell_pct"], f"profit_50_{pnl_pct:.0f}pct")
            continue

        # --- Profit Taking: +25% ---
        if pnl_pct >= 25:
            sell(ticker, cfg["exits"]["profit_25_sell_pct"], f"profit_25_{pnl_pct:.0f}pct")
            continue

        # --- Trailing Stop ---
        if pnl_pct >= cfg["exits"]["trailing_stop_activation_pct"] and max_price > 0:
            trailing_pct = cfg["exits"]["trailing_stop_pct"] / 100.0
            trailing_price = max_price * (1 - trailing_pct)
            if price < trailing_price:
                sell(ticker, 1.0, f"trailing_stop_from_peak_{max_price:.2f}")
                continue

        # --- Time Stop ---
        try:
            days_held = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days
            if days_held > cfg["exits"]["time_stop_days"]:
                # Check current rating
                scores = db.read_radar_scores()
                current_rating = "D"
                for s in scores:
                    if s["ticker"] == ticker:
                        current_rating = s.get("rating", "D")
                        break
                min_rating = cfg["exits"]["time_stop_min_rating"]
                rating_order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}
                if rating_order.get(current_rating, 4) > rating_order.get(min_rating, 1):
                    sell(ticker, 1.0, f"time_stop_{days_held}d_rating_{current_rating}")
        except ValueError:
            pass


def run_daily():
    """Full daily execution cycle."""
    from src.signal_reader import get_todays_signals
    signals = get_todays_signals()

    # Process exits first
    process_exit_signals(signals["exit"])
    process_position_exits()

    # Then new buys
    process_buy_signals(signals["buy"])

    return signals
