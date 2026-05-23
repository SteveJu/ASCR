"""SQLite database layer for ascr — V2 with full schema."""
import sqlite3
import time
import json
import os
from contextlib import contextmanager
from src import config

_DB_PATH = None

def _get_db_path():
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = config.db_path()
    return _DB_PATH

@contextmanager
def get_conn():
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def ensure_events_table(conn):
    """Ensure the events table supports both legacy helpers and event pipeline."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            event_date TEXT,
            ticker TEXT,
            source TEXT,
            headline TEXT,
            title TEXT,
            url TEXT,
            raw_text TEXT,
            event_type TEXT,
            counterparty TEXT,
            is_ai_related INTEGER,
            supply_chain_area TEXT,
            revenue_impact TEXT,
            time_horizon TEXT,
            evidence_delta REAL,
            asymmetry_delta REAL,
            risk_delta REAL,
            summary TEXT,
            confidence REAL DEFAULT 0,
            raw_llm_json TEXT,
            llm_json TEXT,
            hash TEXT UNIQUE,
            created_at REAL,
            verdict TEXT,
            conviction TEXT,
            upside_potential TEXT,
            priced_in_pct REAL,
            investment_thesis TEXT,
            moat TEXT,
            catalyst TEXT,
            invalidation_trigger TEXT
        )
    """)

    existing = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    columns = {
        "date": "TEXT",
        "event_date": "TEXT",
        "ticker": "TEXT",
        "source": "TEXT",
        "headline": "TEXT",
        "title": "TEXT",
        "url": "TEXT",
        "raw_text": "TEXT",
        "event_type": "TEXT",
        "counterparty": "TEXT",
        "is_ai_related": "INTEGER",
        "supply_chain_area": "TEXT",
        "revenue_impact": "TEXT",
        "time_horizon": "TEXT",
        "evidence_delta": "REAL",
        "asymmetry_delta": "REAL",
        "risk_delta": "REAL",
        "summary": "TEXT",
        "confidence": "REAL DEFAULT 0",
        "raw_llm_json": "TEXT",
        "llm_json": "TEXT",
        "hash": "TEXT",
        "created_at": "REAL",
        "verdict": "TEXT",
        "conviction": "TEXT",
        "upside_potential": "TEXT",
        "priced_in_pct": "REAL",
        "investment_thesis": "TEXT",
        "moat": "TEXT",
        "catalyst": "TEXT",
        "invalidation_trigger": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_event_date ON events(event_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON events(hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ticker_date ON events(ticker, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date_ticker ON events(date, ticker)")

def init_db():
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS tickers (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                sector_theme TEXT,
                source TEXT DEFAULT 'universe',
                status TEXT DEFAULT 'active',
                tracking_priority TEXT DEFAULT 'Medium',
                added_at REAL,
                updated_at REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume INTEGER,
                source TEXT DEFAULT 'yfinance',
                PRIMARY KEY (ticker, date)
            )
        """)

        ensure_events_table(conn)

        c.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                evidence_score REAL,
                asymmetry_score REAL,
                momentum_score REAL,
                risk_score REAL,
                opportunity_score REAL,
                rating TEXT,
                tracking_priority TEXT,
                reason TEXT,
                next_trigger TEXT,
                details_json TEXT,
                PRIMARY KEY (ticker, date)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_scores_date_opportunity ON scores(date, opportunity_score)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_scores_rating_date ON scores(rating, date)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                position_size REAL,
                shares REAL NOT NULL,
                thesis TEXT,
                expected_holding_period TEXT,
                max_loss_pct REAL DEFAULT 25,
                trailing_stop_pct REAL DEFAULT 15,
                status TEXT DEFAULT 'open',
                position_status TEXT DEFAULT 'Hold Strong',
                exit_price REAL,
                exit_date TEXT,
                exit_reason TEXT,
                peak_price REAL,
                peak_date TEXT,
                notes TEXT,
                created_at REAL,
                updated_at REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_positions_status_ticker ON positions(status, ticker)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS exit_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                position_id INTEGER,
                date TEXT,
                alert_type TEXT,
                severity TEXT,
                action_suggestion TEXT,
                reason TEXT,
                acknowledged INTEGER DEFAULT 0,
                created_at REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_exit_alerts_date ON exit_alerts(date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_exit_alerts_ticker_date ON exit_alerts(ticker, date)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT,
                report_type TEXT,
                content TEXT,
                created_at REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS discovered_candidates (
                ticker TEXT PRIMARY KEY,
                discovered_at REAL,
                discovery_source TEXT,
                discovery_reason TEXT,
                promoted INTEGER DEFAULT 0,
                dismissed INTEGER DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier TEXT,
                model TEXT,
                purpose TEXT,
                ticker TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cost_usd REAL,
                called_at REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_called_at ON llm_calls(called_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_model_purpose ON llm_calls(model, purpose)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS sec_filings (
                accession TEXT PRIMARY KEY,
                ticker TEXT,
                form_type TEXT,
                filed_date TEXT,
                description TEXT,
                processed INTEGER DEFAULT 0,
                events_json TEXT,
                seen_at REAL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT,
                ticker TEXT,
                message TEXT,
                rating TEXT,
                sent_at REAL,
                delivered INTEGER DEFAULT 0
            )
        """)

# --- Ticker management ---

def upsert_ticker(ticker, company_name="", sector_theme="", source="universe", status="active", tracking_priority="Medium"):
    with get_conn() as conn:
        now = time.time()
        conn.execute("""
            INSERT INTO tickers (ticker, company_name, sector_theme, source, status, tracking_priority, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
              company_name=COALESCE(NULLIF(excluded.company_name,''), tickers.company_name),
              sector_theme=COALESCE(NULLIF(excluded.sector_theme,''), tickers.sector_theme),
              updated_at=excluded.updated_at
        """, (ticker, company_name, sector_theme, source, status, tracking_priority, now, now))

# --- Price ---

def upsert_price(ticker, date, open_, high, low, close, volume, source="yfinance"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO prices (ticker, date, open, high, low, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, volume=excluded.volume
        """, (ticker, date, open_, high, low, close, volume, source))

def get_prices(ticker, days=60):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM prices WHERE ticker=? ORDER BY date DESC LIMIT ?", (ticker, days)).fetchall()
    return [dict(r) for r in rows]

# --- Events ---

def add_event(ticker, event_date, source, title, url="", raw_text="", event_type="", llm_json="", confidence=0):
    with get_conn() as conn:
        ensure_events_table(conn)
        conn.execute("""
            INSERT INTO events (
                ticker, date, event_date, source, headline, title, url, raw_text,
                event_type, raw_llm_json, llm_json, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, event_date, event_date, source, title, title, url, raw_text,
              event_type, llm_json, llm_json, confidence, time.time()))

def get_events(ticker, days=30):
    with get_conn() as conn:
        # Support both old schema (event_date) and new schema (date)
        try:
            rows = conn.execute("""
                SELECT * FROM events WHERE ticker=? AND date >= date('now', ?)
                ORDER BY date DESC
            """, (ticker, f'-{days} days')).fetchall()
        except Exception:
            try:
                rows = conn.execute("""
                    SELECT * FROM events WHERE ticker=? AND event_date >= date('now', ?)
                    ORDER BY event_date DESC
                """, (ticker, f'-{days} days')).fetchall()
            except Exception:
                rows = []
    return [dict(r) for r in rows]

# --- Scores ---

def upsert_score(ticker, date, evidence, asymmetry, momentum, risk, opportunity, rating,
                 tracking_priority="Medium", reason="", next_trigger="", details_json=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO scores (ticker, date, evidence_score, asymmetry_score, momentum_score,
                              risk_score, opportunity_score, rating, tracking_priority, reason, next_trigger, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
              evidence_score=excluded.evidence_score, asymmetry_score=excluded.asymmetry_score,
              momentum_score=excluded.momentum_score, risk_score=excluded.risk_score,
              opportunity_score=excluded.opportunity_score, rating=excluded.rating,
              tracking_priority=excluded.tracking_priority, reason=excluded.reason,
              next_trigger=excluded.next_trigger, details_json=excluded.details_json
        """, (ticker, date, evidence, asymmetry, momentum, risk, opportunity, rating,
              tracking_priority, reason, next_trigger, details_json))

def get_latest_scores(date=None):
    with get_conn() as conn:
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

def get_score_history(ticker, days=30):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM scores WHERE ticker=? ORDER BY date DESC LIMIT ?
        """, (ticker, days)).fetchall()
    return [dict(r) for r in rows]

# --- Positions ---

def add_position(ticker, entry_price, shares, entry_date, thesis="", expected_holding="",
                 max_loss_pct=25, trailing_stop_pct=15, notes=""):
    position_size = entry_price * shares
    with get_conn() as conn:
        now = time.time()
        conn.execute("""
            INSERT INTO positions (ticker, entry_date, entry_price, position_size, shares, thesis,
                                  expected_holding_period, max_loss_pct, trailing_stop_pct,
                                  status, position_status, peak_price, peak_date, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 'Hold Strong', ?, ?, ?, ?, ?)
        """, (ticker, entry_date, entry_price, position_size, shares, thesis,
              expected_holding, max_loss_pct, trailing_stop_pct, entry_price, entry_date, notes, now, now))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def get_open_positions():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY ticker").fetchall()
    return [dict(r) for r in rows]

def get_position(position_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    return dict(row) if row else None

def update_position(position_id, **kwargs):
    kwargs["updated_at"] = time.time()
    set_clause = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [position_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE positions SET {set_clause} WHERE id=?", values)

def close_position(position_id, exit_price, exit_date, exit_reason):
    update_position(position_id, status="closed", position_status="Closed",
                    exit_price=exit_price, exit_date=exit_date, exit_reason=exit_reason)

def update_peak(position_id, price, date):
    """Update peak price if new high."""
    pos = get_position(position_id)
    if pos and (pos["peak_price"] is None or price > pos["peak_price"]):
        update_position(position_id, peak_price=price, peak_date=date)

# --- Exit Alerts ---

def add_exit_alert(ticker, position_id, date, alert_type, severity, action_suggestion, reason):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO exit_alerts (ticker, position_id, date, alert_type, severity, action_suggestion, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, position_id, date, alert_type, severity, action_suggestion, reason, time.time()))

def get_recent_exit_alerts(ticker=None, days=7):
    with get_conn() as conn:
        if ticker:
            rows = conn.execute("""
                SELECT * FROM exit_alerts WHERE ticker=? AND date >= date('now', ?)
                ORDER BY created_at DESC
            """, (ticker, f'-{days} days')).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM exit_alerts WHERE date >= date('now', ?) ORDER BY created_at DESC
            """, (f'-{days} days',)).fetchall()
    return [dict(r) for r in rows]

# --- Reports ---

def save_report(report_date, report_type, content):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO reports (report_date, report_type, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (report_date, report_type, content, time.time()))

# --- Discovered candidates ---

def add_discovered_candidate(ticker, source, reason):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO discovered_candidates (ticker, discovered_at, discovery_source, discovery_reason)
            VALUES (?, ?, ?, ?)
        """, (ticker, time.time(), source, reason))

# --- SEC filings ---

def is_filing_seen(accession):
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM sec_filings WHERE accession=?", (accession,)).fetchone()
    return row is not None

def mark_filing(accession, ticker, form_type, filed_date, description=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO sec_filings (accession, ticker, form_type, filed_date, description, seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (accession, ticker, form_type, filed_date, description, time.time()))

# --- News (via events table) ---

def is_news_seen(url):
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM events WHERE url=?", (url,)).fetchone()
    return row is not None

def add_news(ticker, headline, url, source, published_at, relevance_score=0):
    add_event(ticker, published_at, source, headline, url=url, event_type="news", confidence=relevance_score/100.0)

# --- Alerts log ---

def log_alert(alert_type, ticker, message, rating="", delivered=False):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO alerts_log (alert_type, ticker, message, rating, sent_at, delivered)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (alert_type, ticker, message, rating, time.time(), 1 if delivered else 0))

# --- LLM cost tracking ---

def log_llm_call(tier, model, purpose, ticker="", input_tokens=0, output_tokens=0, cost_usd=0):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO llm_calls (tier, model, purpose, ticker, input_tokens, output_tokens, cost_usd, called_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (tier, model, purpose, ticker, input_tokens, output_tokens, cost_usd, time.time()))

def get_daily_llm_cost():
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0) as total FROM llm_calls
            WHERE called_at >= ?
        """, (time.time() - 86400,)).fetchone()
    return row["total"] if row else 0

# Initialize on import
init_db()
