"""Regression tests for operational bug fixes."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_fetch_prices_writes_volume_not_adj_close():
    import pandas as pd
    from src import price_fetcher

    dates = pd.to_datetime(["2026-05-18"])
    data = pd.DataFrame(
        {
            "Open": [10.0],
            "High": [11.0],
            "Low": [9.0],
            "Close": [10.5],
            "Adj Close": [10.25],
            "Volume": [123456],
        },
        index=dates,
    )

    old_download = price_fetcher.yf.download
    old_upsert = price_fetcher.db.upsert_price
    calls = []
    try:
        price_fetcher.yf.download = lambda *args, **kwargs: data
        price_fetcher.db.upsert_price = lambda *args: calls.append(args)
        price_fetcher.fetch_prices(["AAA"], period="1d")
    finally:
        price_fetcher.yf.download = old_download
        price_fetcher.db.upsert_price = old_upsert

    assert calls == [("AAA", "2026-05-18", 10.0, 11.0, 9.0, 10.5, 123456)]


def test_llm_usage_estimates_and_logs_cost():
    from src import llm_usage
    import src.db as real_db

    calls = []
    real_log = real_db.log_llm_call
    try:
        real_db.log_llm_call = lambda **kwargs: calls.append(kwargs)

        cost = llm_usage.log_ai_call(
            model="gemini-2.5-flash",
            purpose="unit_test",
            input_tokens=1000,
            output_tokens=2000,
            ticker="MU",
        )
    finally:
        real_db.log_llm_call = real_log

    assert round(cost, 6) == 0.0053
    assert calls[0]["model"] == "gemini-2.5-flash"
    assert calls[0]["tier"] == "standard"
    assert calls[0]["purpose"] == "unit_test"
    assert calls[0]["ticker"] == "MU"
    assert calls[0]["input_tokens"] == 1000
    assert calls[0]["output_tokens"] == 2000
    assert round(calls[0]["cost_usd"], 6) == 0.0053
    assert round(llm_usage.estimate_cost("gemini-3.1-flash-lite", 1000, 2000), 6) == 0.00325


def test_gemini_generate_logs_billable_output_tokens():
    from src import gemini_client

    class FakeResponse:
        def json(self):
            return {
                "candidates": [{
                    "content": {"parts": [{"text": "ok"}]},
                }],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 20,
                    "thoughtsTokenCount": 5,
                },
            }

    calls = []
    old_key = gemini_client.API_KEY
    old_post = gemini_client.requests.post
    old_log = gemini_client.log_ai_call
    try:
        gemini_client.API_KEY = "test-key"
        gemini_client.requests.post = lambda *args, **kwargs: FakeResponse()
        gemini_client.log_ai_call = lambda **kwargs: calls.append(kwargs) or 0.0001

        result = gemini_client.generate(
            "prompt",
            model="gemini-2.5-flash-lite",
            purpose="unit_test_generate",
            ticker="MU",
        )
    finally:
        gemini_client.API_KEY = old_key
        gemini_client.requests.post = old_post
        gemini_client.log_ai_call = old_log

    assert result["text"] == "ok"
    assert result["usage"]["input"] == 100
    assert result["usage"]["output"] == 20
    assert result["usage"]["thinking"] == 5
    assert result["usage"]["billable_output"] == 25
    assert calls[0]["model"] == "gemini-2.5-flash-lite"
    assert calls[0]["purpose"] == "unit_test_generate"
    assert calls[0]["ticker"] == "MU"
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["output_tokens"] == 25


def test_rankings_fetch_prices_only_for_event_qualified_tickers():
    from src import recommender

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute("""
            CREATE TABLE events (
                ticker TEXT, date TEXT, evidence_delta REAL, event_type TEXT,
                verdict TEXT, conviction TEXT, upside_potential TEXT,
                investment_thesis TEXT, moat TEXT, catalyst TEXT,
                summary TEXT, headline TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE scores (
                ticker TEXT, date TEXT, evidence_score REAL, asymmetry_score REAL,
                momentum_score REAL, risk_score REAL, opportunity_score REAL, rating TEXT
            )
        """)
        conn.execute("""
            INSERT INTO events VALUES
            ('MU', ?, 6, 'contract', 'BUY', 'HIGH', '20-50%', 'thesis', 'strong', 'earnings', 'summary', 'headline')
        """, (today,))
        conn.executemany("""
            INSERT INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            ("MU", today, 50, 40, 30, 10, 45, "B"),
            ("QQQ", today, 80, 80, 80, 5, 90, "S"),
        ])
        conn.commit()
        conn.close()

        calls = []
        old_db_path = recommender.DB_PATH
        old_get_live_price = recommender.get_live_price
        old_all_tickers = recommender.config.all_tickers
        try:
            recommender.DB_PATH = path
            recommender.get_live_price = lambda ticker: calls.append(ticker) or 100.0
            recommender.config.all_tickers = lambda *args, **kwargs: ["MU", "QQQ"]

            rankings = recommender.get_rankings(days=30, min_event=5)
        finally:
            recommender.DB_PATH = old_db_path
            recommender.get_live_price = old_get_live_price
            recommender.config.all_tickers = old_all_tickers
    finally:
        os.remove(path)

    assert [r["ticker"] for r in rankings] == ["MU"]
    assert calls == ["MU"]


