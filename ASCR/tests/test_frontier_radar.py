"""Tests for standalone frontier radar reporting."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.frontier_radar import render_report


def test_frontier_radar_report_has_standalone_sections():
    payload = {
        "domains": [
            {
                "id": "humanoid_robotics",
                "name": "Humanoid robotics",
                "query_count": 6,
                "bottleneck_count": 4,
            }
        ],
        "discovery": {
            "total_scans": 2,
            "total_companies": 5,
            "total_anomalies": 1,
            "real_signals": 1,
            "top_domains": [{"domain": "humanoid_robotics", "count": 3}],
            "flagged": [
                {
                    "company_name": "Example Robotics",
                    "ticker_guess": "EXRB",
                    "domain_guess": "humanoid_robotics",
                    "sector_guess": "actuators",
                    "thesis": "Actuator supplier thesis",
                }
            ],
        },
        "receipts": [
            {
                "ticker": "AXTI",
                "source": "chokepoints",
                "domain_id": "ai_infrastructure",
                "status": "open",
                "current_gain_pct": 12.3,
            }
        ],
    }

    report = render_report(payload)

    assert "Frontier Radar" in report
    assert "Domains" in report
    assert "Discovery Status" in report
    assert "Pending Review" in report
    assert "Thesis Receipts" in report
    assert "humanoid_robotics" in report
    assert "AXTI" in report
