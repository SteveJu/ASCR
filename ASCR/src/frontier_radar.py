"""Frontier radar.

Standalone reporting layer for early discovery across frontier domains:
humanoid robotics, robotics automation, commercial space, quantum computing,
frontier energy, and AI infrastructure.
"""
from __future__ import annotations

import argparse
import json
from typing import Any


def _domain_rows() -> list[dict[str, Any]]:
    from src.frontier_domains import domains

    rows = []
    for domain in domains():
        rows.append({
            "id": domain["id"],
            "name": domain["name"],
            "query_count": len(domain.get("early_signal_queries", [])),
            "bottleneck_count": len(domain.get("bottlenecks", [])),
            "goal": domain.get("discovery_goal", ""),
        })
    return rows


def _discovery_status() -> dict[str, Any]:
    from src.sector_discovery import get_discovery_status

    return get_discovery_status()


def _receipt_rows(limit: int) -> list[dict[str, Any]]:
    from src.thesis_receipts import list_receipts

    return list_receipts(limit=limit)


def build_payload(limit: int = 12) -> dict[str, Any]:
    """Build a structured frontier radar payload."""
    status = _discovery_status()
    receipts = _receipt_rows(limit)
    return {
        "policy": "discovery_only_watch_only",
        "domains": _domain_rows(),
        "discovery": {
            "flagged": status.get("flagged", []),
            "top_domains": status.get("top_domains", []),
            "top_mentions": status.get("top_mentions", []),
            "total_scans": status.get("total_scans", 0),
            "total_companies": status.get("total_companies", 0),
            "total_anomalies": status.get("total_anomalies", 0),
            "real_signals": status.get("real_signals", 0),
        },
        "receipts": receipts,
    }


def render_report(payload: dict[str, Any], limit: int = 12) -> str:
    """Render a standalone frontier radar report."""
    lines = [
        "Frontier Radar",
        "Policy: discovery-only and watch-only; no direct trading integration.",
        "",
        "Domains",
    ]

    for domain in payload.get("domains", []):
        lines.append(
            f"- {domain['id']}: {domain['name']} "
            f"({domain['query_count']} queries, {domain['bottleneck_count']} bottlenecks)"
        )

    discovery = payload.get("discovery", {})
    lines.extend([
        "",
        "Discovery Status",
        f"- Scan days: {discovery.get('total_scans', 0)}",
        f"- Companies tracked: {discovery.get('total_companies', 0)}",
        f"- Anomalies: {discovery.get('total_anomalies', 0)}",
        f"- Real signals: {discovery.get('real_signals', 0)}",
    ])

    top_domains = discovery.get("top_domains", [])
    if top_domains:
        lines.append("- Top domains: " + ", ".join(
            f"{row['domain']} ({row['count']})" for row in top_domains[:5]
        ))

    flagged = discovery.get("flagged", [])
    if flagged:
        lines.extend(["", "Pending Review"])
        for row in flagged[:limit]:
            ticker = f" ({row['ticker_guess']})" if row.get("ticker_guess") else ""
            domain = row.get("domain_guess") or row.get("sector_guess") or "unknown"
            lines.append(f"- {row['company_name']}{ticker}: {domain} - {row.get('thesis') or '?'}")

    receipts = payload.get("receipts", [])
    lines.extend(["", "Thesis Receipts"])
    if receipts:
        for row in receipts[:limit]:
            ticker = row.get("ticker") or "-"
            gain = row.get("current_gain_pct")
            gain_text = "n/a" if gain is None else f"{gain:+.1f}%"
            lines.append(
                f"- {ticker}: {row.get('source')} / {row.get('domain_id') or '-'} "
                f"status={row.get('status')} gain={gain_text}"
            )
    else:
        lines.append("- No receipts yet. Run `python3 -m src.thesis_receipts --sync`.")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frontier radar report")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--json", action="store_true", help="Render structured payload")
    args = parser.parse_args(argv)

    payload = build_payload(limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_report(payload, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
