"""Backtest database — completely separate from live DB."""
import sqlite3
from pathlib import Path
from src.config import DB_PATH


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (ticker, date)
        );

        CREATE TABLE IF NOT EXISTS sec_filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            filing_date TEXT NOT NULL,
            form_type TEXT NOT NULL,
            accession TEXT UNIQUE,
            title TEXT,
            items TEXT,
            raw_text TEXT,
            url TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS insider_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            filing_date TEXT NOT NULL,
            trader_name TEXT,
            title TEXT,
            transaction_type TEXT,
            shares REAL,
            price REAL,
            value REAL,
            shares_after REAL,
            accession TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            source TEXT,
            headline TEXT,
            event_type TEXT,
            evidence_delta REAL DEFAULT 0,
            verdict TEXT,
            conviction TEXT,
            thesis TEXT,
            bull_case TEXT,
            bear_case TEXT,
            confidence REAL DEFAULT 0,
            hash TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,  -- BUY or SELL
            price REAL NOT NULL,
            shares REAL NOT NULL,
            value REAL NOT NULL,
            reason TEXT,
            portfolio_value REAL,
            cash REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS backtest_daily (
            date TEXT PRIMARY KEY,
            portfolio_value REAL,
            cash REAL,
            positions_count INTEGER,
            daily_return REAL,
            cumulative_return REAL,
            qqq_cumulative REAL,
            spy_cumulative REAL
        );
        """)
    return conn
