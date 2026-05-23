"""Historical backtester — runs the strategy over past data and logs every decision.

Uses ascr's backtest.sqlite for historical prices and fundamentals.
Logs each BUY/NO_BUY/SELL/HOLD decision to ascr_h's decisions table.
Then evaluates outcomes and scores decision quality.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from src import db, config
from src.decision_logger import log_historical_decision, BENCHMARK_TICKER
from src.utils import get_logger

logger = get_logger("hist_backtester")

RADAR_BACKTEST_DB = os.environ.get("ASCR_BACKTEST_DB_PATH", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "data", "backtest.sqlite"))


def _bt_conn():
    """Connect to ascr's backtest DB (read-only)."""
    conn = sqlite3.connect(f"file:{RADAR_BACKTEST_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_historical_backtest(score_start: str, score_end: str,
                             eval_frequency_days: int = 5,
                             initial_cash: float = 100000) -> dict:
    """Run full historical backtest, logging every decision."""
    from src import config as pt_config
    cfg = pt_config.load()

    logger.info(f"=== Historical Backtest: {score_start} to {score_end} ===")

    bt = _bt_conn()

    # Get all tickers
    tickers_rows = bt.execute("SELECT DISTINCT ticker FROM bt_scores").fetchall()
    all_tickers = [r[0] for r in tickers_rows]

    # Get eval dates
    eval_dates = []
    rows = bt.execute("""
        SELECT DISTINCT eval_date FROM bt_scores
        WHERE eval_date BETWEEN ? AND ?
        ORDER BY eval_date
    """, (score_start, score_end)).fetchall()
    eval_dates = [r[0] for r in rows]

    logger.info(f"  {len(eval_dates)} eval dates, {len(all_tickers)} tickers")

    # Portfolio state
    cash = initial_cash
    positions = {}  # ticker -> {shares, entry_price, entry_date, rating, decision_id}
    all_decisions = []
    equity_curve = []

    for eval_date in eval_dates:
        # Get all scores for this date
        scores = bt.execute("""
            SELECT * FROM bt_scores WHERE eval_date=? ORDER BY opportunity DESC
        """, (eval_date,)).fetchall()
        scores = [dict(s) for s in scores]

        # Get benchmark price
        bench_row = bt.execute(
            "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (BENCHMARK_TICKER, eval_date)
        ).fetchone()
        bench_price = bench_row[0] if bench_row else 0

        score_map = {s["ticker"]: s for s in scores}

        # --- HOLD / SELL / TRIM decisions for existing positions ---
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            price_row = bt.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, eval_date)
            ).fetchone()
            if not price_row:
                continue
            current_price = price_row[0]
            pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            days_held = (datetime.strptime(eval_date, "%Y-%m-%d") -
                        datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days

            s = score_map.get(ticker, {})
            score_dict = {
                "rating": s.get("rating", ""), "evidence": s.get("evidence", 0),
                "asymmetry": s.get("asymmetry", 0), "momentum": s.get("momentum", 0),
                "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
                "tracking_priority": s.get("tracking_priority", ""),
            }

            # Check exit rules (config-driven)
            exits = cfg.get("exits", {})
            hard_stop = exits.get("hard_stop_loss_pct", -15)
            time_stop_days = exits.get("time_stop_days", 45)
            time_stop_min = exits.get("time_stop_min_rating", "A")
            trailing_act = exits.get("trailing_stop_activation_pct", 25)
            trailing_pct = exits.get("trailing_stop_pct", 15)

            # Track peak price for trailing stop
            if "peak_price" not in pos:
                pos["peak_price"] = pos["entry_price"]
            pos["peak_price"] = max(pos["peak_price"], current_price)
            peak_pnl = (pos["peak_price"] - pos["entry_price"]) / pos["entry_price"] * 100
            drop_from_peak = (current_price - pos["peak_price"]) / pos["peak_price"] * 100

            decision_type = None
            reason = ""
            current_rating = s.get("rating", "D")

            if pnl_pct <= hard_stop:
                decision_type = "SELL"
                reason = f"hard_stop_loss_{pnl_pct:.0f}pct"
            elif peak_pnl >= trailing_act and drop_from_peak <= -trailing_pct:
                decision_type = "SELL"
                reason = f"trailing_stop_peak_{peak_pnl:.0f}pct_drop_{drop_from_peak:.0f}pct"
            elif days_held > time_stop_days and current_rating not in ("S", "A") and current_rating < time_stop_min:
                decision_type = "SELL"
                reason = f"time_stop_{days_held}d_rating_{current_rating}"
            elif pnl_pct >= 100:
                decision_type = "TRIM"
                reason = f"profit_100pct_recover_cost_{pnl_pct:.0f}pct"
            elif pnl_pct >= 75:
                decision_type = "TRIM"
                reason = f"profit_75pct_{pnl_pct:.0f}pct"
            elif pnl_pct >= 40:
                decision_type = "TRIM"
                reason = f"profit_40pct_{pnl_pct:.0f}pct"
            else:
                decision_type = "HOLD"
                reason = f"holding_{days_held}d_pnl_{pnl_pct:+.1f}pct"

            dec_id = log_historical_decision(
                ticker, decision_type, current_price, eval_date,
                scores=score_dict, reason=reason, benchmark_price=bench_price
            )
            all_decisions.append(dec_id)

            # Execute
            if decision_type == "SELL":
                sell_amount = pos["shares"] * current_price
                cash += sell_amount
                del positions[ticker]
            elif decision_type == "TRIM":
                if pnl_pct >= 100:
                    # Recover cost basis: sell shares worth original investment
                    trim_pct = min(0.50, pos["entry_price"] * pos["shares"] / (current_price * pos["shares"]))
                elif pnl_pct >= 75:
                    trim_pct = 0.25
                else:
                    trim_pct = 0.20
                sell_shares = pos["shares"] * trim_pct
                sell_amount = sell_shares * current_price
                cash += sell_amount
                positions[ticker]["shares"] -= sell_shares

        # --- BUY / NO_BUY decisions for scored tickers ---
        for s in scores:
            ticker = s["ticker"]
            rating = s.get("rating", "D")
            # B-High gets special small sizing (0.5%)
            tracking_priority = s.get("tracking_priority", "")
            if rating == "B" and tracking_priority == "High":
                sizing_pct = cfg["sizing"].get("B_High", 0.005)
            else:
                sizing_pct = cfg["sizing"].get(rating, 0)

            price_row = bt.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, eval_date)
            ).fetchone()
            if not price_row:
                continue
            price = price_row[0]

            score_dict = {
                "rating": rating, "evidence": s.get("evidence", 0),
                "asymmetry": s.get("asymmetry", 0), "momentum": s.get("momentum", 0),
                "risk": s.get("risk", 0), "opportunity": s.get("opportunity", 0),
                "tracking_priority": s.get("tracking_priority", ""),
            }

            if sizing_pct > 0 and ticker not in positions and len(positions) < cfg.get("limits", {}).get("max_positions", 10):
                # BUY
                total_eq = cash + sum(
                    p["shares"] * (bt.execute(
                        "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                        (p_ticker, eval_date)
                    ).fetchone() or [0])[0]
                    for p_ticker, p in positions.items()
                )
                dollar_amount = total_eq * sizing_pct
                if dollar_amount > cash * 0.95 or dollar_amount < 100:
                    dec_id = log_historical_decision(
                        ticker, "NO_BUY", price, eval_date,
                        scores=score_dict, reason=f"insufficient_cash_for_{rating}",
                        benchmark_price=bench_price
                    )
                else:
                    shares = dollar_amount / price
                    cash -= dollar_amount
                    positions[ticker] = {
                        "shares": shares, "entry_price": price,
                        "entry_date": eval_date, "rating": rating,
                    }
                    dec_id = log_historical_decision(
                        ticker, "BUY", price, eval_date,
                        scores=score_dict, reason=f"rating_{rating}_opp_{s['opportunity']:.0f}",
                        benchmark_price=bench_price
                    )
                all_decisions.append(dec_id)
            else:
                # NO_BUY
                if ticker not in positions:
                    no_buy_reason = f"rating_{rating}"
                    if sizing_pct == 0:
                        no_buy_reason += "_no_sizing"
                    elif ticker in positions:
                        no_buy_reason += "_already_held"
                    elif len(positions) >= 15:
                        no_buy_reason += "_max_positions"

                    dec_id = log_historical_decision(
                        ticker, "NO_BUY", price, eval_date,
                        scores=score_dict, reason=no_buy_reason,
                        benchmark_price=bench_price
                    )
                    all_decisions.append(dec_id)

        # Equity curve
        pos_value = sum(
            p["shares"] * (bt.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (t, eval_date)
            ).fetchone() or [0])[0]
            for t, p in positions.items()
        )
        equity_curve.append({
            "date": eval_date,
            "cash": cash,
            "positions_value": pos_value,
            "total_equity": cash + pos_value,
        })

    bt.close()

    logger.info(f"  Logged {len(all_decisions)} decisions")
    logger.info(f"  Final equity: ${equity_curve[-1]['total_equity']:,.0f}" if equity_curve else "  No equity data")

    return {
        "decisions": len(all_decisions),
        "eval_dates": len(eval_dates),
        "equity_curve": equity_curve,
        "final_equity": equity_curve[-1]["total_equity"] if equity_curve else initial_cash,
        "total_return": ((equity_curve[-1]["total_equity"] / initial_cash - 1) * 100
                         if equity_curve else 0),
    }


