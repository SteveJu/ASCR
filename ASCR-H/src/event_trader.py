"""Event Trader — pure executor. No judgment, no analysis.

Reads instructions from ASCR's recommender and executes them.
All buy/sell/hold decisions are made by radar.
"""
import os
import sys
from datetime import datetime

# Paper-trader's own modules first
from src import config as pt_config, db
from src.decision_logger import log_decision
from src.trading_rules import validate_trade, validate_trade_full, is_market_open, next_market_open
from src.utils import get_logger

logger = get_logger("event_trader")

PT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "ascr_h.sqlite")

# Stock-radar modules (for activity logging)
sys.path.insert(0, os.environ.get("ASCR_PROJECT_DIR", "../ASCR"))
try:
    from src.activity_log import log as alog
except ImportError:
    def alog(*a, **kw): pass


# Real-time price cache (60s TTL)
_price_cache = {}
_CACHE_TTL = 60


def _get_live_price(ticker: str) -> float:
    """Real-time price via yfinance, cached 60s, fallback to DB."""
    import time as _time
    now = _time.time()
    if ticker in _price_cache:
        p, t = _price_cache[ticker]
        if now - t < _CACHE_TTL:
            return p
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if not price:
            hist = t.history(period="1d")
            if len(hist) > 0:
                price = float(hist["Close"].iloc[-1])
        if price and price > 0:
            _price_cache[ticker] = (price, now)
            return price
    except Exception as e:
        logger.warning(f"price_fetch_yfinance_failed ticker={ticker} error={e}")
    prices = db.read_radar_prices(ticker, days=1)
    price = prices[0]["close"] if prices else 0.0
    if price > 0:
        _price_cache[ticker] = (price, now)
        logger.info(f"price_fetch_fallback_db ticker={ticker} price=${price:.2f}")
    else:
        logger.warning(f"price_fetch_missing ticker={ticker} sources=yfinance,radar_db")
    return price



# === PENDING TRADE RE-EVALUATION ===
def _check_pending_trades(conn, current_positions, total_equity):
    """Re-evaluate pending trades that are now eligible.

    Key principle: don't execute stale decisions.
    When a trade was blocked (cooldown/PDT), we saved it as pending.
    Now that it's eligible, we re-check: does radar still want this trade?
    If not, cancel it.
    """
    import importlib.util
    today = __import__('datetime').datetime.now().strftime("%Y-%m-%d")

    pending = conn.execute(
        "SELECT * FROM pending_trades WHERE status='pending' AND eligible_date <= ?",
        (today,)
    ).fetchall()

    if not pending:
        return []

    # Get fresh instructions from radar
    try:
        spec = importlib.util.spec_from_file_location(
            "radar_recommender", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "src", "recommender.py"))
        recommender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recommender)
        fresh_instructions = recommender.get_portfolio_instructions(
            current_positions=current_positions)
    except Exception as e:
        logger.warning(f"Can't re-evaluate pending trades (radar unavailable): {e}")
        return []

    fresh_sells = {s["ticker"] for s in fresh_instructions.get("sells", [])}
    fresh_buys = {b["ticker"] for b in fresh_instructions.get("buys", [])}

    results = []
    for p in pending:
        ticker = p["ticker"]
        action = p["action"]

        # Re-evaluate: does radar still want this?
        if action == "SELL" and ticker in fresh_sells:
            # Still wants to sell — execute
            conn.execute(
                "UPDATE pending_trades SET status='executed', resolved_date=?, resolved_reason='re-evaluated: still sell' WHERE id=?",
                (today, p["id"]))
            results.append({"ticker": ticker, "action": "SELL", "reason": f"pending_reeval: {p['original_reason']}"})
            logger.info(f"Pending SELL {ticker}: re-evaluated, still valid → executing")

        elif action == "SELL" and ticker not in fresh_sells:
            # No longer wants to sell — cancel
            conn.execute(
                "UPDATE pending_trades SET status='cancelled', resolved_date=?, resolved_reason='re-evaluated: no longer sell signal' WHERE id=?",
                (today, p["id"]))
            logger.info(f"Pending SELL {ticker}: re-evaluated, signal gone → cancelled")

        elif action == "BUY" and ticker in fresh_buys:
            conn.execute(
                "UPDATE pending_trades SET status='executed', resolved_date=?, resolved_reason='re-evaluated: still buy' WHERE id=?",
                (today, p["id"]))
            results.append({"ticker": ticker, "action": "BUY", "reason": f"pending_reeval: {p['original_reason']}"})
            logger.info(f"Pending BUY {ticker}: re-evaluated, still valid → executing")

        elif action == "BUY" and ticker not in fresh_buys:
            conn.execute(
                "UPDATE pending_trades SET status='cancelled', resolved_date=?, resolved_reason='re-evaluated: no longer buy signal' WHERE id=?",
                (today, p["id"]))
            logger.info(f"Pending BUY {ticker}: re-evaluated, signal gone → cancelled")

        # Expire old pending trades (> 5 days)
        created = __import__('datetime').datetime.strptime(p["created_date"], "%Y-%m-%d")
        if (__import__('datetime').datetime.now() - created).days > 5:
            conn.execute(
                "UPDATE pending_trades SET status='expired', resolved_date=?, resolved_reason='expired after 5 days' WHERE id=?",
                (today, p["id"]))
            logger.info(f"Pending {action} {ticker}: expired after 5 days")

    conn.commit()
    return results


