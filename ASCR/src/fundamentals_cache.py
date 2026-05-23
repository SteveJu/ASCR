"""Cache historical fundamental data from yfinance for backtest use.

Downloads quarterly financials + key stats for all universe tickers.
Stores in backtest.sqlite so backtest scoring can use fundamentals at each eval date.
"""
import sqlite3
import math
import yfinance as yf
from datetime import datetime
from src import config
from src.utils import get_logger

logger = get_logger("fundamentals")

BACKTEST_DB_PATH = None

def _db_path():
    global BACKTEST_DB_PATH
    if BACKTEST_DB_PATH is None:
        import os
        BACKTEST_DB_PATH = os.path.join(config.DATA_DIR, "backtest.sqlite")
    return BACKTEST_DB_PATH

def _get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn

def init_fundamentals_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bt_fundamentals (
            ticker TEXT NOT NULL,
            quarter_end TEXT NOT NULL,
            revenue REAL,
            gross_profit REAL,
            net_income REAL,
            total_debt REAL,
            total_equity REAL,
            revenue_growth_yoy REAL,
            gross_margin REAL,
            profit_margin REAL,
            debt_to_equity REAL,
            PRIMARY KEY (ticker, quarter_end)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bt_info (
            ticker TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            market_cap REAL,
            forward_pe REAL,
            trailing_pe REAL,
            analyst_count INTEGER,
            short_ratio REAL,
            fifty_two_week_low REAL,
            fifty_two_week_high REAL,
            sector TEXT,
            industry TEXT,
            PRIMARY KEY (ticker)
        )
    """)
    conn.commit()
    conn.close()


def download_fundamentals(tickers: list):
    """Download and cache fundamental data for all tickers."""
    conn = _get_conn()
    init_fundamentals_table()

    existing = conn.execute("SELECT COUNT(DISTINCT ticker) FROM bt_fundamentals").fetchone()[0]
    if existing >= len(tickers) * 0.8:
        logger.info(f"Already have fundamentals for {existing} tickers, skipping")
        conn.close()
        return

    for i, ticker in enumerate(tickers):
        try:
            t = yf.Ticker(ticker)
            info = t.info

            # Store current info
            conn.execute("""
                INSERT OR REPLACE INTO bt_info (ticker, fetched_at, market_cap, forward_pe,
                    trailing_pe, analyst_count, short_ratio, fifty_two_week_low,
                    fifty_two_week_high, sector, industry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, datetime.now().strftime("%Y-%m-%d"),
                  info.get("marketCap"), info.get("forwardPE"), info.get("trailingPE"),
                  info.get("numberOfAnalystOpinions"), info.get("shortRatio"),
                  info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh"),
                  info.get("sector", ""), info.get("industry", "")))

            # Quarterly income statement
            inc = t.quarterly_income_stmt
            bs = t.quarterly_balance_sheet

            if inc is not None and not inc.empty:
                for date_col in inc.columns:
                    q_end = date_col.strftime("%Y-%m-%d")
                    revenue = _safe_val(inc, "Total Revenue", date_col)
                    gross_profit = _safe_val(inc, "Gross Profit", date_col)
                    net_income = _safe_val(inc, "Net Income", date_col)

                    total_debt = None
                    total_eq = None
                    if bs is not None and not bs.empty and date_col in bs.columns:
                        total_debt = _safe_val(bs, "Total Debt", date_col)
                        total_eq = _safe_val(bs, "Total Equity Gross Minority Interest", date_col)
                        if total_eq is None:
                            total_eq = _safe_val(bs, "Stockholders Equity", date_col)

                    # Compute derived metrics
                    gm = gross_profit / revenue if revenue and revenue > 0 and gross_profit else None
                    pm = net_income / revenue if revenue and revenue > 0 and net_income else None
                    dte = total_debt / total_eq * 100 if total_eq and total_eq > 0 and total_debt else None

                    conn.execute("""
                        INSERT OR REPLACE INTO bt_fundamentals (ticker, quarter_end, revenue,
                            gross_profit, net_income, total_debt, total_equity,
                            revenue_growth_yoy, gross_margin, profit_margin, debt_to_equity)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                    """, (ticker, q_end, revenue, gross_profit, net_income,
                          total_debt, total_eq, gm, pm, dte))

                # Compute YoY revenue growth
                _compute_revenue_growth(conn, ticker)

            conn.commit()
            if (i + 1) % 5 == 0:
                logger.info(f"  Downloaded fundamentals: {i+1}/{len(tickers)}")

        except Exception as e:
            logger.warning(f"  Error fetching {ticker}: {e}")

    conn.close()
    logger.info(f"Fundamentals download complete for {len(tickers)} tickers")


def _safe_val(df, row_name, col):
    try:
        if row_name in df.index:
            v = df.loc[row_name, col]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return float(v)
    except Exception:
        pass
    return None


def _compute_revenue_growth(conn, ticker):
    """Compute YoY revenue growth for each quarter."""
    rows = conn.execute("""
        SELECT quarter_end, revenue FROM bt_fundamentals
        WHERE ticker=? AND revenue IS NOT NULL
        ORDER BY quarter_end
    """, (ticker,)).fetchall()

    rows = [dict(r) for r in rows]
    # Match each quarter with same quarter previous year
    for i, r in enumerate(rows):
        q_end = r["quarter_end"]
        target_year = int(q_end[:4]) - 1
        target_q = f"{target_year}{q_end[4:]}"

        for j in range(i):
            if rows[j]["quarter_end"] == target_q or (
                abs((datetime.strptime(rows[j]["quarter_end"], "%Y-%m-%d") -
                     datetime.strptime(target_q, "%Y-%m-%d")).days) < 45
                and rows[j]["quarter_end"][:4] == str(target_year)
            ):
                prev_rev = rows[j]["revenue"]
                if prev_rev and prev_rev > 0:
                    growth = (r["revenue"] - prev_rev) / prev_rev
                    conn.execute("""
                        UPDATE bt_fundamentals SET revenue_growth_yoy=?
                        WHERE ticker=? AND quarter_end=?
                    """, (growth, ticker, q_end))
                break


def get_fundamentals_at_date(conn, ticker: str, eval_date: str) -> dict:
    """Get most recent fundamentals available at eval_date (no lookahead).

    Quarterly data has ~45 day reporting lag, so we use quarter_end + 45 days.
    """
    # Most recent quarter with results available (45-day lag for filing)
    row = conn.execute("""
        SELECT * FROM bt_fundamentals
        WHERE ticker=? AND date(quarter_end, '+45 days') <= ?
        ORDER BY quarter_end DESC LIMIT 1
    """, (ticker, eval_date)).fetchone()

    if not row:
        return {}

    result = dict(row)

    # Also get static info
    info = conn.execute("SELECT * FROM bt_info WHERE ticker=?", (ticker,)).fetchone()
    if info:
        result.update(dict(info))

    return result
