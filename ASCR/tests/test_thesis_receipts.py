"""Tests for generalized thesis receipt tracking."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _with_temp_db(fn):
    from src import db

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        return fn(path)
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)


def test_init_db_creates_thesis_receipts_schema():
    from src import db

    def run(path):
        db.init_db()
        conn = sqlite3.connect(path)
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(thesis_receipts)").fetchall()}
        finally:
            conn.close()

    columns = _with_temp_db(run)

    assert {"receipt_key", "source", "domain_id", "ticker", "thesis", "status"} <= columns
    assert {"first_price", "current_gain_pct", "death_signals_json"} <= columns


def test_sync_all_creates_chokepoint_and_frontier_domain_receipts():
    from src import db, thesis_receipts

    def run(path):
        db.init_db()
        result = thesis_receipts.sync_all(as_of="2026-06-01")
        rows = thesis_receipts.list_receipts(limit=200)
        return result, rows

    result, rows = _with_temp_db(run)
    sources = {row["source"] for row in rows}
    domain_rows = [row for row in rows if row["source"] == "frontier_domains"]
    ticker_rows = [row for row in rows if row["source"] == "chokepoints" and row["ticker"]]

    assert result["chokepoints"]["inserted"] > 0
    assert result["frontier_domains"]["inserted"] >= 6
    assert {"chokepoints", "frontier_domains"} <= sources
    assert any(row["domain_id"] == "humanoid_robotics" for row in domain_rows)
    assert any(row["ticker"] == "AXTI" for row in ticker_rows)


def test_refresh_price_outcomes_uses_local_prices():
    from src import db, thesis_receipts

    def run(path):
        db.init_db()
        db.upsert_price("AXTI", "2026-06-01", 10, 11, 9, 10, 1000)
        thesis_receipts.sync_chokepoint_receipts(as_of="2026-06-01")
        db.upsert_price("AXTI", "2026-06-02", 10, 14, 10, 12, 1200)
        refresh = thesis_receipts.refresh_price_outcomes()
        rows = thesis_receipts.list_receipts(source="chokepoints", limit=200)
        axti = next(row for row in rows if row["ticker"] == "AXTI")
        return refresh, axti

    refresh, axti = _with_temp_db(run)

    assert refresh["updated"] >= 1
    assert axti["first_price"] == 10
    assert axti["current_price"] == 12
    assert axti["current_gain_pct"] == 20


def test_bootstrap_receipt_prices_fetches_only_receipt_tickers(monkeypatch):
    from src import db, thesis_receipts, price_fetcher

    calls = []
    old_fetch = price_fetcher.fetch_prices

    def fake_fetch(tickers, period="5d"):
        calls.append((list(tickers), period))
        for ticker in tickers:
            db.upsert_price(ticker, "2026-06-01", 10, 10, 10, 10, 100)

    def run(path):
        db.init_db()
        thesis_receipts.sync_chokepoint_receipts(as_of="2026-06-01")
        price_fetcher.fetch_prices = fake_fetch
        try:
            return thesis_receipts.bootstrap_receipt_prices(period="1mo", limit=3)
        finally:
            price_fetcher.fetch_prices = old_fetch

    result = _with_temp_db(run)

    assert result["tickers"] == 3
    assert result["updated"] == 3
    assert calls
    assert len(calls[0][0]) == 3
    assert calls[0][1] == "1mo"
