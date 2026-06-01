"""Frontier domain discovery configuration.

The goal is to make early discovery domain-general: AI infrastructure, humanoid
robots, robotics, commercial space, quantum computing, and future hard-tech
themes all expose the same query and signal structure.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

import yaml

from src import config


def load_frontier_domains(path: str | None = None) -> dict[str, Any]:
    """Load frontier domain config from default config or explicit path."""
    if path:
        with open(path) as f:
            data = yaml.safe_load(f)
    else:
        data = config.frontier_domains()
    if not isinstance(data, dict):
        raise ValueError("frontier_domains config must be a mapping")
    return data


def domains(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return domain records after basic validation."""
    data = data or load_frontier_domains()
    result = data.get("domains", [])
    if not isinstance(result, list):
        raise ValueError("frontier_domains.domains must be a list")
    for domain in result:
        if not domain.get("id") or not domain.get("name"):
            raise ValueError("each frontier domain needs id and name")
    return result


def domain_ids(data: dict[str, Any] | None = None) -> set[str]:
    """Return configured domain ids."""
    return {domain["id"] for domain in domains(data)}


def discovery_query_specs(
    data: dict[str, Any] | None = None,
    domain_id: str | None = None,
) -> list[dict[str, str]]:
    """Flatten domain queries into query specs with domain metadata."""
    specs: list[dict[str, str]] = []
    for domain in domains(data):
        if domain_id and domain["id"] != domain_id:
            continue
        for query in domain.get("early_signal_queries", []):
            specs.append({
                "query": str(query),
                "domain_id": domain["id"],
                "domain_name": domain["name"],
            })
    return specs


def prompt_context(data: dict[str, Any] | None = None) -> str:
    """Render a compact domain taxonomy for LLM anomaly classification."""
    lines = []
    for domain in domains(data):
        bottlenecks = ", ".join(domain.get("bottlenecks", [])[:5])
        validation = ", ".join(domain.get("validation_signals", [])[:3])
        lines.append(
            f"- {domain['id']} ({domain['name']}): {domain.get('thesis', '')} "
            f"Watch bottlenecks: {bottlenecks}. Validate with: {validation}."
        )
    return "\n".join(lines)


def render_report(data: dict[str, Any] | None = None) -> str:
    """Render configured frontier domains and query counts."""
    rows = domains(data)
    lines = [
        "Frontier Domain Discovery Map",
        "Policy: discovery-only; no direct trading integration.",
        "",
    ]
    for domain in rows:
        query_count = len(domain.get("early_signal_queries", []))
        bottleneck_count = len(domain.get("bottlenecks", []))
        lines.append(f"- {domain['id']}: {domain['name']}")
        lines.append(f"  Queries: {query_count} | Bottlenecks: {bottleneck_count}")
        lines.append(f"  Goal: {domain.get('discovery_goal', '')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frontier domain discovery map")
    parser.add_argument("--config", help="Optional frontier domains YAML path")
    parser.add_argument("--json", action="store_true", help="Render domains as JSON")
    parser.add_argument("--queries", action="store_true", help="Render flattened discovery query specs")
    parser.add_argument("--domain", help="Filter query specs by domain id")
    args = parser.parse_args(argv)

    data = load_frontier_domains(args.config)
    if args.queries:
        payload = discovery_query_specs(data, domain_id=args.domain)
    else:
        payload = domains(data)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
