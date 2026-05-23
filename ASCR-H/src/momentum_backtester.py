"""Momentum Sprint backtester — pure momentum rotation, 5 positions max.

Strategy:
1. Weekly: rank universe by momentum score
2. Buy top-5 momentum (equal weight ~20% each)
3. Sell on: momentum death (<30), rank drop (out of top-10 for 2wk), hard stop (-20%)
4. No profit taking — let winners run
5. Rotate: sell lowest momentum held, buy highest momentum not held
"""
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from src import db, config
from src.decision_logger import log_historical_decision, BENCHMARK_TICKER
from src.utils import get_logger

logger = get_logger("momentum_bt")

RADAR_BACKTEST_DB = os.environ.get("ASCR_BACKTEST_DB_PATH", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "data", "backtest.sqlite"))


def _bt_conn():
    conn = sqlite3.connect(f"file:{RADAR_BACKTEST_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_momentum_backtest(start: str, end: str, initial_cash: float = 10000) -> dict:
    """Run momentum sprint backtest."""
    cfg = config.load()
    max_pos = cfg.get("sizing", {}).get("max_positions", 5)
    pos_pct = cfg.get("sizing", {}).get("per_position_pct", 0.20)
    min_momentum = cfg.get("buy", {}).get("min_momentum", 50)
    # Exit rules are price-only (hard stop + trailing stop)
    hard_stop = cfg.get("sell", {}).get("hard_stop_pct", -20)

    bt = _bt_conn()
    logger.info(f"=== Momentum Sprint Backtest: {start} to {end} ===")
    logger.info(f"  Max positions: {max_pos}, Min momentum: {min_momentum}")

    # Get eval dates
    eval_dates = [r[0] for r in bt.execute("""
        SELECT DISTINCT eval_date FROM bt_scores
        WHERE eval_date BETWEEN ? AND ? ORDER BY eval_date
    """, (start, end)).fetchall()]

    logger.info(f"  {len(eval_dates)} eval dates")

    cash = initial_cash
    positions = {}  # ticker -> {shares, entry, date, peak}
    trades = []
    equity_curve = []
    rank_history = defaultdict(list)  # ticker -> list of (date, rank)

    for eval_date in eval_dates:
        # Get scores ranked by momentum
        scores = [dict(r) for r in bt.execute("""
            SELECT * FROM bt_scores WHERE eval_date=? ORDER BY momentum DESC
        """, (eval_date,)).fetchall()]

        # Build momentum ranking
        mom_rank = {s["ticker"]: i + 1 for i, s in enumerate(scores)}
        for ticker in mom_rank:
            rank_history[ticker].append((eval_date, mom_rank[ticker]))

        # Get benchmark price
        bench_row = bt.execute(
            "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (BENCHMARK_TICKER, eval_date)).fetchone()
        bench_price = bench_row[0] if bench_row else 0

        score_map = {s["ticker"]: s for s in scores}

        def get_price(ticker):
            r = bt.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, eval_date)).fetchone()
            return r[0] if r else 0

        # === SELL PASS ===
        sells_this_round = []
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            price = get_price(ticker)
            if not price:
                continue

            pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
            pos["peak"] = max(pos.get("peak", pos["entry"]), price)

            s = score_map.get(ticker, {})
            mom = s.get("momentum", 0)
            rank = mom_rank.get(ticker, 99)
            score_dict = {
                "rating": s.get("rating", ""), "evidence": s.get("evidence", 0),
                "asymmetry": s.get("asymmetry", 0), "momentum": mom,
                "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
                "tracking_priority": s.get("tracking_priority", ""),
            }

            sell_reason = None

            # Track peak
            pos["peak"] = max(pos.get("peak", pos["entry"]), price)
            drop_from_peak = (price - pos["peak"]) / pos["peak"] * 100

            # 1. Hard stop: -20% from entry
            if pnl_pct <= hard_stop:
                sell_reason = f"hard_stop_{pnl_pct:.0f}pct"

            # 2. Trailing stop: after +20% gain, sell if drops 25% from peak
            trailing_act = cfg.get("sell", {}).get("trailing_stop_activation_pct", 20)
            trailing_drop = cfg.get("sell", {}).get("trailing_stop_from_peak_pct", -25)
            if pnl_pct > trailing_act and drop_from_peak <= trailing_drop:
                sell_reason = f"trailing_stop_peak_{pos['peak']:.0f}_drop_{drop_from_peak:.0f}pct"

            # That's it. No momentum_death, no rank_drop, no time stop.
            # Let winners run.

            if sell_reason:
                sell_amount = pos["shares"] * price
                cash += sell_amount
                realized = (price - pos["entry"]) * pos["shares"]

                dec_id = log_historical_decision(
                    ticker, "SELL", price, eval_date,
                    scores=score_dict, reason=sell_reason,
                    benchmark_price=bench_price)

                trades.append({
                    "date": eval_date, "side": "SELL", "ticker": ticker,
                    "price": price, "shares": pos["shares"], "amount": sell_amount,
                    "pnl_pct": pnl_pct, "realized": realized, "reason": sell_reason,
                })
                sells_this_round.append(ticker)
                del positions[ticker]
            else:
                # HOLD
                days_held = (datetime.strptime(eval_date, "%Y-%m-%d") -
                            datetime.strptime(pos["date"], "%Y-%m-%d")).days
                dec_id = log_historical_decision(
                    ticker, "HOLD", price, eval_date,
                    scores=score_dict,
                    reason=f"hold_mom_{mom:.0f}_rank_{rank}_pnl_{pnl_pct:+.1f}pct_{days_held}d",
                    benchmark_price=bench_price)

        # === BUY PASS — fill up to max_pos with top momentum ===
        slots = max_pos - len(positions)
        if slots > 0:
            # Total equity for sizing
            total_eq = cash + sum(
                p["shares"] * get_price(t) for t, p in positions.items()
            )
            target_amount = total_eq * pos_pct

            for s in scores:
                if slots <= 0:
                    break
                ticker = s["ticker"]
                if ticker in positions:
                    continue
                if ticker in sells_this_round:
                    continue  # don't rebuy same week
                if s["momentum"] < min_momentum:
                    break  # sorted by momentum, so rest are lower

                price = get_price(ticker)
                if not price or price <= 0:
                    continue

                amount = min(target_amount, cash * 0.95)
                if amount < 100:
                    continue

                shares = amount / price
                cash -= amount
                positions[ticker] = {
                    "shares": shares, "entry": price,
                    "date": eval_date, "peak": price,
                }

                score_dict = {
                    "rating": s.get("rating", ""), "evidence": s.get("evidence", 0),
                    "asymmetry": s.get("asymmetry", 0), "momentum": s["momentum"],
                    "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
                    "tracking_priority": s.get("tracking_priority", ""),
                }

                dec_id = log_historical_decision(
                    ticker, "BUY", price, eval_date,
                    scores=score_dict,
                    reason=f"momentum_{s['momentum']:.0f}_rank_{mom_rank[ticker]}",
                    benchmark_price=bench_price)

                trades.append({
                    "date": eval_date, "side": "BUY", "ticker": ticker,
                    "price": price, "shares": shares, "amount": amount,
                    "reason": f"momentum={s['momentum']:.0f} rank={mom_rank[ticker]}",
                })
                slots -= 1

        # NO_BUY for remaining universe (for DQS tracking)
        for s in scores:
            ticker = s["ticker"]
            if ticker in positions or ticker in [t["ticker"] for t in trades if t["date"] == eval_date and t["side"] == "BUY"]:
                continue
            price = get_price(ticker)
            if not price:
                continue
            score_dict = {
                "rating": s.get("rating", ""), "evidence": s.get("evidence", 0),
                "asymmetry": s.get("asymmetry", 0), "momentum": s["momentum"],
                "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
                "tracking_priority": s.get("tracking_priority", ""),
            }
            log_historical_decision(
                ticker, "NO_BUY", price, eval_date,
                scores=score_dict,
                reason=f"momentum_{s['momentum']:.0f}_rank_{mom_rank.get(ticker,99)}",
                benchmark_price=bench_price)

        # Equity curve
        pos_value = sum(p["shares"] * get_price(t) for t, p in positions.items())
        total_eq = cash + pos_value
        daily_ret = (total_eq / equity_curve[-1]["equity"] - 1) * 100 if equity_curve else 0

        equity_curve.append({
            "date": eval_date, "cash": cash, "positions_value": pos_value,
            "equity": total_eq, "num_positions": len(positions),
            "daily_return": daily_ret,
        })

    bt.close()

    # Final stats
    final_eq = equity_curve[-1]["equity"] if equity_curve else initial_cash
    total_return = (final_eq / initial_cash - 1) * 100
    peak = max(e["equity"] for e in equity_curve) if equity_curve else initial_cash
    max_dd = min((e["equity"] - peak) / peak * 100 for e in equity_curve) if equity_curve else 0

    buy_trades = [t for t in trades if t["side"] == "BUY"]
    sell_trades = [t for t in trades if t["side"] == "SELL"]
    winners = [t for t in sell_trades if t.get("pnl_pct", 0) > 0]
    losers = [t for t in sell_trades if t.get("pnl_pct", 0) <= 0]

    # Get QQQ return for same period
    bt2 = _bt_conn()
    qqq_s = bt2.execute("SELECT close FROM bt_prices WHERE ticker='QQQ' AND date>=? ORDER BY date ASC LIMIT 1", (start,)).fetchone()
    qqq_e = bt2.execute("SELECT close FROM bt_prices WHERE ticker='QQQ' AND date<=? ORDER BY date DESC LIMIT 1", (end,)).fetchone()
    qqq_return = (qqq_e[0] / qqq_s[0] - 1) * 100 if qqq_s and qqq_e else 0
    bt2.close()

    result = {
        "total_return": round(total_return, 1),
        "qqq_return": round(qqq_return, 1),
        "alpha": round(total_return - qqq_return, 1),
        "max_drawdown": round(max_dd, 1),
        "final_equity": round(final_eq, 0),
        "num_buys": len(buy_trades),
        "num_sells": len(sell_trades),
        "win_rate": round(len(winners) / max(len(sell_trades), 1) * 100, 0),
        "avg_winner": round(sum(t["pnl_pct"] for t in winners) / max(len(winners), 1), 1),
        "avg_loser": round(sum(t["pnl_pct"] for t in losers) / max(len(losers), 1), 1),
        "biggest_win": max((t["pnl_pct"] for t in sell_trades), default=0),
        "biggest_loss": min((t["pnl_pct"] for t in sell_trades), default=0),
        "remaining_positions": {
            t: {"shares": p["shares"], "entry": p["entry"],
                "pnl_pct": round((equity_curve[-1]["equity"] / initial_cash - 1) * 100, 1) if equity_curve else 0}
            for t, p in positions.items()
        },
        "trades": trades,
        "equity_curve": equity_curve,
        "decisions_logged": len(buy_trades) + len(sell_trades),
    }

    logger.info(f"  Return: {total_return:+.1f}% (QQQ: {qqq_return:+.1f}%, Alpha: {total_return - qqq_return:+.1f}%)")
    logger.info(f"  {len(buy_trades)} buys, {len(sell_trades)} sells, {len(winners)} wins ({result['win_rate']:.0f}%)")
    logger.info(f"  Final equity: ${final_eq:,.0f}")

    return result


