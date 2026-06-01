"""Chokepoint intelligence watchlist.

This module tracks public chokepoint research ideas as discovery inputs. It is
deliberately watch-only and does not feed buy/sell decisions.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

import yaml

from src import config

POSITIVE_COMPONENTS = ("scarcity", "dependency", "concentration", "validation", "timing")
NEGATIVE_COMPONENTS = ("crowding_risk",)
DEFAULT_PHASES = ("early", "front_run", "consensus", "crowded", "broken")


def load_chokepoints(path: str | None = None) -> dict[str, Any]:
    """Load chokepoint config from the default config path or an explicit path."""
    if path:
        with open(path) as f:
            data = yaml.safe_load(f)
    else:
        data = config.chokepoints()
    if not isinstance(data, dict):
        raise ValueError("chokepoints config must be a mapping")
    return data


def _component(candidate: dict[str, Any], name: str, max_component: int) -> int:
    score = candidate.get("score", {})
    value = score.get(name, 0)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{candidate.get('ticker', '?')} score.{name} must be numeric")
    if value < 0 or value > max_component:
        raise ValueError(
            f"{candidate.get('ticker', '?')} score.{name}={value} outside 0..{max_component}"
        )
    return int(value)


def compute_score(candidate: dict[str, Any], max_component: int = 20) -> dict[str, Any]:
    """Compute a 0-100 chokepoint score for one candidate."""
    positive = sum(_component(candidate, name, max_component) for name in POSITIVE_COMPONENTS)
    negative = sum(_component(candidate, name, max_component) for name in NEGATIVE_COMPONENTS)
    raw = positive - negative
    score = max(0, min(100, raw))

    if score >= 70:
        tier = "high"
    elif score >= 50:
        tier = "medium"
    else:
        tier = "low"

    return {
        "score": score,
        "positive": positive,
        "negative": negative,
        "tier": tier,
        "crowding_risk": _component(candidate, "crowding_risk", max_component),
    }


def flatten_candidates(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return scored candidates with inherited theme metadata."""
    data = data or load_chokepoints()
    score_model = data.get("score_model", {})
    max_component = int(score_model.get("max_component", 20))
    phases = set((data.get("rotation_phases") or {}).keys()) or set(DEFAULT_PHASES)

    rows: list[dict[str, Any]] = []
    for theme in data.get("themes", []):
        phase = theme.get("phase", "early")
        if phase not in phases:
            raise ValueError(f"{theme.get('id', '?')} has unknown phase: {phase}")

        for candidate in theme.get("candidates", []):
            if not candidate.get("ticker"):
                raise ValueError(f"{theme.get('id', '?')} has candidate without ticker")
            score = compute_score(candidate, max_component=max_component)
            rows.append({
                "ticker": candidate["ticker"],
                "name": candidate.get("name", candidate["ticker"]),
                "market": candidate.get("market", ""),
                "status": candidate.get("status", "watch_only"),
                "theme_id": theme.get("id", ""),
                "theme": theme.get("name", ""),
                "phase": phase,
                "thesis": theme.get("thesis", ""),
                "chokepoint_pattern": theme.get("chokepoint_pattern", ""),
                "validation_events": theme.get("validation_events", []),
                "death_signals": theme.get("death_signals", []),
                "validation_catalysts": candidate.get("validation_catalysts", []),
                "source_tags": candidate.get("source_tags", []),
                **score,
            })

    return sorted(rows, key=lambda row: (row["score"], -row["crowding_risk"], row["ticker"]), reverse=True)


def filter_candidates(
    candidates: list[dict[str, Any]],
    phase: str | None = None,
    theme: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Filter flattened candidates for reporting."""
    rows = candidates
    if phase:
        rows = [row for row in rows if row["phase"] == phase]
    if theme:
        rows = [row for row in rows if row["theme_id"] == theme]
    if limit:
        rows = rows[:limit]
    return rows


def render_report(
    candidates: list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
    limit: int | None = None,
) -> str:
    """Render a text report for terminal or Telegram reuse."""
    generated_at = generated_at or datetime.now()
    rows = candidates[:limit] if limit else candidates

    lines = [
        "Chokepoint Intelligence Report",
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "Policy: watch-only discovery input; no direct trading integration.",
        "",
    ]
    if not rows:
        lines.append("No chokepoint candidates matched the filters.")
        return "\n".join(lines)

    for idx, row in enumerate(rows, start=1):
        catalysts = "; ".join(row.get("validation_catalysts", [])[:2]) or "No catalyst listed"
        lines.append(
            f"{idx:>2}. {row['ticker']:<8} score={row['score']:>3} "
            f"tier={row['tier']:<6} phase={row['phase']:<10} theme={row['theme']}"
        )
        lines.append(f"    Thesis: {row['thesis']}")
        lines.append(f"    Validate: {catalysts}")
        if row.get("crowding_risk", 0) >= 12:
            lines.append("    Risk: social/valuation crowding is already elevated.")
        lines.append("")

    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chokepoint intelligence watchlist report")
    parser.add_argument("--config", help="Optional chokepoints YAML path")
    parser.add_argument("--report", action="store_true", help="Render text report")
    parser.add_argument("--json", action="store_true", help="Render scored candidates as JSON")
    parser.add_argument("--phase", choices=DEFAULT_PHASES, help="Filter by rotation phase")
    parser.add_argument("--theme", help="Filter by theme id")
    parser.add_argument("--limit", type=int, default=20, help="Maximum candidates to show")
    args = parser.parse_args(argv)

    data = load_chokepoints(args.config)
    rows = filter_candidates(
        flatten_candidates(data),
        phase=args.phase,
        theme=args.theme,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        print(render_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
