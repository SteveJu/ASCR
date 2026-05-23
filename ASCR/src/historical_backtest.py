"""Historical backtest — simulate running stock radar over past data.

Track 1: Use 2024-H2 to build scores, evaluate 2025-H1 forward returns.
         Use 2025-H2 to build scores, evaluate 2026-H1 forward returns.

This module:
1. Downloads 2 years of daily prices for all universe tickers
2. At each evaluation date (weekly), computes scores using ONLY data available at that point
3. Tracks forward returns (5/10/20/60 trading days)
4. Evaluates: IC, tier returns, top-N spread, hit rate, ranking quality
5. Simulates paper portfolio with configurable sizing rules

Key: NO lookahead bias. Scores use only past prices; forward returns are out-of-sample.
"""
import os
import sys
import json
import sqlite3
import time
from datetime import datetime, timedelta
from collections import defaultdict
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import config
from src.ranking_quality import _spearman_corr
from src.utils import get_logger

logger = get_logger("hist_backtest")

BACKTEST_DB = os.path.join(config.DATA_DIR, "backtest.sqlite")


def _get_conn():
    conn = sqlite3.connect(BACKTEST_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_backtest_db():
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS bt_prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bt_scores (
            eval_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            evidence REAL, asymmetry REAL, momentum REAL, risk REAL,
            opportunity REAL,
            rating TEXT,
            tracking_priority TEXT,
            PRIMARY KEY (eval_date, ticker)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bt_forward_returns (
            eval_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            entry_price REAL,
            return_5d REAL, return_10d REAL, return_20d REAL, return_60d REAL,
            max_gain_60d REAL, max_drawdown_60d REAL,
            PRIMARY KEY (eval_date, ticker)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bt_portfolio (
            eval_date TEXT NOT NULL,
            action TEXT,
            ticker TEXT,
            shares REAL, price REAL, amount REAL,
            rating TEXT, reason TEXT,
            cash_after REAL, equity_after REAL,
            PRIMARY KEY (eval_date, ticker, action)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bt_equity_curve (
            date TEXT PRIMARY KEY,
            cash REAL, positions_value REAL, total_equity REAL,
            num_positions INTEGER, daily_return REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bt_summary (
            period TEXT PRIMARY KEY,
            ic_5d REAL, ic_10d REAL, ic_20d REAL, ic_60d REAL,
            tier_monotonic_20d INTEGER,
            top5_spread_20d REAL,
            total_return REAL, max_drawdown REAL,
            win_rate REAL, profit_factor REAL,
            num_trades INTEGER,
            details_json TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"Backtest DB initialized: {BACKTEST_DB}")


def download_historical_prices(tickers: list, start: str = "2024-01-01", end: str = None):
    """Download historical daily prices for all tickers."""
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    conn = _get_conn()

    # Check what we already have
    existing = conn.execute("SELECT COUNT(DISTINCT ticker) FROM bt_prices").fetchone()[0]
    if existing >= len(tickers):
        date_range = conn.execute("SELECT MIN(date), MAX(date) FROM bt_prices").fetchone()
        if date_range[0] and date_range[0] <= start:
            logger.info(f"Already have {existing} tickers from {date_range[0]} to {date_range[1]}, skipping download")
            conn.close()
            return

    logger.info(f"Downloading prices for {len(tickers)} tickers from {start} to {end}...")

    # Download in batches
    batch_size = 10
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        ticker_str = " ".join(batch)
        try:
            data = yf.download(ticker_str, start=start, end=end, progress=False, threads=True)
            if data.empty:
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        closes = data["Close"]
                        opens = data["Open"]
                        highs = data["High"]
                        lows = data["Low"]
                        volumes = data["Volume"]
                    else:
                        closes = data["Close"][ticker]
                        opens = data["Open"][ticker]
                        highs = data["High"][ticker]
                        lows = data["Low"][ticker]
                        volumes = data["Volume"][ticker]

                    for dt in closes.index:
                        date_str = dt.strftime("%Y-%m-%d")
                        c = float(closes.loc[dt]) if not closes.isna().loc[dt] else None
                        o = float(opens.loc[dt]) if not opens.isna().loc[dt] else None
                        h = float(highs.loc[dt]) if not highs.isna().loc[dt] else None
                        l = float(lows.loc[dt]) if not lows.isna().loc[dt] else None
                        v = int(volumes.loc[dt]) if not volumes.isna().loc[dt] else None

                        if c and c > 0:
                            conn.execute("""
                                INSERT OR REPLACE INTO bt_prices (ticker, date, open, high, low, close, volume)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (ticker, date_str, o, h, l, c, v))

                except Exception as e:
                    logger.warning(f"Error processing {ticker}: {e}")

            conn.commit()
            logger.info(f"  Downloaded batch {i//batch_size + 1}/{(len(tickers)+batch_size-1)//batch_size}")

        except Exception as e:
            logger.error(f"Batch download failed: {e}")

    total = conn.execute("SELECT COUNT(*) FROM bt_prices").fetchone()[0]
    tickers_count = conn.execute("SELECT COUNT(DISTINCT ticker) FROM bt_prices").fetchone()[0]
    conn.close()
    logger.info(f"Downloaded {total} price records for {tickers_count} tickers")


def _score_at_date(conn, ticker: str, eval_date: str) -> dict:
    """Compute score for a ticker using ONLY data available at eval_date.

    This is a simplified version of the scoring engine that uses historical prices.
    No yfinance fundamentals call — uses price-based signals only for backtest.
    """
    rows = conn.execute("""
        SELECT * FROM bt_prices WHERE ticker=? AND date <= ? ORDER BY date DESC LIMIT 260
    """, (ticker, eval_date)).fetchall()

    if len(rows) < 20:
        return None

    prices = [dict(r) for r in rows]
    prices_asc = list(reversed(prices))
    closes = [p["close"] for p in prices_asc if p["close"]]
    volumes = [p["volume"] for p in prices_asc if p["volume"]]
    current = closes[-1] if closes else 0

    if not current or current <= 0:
        return None

    # --- Momentum (same logic as live scoring) ---
    momentum = 0
    if len(closes) >= 20:
        sma20 = sum(closes[-20:]) / 20
        if current > sma20:
            trend_pct = (current - sma20) / sma20 * 100
            momentum += min(25, trend_pct * 2.5)

    if len(closes) >= 252:
        high_52w = max(closes[-252:])
        pct_of_high = current / high_52w if high_52w > 0 else 0
        if pct_of_high >= 0.95:
            momentum += 25
        elif pct_of_high >= 0.85:
            momentum += 15
    elif len(closes) >= 60:
        high_avail = max(closes)
        pct_of_high = current / high_avail if high_avail > 0 else 0
        if pct_of_high >= 0.95:
            momentum += 20
        elif pct_of_high >= 0.85:
            momentum += 10

    if len(volumes) >= 20:
        avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) > 20 else sum(volumes[:-1]) / max(len(volumes)-1, 1)
        if avg_vol > 0:
            vol_ratio = volumes[-1] / avg_vol
            if vol_ratio >= 3:
                momentum += 25
            elif vol_ratio >= 2:
                momentum += 15
            elif vol_ratio >= 1.5:
                momentum += 5

    if len(closes) >= 20:
        ret_20d = (closes[-1] - closes[-20]) / closes[-20] * 100
        if ret_20d > 10:
            momentum += 25
        elif ret_20d > 5:
            momentum += 15
        elif ret_20d > 0:
            momentum += 5

    momentum = min(100, momentum)

    # --- Risk (volatility-based for backtest) ---
    risk = 0
    if len(closes) >= 20:
        daily_returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        if daily_returns:
            vol = (sum(r**2 for r in daily_returns[-20:]) / 20) ** 0.5
            annualized_vol = vol * (252 ** 0.5)
            if annualized_vol > 0.8:
                risk += 40
            elif annualized_vol > 0.5:
                risk += 25
            elif annualized_vol > 0.3:
                risk += 15

    # Max drawdown from 60d high
    if len(closes) >= 60:
        high_60d = max(closes[-60:])
        dd_from_60d = (current - high_60d) / high_60d * 100
        if dd_from_60d < -30:
            risk += 30
        elif dd_from_60d < -20:
            risk += 20
        elif dd_from_60d < -10:
            risk += 10

    # Fetch fundamentals for this ticker at this date
    from src.fundamentals_cache import get_fundamentals_at_date
    fundies = get_fundamentals_at_date(conn, ticker, eval_date)

    # Fundamental risk factors
    if fundies:
        dte = fundies.get("debt_to_equity")
        if dte is not None:
            if dte > 200:
                risk += 20
            elif dte > 100:
                risk += 10

        gm = fundies.get("gross_margin")
        if gm is not None and gm < 0:
            risk += 20

        pm = fundies.get("profit_margin")
        if pm is not None and pm < 0:
            risk += 10

    risk = min(100, risk)

    # --- Evidence (fundamentals + price confirmation) ---
    evidence = 0

    if fundies:
        # Revenue growth
        rev_growth = fundies.get("revenue_growth_yoy")
        if rev_growth is not None:
            if rev_growth > 0.5:
                evidence += 30
            elif rev_growth > 0.2:
                evidence += 20
            elif rev_growth > 0.1:
                evidence += 10

        # Gross margin
        gm = fundies.get("gross_margin")
        if gm is not None and gm > 0.4:
            evidence += 15

        # Profitability
        pm = fundies.get("profit_margin")
        if pm is not None and pm > 0.1:
            evidence += 15

        # Forward PE vs trailing PE (guidance raise implied)
        fwd_pe = fundies.get("forward_pe")
        trail_pe = fundies.get("trailing_pe")
        if fwd_pe and trail_pe and fwd_pe < trail_pe * 0.85 and fwd_pe > 0:
            evidence += 20

    # Price-based confirmation (even without fundamentals)
    if len(closes) >= 60:
        sma50 = sum(closes[-50:]) / 50
        sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
        if current > sma50 > sma200:
            evidence += 15  # golden cross = institutional buying

    if len(volumes) >= 20:
        recent_avg_vol = sum(volumes[-5:]) / 5
        older_avg_vol = sum(volumes[-20:-5]) / 15 if len(volumes) >= 20 else recent_avg_vol
        if older_avg_vol > 0 and recent_avg_vol / older_avg_vol > 1.3:
            evidence += 10

    evidence = min(100, evidence)

    # --- Asymmetry (how much upside vs downside?) ---
    asymmetry = 0

    # Revenue growth = future earnings potential
    if fundies:
        rev_growth = fundies.get("revenue_growth_yoy")
        if rev_growth is not None:
            if rev_growth > 0.5:
                asymmetry += 30  # hyper growth
            elif rev_growth > 0.2:
                asymmetry += 20
            elif rev_growth > 0.1:
                asymmetry += 10

        # PE compression = market not pricing in growth
        fwd_pe = fundies.get("forward_pe")
        trail_pe = fundies.get("trailing_pe")
        if fwd_pe and trail_pe and fwd_pe > 0 and trail_pe > 0:
            if fwd_pe < trail_pe * 0.7:
                asymmetry += 20
            elif fwd_pe < trail_pe * 0.85:
                asymmetry += 10

        # Low analyst coverage = underfollowed
        analyst_count = fundies.get("analyst_count")
        if analyst_count is not None and analyst_count < 5:
            asymmetry += 15
        elif analyst_count is not None and analyst_count < 10:
            asymmetry += 10

    # Price range position: more upside if below 52w high
    if len(closes) >= 252:
        low_52w = min(closes[-252:])
        high_52w = max(closes[-252:])
        if low_52w > 0 and high_52w > low_52w:
            range_pct = (high_52w - low_52w) / low_52w * 100
            position_in_range = (current - low_52w) / (high_52w - low_52w)

            # Wide range = volatile = more asymmetry
            if range_pct > 100:
                asymmetry += 15
            elif range_pct > 50:
                asymmetry += 10

            # Below midpoint of range = more upside potential
            if position_in_range < 0.4:
                asymmetry += 15
            elif position_in_range < 0.6:
                asymmetry += 10

    # Breakout setup: volume surge + near highs
    if len(volumes) >= 30 and len(closes) >= 20:
        vol_5d = sum(volumes[-5:]) / 5
        vol_30d = sum(volumes[-30:]) / 30
        sma20 = sum(closes[-20:]) / 20
        if vol_30d > 0 and vol_5d / vol_30d > 1.5 and current > sma20:
            asymmetry += 10

    asymmetry = min(100, asymmetry)

    # --- Opportunity (V2 weights: momentum boosted, regime-aware) ---
    # Detect regime at this date using SPY/QQQ
    spy_prices = conn.execute(
        "SELECT close FROM bt_prices WHERE ticker='SPY' AND date<=? ORDER BY date DESC LIMIT 200",
        (eval_date,)
    ).fetchall()
    qqq_prices = conn.execute(
        "SELECT close FROM bt_prices WHERE ticker='QQQ' AND date<=? ORDER BY date DESC LIMIT 200",
        (eval_date,)
    ).fetchall()

    regime = 'neutral'
    if len(spy_prices) >= 200:
        spy_closes = [r[0] for r in reversed(spy_prices)]
        spy_50 = sum(spy_closes[-50:]) / 50
        spy_200 = sum(spy_closes[-200:]) / 200
        if spy_closes[-1] > spy_50 > spy_200:
            regime = 'risk_on'
        elif spy_closes[-1] < spy_50 < spy_200:
            regime = 'risk_off'

    # Regime-dependent weights
    regime_weights = {
        'risk_on':  {'evidence': 0.28, 'asymmetry': 0.28, 'momentum': 0.30, 'risk': -0.14},
        'neutral':  {'evidence': 0.30, 'asymmetry': 0.30, 'momentum': 0.25, 'risk': -0.15},
        'risk_off': {'evidence': 0.35, 'asymmetry': 0.25, 'momentum': 0.20, 'risk': -0.20},
    }
    w = regime_weights.get(regime, regime_weights['neutral'])

    opportunity = evidence * w['evidence'] + asymmetry * w['asymmetry'] + momentum * w['momentum'] + risk * w['risk']

    # --- Rating (calibrated for AI supply chain universe) ---
    if opportunity >= 60 and evidence >= 55 and asymmetry >= 40 and risk <= 35:
        rating = "S"
    elif opportunity >= 48 and evidence >= 40 and risk <= 45:
        rating = "A"
    elif opportunity >= 35:
        rating = "B"
    elif opportunity >= 22:
        rating = "C"
    else:
        rating = "D"

    # Tracking priority
    if rating in ("S", "A"):
        tracking = "High"
    elif rating == "B" and (evidence >= 40 or momentum >= 50):
        tracking = "High"
    elif rating == "B":
        tracking = "Medium"
    else:
        tracking = "Low"

    return {
        "ticker": ticker,
        "evidence": evidence, "asymmetry": asymmetry,
        "momentum": momentum, "risk": risk,
        "opportunity": round(opportunity, 1),
        "rating": rating, "tracking_priority": tracking,
    }


def _compute_forward_returns(conn, ticker: str, eval_date: str, entry_price: float) -> dict:
    """Compute forward returns from eval_date."""
    rows = conn.execute("""
        SELECT date, close FROM bt_prices
        WHERE ticker=? AND date > ? ORDER BY date ASC LIMIT 70
    """, (ticker, eval_date)).fetchall()

    if not rows or entry_price <= 0:
        return {}

    closes = [dict(r)["close"] for r in rows]
    dates = [dict(r)["date"] for r in rows]

    result = {"entry_price": entry_price}

    for period, label in [(5, "return_5d"), (10, "return_10d"), (20, "return_20d"), (60, "return_60d")]:
        if len(closes) >= period:
            fwd_price = closes[period - 1]
            result[label] = (fwd_price - entry_price) / entry_price * 100

    if len(closes) >= 60:
        result["max_gain_60d"] = (max(closes[:60]) - entry_price) / entry_price * 100
        result["max_drawdown_60d"] = (min(closes[:60]) - entry_price) / entry_price * 100
    elif closes:
        result["max_gain_60d"] = (max(closes) - entry_price) / entry_price * 100
        result["max_drawdown_60d"] = (min(closes) - entry_price) / entry_price * 100

    return result


def run_backtest(period_name: str, score_start: str, score_end: str,
                  eval_frequency_days: int = 5, initial_cash: float = 100000):
    """Run full historical backtest for a period.

    Args:
        period_name: e.g. "2025H2_scores_2026H1_returns"
        score_start: first scoring date
        score_end: last scoring date
        eval_frequency_days: score every N calendar days
        initial_cash: starting paper cash
    """
    tickers = config.all_tickers()
    logger.info(f"=== Backtest: {period_name} ===")
    logger.info(f"  Scoring: {score_start} to {score_end}, every {eval_frequency_days}d")
    logger.info(f"  Universe: {len(tickers)} tickers")

    conn = _get_conn()

    # Clear previous results for this period (if re-running)
    # We use eval_date range to identify them

    # Generate evaluation dates
    eval_dates = []
    current = datetime.strptime(score_start, "%Y-%m-%d")
    end = datetime.strptime(score_end, "%Y-%m-%d")
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        # Check if this is a trading day (has price data)
        has_data = conn.execute(
            "SELECT 1 FROM bt_prices WHERE date=? LIMIT 1", (date_str,)
        ).fetchone()
        if has_data:
            eval_dates.append(date_str)
        current += timedelta(days=eval_frequency_days)

    logger.info(f"  {len(eval_dates)} evaluation dates")

    # Download fundamentals if needed
    from src.fundamentals_cache import download_fundamentals, init_fundamentals_table
    init_fundamentals_table()
    download_fundamentals(tickers)

    # Score + forward returns for each eval date
    all_pairs = []

    for i, eval_date in enumerate(eval_dates):
        if i % 10 == 0:
            logger.info(f"  Scoring {eval_date} ({i+1}/{len(eval_dates)})...")

        for ticker in tickers:
            score = _score_at_date(conn, ticker, eval_date)
            if score is None:
                continue

            # Save score
            conn.execute("""
                INSERT OR REPLACE INTO bt_scores (eval_date, ticker, evidence, asymmetry,
                    momentum, risk, opportunity, rating, tracking_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (eval_date, ticker, score["evidence"], score["asymmetry"],
                  score["momentum"], score["risk"], score["opportunity"],
                  score["rating"], score["tracking_priority"]))

            # Get entry price
            price_row = conn.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, eval_date)
            ).fetchone()
            if not price_row:
                continue
            entry_price = price_row[0]

            # Forward returns
            fwd = _compute_forward_returns(conn, ticker, eval_date, entry_price)
            if fwd:
                conn.execute("""
                    INSERT OR REPLACE INTO bt_forward_returns (eval_date, ticker, entry_price,
                        return_5d, return_10d, return_20d, return_60d,
                        max_gain_60d, max_drawdown_60d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (eval_date, ticker, entry_price,
                      fwd.get("return_5d"), fwd.get("return_10d"),
                      fwd.get("return_20d"), fwd.get("return_60d"),
                      fwd.get("max_gain_60d"), fwd.get("max_drawdown_60d")))

                all_pairs.append({
                    "eval_date": eval_date,
                    "ticker": ticker,
                    "rating": score["rating"],
                    "tracking_priority": score["tracking_priority"],
                    "opportunity_score": score["opportunity"],
                    "evidence_score": score["evidence"],
                    "asymmetry_score": score["asymmetry"],
                    "momentum_score": score["momentum"],
                    "risk_score": score["risk"],
                    **{k: v for k, v in fwd.items() if k.startswith("return_")},
                })

        conn.commit()

    # --- Portfolio simulation ---
    logger.info(f"\n  Simulating portfolio...")
    cash = initial_cash
    positions = {}  # ticker -> {shares, entry_price, entry_date, rating}
    equity_curve = []
    trades = []

    for eval_date in eval_dates:
        # Get scores for this date
        scores = conn.execute("""
            SELECT * FROM bt_scores WHERE eval_date=? ORDER BY opportunity DESC
        """, (eval_date,)).fetchall()
        scores = [dict(s) for s in scores]

        # Exit check for existing positions
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            price_row = conn.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, eval_date)
            ).fetchone()
            if not price_row:
                continue
            current_price = price_row[0]
            pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            days_held = (datetime.strptime(eval_date, "%Y-%m-%d") - datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days

            sell_reason = None
            sell_pct = 0

            if pnl_pct <= -25:
                sell_reason = "hard_stop"
                sell_pct = 1.0
            elif days_held > 60:
                current_rating = "D"
                for s in scores:
                    if s["ticker"] == ticker:
                        current_rating = s["rating"]
                        break
                if current_rating not in ("S", "A"):
                    sell_reason = f"time_stop_{days_held}d_{current_rating}"
                    sell_pct = 1.0
            elif pnl_pct >= 25:
                sell_reason = f"profit_taking_{pnl_pct:.0f}pct"
                sell_pct = 0.25

            if sell_reason and sell_pct > 0:
                sell_shares = pos["shares"] * sell_pct
                sell_amount = sell_shares * current_price
                cash += sell_amount
                realized = (current_price - pos["entry_price"]) * sell_shares
                trades.append({
                    "date": eval_date, "ticker": ticker, "side": "SELL",
                    "shares": sell_shares, "price": current_price,
                    "amount": sell_amount, "realized_pnl": realized,
                    "reason": sell_reason,
                })
                if sell_pct >= 1.0:
                    del positions[ticker]
                else:
                    positions[ticker]["shares"] -= sell_shares

        # Buy signals: S and A only
        for s in scores:
            if s["rating"] not in ("S", "A"):
                continue
            ticker = s["ticker"]
            if ticker in positions:
                continue
            if len(positions) >= 15:
                continue

            sizing = 0.02 if s["rating"] == "S" else 0.01
            total_equity = cash + sum(
                p["shares"] * (conn.execute(
                    "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                    (p["ticker"] if "ticker" in p else t, eval_date)
                ).fetchone() or [0])[0]
                for t, p in positions.items()
            )
            dollar_amount = total_equity * sizing
            if dollar_amount > cash * 0.95 or dollar_amount < 100:
                continue

            price_row = conn.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, eval_date)
            ).fetchone()
            if not price_row or price_row[0] <= 0:
                continue

            buy_price = price_row[0]
            shares = dollar_amount / buy_price
            cash -= dollar_amount
            positions[ticker] = {
                "shares": shares, "entry_price": buy_price,
                "entry_date": eval_date, "rating": s["rating"],
                "ticker": ticker,
            }
            trades.append({
                "date": eval_date, "ticker": ticker, "side": "BUY",
                "shares": shares, "price": buy_price,
                "amount": dollar_amount, "rating": s["rating"],
                "reason": f"rating_{s['rating']}_opp_{s['opportunity']:.0f}",
            })

        # Record equity
        pos_value = 0
        for t, p in positions.items():
            pr = conn.execute(
                "SELECT close FROM bt_prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (t, eval_date)
            ).fetchone()
            if pr:
                pos_value += p["shares"] * pr[0]

        total_eq = cash + pos_value
        daily_ret = (total_eq / equity_curve[-1]["total_equity"] - 1) * 100 if equity_curve else 0

        equity_curve.append({
            "date": eval_date, "cash": cash, "positions_value": pos_value,
            "total_equity": total_eq, "num_positions": len(positions),
            "daily_return": daily_ret,
        })

        conn.execute("""
            INSERT OR REPLACE INTO bt_equity_curve (date, cash, positions_value, total_equity,
                num_positions, daily_return)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (eval_date, cash, pos_value, total_eq, len(positions), daily_ret))

    conn.commit()

    # --- Compute ranking quality metrics ---
    logger.info(f"\n  Computing ranking quality metrics...")
    summary = _compute_summary(all_pairs, equity_curve, trades, period_name)

    conn.execute("""
        INSERT OR REPLACE INTO bt_summary (period, ic_5d, ic_10d, ic_20d, ic_60d,
            tier_monotonic_20d, top5_spread_20d, total_return, max_drawdown,
            win_rate, profit_factor, num_trades, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (period_name, summary["ic_5d"], summary["ic_10d"], summary["ic_20d"], summary["ic_60d"],
          1 if summary["tier_monotonic_20d"] else 0,
          summary["top5_spread_20d"],
          summary["total_return"], summary["max_drawdown"],
          summary["win_rate"], summary["profit_factor"],
          summary["num_trades"], json.dumps(summary["details"])))
    conn.commit()
    conn.close()

    return summary


def _compute_summary(pairs: list, equity_curve: list, trades: list, period: str) -> dict:
    """Compute comprehensive backtest summary."""

    # IC for each return window
    ics = {}
    for window in ["return_5d", "return_10d", "return_20d", "return_60d"]:
        valid = [p for p in pairs if p.get(window) is not None]
        if len(valid) >= 10:
            scores = [p["opportunity_score"] for p in valid]
            returns = [p[window] for p in valid]
            ics[window.replace("return_", "ic_")] = round(_spearman_corr(scores, returns), 4)
        else:
            ics[window.replace("return_", "ic_")] = None

    # Tier returns (20d)
    tier_returns = defaultdict(list)
    for p in pairs:
        if p.get("return_20d") is not None:
            tier_returns[p["rating"]].append(p["return_20d"])

    tier_avgs = {}
    for tier in ["S", "A", "B", "C", "D"]:
        rets = tier_returns.get(tier, [])
        if rets:
            tier_avgs[tier] = {
                "count": len(rets),
                "avg": round(sum(rets) / len(rets), 2),
                "hit_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            }

    # Check monotonicity
    order = ["S", "A", "B", "C", "D"]
    avgs_list = [(t, tier_avgs[t]["avg"]) for t in order if t in tier_avgs]
    monotonic = all(avgs_list[i][1] >= avgs_list[i+1][1] for i in range(len(avgs_list)-1)) if len(avgs_list) >= 2 else None

    # Top-5 spread
    from collections import defaultdict as dd
    by_date = dd(list)
    for p in pairs:
        if p.get("return_20d") is not None:
            by_date[p["eval_date"]].append(p)
    top5_spreads = []
    for date, date_pairs in by_date.items():
        if len(date_pairs) >= 10:
            ranked = sorted(date_pairs, key=lambda x: x["opportunity_score"], reverse=True)
            top5_avg = sum(p["return_20d"] for p in ranked[:5]) / 5
            bot5_avg = sum(p["return_20d"] for p in ranked[-5:]) / 5
            top5_spreads.append(top5_avg - bot5_avg)
    top5_spread = round(sum(top5_spreads) / len(top5_spreads), 2) if top5_spreads else None

    # Portfolio metrics
    total_return = 0
    max_dd = 0
    if equity_curve:
        final = equity_curve[-1]["total_equity"]
        initial = equity_curve[0]["total_equity"]
        total_return = round((final / initial - 1) * 100, 2) if initial > 0 else 0

        peak = 0
        for ec in equity_curve:
            peak = max(peak, ec["total_equity"])
            dd = (ec["total_equity"] - peak) / peak * 100 if peak > 0 else 0
            max_dd = min(max_dd, dd)
        max_dd = round(max_dd, 2)

    # Trade metrics
    sell_trades = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sell_trades if t.get("realized_pnl", 0) > 0]
    losses = [t for t in sell_trades if t.get("realized_pnl", 0) <= 0]
    win_rate = round(len(wins) / max(len(sell_trades), 1) * 100, 1)
    gross_wins = sum(t.get("realized_pnl", 0) for t in wins)
    gross_losses = abs(sum(t.get("realized_pnl", 0) for t in losses))
    profit_factor = round(gross_wins / max(gross_losses, 0.01), 2)

    # Tracking priority analysis
    priority_returns = defaultdict(list)
    for p in pairs:
        if p.get("return_20d") is not None:
            priority_returns[p["tracking_priority"]].append(p["return_20d"])

    priority_summary = {}
    for prio in ["High", "Medium", "Low"]:
        rets = priority_returns.get(prio, [])
        if rets:
            priority_summary[prio] = {
                "count": len(rets),
                "avg_20d": round(sum(rets) / len(rets), 2),
                "hit_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            }

    return {
        **ics,
        "tier_monotonic_20d": monotonic,
        "top5_spread_20d": top5_spread,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "num_trades": len(trades),
        "details": {
            "tier_returns_20d": tier_avgs,
            "priority_returns_20d": priority_summary,
            "num_eval_dates": len(set(p["eval_date"] for p in pairs)),
            "num_pairs": len(pairs),
            "num_buy_trades": len([t for t in trades if t["side"] == "BUY"]),
            "num_sell_trades": len(sell_trades),
            "equity_start": equity_curve[0]["total_equity"] if equity_curve else None,
            "equity_end": equity_curve[-1]["total_equity"] if equity_curve else None,
        },
    }


def format_backtest_report(summary: dict, period: str) -> str:
    """Format backtest results as markdown report."""
    lines = [f"# 📊 Historical Backtest — {period}\n"]

    # Headline metrics
    lines.append("## Portfolio Performance\n")
    lines.append(f"- **Total Return:** {summary['total_return']:+.1f}%")
    lines.append(f"- **Max Drawdown:** {summary['max_drawdown']:.1f}%")
    lines.append(f"- **Win Rate:** {summary['win_rate']:.0f}%")
    lines.append(f"- **Profit Factor:** {summary['profit_factor']:.2f}")
    lines.append(f"- **Total Trades:** {summary['num_trades']}")
    lines.append("")

    # Information coefficients
    lines.append("## Information Coefficients (Score → Return)\n")
    lines.append("| Period | IC | Quality |")
    lines.append("|--------|-----|---------|")
    for label, key in [("5d", "ic_5d"), ("10d", "ic_10d"), ("20d", "ic_20d"), ("60d", "ic_60d")]:
        ic = summary.get(key)
        if ic is not None:
            q = "🟢 good" if ic >= 0.10 else ("🟡 marginal" if ic >= 0.05 else "🔴 weak")
            lines.append(f"| {label} | {ic:.4f} | {q} |")
        else:
            lines.append(f"| {label} | — | insufficient data |")
    lines.append("")

    # Tier monotonicity
    mono = summary.get("tier_monotonic_20d")
    if mono is not None:
        emoji = "✅" if mono else "⚠️"
        lines.append(f"{emoji} **Tier Monotonicity (20d):** {'Correct — S > A > B > C > D' if mono else 'VIOLATED'}\n")

    # Top-5 spread
    spread = summary.get("top5_spread_20d")
    if spread is not None:
        emoji = "🟢" if spread > 0 else "🔴"
        lines.append(f"{emoji} **Top-5 vs Bottom-5 Spread (20d):** {spread:+.1f}%\n")

    # Tier returns
    details = summary.get("details", {})
    tier_ret = details.get("tier_returns_20d", {})
    if tier_ret:
        lines.append("## Tier Forward Returns (20d)\n")
        lines.append("| Tier | Count | Avg Return | Hit Rate |")
        lines.append("|------|-------|-----------|----------|")
        for tier in ["S", "A", "B", "C", "D"]:
            d = tier_ret.get(tier, {})
            if d:
                lines.append(f"| {tier} | {d['count']} | {d['avg']:+.1f}% | {d['hit_rate']:.0f}% |")
            else:
                lines.append(f"| {tier} | 0 | — | — |")
        lines.append("")

    # Priority returns
    prio_ret = details.get("priority_returns_20d", {})
    if prio_ret:
        lines.append("## Tracking Priority Returns (20d)\n")
        lines.append("| Priority | Count | Avg Return | Hit Rate |")
        lines.append("|----------|-------|-----------|----------|")
        for p in ["High", "Medium", "Low"]:
            d = prio_ret.get(p, {})
            if d:
                lines.append(f"| {p} | {d['count']} | {d['avg_20d']:+.1f}% | {d['hit_rate']:.0f}% |")
        lines.append("")

    # Verdict
    lines.append("## 🎯 Verdict\n")
    ic_20d = summary.get("ic_20d")
    if ic_20d is not None:
        if ic_20d >= 0.10 and mono:
            lines.append("✅ **Scoring system shows meaningful predictive power.** Ready for live paper trading.")
        elif ic_20d >= 0.05:
            lines.append("🟡 **Marginal signal detected.** May work in trending markets. Consider refining evidence scoring.")
        else:
            lines.append("🔴 **Weak signal.** Scoring system needs recalibration before live deployment.")
    else:
        lines.append("ℹ️ Insufficient data for verdict.")

    return "\n".join(lines)


def run_all_periods():
    """Run backtests for both validation periods."""
    tickers = config.all_tickers()

    # Download full history
    download_historical_prices(tickers, start="2024-01-01")

    # Download fundamentals
    from src.fundamentals_cache import download_fundamentals, init_fundamentals_table
    init_fundamentals_table()
    download_fundamentals(tickers)

    results = {}

    # Period 1: Score in 2024-H2, evaluate 2025-H1 returns
    logger.info("\n" + "=" * 60)
    logger.info("PERIOD 1: 2024-H2 scores → 2025-H1 forward returns")
    logger.info("=" * 60)
    s1 = run_backtest(
        "2024H2_to_2025H1",
        score_start="2024-07-01", score_end="2024-12-31",
        eval_frequency_days=5
    )
    results["2024H2_to_2025H1"] = s1

    # Period 2: Score in 2025-H2, evaluate 2026-H1 returns
    logger.info("\n" + "=" * 60)
    logger.info("PERIOD 2: 2025-H2 scores → 2026-H1 forward returns")
    logger.info("=" * 60)
    s2 = run_backtest(
        "2025H2_to_2026H1",
        score_start="2025-07-01", score_end="2025-12-31",
        eval_frequency_days=5
    )
    results["2025H2_to_2026H1"] = s2

    # Generate combined report
    report_dir = os.path.join(config.REPORTS_DIR, "backtest")
    os.makedirs(report_dir, exist_ok=True)

    combined = "# 📊 Historical Backtest — Full Report\n\n"
    combined += f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"

    for period, summary in results.items():
        combined += format_backtest_report(summary, period)
        combined += "\n\n---\n\n"

    filepath = os.path.join(report_dir, f"historical_{datetime.now().strftime('%Y-%m-%d')}.md")
    with open(filepath, "w") as f:
        f.write(combined)

    logger.info(f"\nFull report saved: {filepath}")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Historical backtest")
    parser.add_argument("--download-only", action="store_true", help="Only download prices")
    parser.add_argument("--period", choices=["all", "2024H2", "2025H2"], default="all")
    args = parser.parse_args()

    init_backtest_db()

    if args.download_only:
        download_historical_prices(config.all_tickers(), start="2024-01-01")
    elif args.period == "2024H2":
        download_historical_prices(config.all_tickers(), start="2024-01-01")
        s = run_backtest("2024H2_to_2025H1", "2024-07-01", "2024-12-31")
        print(format_backtest_report(s, "2024H2_to_2025H1"))
    elif args.period == "2025H2":
        download_historical_prices(config.all_tickers(), start="2024-01-01")
        s = run_backtest("2025H2_to_2026H1", "2025-07-01", "2025-12-31")
        print(format_backtest_report(s, "2025H2_to_2026H1"))
    else:
        results = run_all_periods()
        for period, summary in results.items():
            print(format_backtest_report(summary, period))
            print("\n" + "=" * 60 + "\n")
