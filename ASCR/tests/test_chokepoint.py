"""Tests for chokepoint intelligence watchlist."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chokepoint import compute_score, flatten_candidates, render_report


def test_compute_score_subtracts_crowding_risk():
    candidate = {
        "ticker": "TEST",
        "score": {
            "scarcity": 20,
            "dependency": 18,
            "concentration": 12,
            "validation": 10,
            "timing": 15,
            "crowding_risk": 9,
        },
    }

    result = compute_score(candidate)

    assert result["score"] == 66
    assert result["positive"] == 75
    assert result["negative"] == 9
    assert result["tier"] == "medium"


def test_flatten_candidates_inherits_theme_metadata_and_sorts():
    data = {
        "score_model": {"max_component": 20},
        "rotation_phases": {"early": {}, "front_run": {}},
        "themes": [
            {
                "id": "theme_a",
                "name": "Theme A",
                "phase": "early",
                "thesis": "A bottleneck thesis",
                "candidates": [
                    {
                        "ticker": "LOW",
                        "score": {
                            "scarcity": 8,
                            "dependency": 8,
                            "concentration": 8,
                            "validation": 8,
                            "timing": 8,
                            "crowding_risk": 4,
                        },
                    },
                    {
                        "ticker": "HIGH",
                        "score": {
                            "scarcity": 18,
                            "dependency": 18,
                            "concentration": 18,
                            "validation": 18,
                            "timing": 18,
                            "crowding_risk": 5,
                        },
                    },
                ],
            }
        ],
    }

    rows = flatten_candidates(data)

    assert [row["ticker"] for row in rows] == ["HIGH", "LOW"]
    assert rows[0]["theme_id"] == "theme_a"
    assert rows[0]["phase"] == "early"
    assert rows[0]["thesis"] == "A bottleneck thesis"


def test_default_config_is_watch_only_and_report_says_so():
    rows = flatten_candidates()
    report = render_report(rows, limit=3)

    assert rows
    assert all(row["status"] == "watch_only" for row in rows)
    assert "watch-only discovery input" in report
    assert "no direct trading integration" in report
