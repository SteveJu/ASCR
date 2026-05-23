"""Tests for feedback-informed scoring adjustments."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import tempfile

from src.scoring_feedback import _bounded_alpha, feedback_adjustment, load_feedback_state


def test_bounded_alpha_requires_minimum_samples():
    cfg = {"min_samples": 5, "full_confidence_samples": 30, "max_adjustment": 12}
    assert _bounded_alpha(20, 80, 3, cfg, 0.5, 0.15) == 0


def test_bounded_alpha_caps_large_feedback():
    cfg = {"min_samples": 5, "full_confidence_samples": 5, "max_adjustment": 6}
    adj = _bounded_alpha(100, 100, 20, cfg, 0.5, 0.15)
    assert adj == 6


def test_feedback_adjustment_uses_shrunk_ticker_and_rating(monkeypatch=None):
    state = {
        "by_ticker": {
            "TEST": {"sample_count": 30, "avg_alpha": 8, "avg_quality": 65},
        },
        "by_rating": {
            "B": {"sample_count": 30, "avg_alpha": 4, "avg_quality": 55},
        },
        "by_decision_type": {},
        "by_event_type": {},
    }
    load_feedback_state.cache_clear()
    original = load_feedback_state.__wrapped__ if hasattr(load_feedback_state, "__wrapped__") else None

    # Patch the cached function by replacing module global through direct assignment.
    import src.scoring_feedback as sf
    real_loader = sf.load_feedback_state
    sf.load_feedback_state = lambda horizon_days=20, as_of_date=None: state
    try:
        cfg = {
            "enabled": True,
            "horizon_days": 20,
            "min_samples": 5,
            "full_confidence_samples": 30,
            "max_adjustment": 12,
            "components": {
                "ticker_alpha": 0.50,
                "rating_alpha": 0.25,
                "event_type_alpha": 0.25,
                "quality": 0.15,
            },
        }
        adj, details = sf.feedback_adjustment("TEST", "B", [], cfg)
    finally:
        sf.load_feedback_state = real_loader

    assert adj["evidence"] > 0
    assert adj["asymmetry"] > 0
    assert adj["risk"] == 0
    assert len(details["pieces"]) == 2


def test_load_feedback_state_respects_as_of_date():
    import src.scoring_feedback as sf

    with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
        conn = sqlite3.connect(tmp.name)
        conn.executescript("""
            CREATE TABLE decisions (
                decision_id INTEGER PRIMARY KEY,
                decision_date TEXT,
                ticker TEXT,
                decision_type TEXT,
                rating TEXT
            );
            CREATE TABLE decision_outcomes (
                decision_id INTEGER,
                evaluation_date TEXT,
                horizon_days INTEGER,
                forward_return REAL,
                alpha_return REAL
            );
            CREATE TABLE decision_quality_scores (
                decision_id INTEGER,
                evaluation_date TEXT,
                horizon_days INTEGER,
                quality_score REAL
            );
        """)
        conn.executemany(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?)",
            [
                (1, "2026-01-01", "TEST", "BUY", "B"),
                (2, "2026-03-01", "TEST", "BUY", "B"),
            ],
        )
        conn.executemany(
            "INSERT INTO decision_outcomes VALUES (?, ?, ?, ?, ?)",
            [
                (1, "2026-01-25", 20, 6, 5),
                (2, "2026-03-25", 20, 26, 25),
            ],
        )
        conn.executemany(
            "INSERT INTO decision_quality_scores VALUES (?, ?, ?, ?)",
            [
                (1, "2026-01-25", 20, 60),
                (2, "2026-03-25", 20, 80),
            ],
        )
        conn.commit()
        conn.close()

        real_scoring = sf.config.scoring
        sf.config.scoring = lambda: {"feedback_alpha": {"paper_trader_db": tmp.name}}
        sf.load_feedback_state.cache_clear()
        try:
            early = sf.load_feedback_state(20, "2026-02-01")
            full = sf.load_feedback_state(20, None)
        finally:
            sf.config.scoring = real_scoring
            sf.load_feedback_state.cache_clear()

        assert early["by_ticker"]["TEST"]["sample_count"] == 1
        assert round(early["by_ticker"]["TEST"]["avg_alpha"], 2) == 5
        assert full["by_ticker"]["TEST"]["sample_count"] == 2
        assert round(full["by_ticker"]["TEST"]["avg_alpha"], 2) == 15


if __name__ == "__main__":
    test_bounded_alpha_requires_minimum_samples()
    test_bounded_alpha_caps_large_feedback()
    test_feedback_adjustment_uses_shrunk_ticker_and_rating()
    test_load_feedback_state_respects_as_of_date()
    print("All scoring feedback tests passed!")
