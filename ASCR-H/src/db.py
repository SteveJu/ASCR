"""Database layer for ascr_h."""
import sqlite3
import time
import os
from contextlib import contextmanager
from src import config
from src.utils import get_logger

logger = get_logger("db")
_journal_mode_initialized = False
POSITION_EPS = 1e-8

@contextmanager
def get_conn():
    global _journal_mode_initialized
    conn = sqlite3.connect(config.db_path())
    conn.row_factory = sqlite3.Row
    if not _journal_mode_initialized:
        conn.execute("PRAGMA journal_mode=WAL")
        _journal_mode_initialized = True
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def radar_conn():
    """Read-only connection to ascr DB."""
    path = config.radar_db_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Stock radar DB not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            original_reason TEXT,
            blocked_reason TEXT,
            created_date TEXT NOT NULL,
            eligible_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            resolved_date TEXT,
            resolved_reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")

        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                reason TEXT,
                signal_id TEXT,
                rating TEXT,
                created_at REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_positions (
                ticker TEXT PRIMARY KEY,
                entry_date TEXT,
                avg_entry_price REAL,
                quantity REAL,
                cost_basis REAL,
                current_value REAL,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                max_price_since_entry REAL,
                peak_date TEXT,
                rating_at_entry TEXT,
                sector TEXT,
                status TEXT DEFAULT 'open'
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_equity_curve (
                date TEXT PRIMARY KEY,
                cash REAL,
                positions_value REAL,
                total_equity REAL,
                daily_return REAL,
                drawdown REAL,
                peak_equity REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS shadow_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT,
                ticker TEXT,
                rating TEXT,
                evidence_score REAL,
                asymmetry_score REAL,
                momentum_score REAL,
                risk_score REAL,
                opportunity_score REAL,
                entry_reference_price REAL,
                return_5d REAL,
                return_10d REAL,
                return_20d REAL,
                return_60d REAL,
                max_gain_60d REAL,
                max_drawdown_60d REAL,
                sector TEXT,
                created_at REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_ticker ON shadow_tracks(ticker)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_date ON shadow_tracks(signal_date)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL,
                peak_equity REAL,
                updated_at REAL
            )
        """)

# --- Account ---

def get_account():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM account WHERE id=1").fetchone()
        if row:
            return dict(row)
    return None

def init_account(cash):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO account (id, cash, peak_equity, updated_at)
            VALUES (1, ?, ?, ?)
        """, (cash, cash, time.time()))
    logger.info(f"account_init cash=${cash:,.2f}")

def update_cash(cash):
    with get_conn() as conn:
        row = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
        old_cash = row["cash"] if row else None
        conn.execute("UPDATE account SET cash=?, updated_at=? WHERE id=1", (cash, time.time()))
    if old_cash is None:
        logger.warning(f"cash_update skipped_old_missing new=${cash:,.2f}")
    else:
        logger.info(f"cash_update old=${old_cash:,.2f} new=${cash:,.2f} delta=${cash - old_cash:+,.2f}")

def update_peak_price(ticker, price):
    """Update the max price since entry for a position."""
    from datetime import datetime
    with get_conn() as conn:
        row = conn.execute(
            "SELECT max_price_since_entry FROM paper_positions WHERE ticker=? AND status='open'",
            (ticker,)
        ).fetchone()
        old_peak = row["max_price_since_entry"] if row else None
        conn.execute(
            "UPDATE paper_positions SET max_price_since_entry=?, peak_date=? WHERE ticker=? AND status='open'",
            (price, datetime.now().strftime("%Y-%m-%d"), ticker)
        )
    logger.info(f"position_peak_update ticker={ticker} old={old_peak} new={price}")


