"""Frontier promotion gate and shadow tracking.

This module turns discovery anomalies into review decisions. It does not add
tickers to the trading universe and does not create buy recommendations.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from src import config, db


HARD_EVIDENCE_KEYWORDS = {
    "award",
    "awarded",
    "backlog",
    "capacity expansion",
    "certification",
    "certified",
    "contract",
    "customer",
    "defense",
    "delivery",
    "deployment",
    "funded",
    "government",
    "launch",
    "milestone",
    "order",
    "paid",
    "prepayment",
    "production",
    "purchase",
    "ramp",
    "revenue",
    "shipment",
    "shipments",
    "signed",
    "supplier",
}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _safe_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _gate() -> dict[str, Any]:
    try:
        from src.frontier_domains import promotion_gate

        return promotion_gate()
    except Exception:
        return {
            "min_promotion_score": 75,
            "min_watch_score": 55,
            "min_mentions": 3,
            "eligible_confidence": ["high", "medium"],
            "require_ticker": True,
            "require_hard_evidence": True,
            "shadow_source": "frontier_discovery",
        }


def _domain_death_signals(domain_id: str | None) -> list[str]:
    if not domain_id:
        return []
    try:
        from src.frontier_domains import domains

        for domain in domains():
            if domain.get("id") == domain_id:
                return domain.get("death_signals", [])
    except Exception:
        pass
    return []


def _confidence_score(confidence: str) -> int:
    confidence = (confidence or "").lower()
    if confidence == "high":
        return 20
    if confidence == "medium":
        return 12
    if confidence == "low":
        return 5
    return 0


def _text_has_hard_evidence(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in HARD_EVIDENCE_KEYWORDS)


def _known_domain(domain_id: str | None) -> bool:
    if not domain_id:
        return False
    try:
        from src.frontier_domains import domain_ids

        return domain_id in domain_ids()
    except Exception:
        return False


def evaluate_anomaly(row: dict[str, Any], gate: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score one discovery anomaly for promotion review or shadow watch."""
    gate = gate or _gate()
    assessment = _safe_json(row.get("llm_assessment"), {})
    headlines = _safe_json(row.get("headlines"), [])
    if isinstance(headlines, str):
        headlines = [headlines]

    ticker = (row.get("ticker_guess") or assessment.get("ticker") or "").upper()
    domain = row.get("domain_guess") or assessment.get("domain")
    subdomain = assessment.get("subdomain") or assessment.get("subdomain_id")
    sector = row.get("sector_guess") or assessment.get("sector")
    confidence = (assessment.get("confidence") or "").lower()
    mentions = int(row.get("mention_count") or 0)
    is_real = bool(row.get("is_real_signal"))
    text = " ".join(str(part) for part in [
        row.get("company_name", ""),
        row.get("thesis", ""),
        assessment.get("reasoning", ""),
        assessment.get("thesis", ""),
        " ".join(headlines),
    ])

    has_hard_evidence = _text_has_hard_evidence(text)
    reasons: list[str] = []
    score = 0

    if is_real:
        score += 25
        reasons.append("llm_real_signal")
    if ticker:
        score += 15
        reasons.append("public_ticker")
    conf_score = _confidence_score(confidence)
    if conf_score:
        score += conf_score
        reasons.append(f"confidence_{confidence}")
    if mentions >= int(gate.get("min_mentions", 3)):
        score += 10
        reasons.append(f"mentions_{mentions}")
    if mentions >= 5:
        score += 5
        reasons.append("mention_cluster")
    if _known_domain(domain):
        score += 10
        reasons.append(f"known_domain_{domain}")
    if subdomain or sector:
        score += 5
        reasons.append("specific_bottleneck")
    if has_hard_evidence:
        score += 20
        reasons.append("hard_validation_language")

    eligible_confidence = {str(c).lower() for c in gate.get("eligible_confidence", [])}
    ticker_ok = bool(ticker) or not gate.get("require_ticker", True)
    evidence_ok = has_hard_evidence or not gate.get("require_hard_evidence", True)
    confidence_ok = confidence in eligible_confidence

    if not is_real:
        decision = "dismissed"
    elif (
        score >= int(gate.get("min_promotion_score", 75))
        and ticker_ok
        and evidence_ok
        and confidence_ok
    ):
        decision = "promotion_review"
    elif score >= int(gate.get("min_watch_score", 55)) and ticker:
        decision = "shadow_watch"
    else:
        decision = "monitor"

    return {
        "id": row.get("id"),
        "company_name": row.get("company_name", ""),
        "ticker": ticker or None,
        "domain": domain,
        "subdomain": subdomain,
        "sector": sector,
        "thesis": row.get("thesis") or assessment.get("thesis"),
        "confidence": confidence or None,
        "mentions": mentions,
        "score": min(score, 100),
        "decision": decision,
        "has_hard_evidence": has_hard_evidence,
        "reasons": reasons,
        "headlines": headlines[:5],
    }


