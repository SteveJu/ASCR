"""Tests for article-level news deduplication."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_article_hash_ignores_publisher_suffix():
    from src.event_deduper import article_hash, canonical_news_title

    a = {"title": "Schneider Electric sees India data center growth on AI boom - KFGO"}
    b = {"title": "Schneider Electric sees India data center growth on AI boom - WTVB"}

    assert canonical_news_title(a["title"]) == canonical_news_title(b["title"])
    assert article_hash(a) == article_hash(b)


def test_deduplicate_articles_removes_syndicated_copies():
    from src.event_deduper import deduplicate_articles

    articles = [
        {"title": "Lumentum optical stock rides AI wave - Investing.com", "source_query": "optical"},
        {"title": "Lumentum optical stock rides AI wave - Investing.com UK", "source_query": "optical"},
        {"title": "Micron signs new HBM supply agreement with NVIDIA", "source_query": "memory"},
    ]

    kept = deduplicate_articles(articles)

    assert [a["title"] for a in kept] == [articles[0]["title"], articles[2]["title"]]
    assert all("_hash" in a and "_canonical_title" in a for a in kept)


def test_find_duplicate_article_matches_existing_canonical_title():
    from src import db
    from src.event_deduper import find_duplicate_article, stable_hash

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        db.init_db()
        existing_title = (
            "Applied Materials Q2 Earnings Preview: AI-Driven Semiconductor "
            "Equipment Demand Continues to Accelerate - Bitget"
        )
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (date, ticker, source, headline, hash) VALUES (?, ?, ?, ?, ?)",
                ("2026-06-05", "AMAT", "semiconductor equipment AI capex", existing_title, stable_hash(existing_title)),
            )

        new_article = {
            "title": (
                "Applied Materials Q2 Earnings Preview: AI-Driven Semiconductor "
                "Equipment Demand Continues to Accelerate - bitget.com"
            ),
            "source_query": "semiconductor equipment AI capex",
        }
        with db.get_conn() as conn:
            duplicate = find_duplicate_article(conn, new_article)
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)

    assert duplicate is not None
    assert duplicate["reason"] == "canonical_title"