def refresh_position_prices():
    """Update current_value and unrealized_pnl for all open positions with live prices."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("refresh_position_prices skipped: yfinance not installed")
        return

    with get_conn() as conn:
        positions = conn.execute("SELECT ticker, quantity, cost_basis, avg_entry_price, max_price_since_entry FROM paper_positions WHERE status='open' AND quantity > 0").fetchall()

        for p in positions:
            try:
                t = yf.Ticker(p["ticker"])
                price = t.info.get("regularMarketPrice") or t.info.get("previousClose")
                if not price:
                    logger.warning(f"price_refresh_missing ticker={p['ticker']} source=yfinance")
                    continue
                value = p["quantity"] * price
                pnl = value - p["cost_basis"]
                peak = max(p["max_price_since_entry"] or 0, price)
                peak_date = None
                if price >= peak:
                    from datetime import datetime
                    peak_date = datetime.now().strftime("%Y-%m-%d")

                conn.execute("""
                    UPDATE paper_positions
                    SET current_value=?, unrealized_pnl=?, max_price_since_entry=?
                    WHERE ticker=? AND status='open'
                """, (value, pnl, peak, p["ticker"]))
                if peak_date:
                    conn.execute("UPDATE paper_positions SET peak_date=? WHERE ticker=? AND status='open'",
                               (peak_date, p["ticker"]))
                logger.info(f"position_price_refresh ticker={p['ticker']} price=${price:.2f} value=${value:,.2f} pnl=${pnl:+,.2f}")
            except Exception as e:
                logger.warning(f"price_refresh_failed ticker={p['ticker']} error={e}")


def update_peak_equity(peak):
    with get_conn() as conn:
        row = conn.execute("SELECT peak_equity FROM account WHERE id=1").fetchone()
        old_peak = row["peak_equity"] if row else None
        conn.execute("UPDATE account SET peak_equity=?, updated_at=? WHERE id=1", (peak, time.time()))
    logger.info(f"equity_peak_update old={old_peak} new={peak}")

# --- Orders ---

def add_order(date, ticker, side, quantity, price, reason="", signal_id="", rating=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO paper_orders (date, ticker, side, quantity, price, reason, signal_id, rating, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, ticker, side, quantity, price, reason, signal_id, rating, time.time()))
    logger.info(
        f"order_add date={date} side={side} ticker={ticker} qty={quantity:.4f} "
        f"price=${price:.2f} amount=${quantity * price:,.2f} reason={reason}"
    )

def get_orders(ticker=None, days=30):
    with get_conn() as conn:
        if ticker:
            rows = conn.execute("SELECT * FROM paper_orders WHERE ticker=? ORDER BY date DESC", (ticker,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM paper_orders ORDER BY date DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]

# --- Positions ---

def get_position(ticker):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM paper_positions WHERE ticker=? AND status='open'", (ticker,)).fetchone()
    return dict(row) if row else None

def get_all_positions(status="open"):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM paper_positions WHERE status=?", (status,)).fetchall()
    return [dict(r) for r in rows]

def upsert_position(ticker, entry_date, avg_price, quantity, cost_basis, current_value,
                     realized_pnl=0, unrealized_pnl=0, max_price=None, peak_date=None,
                     rating="", sector="", status="open"):
    """Set an absolute position snapshot.

    Use increase_position/reduce_position for trade fills. This helper is for
    price refreshes and explicit snapshot replacement.
    """
    with get_conn() as conn:
        old = conn.execute("SELECT quantity, current_value, status FROM paper_positions WHERE ticker=?", (ticker,)).fetchone()
        conn.execute("""
            INSERT INTO paper_positions (ticker, entry_date, avg_entry_price, quantity, cost_basis,
                current_value, realized_pnl, unrealized_pnl, max_price_since_entry, peak_date,
                rating_at_entry, sector, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                avg_entry_price=excluded.avg_entry_price, quantity=excluded.quantity,
                cost_basis=excluded.cost_basis, current_value=excluded.current_value,
                realized_pnl=excluded.realized_pnl, unrealized_pnl=excluded.unrealized_pnl,
                max_price_since_entry=excluded.max_price_since_entry, peak_date=excluded.peak_date,
                status=excluded.status
        """, (ticker, entry_date, avg_price, quantity, cost_basis, current_value,
              realized_pnl, unrealized_pnl, max_price, peak_date, rating, sector, status))
    old_qty = old["quantity"] if old else None
    old_value = old["current_value"] if old else None
    old_status = old["status"] if old else None
    logger.info(
        f"position_upsert ticker={ticker} old_qty={old_qty} new_qty={quantity:.4f} "
        f"old_value={old_value} new_value=${current_value:,.2f} old_status={old_status} new_status={status}"
    )


def increase_position(ticker, entry_date, buy_price, buy_quantity,
                      rating="", sector=""):
    """Increase an open position using weighted-average cost accounting."""
    if buy_quantity <= 0 or buy_price <= 0:
        logger.warning(
            f"position_increase_noop ticker={ticker} qty={buy_quantity} price={buy_price}"
        )
        return None

    buy_cost = buy_quantity * buy_price
    with get_conn() as conn:
        pos = conn.execute(
            "SELECT * FROM paper_positions WHERE ticker=? AND status='open'",
            (ticker,),
        ).fetchone()

        if pos and pos["quantity"] > POSITION_EPS:
            pos = dict(pos)
            old_qty = pos["quantity"]
            old_cost = pos["cost_basis"] or (pos["avg_entry_price"] * old_qty)
            new_qty = old_qty + buy_quantity
            new_cost = old_cost + buy_cost
            new_avg = new_cost / new_qty
            current_value = new_qty * buy_price
            old_peak = pos.get("max_price_since_entry") or pos["avg_entry_price"] or buy_price
            peak = max(old_peak, buy_price)
            peak_date = entry_date if buy_price >= peak else pos.get("peak_date")
            realized_pnl = pos.get("realized_pnl", 0) or 0
            unrealized_pnl = current_value - new_cost
            keep_rating = rating or pos.get("rating_at_entry", "")
            keep_sector = sector or pos.get("sector", "")

            conn.execute("""
                UPDATE paper_positions
                SET avg_entry_price=?, quantity=?, cost_basis=?, current_value=?,
                    realized_pnl=?, unrealized_pnl=?, max_price_since_entry=?,
                    peak_date=?, rating_at_entry=?, sector=?, status='open'
                WHERE ticker=? AND status='open'
            """, (
                new_avg, new_qty, new_cost, current_value, realized_pnl,
                unrealized_pnl, peak, peak_date, keep_rating, keep_sector, ticker,
            ))
            result = {
                "ticker": ticker, "entry_date": pos["entry_date"],
                "avg_entry_price": new_avg, "quantity": new_qty,
                "cost_basis": new_cost, "current_value": current_value,
                "realized_pnl": realized_pnl, "unrealized_pnl": unrealized_pnl,
                "max_price_since_entry": peak, "peak_date": peak_date,
                "rating_at_entry": keep_rating, "sector": keep_sector,
                "status": "open",
            }
            logger.info(
                f"position_increase ticker={ticker} old_qty={old_qty:.4f} "
                f"buy_qty={buy_quantity:.4f} new_qty={new_qty:.4f} "
                f"buy_price=${buy_price:.2f} avg=${new_avg:.2f}"
            )
        else:
            conn.execute("""
                INSERT INTO paper_positions (ticker, entry_date, avg_entry_price, quantity,
                    cost_basis, current_value, realized_pnl, unrealized_pnl,
                    max_price_since_entry, peak_date, rating_at_entry, sector, status)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, 'open')
                ON CONFLICT(ticker) DO UPDATE SET
                    entry_date=excluded.entry_date,
                    avg_entry_price=excluded.avg_entry_price,
                    quantity=excluded.quantity,
                    cost_basis=excluded.cost_basis,
                    current_value=excluded.current_value,
                    realized_pnl=0,
                    unrealized_pnl=0,
                    max_price_since_entry=excluded.max_price_since_entry,
                    peak_date=excluded.peak_date,
                    rating_at_entry=excluded.rating_at_entry,
                    sector=excluded.sector,
                    status='open'
            """, (
                ticker, entry_date, buy_price, buy_quantity, buy_cost, buy_cost,
                buy_price, entry_date, rating, sector,
            ))
            result = {
                "ticker": ticker, "entry_date": entry_date,
                "avg_entry_price": buy_price, "quantity": buy_quantity,
                "cost_basis": buy_cost, "current_value": buy_cost,
                "realized_pnl": 0, "unrealized_pnl": 0,
                "max_price_since_entry": buy_price, "peak_date": entry_date,
                "rating_at_entry": rating, "sector": sector, "status": "open",
            }
            logger.info(
                f"position_open ticker={ticker} qty={buy_quantity:.4f} "
                f"price=${buy_price:.2f} cost=${buy_cost:,.2f}"
            )

    return result

def close_position(ticker, realized_pnl):
    closed = False
    with get_conn() as conn:
        pos = conn.execute("SELECT * FROM paper_positions WHERE ticker=? AND status='open'", (ticker,)).fetchone()
        if pos:
            total_realized = (pos["realized_pnl"] or 0) + realized_pnl
            conn.execute("""
                UPDATE paper_positions SET status='closed', quantity=0, current_value=0,
                    cost_basis=0, realized_pnl=?, unrealized_pnl=0
                WHERE ticker=? AND status='open'
            """, (total_realized, ticker))
            closed = True
    if closed:
        logger.info(f"position_close ticker={ticker} realized_delta=${realized_pnl:+,.2f} total_realized=${total_realized:+,.2f}")
    else:
        logger.warning(f"position_close_noop ticker={ticker} reason=no_open_position")

def reduce_position(ticker, sell_quantity, sell_price):
    """Reduce position by selling some shares. Returns realized P&L from this sale."""
    with get_conn() as conn:
        pos = conn.execute("SELECT * FROM paper_positions WHERE ticker=? AND status='open'", (ticker,)).fetchone()
        if not pos or pos["quantity"] <= 0:
            logger.warning(f"position_reduce_noop ticker={ticker} reason=no_open_position")
            return 0

        pos = dict(pos)
        avg_entry = pos["avg_entry_price"]
        sell_qty = min(sell_quantity, pos["quantity"])
        realized = (sell_price - avg_entry) * sell_qty
        new_qty = pos["quantity"] - sell_qty

        if new_qty <= POSITION_EPS:  # effectively closed
            total_realized = (pos["realized_pnl"] or 0) + realized
            conn.execute("""
                UPDATE paper_positions SET status='closed', quantity=0, current_value=0,
                    cost_basis=0, realized_pnl=?, unrealized_pnl=0
                WHERE ticker=? AND status='open'
            """, (total_realized, ticker))
        else:
            new_cost = avg_entry * new_qty
            new_value = new_qty * sell_price
            new_unrealized = new_value - new_cost
            conn.execute("""
                UPDATE paper_positions SET quantity=?, cost_basis=?,
                    current_value=?, unrealized_pnl=?, realized_pnl=realized_pnl+?
                WHERE ticker=? AND status='open'
            """, (new_qty, new_cost, new_value, new_unrealized, realized, ticker))

        logger.info(
            f"position_reduce ticker={ticker} sell_qty={sell_qty:.4f} price=${sell_price:.2f} "
            f"old_qty={pos['quantity']:.4f} new_qty={new_qty:.4f} realized=${realized:+,.2f}"
        )
        return realized

# --- Equity Curve ---

def record_equity(date, cash, positions_value, total_equity, daily_return, drawdown, peak_equity):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO paper_equity_curve (date, cash, positions_value, total_equity,
                daily_return, drawdown, peak_equity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, cash, positions_value, total_equity, daily_return, drawdown, peak_equity))
    logger.info(
        f"equity_record date={date} cash=${cash:,.2f} positions=${positions_value:,.2f} "
        f"total=${total_equity:,.2f} daily_return={daily_return:+.2f}% drawdown={drawdown:+.2f}% peak=${peak_equity:,.2f}"
    )

def get_equity_curve(days=365):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM paper_equity_curve ORDER BY date DESC LIMIT ?", (days,)).fetchall()
    return [dict(r) for r in rows]

# --- Shadow Tracks ---

def add_shadow(signal_date, ticker, rating, evidence, asymmetry, momentum, risk, opp,
               entry_price, sector=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO shadow_tracks (signal_date, ticker, rating, evidence_score, asymmetry_score,
                momentum_score, risk_score, opportunity_score, entry_reference_price, sector, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (signal_date, ticker, rating, evidence, asymmetry, momentum, risk, opp, entry_price, sector, time.time()))
    logger.info(f"shadow_add date={signal_date} ticker={ticker} rating={rating} entry=${entry_price:.2f} sector={sector}")

def get_pending_shadows(max_days=60):
    """Get shadow tracks that still need forward return updates."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM shadow_tracks
            WHERE return_60d IS NULL
            ORDER BY signal_date
        """).fetchall()
    return [dict(r) for r in rows]

def update_shadow_returns(shadow_id, **kwargs):
    set_clause = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [shadow_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE shadow_tracks SET {set_clause} WHERE id=?", values)
    logger.info(f"shadow_update id={shadow_id} fields={sorted(kwargs.keys())}")

# --- Read from ASCR ---

def read_radar_scores(date=None):
    with radar_conn() as conn:
        if date:
            rows = conn.execute("SELECT * FROM scores WHERE date=? ORDER BY opportunity_score DESC", (date,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT s.* FROM scores s
                INNER JOIN (SELECT ticker, MAX(date) as max_date FROM scores GROUP BY ticker) latest
                ON s.ticker=latest.ticker AND s.date=latest.max_date
                ORDER BY s.opportunity_score DESC
            """).fetchall()
    return [dict(r) for r in rows]

def read_radar_exit_alerts(days=1):
    with radar_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM exit_alerts WHERE date >= date('now', ?) ORDER BY created_at DESC
        """, (f'-{days} days',)).fetchall()
    return [dict(r) for r in rows]

def read_radar_prices(ticker, days=5):
    with radar_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM prices WHERE ticker=? ORDER BY date DESC LIMIT ?
        """, (ticker, days)).fetchall()
    return [dict(r) for r in rows]

# Init on import
init_db()