def evaluate_candidates(status: str = "flagged", limit: int = 25) -> list[dict[str, Any]]:
    """Evaluate recent discovery anomalies with the promotion gate."""
    from src import sector_discovery

    sector_discovery.init_discovery_db()
    gate = _gate()
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE status = ?"
        params.append(status)
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT * FROM discovery_anomalies
            {where}
            ORDER BY detected_date DESC, id DESC
            LIMIT ?
        """, params).fetchall()

    return [evaluate_anomaly(dict(row), gate=gate) for row in rows]


def sync_shadow_receipts(as_of: str | None = None, limit: int = 50) -> dict[str, int]:
    """Create watch-only thesis receipts for promotable frontier anomalies."""
    from src import thesis_receipts

    as_of = as_of or _today()
    source = str(_gate().get("shadow_source", "frontier_discovery"))
    inserted = updated = skipped = 0
    candidates = evaluate_candidates(limit=limit)
    with db.get_conn() as conn:
        db.ensure_thesis_receipts_table(conn)
        for candidate in candidates:
            if candidate["decision"] not in {"promotion_review", "shadow_watch"}:
                skipped += 1
                continue
            ticker = candidate.get("ticker")
            if not ticker:
                skipped += 1
                continue
            latest = thesis_receipts._latest_price(conn, ticker)
            action = thesis_receipts._upsert_receipt(conn, {
                "receipt_key": f"{source}:{candidate['id']}:{ticker}",
                "source": source,
                "domain_id": candidate.get("domain") or "",
                "theme_id": candidate.get("subdomain") or candidate.get("sector") or "",
                "ticker": ticker,
                "company_name": candidate.get("company_name", ""),
                "thesis": candidate.get("thesis") or "",
                "status": "open",
                "discovered_at": as_of,
                "first_price": latest.get("close") if latest else None,
                "first_price_date": latest.get("date") if latest else None,
                "validation_events": candidate.get("headlines", []),
                "validation_catalysts": candidate.get("reasons", []),
                "death_signals": _domain_death_signals(candidate.get("domain")),
            })
            inserted += action == "inserted"
            updated += action == "updated"
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def render_report(candidates: list[dict[str, Any]] | None = None, limit: int = 12) -> str:
    """Render promotion gate decisions."""
    rows = candidates if candidates is not None else evaluate_candidates(limit=limit)
    lines = [
        "Frontier Promotion Gate",
        "Policy: review and shadow only; no automatic universe promotion.",
        "",
    ]
    if not rows:
        lines.append("No flagged frontier anomalies.")
        return "\n".join(lines)

    for row in rows[:limit]:
        ticker = f" ({row['ticker']})" if row.get("ticker") else ""
        lines.append(
            f"- {row['decision']}: {row['company_name']}{ticker} "
            f"score={row['score']} domain={row.get('domain') or '-'}"
        )
        if row.get("subdomain"):
            lines.append(f"  Subdomain: {row['subdomain']}")
        if row.get("thesis"):
            lines.append(f"  Thesis: {row['thesis']}")
        lines.append(f"  Reasons: {', '.join(row.get('reasons', [])) or '-'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frontier promotion gate")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--sync-shadow", action="store_true")
    args = parser.parse_args(argv)

    if args.sync_shadow:
        payload: Any = sync_shadow_receipts(limit=args.limit)
    else:
        payload = evaluate_candidates(limit=args.limit)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if isinstance(payload, list):
            print(render_report(payload, limit=args.limit))
        else:
            print(f"Frontier shadow receipts: {payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