def _save_pending_trade(conn, ticker, action, reason, blocked_reason, eligible_date):
    """Save a blocked trade for later re-evaluation."""
    today = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO pending_trades (ticker, action, original_reason, blocked_reason, created_date, eligible_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ticker, action, reason, blocked_reason, today, eligible_date))
    conn.commit()
    logger.info(f"Saved pending {action} {ticker}: blocked by {blocked_reason}, eligible {eligible_date}")


def run_daily() -> dict:
    """Execute daily: get instructions from radar, execute them.

    Enforces trading rules:
    - Market hours (Mon-Fri 9:30-16:00 ET)
    - PDT rule (<$25K no day trading)
    - T+1 settlement
    - No duplicate same-day buys
    """
    # Check trading day
    now = datetime.now()
    from src.trading_rules import is_trading_day
    is_day, day_reason = is_trading_day(now)
    if not is_day:
        next_open = next_market_open(now)
        logger.info(f"Not a trading day ({day_reason}). Next open: {next_open}")
        positions = {p["ticker"]: p for p in db.get_all_positions("open")}
        account = db.get_account()
        cash = account["cash"]
        pos_value = sum(p["quantity"] * _get_live_price(p["ticker"]) for p in positions.values())
        total_eq = cash + pos_value
        return {
            "date": now.strftime("%Y-%m-%d"),
            "cash": cash, "positions_value": pos_value,
            "total_equity": total_eq, "num_positions": len(positions),
            "return_pct": (total_eq / 10000 - 1) * 100,
            "actions": [],
            "instructions": {"buys": [], "sells": [], "holds": []},
            "skipped": f"not trading day: {day_reason}",
        }

    cfg = pt_config.load()
    max_pos = cfg.get("sizing", {}).get("max_positions", 10)
    pos_pct = cfg.get("sizing", {}).get("per_position_pct", 0.10)
    today = datetime.now().strftime("%Y-%m-%d")

    # Get current state
    positions = {p["ticker"]: p for p in db.get_all_positions("open")}
    account = db.get_account()
    cash = account["cash"]

    # Build current positions dict for radar
    current = {}
    for ticker, pos in positions.items():
        price = _get_live_price(ticker)
        current[ticker] = {
            "quantity": pos["quantity"],
            "avg_entry_price": pos["avg_entry_price"],
            "peak_price": pos.get("max_price_since_entry") or pos["avg_entry_price"],
            "entry_date": pos.get("entry_date", ""),
            "current_price": price,
        }

    # Refresh all position prices (current_value, unrealized_pnl)
    try:
        from src.db import refresh_position_prices
        refresh_position_prices()
    except Exception as e:
        logger.warning(f"Price refresh: {e}")

    # Update peak prices with live data
    for ticker, info in current.items():
        old_peak = info["peak_price"]
        if info["current_price"] > old_peak:
            db.update_peak_price(ticker, info["current_price"])
            info["peak_price"] = info["current_price"]
            logger.info(f"  📈 {ticker} peak updated: ${old_peak:.2f} → ${info['current_price']:.2f}")

    total_equity = cash + sum(c["current_price"] * c["quantity"] for c in current.values())

    # === ASK RADAR FOR INSTRUCTIONS ===
    import importlib.util
    import signal as _signal

    def _radar_timeout_handler(signum, frame):
        raise TimeoutError("Radar took >120s — likely hung")

    instructions = None
    old_handler = None
    try:
        # Set 120s timeout for radar call
        old_handler = _signal.signal(_signal.SIGALRM, _radar_timeout_handler)
        _signal.alarm(120)

        spec = importlib.util.spec_from_file_location(
            "radar_recommender", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "src", "recommender.py"))
        recommender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recommender)
        get_portfolio_instructions = recommender.get_portfolio_instructions
        instructions = get_portfolio_instructions(
            current_positions=current,
            max_pos=max_pos,
        )
    except (TimeoutError, FileNotFoundError, ImportError, Exception) as e:
        logger.error(f"Radar unavailable: {e}")
        try:
            from src.telegram_notifier import send_paper
            send_paper(f"🚨 <b>RADAR UNAVAILABLE</b>\n\n"
                      f"Error: {str(e)}\n\n"
                      f"Action: HOLDING all positions. No trades today.\n"
                      f"Manual check required.")
        except Exception as notify_err:
            logger.warning(f"Telegram radar_unavailable alert failed: {notify_err}")
        # Return safe result — hold everything, do nothing
        return {
            "actions": [],
            "positions": current,
            "portfolio_value": sum(p.get("current_price", 0) * p.get("shares", 0) for p in current.values()),
            "cash": cash,
            "error": f"radar_unavailable: {e}",
        }
    finally:
        _signal.alarm(0)
        if old_handler is not None:
            _signal.signal(_signal.SIGALRM, old_handler)

    actions = []

    # === BUBBLE CHECK ===
    bubble = instructions.get("bubble", {})
    if bubble.get("level"):
        try:
            from src.telegram_notifier import send_paper
            send_paper(f"<b>{bubble['message']}</b>")
            if bubble["action"] == "liquidate":
                send_paper("💀 <b>MELTDOWN triggered - liquidating all positions</b>")
        except Exception as notify_err:
            logger.warning(f"Telegram bubble alert failed: {notify_err}")

    # === EXECUTE SELLS ===
    for sell in instructions["sells"]:
        ticker = sell["ticker"]
        if ticker not in positions:
            continue

        pos = positions[ticker]

        # Trading rules check
        allowed, rule_msgs = validate_trade_full(
            ticker, "SELL", total_equity,
            trade_value=pos["quantity"] * pos.get("current_price", pos["avg_entry_price"]),
            portfolio_value=total_equity,
            db_path=PT_DB,
            entry_price=pos.get("avg_entry_price", 0),
            current_price=pos.get("current_price", 0))
        if not allowed:
            logger.warning(f"  ⛔ SELL {ticker} blocked: {rule_msgs}")
            alog("trader", "sell_blocked", ticker, reasons=str(rule_msgs))
            continue
        price = _get_live_price(ticker)
        if not price:
            logger.warning(f"SELL {ticker} skipped: no live price")
            continue

        sell_amount = pos["quantity"] * price
        pnl_pct = sell["pnl_pct"]

        realized = db.reduce_position(ticker, pos["quantity"], price)
        db.update_cash(cash + sell_amount)
        cash += sell_amount
        db.add_order(today, ticker, "SELL", pos["quantity"], price,
                     sell["reason"], rating="")
        log_decision(ticker, "SELL", price, scores={},
                     reason=sell["reason"], mode="live_event")
        alog("trader", "sell", ticker, price=price, pnl_pct=pnl_pct,
             reason=sell["reason"], urgency=sell["urgency"])

        actions.append({
            "type": "SELL", "ticker": ticker, "shares": pos["quantity"],
            "price": price, "pnl_pct": pnl_pct, "reason": sell["reason"],
        })
        del positions[ticker]
        logger.info(f"  🔴 SELL {ticker} @ ${price:.2f} ({pnl_pct:+.1f}%) — {sell['reason']}")

    # === EXECUTE BUYS (paper trader decides cash allocation) ===
    slots = max_pos - len(positions)
    target_per_position = total_equity * pos_pct

    for buy in instructions["buys"]:
        if slots <= 0:
            break
        ticker = buy["ticker"]
        if ticker in positions:
            continue

        # Trading rules check
        allowed, rule_msgs = validate_trade_full(
            ticker, "BUY", total_equity,
            trade_value=target_per_position,
            portfolio_value=total_equity,
            db_path=PT_DB)
        if not allowed:
            logger.warning(f"  ⛔ BUY {ticker} blocked: {rule_msgs}")
            alog("trader", "buy_blocked", ticker, reasons=str(rule_msgs))
            continue

        price = _get_live_price(ticker)
        if not price:
            logger.warning(f"BUY {ticker} skipped: no live price")
            continue

        amount = min(target_per_position, cash)
        if amount < 50:
            # Cash alert — executor's responsibility
            try:
                from src.telegram_notifier import send_paper
                send_paper(f"⚠️ Not enough cash: wanted {ticker} (rank #{buy['rank']}) but only ${cash:.0f} remains")
            except Exception as notify_err:
                logger.warning(f"Telegram cash alert failed ticker={ticker}: {notify_err}")
            alog("trader", "no_cash_alert", ticker,
                 needed=round(target_per_position, 0), available=round(cash, 0))
            break

        shares = amount / price
        db.increase_position(ticker, today, price, shares, "", "")
        db.update_cash(cash - amount)
        cash -= amount
        db.add_order(today, ticker, "BUY", shares, price,
                     buy["reason"], rating="")
        log_decision(ticker, "BUY", price, scores={},
                     reason=buy["reason"], mode="live_event")
        alog("trader", "buy", ticker, price=price, shares=round(shares, 2),
             amount=round(amount, 0), reason=buy["reason"])

        positions[ticker] = {"ticker": ticker, "quantity": shares,
                             "avg_entry_price": price}
        actions.append({
            "type": "BUY", "ticker": ticker, "shares": shares,
            "price": price, "amount": amount, "reason": buy["reason"],
        })
        logger.info(f"  🟢 BUY {ticker} {shares:.1f}sh @ ${price:.2f} — {buy['reason']}")
        slots -= 1

    # (alerts handled inline during buy execution)

    # === LOG HOLDS ===
    for hold in instructions["holds"]:
        alog("trader", "hold", hold["ticker"], note=hold["note"])

    # === RECORD EQUITY ===
    positions = {p["ticker"]: p for p in db.get_all_positions("open")}
    final_prices = {t: _get_live_price(t) for t in positions}
    pos_value = sum(p["quantity"] * final_prices[t] for t, p in positions.items())
    total_eq = cash + pos_value
    peak = max(account.get("peak_equity", 10000), total_eq)
    db.update_peak_equity(peak)
    dd = (total_eq - peak) / peak * 100 if peak > 0 else 0
    db.record_equity(today, cash, pos_value, total_eq,
                     (total_eq / 10000 - 1) * 100, dd, peak)

    alog("trader", "daily_summary",
         cash=round(cash, 0), positions_value=round(pos_value, 0),
         total_equity=round(total_eq, 0),
         return_pct=round((total_eq / 10000 - 1) * 100, 2),
         drawdown=round(dd, 2), num_positions=len(positions),
         holdings=",".join(positions.keys()),
         sells=len(instructions["sells"]),
         buys=len(instructions["buys"]))

    logger.info(f"  💰 Cash: ${cash:,.0f} | Positions: ${pos_value:,.0f} | "
                f"Total: ${total_eq:,.0f} ({(total_eq / 10000 - 1) * 100:+.1f}%)")

    return {
        "date": today, "cash": cash, "positions_value": pos_value,
        "total_equity": total_eq, "num_positions": len(positions),
        "return_pct": (total_eq / 10000 - 1) * 100,
        "actions": actions,
        "instructions": instructions,
        "positions": {t: {
            "pnl_pct": (final_prices[t] / p["avg_entry_price"] - 1) * 100,
            "current_price": final_prices[t],
        } for t, p in positions.items()},
    }


