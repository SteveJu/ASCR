"""Tests for quant-style news relevance filtering."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_quant_prefilter_keeps_material_supply_chain_news(monkeypatch):
    from src import event_pipeline

    monkeypatch.setattr(event_pipeline, "QUANT_PREFILTER_MIN_SCORE", 50)
    monkeypatch.setattr(event_pipeline, "QUANT_PREFILTER_MAX_PER_BATCH", 12)

    articles = [
        {
            "title": "3 Best AI Stocks to Buy Now - The Motley Fool",
            "source_query": "AI data center deal contract billion",
        },
        {
            "title": "Micron signs multi-year HBM supply agreement with NVIDIA worth $2 billion",
            "source_query": "Micron HBM AI memory",
        },
    ]

    filtered = event_pipeline._python_prefilter(articles)

    assert [a["title"] for a in filtered] == [articles[1]["title"]]
    assert filtered[0]["filter_ticker"] == "MU"
    assert filtered[0]["quant_score"] >= 80


def test_quant_prefilter_drops_generic_13f_notice(monkeypatch):
    from src import event_pipeline

    monkeypatch.setattr(event_pipeline, "QUANT_PREFILTER_MIN_SCORE", 50)

    filtered = event_pipeline._python_prefilter([
        {
            "title": "13F filing: Citadel updated holdings (filed 2026-05-28)",
            "source_query": "13f_institutional",
        }
    ])

    assert filtered == []


def test_filter_headlines_orders_by_quant_score_and_caps(monkeypatch):
    from src import event_pipeline

    monkeypatch.setattr(event_pipeline, "QUANT_PREFILTER_MIN_SCORE", 45)
    monkeypatch.setattr(event_pipeline, "QUANT_PREFILTER_MAX_PER_BATCH", 2)

    articles = [
        {"title": "Arista launches new AI networking switch", "source_query": "networking"},
        {"title": "Vertiv signs $1 billion data center cooling contract with Microsoft", "source_query": "power"},
        {"title": "Broadcom price target raised by analyst", "source_query": "networking"},
    ]

    filtered = event_pipeline.filter_headlines(articles)

    assert len(filtered) == 2
    assert filtered[0]["title"] == articles[1]["title"]
    assert filtered[0]["quant_score"] >= filtered[1]["quant_score"]
    assert all("quant_score" in item for item in filtered)
