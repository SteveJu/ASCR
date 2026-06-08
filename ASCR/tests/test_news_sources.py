"""Tests for additional news/source intake."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_fetch_rss_sources_filters_and_enriches(monkeypatch):
    from src import news_sources

    cfg = {
        "rss_feeds": [{
            "id": "wire",
            "enabled": True,
            "source": "globenewswire",
            "label": "Wire",
            "url": "https://example.com/feed.xml",
            "max_items": 5,
            "sleep_seconds": 0,
        }]
    }
    feed = SimpleNamespace(entries=[
        {
            "title": "Micron signs new HBM supply agreement with NVIDIA",
            "link": "https://example.com/mu",
            "published": "2026-06-06T12:00:00Z",
            "summary": "AI memory capacity reservation",
        },
        {
            "title": "Local restaurant opens downtown",
            "link": "https://example.com/noise",
            "published": "2026-06-06T12:00:00Z",
        },
    ])

    monkeypatch.setattr(news_sources.feedparser, "parse", lambda url: feed)
    monkeypatch.setattr(news_sources.config, "all_tickers", lambda include_benchmarks=False: ["MU", "NVDA"])
    monkeypatch.setattr(news_sources.config, "discovery_keywords", lambda: ["HBM", "NVIDIA"])

    articles = news_sources.fetch_rss_sources(max_per_feed=5, cfg=cfg)

    assert len(articles) == 1
    assert articles[0]["source_query"] == "globenewswire"
    assert articles[0]["source_type"] == "rss"
    assert articles[0]["_hash"]


def test_fetch_x_accounts_skips_without_token(monkeypatch):
    from src import news_sources

    cfg = {
        "x_accounts": {
            "enabled": True,
            "bearer_token_env": "MISSING_TEST_X_TOKEN",
            "users": [{"username": "aleabitoreddit", "source": "x_serenity"}],
        }
    }
    monkeypatch.delenv("MISSING_TEST_X_TOKEN", raising=False)

    assert news_sources.fetch_x_accounts(cfg=cfg) == []


def test_fetch_x_accounts_ingests_relevant_posts(monkeypatch):
    from src import news_sources

    cfg = {
        "x_accounts": {
            "enabled": True,
            "bearer_token_env": "TEST_X_TOKEN",
            "max_items_per_user": 10,
            "min_like_count": 0,
            "sleep_seconds": 0,
            "users": [{"username": "aleabitoreddit", "label": "Serenity", "source": "x_serenity"}],
        }
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, headers=None, params=None, timeout=15):
        if url.endswith("/by/username/aleabitoreddit"):
            return FakeResponse({"data": {"id": "123", "username": "aleabitoreddit"}})
        if url.endswith("/users/123/tweets"):
            return FakeResponse({"data": [
                {
                    "id": "1",
                    "text": "AAOI optical demand is accelerating with 800G AI data center deployments",
                    "created_at": "2026-06-06T12:00:00Z",
                    "public_metrics": {"like_count": 10},
                },
                {
                    "id": "2",
                    "text": "unrelated weekend post",
                    "created_at": "2026-06-06T12:01:00Z",
                    "public_metrics": {"like_count": 10},
                },
            ]})
        raise AssertionError(url)

    monkeypatch.setenv("TEST_X_TOKEN", "token")
    monkeypatch.setattr(news_sources.requests, "get", fake_get)
    monkeypatch.setattr(news_sources.config, "all_tickers", lambda include_benchmarks=False: ["AAOI"])
    monkeypatch.setattr(news_sources.config, "discovery_keywords", lambda: ["optical", "800G"])

    articles = news_sources.fetch_x_accounts(cfg=cfg)

    assert len(articles) == 1
    assert articles[0]["source_query"] == "x_serenity"
    assert articles[0]["source_type"] == "x"
    assert articles[0]["url"] == "https://x.com/aleabitoreddit/status/1"