def test_universe_scanner_no_recommendations_can_send_summary():
    import sys
    import types
    from src import universe_scanner

    sent = []
    fake_pruner = types.ModuleType("src.universe_pruner")
    fake_pruner.auto_prune = lambda dry_run=True: {"remove": [], "watch": []}
    fake_pruner.format_telegram_report = lambda result: "unused"

    old_scan_news = universe_scanner.scan_news_for_new_tickers
    old_scan_events = universe_scanner.scan_events_for_unknown
    old_evaluate = universe_scanner.evaluate_candidates
    old_send = universe_scanner.send
    old_pruner = sys.modules.get("src.universe_pruner")
    try:
        universe_scanner.scan_news_for_new_tickers = lambda: []
        universe_scanner.scan_events_for_unknown = lambda: []
        universe_scanner.evaluate_candidates = lambda headlines, unknown: []
        universe_scanner.send = lambda message: sent.append(message) or True
        sys.modules["src.universe_pruner"] = fake_pruner

        result = universe_scanner.run_scan()
    finally:
        universe_scanner.scan_news_for_new_tickers = old_scan_news
        universe_scanner.scan_events_for_unknown = old_scan_events
        universe_scanner.evaluate_candidates = old_evaluate
        universe_scanner.send = old_send
        if old_pruner is None:
            sys.modules.pop("src.universe_pruner", None)
        else:
            sys.modules["src.universe_pruner"] = old_pruner

    assert result["recommended"] == []
    assert sent
    assert "no new suggestions" in sent[0]


def test_healthcheck_parse_launchctl_list_exact_labels():
    from scripts.healthcheck import parse_launchctl_list

    statuses = parse_launchctl_list(
        "47350\t-9\tcom.ASCR.event-daemon\n"
        "-\t1\tcom.ASCR.universe-scan\n"
        "-\t0\tcom.ASCR-H.daily\n"
    )

    assert statuses["com.ASCR.event-daemon"] == {"pid": "47350", "status": "-9"}
    assert statuses["com.ASCR.universe-scan"] == {"pid": None, "status": "1"}
    assert statuses["com.ASCR-H.daily"] == {"pid": None, "status": "0"}


def test_event_pipeline_init_creates_query_indexes():
    from src import db, event_pipeline

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        event_pipeline.init_events_db()
        conn = sqlite3.connect(path)
        indexes = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)

    assert "idx_events_hash" in indexes
    assert "idx_events_ticker_date" in indexes
    assert "idx_events_date_ticker" in indexes


def test_portfolio_instructions_full_book_rotation_does_not_name_error():
    from src import recommender
    import src.bubble_detector as bubble_detector

    old_rankings = recommender.get_rankings
    old_bubble = bubble_detector.check_bubble_burst
    old_sell_signals = recommender.check_sell_signals
    try:
        bubble_detector.check_bubble_burst = lambda **kwargs: {
            "level": "NORMAL",
            "action": "none",
            "stats": {},
        }
        recommender.check_sell_signals = lambda ticker: {
            "should_sell": False,
            "reason": "",
            "total_neg_ev": 0,
            "total_risk": 0,
        }
        recommender.get_rankings = lambda **kwargs: [
            {"ticker": "NEW", "ev_score": 8, "verdict_score": 10, "top_summary": "better"},
            {"ticker": "OLD", "ev_score": 6, "verdict_score": 1, "top_summary": "held"},
        ]

        result = recommender.get_portfolio_instructions(
            {"OLD": {"avg_entry_price": 100, "peak_price": 100, "current_price": 100}},
            max_pos=1,
        )
    finally:
        recommender.get_rankings = old_rankings
        bubble_detector.check_bubble_burst = old_bubble
        recommender.check_sell_signals = old_sell_signals

    assert result["sells"][0]["ticker"] == "OLD"
    assert result["buys"][0]["ticker"] == "NEW"


def test_db_and_event_pipeline_share_compatible_events_schema():
    from src import db, event_pipeline

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        db.init_db()
        db.add_event("MU", "2026-05-23", "unit", "headline", event_type="news", confidence=0.7)
        event_pipeline.init_events_db()

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        row = conn.execute("SELECT date, event_date, headline, title FROM events WHERE ticker='MU'").fetchone()
        conn.close()
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)

    assert {"date", "event_date", "headline", "title", "raw_llm_json", "llm_json", "hash"} <= columns
    assert row["date"] == "2026-05-23"
    assert row["event_date"] == "2026-05-23"
    assert row["headline"] == "headline"
    assert row["title"] == "headline"


if __name__ == "__main__":
    test_fetch_prices_writes_volume_not_adj_close()
    test_llm_usage_estimates_and_logs_cost()
    test_gemini_generate_logs_billable_output_tokens()
    test_rankings_fetch_prices_only_for_event_qualified_tickers()
    test_universe_scanner_no_recommendations_can_send_summary()
    test_healthcheck_parse_launchctl_list_exact_labels()
    test_event_pipeline_init_creates_query_indexes()
    test_portfolio_instructions_full_book_rotation_does_not_name_error()
    test_db_and_event_pipeline_share_compatible_events_schema()
    print("Regression tests passed")