def format_momentum_report(result: dict) -> str:
    """Format backtest results."""
    lines = ["# 🎰 Momentum Sprint Backtest\n"]

    r = result
    lines.append(f"**Return: {r['total_return']:+.1f}%** | QQQ: {r['qqq_return']:+.1f}% | "
                 f"Alpha: {r['alpha']:+.1f}%")
    lines.append(f"${10000:,} → ${r['final_equity']:,.0f} | Max DD: {r['max_drawdown']:.1f}%\n")

    lines.append(f"Buys: {r['num_buys']} | Sells: {r['num_sells']} | "
                 f"Win rate: {r['win_rate']:.0f}%")
    lines.append(f"Avg winner: {r['avg_winner']:+.1f}% | Avg loser: {r['avg_loser']:.1f}%")
    lines.append(f"Best: {r['biggest_win']:+.1f}% | Worst: {r['biggest_loss']:.1f}%\n")

    # Trade log
    lines.append("## Trades\n")
    lines.append(f"{'Date':12s} {'Side':5s} {'Ticker':6s} {'Price':>8s} {'P&L':>8s} {'Reason'}")
    lines.append("-" * 65)
    for t in result["trades"]:
        pnl = f"{t.get('pnl_pct', 0):+.1f}%" if t["side"] == "SELL" else ""
        emoji = "🟢" if t["side"] == "BUY" else ("✅" if t.get("pnl_pct", 0) > 0 else "❌")
        lines.append(f"{t['date']:12s} {emoji}{t['side']:4s} {t['ticker']:6s} "
                     f"${t['price']:7.2f} {pnl:>8s} {t['reason']}")

    # Remaining positions
    if result["remaining_positions"]:
        lines.append("\n## Open Positions")
        for ticker, p in result["remaining_positions"].items():
            lines.append(f"  {ticker}: {p['shares']:.1f} shares @ ${p['entry']:.2f}")

    return "\n".join(lines)
