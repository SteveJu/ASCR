"""Intraday Position Monitor — executor watches its own positions.

Runs mid-day (12:30 PM) to check:
- Approaching hard stop (-15% warning, -20% trigger)
- Approaching trailing stop after activation (+30% peak gain, -30% from peak)
- Big intraday moves (>5%)
- Update peak prices in DB

Sends alerts to ASCR-H Bot bot (executor's own channel).
"""
import os
import sys
import time
import yfinance as yf
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, config
from src.trading_rules import is_market_open
from src.utils import get_logger

logger = get_logger("intraday_monitor")


def _get_realtime_price(ticker: str) -> float | None:
    """Get real-time price via yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if not price:
            hist = t.history(period="1d")
            if len(hist) > 0:
                price = float(hist["Close"].iloc[-1])
        if price and price > 0:
            return price
        logger.warning(f"price_fetch_missing ticker={ticker} source=yfinance")
        return None
    except Exception as e:
        logger.warning(f"price_fetch_failed ticker={ticker} source=yfinance error={e}")
        return None


def run_intraday_check() -> dict:
    """Check all open positions against stop levels."""
    # Only run during market hours
    mkt_open, mkt_reason = is_market_open()
    if not mkt_open:
        logger.info(f"Market closed ({mkt_reason}), skipping intraday check")
        return {"skipped": mkt_reason}

    cfg = config.load()
    hard_stop = cfg.get("sell", {}).get("hard_stop_pct", -20)
    trailing_activation = cfg.get("sell", {}).get("trailing_stop_activation_pct", 30)
    trailing_pct = cfg.get("sell", {}).get("trailing_stop_from_peak_pct", -30)

    positions = db.get_all_positions("open")
    if not positions:
        return {"positions": 0, "alerts": []}

    alerts = []
    updated_peaks = []

    for pos in positions:
        ticker = pos["ticker"]
        entry = pos["avg_entry_price"]
        peak = pos.get("max_price_since_entry") or entry

        price = _get_realtime_price(ticker)
        if not price:
            continue

        # Update peak in DB if new high
        if price > peak:
            try:
                db.update_peak_price(ticker, price)
                updated_peaks.append(ticker)
                peak = price
            except Exception as e:
                logger.warning(f"Peak price update failed ticker={ticker}: {e}")

        pnl_pct = (price - entry) / entry * 100
        drop_from_peak = (price - peak) / peak * 100 if peak > 0 else 0
        peak_pnl_pct = (peak - entry) / entry * 100 if entry > 0 else 0
        trailing_active = peak_pnl_pct >= trailing_activation

        # Hard stop zone: warn at 75% of trigger
        warn_hard = hard_stop * 0.75  # -15% for -20% stop
        if pnl_pct <= hard_stop:
            qty = pos.get("quantity", 0)
            pnl_dollar = (price - entry) * qty
            alerts.append({
                "level": "CRITICAL",
                "msg": f"🚨 <b>{ticker}</b> {pnl_pct:+.1f}% - hard stop triggered ({hard_stop}%)\n"
                       f"   entry ${entry:.2f} → ${price:.2f} (peak ${peak:.2f})\n"
                       f"   {qty:.2f} shares | P&L ${pnl_dollar:+,.0f}",
            })
        elif pnl_pct <= warn_hard:
            qty = pos.get("quantity", 0)
            pnl_dollar = (price - entry) * qty
            alerts.append({
                "level": "WARNING",
                "msg": f"🔴 <b>{ticker}</b> {pnl_pct:+.1f}% - near {hard_stop}% hard stop\n"
                       f"   entry ${entry:.2f} → ${price:.2f} (peak ${peak:.2f})\n"
                       f"   {qty:.2f} shares | P&L ${pnl_dollar:+,.0f}",
            })
        # Trailing stop zone
        elif trailing_active and drop_from_peak <= trailing_pct:
            qty = pos.get("quantity", 0)
            pnl_dollar = (price - entry) * qty
            alerts.append({
                "level": "CRITICAL",
                "msg": f"🚨 <b>{ticker}</b> {drop_from_peak:.0f}% from peak - trailing stop triggered\n"
                       f"   entry ${entry:.2f} → peak ${peak:.2f} → ${price:.2f}\n"
                       f"   peak gain {peak_pnl_pct:+.1f}% | {qty:.2f} shares | "
                       f"P&L ${pnl_dollar:+,.0f} | total P&L {pnl_pct:+.1f}%",
            })
        elif trailing_active and drop_from_peak <= trailing_pct * 0.8:
            qty = pos.get("quantity", 0)
            pnl_dollar = (price - entry) * qty
            alerts.append({
                "level": "WARNING",
                "msg": f"🟡 <b>{ticker}</b> {drop_from_peak:.0f}% from peak - near {trailing_pct}% trailing stop\n"
                       f"   entry ${entry:.2f} → peak ${peak:.2f} → ${price:.2f}\n"
                       f"   peak gain {peak_pnl_pct:+.1f}% | {qty:.2f} shares | "
                       f"P&L ${pnl_dollar:+,.0f} | total P&L {pnl_pct:+.1f}%",
            })
        # Big move alert
        elif abs(pnl_pct) >= 5:
            emoji = "📈" if pnl_pct > 0 else "📉"
            qty = pos.get("quantity", 0)
            value = qty * price
            pnl_dollar = (price - entry) * qty
            alerts.append({
                "level": "INFO",
                "msg": f"{emoji} <b>{ticker}</b> {pnl_pct:+.1f}%\n"
                       f"   entry ${entry:.2f} → ${price:.2f} (peak ${peak:.2f})\n"
                       f"   {qty:.2f} shares | value ${value:,.0f} | P&L ${pnl_dollar:+,.0f}\n"
                       f"   from peak: {drop_from_peak:+.1f}%",
            })

        time.sleep(0.3)  # Rate limit yfinance

    # Push to Telegram if any alerts
    if alerts:
        try:
            from src.telegram_notifier import _send
            lines = ["📊 <b>Intraday Position Monitor</b>\n"]
            lines += [a["msg"] for a in alerts]
            if updated_peaks:
                lines.append(f"\n📈 Peak updated: {', '.join(updated_peaks)}")
            _send("\n".join(lines))
        except Exception as e:
            logger.warning(f"Telegram push: {e}")

    if updated_peaks:
        logger.info(f"Peak prices updated: {updated_peaks}")
    logger.info(f"Intraday check: {len(positions)} positions, {len(alerts)} alerts")

    return {
        "positions": len(positions),
        "alerts": [a["msg"] for a in alerts],
        "updated_peaks": updated_peaks,
    }


if __name__ == "__main__":
    result = run_intraday_check()
    if "skipped" in result:
        print(f"Skipped: {result['skipped']}")
    else:
        print(f"Checked {result['positions']} positions, {len(result['alerts'])} alerts")



def run_intraday_trades() -> dict:
    """Mid-day trade execution: stop-loss sells + fill empty slots.

    Called at 12:30 alongside monitoring. Complements 09:45 daily run.
    """
    from src.trading_rules import is_market_open, validate_trade_full
    import importlib.util

    mkt_open, mkt_reason = is_market_open()
    if not mkt_open:
        logger.info(f"Market closed ({mkt_reason}), skipping intraday trades")
        return {"skipped": mkt_reason}

    PT_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ascr_h.sqlite")
    cfg = config.load()
    max_pos = cfg.get("sizing", {}).get("max_positions", 10)
    pos_pct = cfg.get("sizing", {}).get("per_position_pct", 0.10)

    positions = {p["ticker"]: p for p in db.get_all_positions("open")}
    account = db.get_account()
    cash = account["cash"]
    pos_value = sum(p["quantity"] * (_get_realtime_price(p["ticker"]) or p["avg_entry_price"])
                    for p in positions.values())
    total_equity = cash + pos_value

    # Ask radar for instructions
    try:
        spec = importlib.util.spec_from_file_location(
            "radar_recommender", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "src", "recommender.py"))
        recommender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recommender)

        current = {}
        for ticker, pos in positions.items():
            price = _get_realtime_price(ticker) or pos["avg_entry_price"]
            current[ticker] = {
                "quantity": pos["quantity"],
                "avg_entry_price": pos["avg_entry_price"],
                "peak_price": pos.get("max_price_since_entry") or pos["avg_entry_price"],
                "entry_date": pos.get("entry_date", ""),
                "current_price": price,
            }

        instructions = recommender.get_portfolio_instructions(
            current,
            max_pos=max_pos,
            cash_available=cash,
            portfolio_value=total_equity,
            target_position_pct=pos_pct,
        )
    except Exception as e:
        logger.error(f"Radar unavailable for intraday: {e}")
        return {"error": str(e)}

    actions = []

    # Execute urgent sells (stop-loss)
    for sell in instructions.get("sells", []):
        ticker = sell["ticker"]
        urgency = sell.get("urgency", "normal")
        if urgency != "urgent":
            continue  # Only execute urgent (stop-loss) sells intraday

        if ticker not in positions:
            continue

        price = _get_realtime_price(ticker)
        if not price:
            logger.warning(f"Intraday SELL {ticker} skipped: no live price")
            continue

        pos = positions[ticker]
        qty = pos["quantity"]
        entry = pos["avg_entry_price"]
        allowed, msgs = validate_trade_full(
            ticker, "SELL", total_equity,
            trade_value=qty * price,
            portfolio_value=total_equity,
            db_path=PT_DB,
            entry_price=entry,
            current_price=price)
        if not allowed:
            logger.warning(f"  Intraday SELL {ticker} blocked: {msgs}")
            continue

        proceeds = qty * price
        realized = db.reduce_position(ticker, qty, price)
        today = datetime.now().strftime("%Y-%m-%d")
        db.add_order(today, ticker, "SELL", qty, price, sell.get("reason", "intraday_stop"))
        cash += proceeds
        del positions[ticker]
        db.update_cash(cash)
        actions.append(f"SELL {ticker} @${price:.2f} ({sell.get('reason', '')})")
        logger.info(f"  Intraday SELL {ticker} @${price:.2f} -- {sell.get('reason', '')}")

    # Fill empty slots with buys
    slots = max_pos - len(positions)
    target_per_position = total_equity * pos_pct

    for buy in instructions.get("buys", []):
        if slots <= 0 or cash < 50:
            break
        ticker = buy["ticker"]
        if ticker in positions:
            continue

        allowed, msgs = validate_trade_full(
            ticker, "BUY", total_equity, trade_value=target_per_position,
            portfolio_value=total_equity, db_path=PT_DB)
        if not allowed:
            logger.warning(f"  Intraday BUY {ticker} blocked: {msgs}")
            continue

        price = _get_realtime_price(ticker)
        if not price:
            logger.warning(f"Intraday BUY {ticker} skipped: no live price")
            continue

        amount = min(target_per_position, cash)
        if amount < 50:
            break
        qty = amount / price
        today = datetime.now().strftime("%Y-%m-%d")
        db.increase_position(ticker, today, price, qty)
        db.add_order(today, ticker, "BUY", qty, price, f"intraday_fill: {buy.get('reason', '')}")
        cash -= amount
        db.update_cash(cash)
        slots -= 1
        positions[ticker] = True  # prevent double buy
        actions.append(f"BUY {ticker} @${price:.2f}")
        logger.info(f"  Intraday BUY {ticker} @${price:.2f} -- filling slot")

    # Push summary if any trades
    if actions:
        try:
            from src.telegram_notifier import _send
            lines = ["<b>Intraday Trades</b> (12:30)\n"]
            lines += [f"  {a}" for a in actions]
            lines.append(f"\nCash: ${cash:,.0f}")
            _send("\n".join(lines))
        except Exception as e:
            logger.warning(f"Telegram intraday trades push failed: {e}")

    logger.info(f"Intraday trades: {len(actions)} actions")
    return {"actions": actions, "cash": cash}
