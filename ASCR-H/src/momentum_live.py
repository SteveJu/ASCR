"""Live momentum trader — reads ASCR scores, manages paper positions.

Uses existing db.py API (paper_positions, paper_orders, account tables).
"""
from datetime import datetime
from src import db, config
from src.decision_logger import log_decision
from src.utils import get_logger

logger = get_logger("momentum_live")


def _get_latest_scores() -> list:
    """Get latest scores from ASCR, ranked by momentum."""
    with db.radar_conn() as conn:
        latest_date = conn.execute("SELECT MAX(date) FROM scores").fetchone()[0]
        if not latest_date:
            return []
        rows = conn.execute("""
            SELECT ticker, date, evidence_score as evidence, asymmetry_score as asymmetry,
                   momentum_score as momentum, risk_score as risk,
                   opportunity_score as opportunity, rating, tracking_priority
            FROM scores WHERE date = ? ORDER BY momentum DESC
        """, (latest_date,)).fetchall()
    return [dict(r) for r in rows]


def _get_live_price(ticker: str) -> float:
    prices = db.read_radar_prices(ticker, days=1)
    return prices[0]["close"] if prices else 0.0


def run_daily() -> dict:
    """Execute daily momentum strategy."""
    cfg = config.load()
    max_pos = cfg.get("sizing", {}).get("max_positions", 5)
    pos_pct = cfg.get("sizing", {}).get("per_position_pct", 0.20)
    min_momentum = cfg.get("buy", {}).get("min_momentum", 50)
    hard_stop = cfg.get("sell", {}).get("hard_stop_pct", -20)
    trail_act = cfg.get("sell", {}).get("trailing_stop_activation_pct", 20)
    trail_drop = cfg.get("sell", {}).get("trailing_stop_from_peak_pct", -25)

    today = datetime.now().strftime("%Y-%m-%d")
    scores = _get_latest_scores()
    if not scores:
        logger.warning("No scores available")
        return {"error": "no scores"}

    logger.info(f"=== Momentum Live — {today} ===")
    logger.info(f"  {len(scores)} scores, top: {scores[0]['ticker']}({scores[0]['momentum']:.0f})")

    positions = {p["ticker"]: p for p in db.get_all_positions("open")}
    account = db.get_account()
    cash = account["cash"]
    actions = []

    # === SELL PASS ===
    for ticker, pos in list(positions.items()):
        price = _get_live_price(ticker)
        if not price:
            continue

        entry = pos["avg_entry_price"]
        peak = max(pos.get("max_price_since_entry") or entry, price)
        pnl_pct = (price - entry) / entry * 100
        drop_from_peak = (price - peak) / peak * 100 if peak > 0 else 0

        # Update position tracking
        db.upsert_position(
            ticker, pos["entry_date"], entry, pos["quantity"], pos["cost_basis"],
            pos["quantity"] * price, pos.get("realized_pnl", 0),
            pos["quantity"] * price - pos["cost_basis"],
            peak, today if price >= peak else pos.get("peak_date"),
            pos.get("rating_at_entry", ""), pos.get("sector", ""), "open"
        )

        score = next((s for s in scores if s["ticker"] == ticker), {})
        score_dict = {
            "rating": score.get("rating", ""), "evidence": score.get("evidence", 0),
            "asymmetry": score.get("asymmetry", 0), "momentum": score.get("momentum", 0),
            "risk": score.get("risk", 0), "opportunity": score.get("opportunity", 0),
            "tracking_priority": score.get("tracking_priority", ""),
        }

        sell_reason = None
        if pnl_pct <= hard_stop:
            sell_reason = f"hard_stop_{pnl_pct:.0f}pct"
        elif pnl_pct > trail_act and drop_from_peak <= trail_drop:
            sell_reason = f"trailing_stop_peak_{peak:.0f}_drop_{drop_from_peak:.0f}pct"

        if sell_reason:
            sell_amount = pos["quantity"] * price
            realized = db.reduce_position(ticker, pos["quantity"], price)
            db.update_cash(cash + sell_amount)
            cash += sell_amount
            db.add_order(today, ticker, "SELL", pos["quantity"], price, sell_reason,
                        rating=score.get("rating", ""))
            log_decision(ticker, "SELL", price, scores=score_dict,
                        reason=sell_reason, mode="live_paper")
            actions.append({
                "type": "SELL", "ticker": ticker, "shares": pos["quantity"],
                "price": price, "pnl_pct": pnl_pct, "reason": sell_reason,
            })
            del positions[ticker]
            logger.info(f"  🔴 SELL {ticker} @ ${price:.2f} ({pnl_pct:+.1f}%) — {sell_reason}")
        else:
            mom = score.get("momentum", 0)
            rank = next((i+1 for i, s in enumerate(scores) if s["ticker"] == ticker), 99)
            log_decision(ticker, "HOLD", price, scores=score_dict,
                        reason=f"hold_pnl_{pnl_pct:+.1f}pct_mom_{mom:.0f}_rank_{rank}",
                        mode="live_paper")

    # === BUY PASS ===
    slots = max_pos - len(positions)
    if slots > 0:
        total_eq = cash + sum(
            p["quantity"] * _get_live_price(t) for t, p in positions.items()
        )
        target = total_eq * pos_pct

        for s in scores:
            if slots <= 0:
                break
            ticker = s["ticker"]
            if ticker in positions:
                continue
            if s["momentum"] < min_momentum:
                break

            price = _get_live_price(ticker)
            if not price:
                continue

            amount = min(target, cash * 0.95)
            if amount < 100:
                break

            shares = amount / price
            rank = next((i+1 for i, x in enumerate(scores) if x["ticker"] == ticker), 99)

            db.increase_position(ticker, today, price, shares, s.get("rating", ""), "")
            db.update_cash(cash - amount)
            cash -= amount
            db.add_order(today, ticker, "BUY", shares, price,
                        f"momentum={s['momentum']:.0f} rank={rank}",
                        rating=s.get("rating", ""))

            score_dict = {
                "rating": s.get("rating", ""), "evidence": s.get("evidence", 0),
                "asymmetry": s.get("asymmetry", 0), "momentum": s["momentum"],
                "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
                "tracking_priority": s.get("tracking_priority", ""),
            }
            log_decision(ticker, "BUY", price, scores=score_dict,
                        reason=f"momentum_{s['momentum']:.0f}_rank_{rank}",
                        mode="live_paper")

            positions[ticker] = {"ticker": ticker, "quantity": shares,
                                "avg_entry_price": price}
            actions.append({
                "type": "BUY", "ticker": ticker, "shares": shares,
                "price": price, "amount": amount, "momentum": s["momentum"], "rank": rank,
            })
            logger.info(f"  🟢 BUY {ticker} {shares:.1f}sh @ ${price:.2f} (mom={s['momentum']:.0f} rank={rank})")
            slots -= 1

    # NO_BUY for rest
    for s in scores:
        ticker = s["ticker"]
        if ticker in positions:
            continue
        bought_tickers = [a["ticker"] for a in actions if a["type"] == "BUY"]
        if ticker in bought_tickers:
            continue
        price = _get_live_price(ticker)
        if not price:
            continue
        score_dict = {
            "rating": s.get("rating", ""), "evidence": s.get("evidence", 0),
            "asymmetry": s.get("asymmetry", 0), "momentum": s["momentum"],
            "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
            "tracking_priority": s.get("tracking_priority", ""),
        }
        rank = next((i+1 for i, x in enumerate(scores) if x["ticker"] == ticker), 99)
        log_decision(ticker, "NO_BUY", price, scores=score_dict,
                    reason=f"momentum_{s['momentum']:.0f}_rank_{rank}",
                    mode="live_paper")

    # Record equity curve
    positions = {p["ticker"]: p for p in db.get_all_positions("open")}
    pos_value = sum(p["quantity"] * _get_live_price(t) for t, p in positions.items())
    total_eq = cash + pos_value
    db.update_peak_equity(max(account.get("peak_equity", 10000), total_eq))
    peak = max(account.get("peak_equity", 10000), total_eq)
    dd = (total_eq - peak) / peak * 100 if peak > 0 else 0
    daily_ret = (total_eq / 10000 - 1) * 100  # vs initial
    db.record_equity(today, cash, pos_value, total_eq, daily_ret, dd, peak)

    logger.info(f"  💰 Cash: ${cash:,.0f} | Positions: ${pos_value:,.0f} | Total: ${total_eq:,.0f} ({daily_ret:+.1f}%)")

    return {
        "date": today, "cash": cash, "positions_value": pos_value,
        "total_equity": total_eq, "num_positions": len(positions),
        "return_pct": daily_ret, "actions": actions,
        "positions": {t: {
            "shares": p["quantity"], "entry": p["avg_entry_price"],
            "current": _get_live_price(t),
            "pnl_pct": (_get_live_price(t) - p["avg_entry_price"]) / p["avg_entry_price"] * 100
                       if p["avg_entry_price"] > 0 else 0,
        } for t, p in positions.items()},
    }


