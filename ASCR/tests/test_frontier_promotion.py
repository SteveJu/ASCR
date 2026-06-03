"""Tests for frontier promotion gate and shadow receipts."""
import json
import os
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


def test_frontier_promotion_gate_flags_hard_evidence_for_review():
    from src import db, frontier_promotion, sector_discovery

    def run(path):
        db.init_db()
        sector_discovery.init_discovery_db()
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO discovery_anomalies
                (detected_date, company_name, ticker_guess, mention_count,
                 first_seen, headlines, llm_assessment, is_real_signal,
                 domain_guess, sector_guess, thesis, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-06-03",
                    "Example Robotics",
                    "EXRB",
                    5,
                    "2026-06-01",
                    json.dumps([
                        "Example Robotics signs humanoid actuator production order",
                        "Example Robotics supplier backlog rises after OEM contract",
                    ]),
                    json.dumps({
                        "ticker": "EXRB",
                        "domain": "humanoid_robotics",
                        "subdomain": "humanoid_actuators_reducers",
                        "sector": "humanoid actuators",
                        "thesis": "Actuator supplier with named OEM production order.",
                        "confidence": "high",
                        "reasoning": "Production order and backlog are hard validation.",
                    }),
                    1,
                    "humanoid_robotics",
                    "humanoid actuators",
                    "Actuator supplier with named OEM production order.",
                    "flagged",
                ),
            )
        return frontier_promotion.evaluate_candidates()

    rows = _with_temp_db(run)

    assert rows[0]["decision"] == "promotion_review"
    assert rows[0]["ticker"] == "EXRB"
    assert rows[0]["score"] >= 75
    assert rows[0]["has_hard_evidence"] is True
    assert "hard_validation_language" in rows[0]["reasons"]


def test_frontier_promotion_gate_keeps_generic_low_quality_signal_on_monitor():
    from src import frontier_promotion

    row = frontier_promotion.evaluate_anomaly({
        "company_name": "Concept Space",
        "ticker_guess": "",
        "mention_count": 3,
        "headlines": json.dumps(["Concept Space says space economy is a huge opportunity"]),
        "llm_assessment": json.dumps({
            "domain": "commercial_space",
            "confidence": "low",
            "reasoning": "Generic concept commentary without funded contract.",
        }),
        "is_real_signal": 1,
        "domain_guess": "commercial_space",
        "sector_guess": "space tourism",
        "thesis": "Generic market-size story.",
    })

    assert row["decision"] == "monitor"
    assert row["ticker"] is None


def test_frontier_shadow_sync_creates_thesis_receipt():
    from src import db, frontier_promotion, sector_discovery, thesis_receipts

    def run(path):
        db.init_db()
        db.upsert_price("EXRB", "2026-06-03", 10, 11, 9, 10, 1000)
        sector_discovery.init_discovery_db()
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO discovery_anomalies
                (detected_date, company_name, ticker_guess, mention_count,
                 first_seen, headlines, llm_assessment, is_real_signal,
                 domain_guess, sector_guess, thesis, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-06-03",
                    "Example Robotics",
                    "EXRB",
                    5,
                    "2026-06-01",
                    json.dumps(["Example Robotics wins humanoid robot supplier contract"]),
                    json.dumps({
                        "ticker": "EXRB",
                        "domain": "humanoid_robotics",
                        "subdomain": "humanoid_actuators_reducers",
                        "sector": "humanoid actuators",
                        "thesis": "Supplier contract validates actuator bottleneck.",
                        "confidence": "high",
                        "reasoning": "Supplier contract is hard validation.",
                    }),
                    1,
                    "humanoid_robotics",
                    "humanoid actuators",
                    "Supplier contract validates actuator bottleneck.",
                    "flagged",
                ),
            )
        result = frontier_promotion.sync_shadow_receipts(as_of="2026-06-03")
        rows = thesis_receipts.list_receipts(source="frontier_discovery", limit=10)
        return result, rows

    result, rows = _with_temp_db(run)

    assert result["inserted"] == 1
    assert rows[0]["ticker"] == "EXRB"
    assert rows[0]["domain_id"] == "humanoid_robotics"
    assert rows[0]["first_price"] == 10
