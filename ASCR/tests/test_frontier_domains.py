"""Tests for domain-general frontier discovery configuration."""
import os
import json
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.frontier_domains import discovery_query_specs, domain_ids, prompt_context, promotion_gate, render_report
from src import sector_discovery


def test_frontier_config_covers_requested_domains():
    ids = domain_ids()

    assert "humanoid_robotics" in ids
    assert "robotics_automation" in ids
    assert "commercial_space" in ids
    assert "quantum_computing" in ids


def test_frontier_queries_are_domain_tagged():
    specs = discovery_query_specs()

    assert specs
    assert all(spec["query"] for spec in specs)
    assert all(spec["domain_id"] for spec in specs)
    assert any("humanoid" in spec["query"] for spec in specs)
    assert any(spec["domain_id"] == "commercial_space" for spec in specs)
    assert any(spec["domain_id"] == "quantum_computing" for spec in specs)
    assert any(spec.get("subdomain_id") == "space_launch_propulsion" for spec in specs)
    assert any(spec.get("subdomain_id") == "quantum_cryogenics_control" for spec in specs)


def test_frontier_promotion_gate_is_conservative():
    gate = promotion_gate()

    assert gate["min_promotion_score"] >= gate["min_watch_score"]
    assert gate["require_ticker"] is True
    assert gate["require_hard_evidence"] is True


def test_sector_discovery_uses_frontier_queries():
    specs = sector_discovery._discovery_query_specs()
    domains = {spec["domain_id"] for spec in specs}

    assert "legacy_ai_supply_chain" in domains
    assert "humanoid_robotics" in domains
    assert "commercial_space" in domains
    assert "quantum_computing" in domains


def test_anomaly_prompt_is_domain_general():
    prompt = sector_discovery.build_anomaly_assessment_prompt([
        {
            "company": "Example Robotics",
            "recent_mentions": 3,
            "baseline_mentions": 0,
            "headlines": ["Example Robotics signs humanoid actuator supply contract"],
        }
    ])

    assert "frontier technology" in prompt
    assert "humanoid_robotics" in prompt
    assert "humanoid_actuators_reducers" in prompt
    assert "commercial_space" in prompt
    assert "quantum_computing" in prompt
    assert "AI supply chain node" not in prompt


def test_frontier_report_says_discovery_only():
    report = render_report()
    context = prompt_context()

    assert "discovery-only" in report
    assert "Humanoid robotics" in report
    assert "Quantum computing" in context


def test_discovery_status_surfaces_domain_counts():
    from src import db

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    old_db_path = db._DB_PATH
    try:
        db._DB_PATH = path
        sector_discovery.init_discovery_db()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO mention_tracker (scan_date, company_name, headline, source_query, context) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "2026-06-01",
                    "Example Robotics",
                    "Example Robotics signs humanoid actuator supplier contract",
                    "humanoid robot actuator supplier production order",
                    json.dumps({
                        "domain_id": "humanoid_robotics",
                        "subdomain_id": "humanoid_actuators_reducers",
                    }),
                ),
            )
            conn.execute(
                "INSERT INTO discovery_anomalies "
                "(detected_date, company_name, ticker_guess, mention_count, first_seen, headlines, "
                "llm_assessment, is_real_signal, domain_guess, sector_guess, thesis, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "2026-06-01",
                    "Example Robotics",
                    "EXRB",
                    3,
                    "2026-06-01",
                    "[]",
                    "{}",
                    1,
                    "humanoid_robotics",
                    "humanoid actuators",
                    "Actuator supplier thesis",
                    "flagged",
                ),
            )

        status = sector_discovery.get_discovery_status()
        text = sector_discovery.format_telegram_status()
    finally:
        db._DB_PATH = old_db_path
        os.remove(path)

    assert status["top_domains"][0] == {"domain": "humanoid_robotics", "count": 1}
    assert status["top_subdomains"][0] == {"subdomain": "humanoid_actuators_reducers", "count": 1}
    assert status["flagged"][0]["domain_guess"] == "humanoid_robotics"
    assert "humanoid_robotics" in text
    assert "humanoid_actuators_reducers" in text