def format_daily_telegram(result: dict) -> str:
    """Format daily result for Telegram."""
    lines = [f"📊 <b>Momentum Sprint — {result['date']}</b>\n"]
    lines.append(f"💰 ${result['total_equity']:,.0f} ({result['return_pct']:+.1f}%)")
    lines.append(f"Cash: ${result['cash']:,.0f} | Positions: {result['num_positions']}\n")

    if result["actions"]:
        lines.append("<b>Today's Actions:</b>")
        for a in result["actions"]:
            if a["type"] == "BUY":
                lines.append(f"🟢 BUY {a['ticker']} {a['shares']:.1f}sh @ ${a['price']:.2f} "
                            f"(mom={a['momentum']:.0f})")
            else:
                lines.append(f"🔴 SELL {a['ticker']} @ ${a['price']:.2f} "
                            f"({a['pnl_pct']:+.1f}%) — {a['reason']}")
        lines.append("")

    if result["positions"]:
        lines.append("<b>Holdings:</b>")
        for ticker, p in sorted(result["positions"].items(),
                                key=lambda x: x[1]["pnl_pct"], reverse=True):
            emoji = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            lines.append(f"  {emoji} {ticker}: {p['pnl_pct']:+.1f}% "
                        f"(${p['current']:.2f}, {p['shares']:.1f}sh)")

    return "\n".join(lines)
