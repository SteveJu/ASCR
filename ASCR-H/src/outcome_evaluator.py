"""Outcome evaluator — computes forward returns and benchmark alpha for each decision.

Runs daily to fill in outcomes as horizons become available.
Strictly no lookahead: only evaluates horizons where enough days have passed.
"""
import time
from datetime import datetime, timedelta
from src import db
from src.decision_logger import BENCHMARK_TICKER
from src.utils import get_logger

logger = get_logger("outcome_eval")

HORIZONS = [5, 10, 20, 60]


def _get_price_at_date(ticker: str, target_date: str) -> float:
    """Get closing price on or before target_date from radar DB."""
    try:
        with db.radar_conn() as conn:
            row = conn.execute(
                "SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, target_date)
            ).fetchone()
            if row:
                return row[0]
            logger.warning(f"outcome_price_missing ticker={ticker} target_date={target_date}")
            return 0.0
    except Exception as e:
        logger.warning(f"outcome_price_failed ticker={ticker} target_date={target_date} error={e}")
        return 0.0


def _get_price_series(ticker: str, start_date: str, days: int) -> list:
    """Get price series from start_date forward."""
    try:
        with db.radar_conn() as conn:
            rows = conn.execute(
                "SELECT date, close FROM prices WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT ?",
                (ticker, start_date, days + 5)
            ).fetchall()
            series = [{"date": r[0], "close": r[1]} for r in rows if r[1] and r[1] > 0]
            if not series:
                logger.warning(f"outcome_price_series_missing ticker={ticker} start_date={start_date} days={days}")
            return series
    except Exception as e:
        logger.warning(f"outcome_price_series_failed ticker={ticker} start_date={start_date} days={days} error={e}")
        return []


def evaluate_outcomes():
    """Evaluate all pending decision outcomes."""
    from src.decision_logger import get_pending_decisions

    pending = get_pending_decisions()
    today = datetime.now()
    evaluated = 0

    for decision in pending:
        dec_id = decision["decision_id"]
        dec_date = decision["decision_date"]
        ticker = decision["ticker"]
        entry_price = decision["price_at_decision"]
        bench_entry = decision["benchmark_price_at_decision"]

        if not entry_price or entry_price <= 0:
            continue

        try:
            dec_dt = datetime.strptime(dec_date, "%Y-%m-%d")
        except ValueError:
            continue

        # Get price series for max/min calculations
        price_series = _get_price_series(ticker, dec_date, 70)
        closes = [p["close"] for p in price_series]

        for horizon in decision.get("_missing_horizons", HORIZONS):
            days_elapsed = (today - dec_dt).days
            if days_elapsed < horizon:
                continue  # not enough time passed

            # Get horizon price
            horizon_date = (dec_dt + timedelta(days=int(horizon * 1.5))).strftime("%Y-%m-%d")
            # Use trading-day count instead
            if len(closes) <= horizon:
                logger.info(
                    f"outcome_skip_insufficient_prices decision_id={dec_id} ticker={ticker} "
                    f"horizon={horizon} closes={len(closes)}"
                )
                continue

            price_at_h = closes[min(horizon, len(closes) - 1)]
            bench_at_h = _get_price_at_date(BENCHMARK_TICKER, horizon_date)

            fwd_return = (price_at_h - entry_price) / entry_price * 100
            bench_return = (bench_at_h - bench_entry) / bench_entry * 100 if bench_entry > 0 else 0
            alpha = fwd_return - bench_return

            # Max gain and drawdown within horizon
            subset = closes[:min(horizon + 1, len(closes))]
            max_gain = (max(subset) - entry_price) / entry_price * 100 if subset else 0
            max_dd = (min(subset) - entry_price) / entry_price * 100 if subset else 0

            # Thesis confirmation (simple: positive alpha = confirmed for BUY)
            dec_type = decision["decision_type"]
            if dec_type == "BUY":
                thesis_confirmed = 1 if alpha > 0 else 0
            elif dec_type in ("SELL", "TRIM"):
                # For sell: confirmed if stock went down after selling
                thesis_confirmed = 1 if fwd_return < 5 else 0
            elif dec_type == "NO_BUY":
                thesis_confirmed = 1 if fwd_return < bench_return else 0
            elif dec_type == "HOLD":
                thesis_confirmed = 1 if fwd_return > 0 else 0
            else:
                thesis_confirmed = None

            eval_date = (dec_dt + timedelta(days=int(horizon * 1.5))).strftime("%Y-%m-%d")
            if eval_date > today.strftime("%Y-%m-%d"):
                eval_date = today.strftime("%Y-%m-%d")

            with db.get_conn() as conn:
                conn.execute("""
                    INSERT INTO decision_outcomes (decision_id, evaluation_date, horizon_days,
                        price_at_horizon, benchmark_price_at_horizon,
                        forward_return, benchmark_return, alpha_return,
                        max_gain, max_drawdown, thesis_confirmed, exit_triggered, outcome_notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
                """, (dec_id, eval_date, horizon, price_at_h, bench_at_h,
                      fwd_return, bench_return, alpha, max_gain, max_dd,
                      thesis_confirmed))

            evaluated += 1

    logger.info(f"Evaluated {evaluated} outcomes across {len(pending)} decisions")
    return evaluated