def format_daily_telegram(result: dict) -> str:
    """Format daily run result for Telegram push."""
    lines = []
    d = result.get("date", "")
    total = result.get("total_equity", 0)
    ret = result.get("return_pct", 0)
    cash = result.get("cash", 0)
    n = result.get("num_positions", 0)

    emoji = "📈" if ret >= 0 else "📉"
    lines.append("{} <b>Daily Report {}</b>".format(emoji, d))
    lines.append("💰 Total: ${:,.0f} ({:+.1f}%) | Cash: ${:,.0f} | Pos: {}".format(total, ret, cash, n))

    if result.get("skipped"):
        lines.append("")
        lines.append("⏸ {}".format(result["skipped"]))
        return "\n".join(lines)

    for a in result.get("actions", []):
        if a["type"] == "BUY":
            lines.append("")
            lines.append("🟢 BUY {} {:.1f}sh @ ${:.2f}".format(a["ticker"], a["shares"], a["price"]))
        elif a["type"] == "SELL":
            lines.append("")
            lines.append("🔴 SELL {} @ ${:.2f} ({:+.1f}%)".format(a["ticker"], a["price"], a.get("pnl_pct", 0)))

    positions = result.get("positions", {})
    if positions:
        lines.append("")
        lines.append("📊 <b>Positions</b>")
        for t, p in sorted(positions.items(), key=lambda x: x[1].get("pnl_pct", 0), reverse=True):
            pnl = p.get("pnl_pct", 0)
            icon = "🟢" if pnl >= 0 else "🔴"
            lines.append("  {} {}: {:+.1f}%".format(icon, t, pnl))

    return "\n".join(lines)
