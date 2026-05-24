"""Tests for event pipeline LLM retry/fallback behavior."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_with_retry_retries_empty_result(monkeypatch):
    from src import event_pipeline

    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 2:
            return None
        return {"ok": True}

    monkeypatch.setattr(event_pipeline, "LLM_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(event_pipeline.time, "sleep", lambda _: None)

    assert event_pipeline._with_retry("test call", flaky) == {"ok": True}
    assert calls["count"] == 2


def test_analyze_article_retries_gemini_before_sonnet_fallback(monkeypatch):
    from src import event_pipeline, gemini_client

    gemini_calls = {"count": 0}
    sonnet_calls = {"count": 0}

    def no_gemini_result(*args, **kwargs):
        gemini_calls["count"] += 1
        return None

    def sonnet_success(article):
        sonnet_calls["count"] += 1
        return {
            "ticker": "MU",
            "verdict": "BUY",
            "summary": "fallback worked",
            "_usage": {"input": 10, "output": 5},
        }

    monkeypatch.setattr(event_pipeline, "LLM_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(event_pipeline.time, "sleep", lambda _: None)
    monkeypatch.setattr(gemini_client, "analyze_headline", no_gemini_result)
    monkeypatch.setattr(event_pipeline, "_analyze_article_sonnet", sonnet_success)

    result = event_pipeline.analyze_article({
        "title": "Micron wins AI memory contract",
        "source_query": "Micron HBM AI memory",
    })

    assert result["ticker"] == "MU"
    assert gemini_calls["count"] == 2
    assert sonnet_calls["count"] == 1