def evaluate_historical_outcomes():
    """Evaluate outcomes for all historical decisions using backtest price data."""
    bt = _bt_conn()

    with db.get_conn() as conn:
        decisions = conn.execute("""
            SELECT * FROM decisions WHERE mode='historical_backtest'
        """).fetchall()

    decisions = [dict(d) for d in decisions]
    evaluated = 0

    for decision in decisions:
        dec_id = decision["decision_id"]
        ticker = decision["ticker"]
        dec_date = decision["decision_date"]
        entry_price = decision["price_at_decision"]
        bench_entry = decision["benchmark_price_at_decision"]

        if not entry_price or entry_price <= 0:
            continue

        # Check which horizons already evaluated
        with db.get_conn() as conn:
            existing = conn.execute(
                "SELECT horizon_days FROM decision_outcomes WHERE decision_id=?", (dec_id,)
            ).fetchall()
        existing_h = {r[0] for r in existing}

        # Get forward prices
        price_rows = bt.execute("""
            SELECT date, close FROM bt_prices
            WHERE ticker=? AND date > ? ORDER BY date ASC LIMIT 70
        """, (ticker, dec_date)).fetchall()
        closes = [r[1] for r in price_rows if r[1] and r[1] > 0]

        # Benchmark forward prices
        bench_rows = bt.execute("""
            SELECT date, close FROM bt_prices
            WHERE ticker='QQQ' AND date > ? ORDER BY date ASC LIMIT 70
        """, (dec_date,)).fetchall()
        bench_closes = [r[1] for r in bench_rows if r[1] and r[1] > 0]

        for horizon in [5, 10, 20, 60]:
            if horizon in existing_h:
                continue
            if len(closes) < horizon:
                continue

            fwd_price = closes[horizon - 1]
            fwd_return = (fwd_price - entry_price) / entry_price * 100

            bench_fwd = bench_closes[horizon - 1] if len(bench_closes) >= horizon else 0
            bench_return = (bench_fwd - bench_entry) / bench_entry * 100 if bench_entry > 0 else 0
            alpha = fwd_return - bench_return

            subset = closes[:horizon]
            max_gain = (max(subset) - entry_price) / entry_price * 100
            max_dd = (min(subset) - entry_price) / entry_price * 100

            dec_type = decision["decision_type"]
            if dec_type == "BUY":
                thesis_confirmed = 1 if alpha > 0 else 0
            elif dec_type in ("SELL", "TRIM"):
                thesis_confirmed = 1 if fwd_return < 5 else 0
            elif dec_type == "NO_BUY":
                thesis_confirmed = 1 if fwd_return < bench_return else 0
            else:
                thesis_confirmed = 1 if fwd_return > 0 else 0

            eval_date = price_rows[min(horizon - 1, len(price_rows) - 1)][0] if price_rows else dec_date

            with db.get_conn() as conn:
                conn.execute("""
                    INSERT INTO decision_outcomes (decision_id, evaluation_date, horizon_days,
                        price_at_horizon, benchmark_price_at_horizon,
                        forward_return, benchmark_return, alpha_return,
                        max_gain, max_drawdown, thesis_confirmed, exit_triggered, outcome_notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
                """, (dec_id, eval_date, horizon, fwd_price, bench_fwd,
                      fwd_return, bench_return, alpha, max_gain, max_dd,
                      thesis_confirmed))
            evaluated += 1

    bt.close()
    logger.info(f"Evaluated {evaluated} historical outcomes")
    return evaluated
