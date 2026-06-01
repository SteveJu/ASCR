"""Thesis receipt tracking.

Receipts preserve the original thesis, source, validation path, and subsequent
price outcome. They are research accountability records only; they do not create
buy/sell recommendations.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Any

from src import db


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _json(value: Any) -> str:
    return json.dumps(value or [], sort_keys=True)


def _latest_price(conn, ticker: str) -> dict[str, Any] | None:
    row = conn.execute("""
        SELECT date, close FROM prices
        WHERE ticker = ?
        ORDER BY date DESC
        LIMIT 1
    """, (ticker,)).fetchone()
    return dict(row) if row else None


def _price_stats(conn, ticker: str, first_price: float, discovered_at: str | None) -> dict[str, Any]:
    if not first_price:
        return {}
    params: tuple[Any, ...]
    where = "ticker = ?"
    params = (ticker,)
    if discovered_at:
        where += " AND date >= ?"
        params = (ticker, discovered_at)

    rows = conn.execute(f"""
        SELECT date, close FROM prices
        WHERE {where}
        ORDER BY date ASC
    """, params).fetchall()
    if not rows:
        return {}

    closes = [float(row["close"]) for row in rows if row["close"] is not None]
    if not closes:
        return {}

    current = closes[-1]
    max_price = max(closes)
    min_price = min(closes)
    return {
        "current_price": current,
        "current_price_date": rows[-1]["date"],
        "max_gain_pct": round((max_price / first_price - 1) * 100, 2),
        "current_gain_pct": round((current / first_price - 1) * 100, 2),
        "max_drawdown_pct": round((min_price / first_price - 1) * 100, 2),
    }


def _upsert_receipt(conn, receipt: dict[str, Any]) -> str:
    """Insert or update a receipt without overwriting outcome fields."""
    db.ensure_thesis_receipts_table(conn)
    now = time.time()
    existing = conn.execute(
        "SELECT * FROM thesis_receipts WHERE receipt_key = ?",
        (receipt["receipt_key"],),
    ).fetchone()

    first_price = receipt.get("first_price")
    first_price_date = receipt.get("first_price_date")
    if existing:
        first_price = existing["first_price"] if existing["first_price"] is not None else first_price
        first_price_date = existing["first_price_date"] or first_price_date
        status = existing["status"] or receipt.get("status", "open")
        conn.execute("""
            UPDATE thesis_receipts
            SET source = ?, domain_id = ?, theme_id = ?, ticker = ?, company_name = ?,
                thesis = ?, status = ?, discovered_at = COALESCE(discovered_at, ?),
                first_price = ?, first_price_date = ?,
                validation_events_json = ?, validation_catalysts_json = ?,
                death_signals_json = ?, updated_at = ?
            WHERE receipt_key = ?
        """, (
            receipt.get("source", ""),
            receipt.get("domain_id", ""),
            receipt.get("theme_id", ""),
            receipt.get("ticker"),
            receipt.get("company_name", ""),
            receipt.get("thesis", ""),
            status,
            receipt.get("discovered_at"),
            first_price,
            first_price_date,
            _json(receipt.get("validation_events")),
            _json(receipt.get("validation_catalysts")),
            _json(receipt.get("death_signals")),
            now,
            receipt["receipt_key"],
        ))
        return "updated"

    conn.execute("""
        INSERT INTO thesis_receipts (
            receipt_key, source, domain_id, theme_id, ticker, company_name,
            thesis, status, discovered_at, first_price, first_price_date,
            validation_events_json, validation_catalysts_json, death_signals_json,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        receipt["receipt_key"],
        receipt.get("source", ""),
        receipt.get("domain_id", ""),
        receipt.get("theme_id", ""),
        receipt.get("ticker"),
        receipt.get("company_name", ""),
        receipt.get("thesis", ""),
        receipt.get("status", "open"),
        receipt.get("discovered_at"),
        first_price,
        first_price_date,
        _json(receipt.get("validation_events")),
        _json(receipt.get("validation_catalysts")),
        _json(receipt.get("death_signals")),
        now,
        now,
    ))
    return "inserted"


def sync_chokepoint_receipts(as_of: str | None = None) -> dict[str, int]:
    """Sync ticker-level receipts from chokepoints.yaml."""
    from src.chokepoint import flatten_candidates

    as_of = as_of or _today()
    inserted = updated = 0
    with db.get_conn() as conn:
        db.ensure_thesis_receipts_table(conn)
        for row in flatten_candidates():
            ticker = row.get("ticker")
            latest = _latest_price(conn, ticker) if ticker else None
            action = _upsert_receipt(conn, {
                "receipt_key": f"chokepoint:{row.get('theme_id')}:{ticker}",
                "source": "chokepoints",
                "domain_id": row.get("domain_id", "ai_infrastructure"),
                "theme_id": row.get("theme_id", ""),
                "ticker": ticker,
                "company_name": row.get("name", ""),
                "thesis": row.get("thesis", ""),
                "status": "open",
                "discovered_at": as_of,
                "first_price": latest.get("close") if latest else None,
                "first_price_date": latest.get("date") if latest else None,
                "validation_events": row.get("validation_events", []),
                "validation_catalysts": row.get("validation_catalysts", []),
                "death_signals": row.get("death_signals", []),
            })
            inserted += action == "inserted"
            updated += action == "updated"
    return {"inserted": inserted, "updated": updated}


def sync_frontier_domain_receipts(as_of: str | None = None) -> dict[str, int]:
    """Sync domain-level receipts from frontier_domains.yaml."""
    from src.frontier_domains import domains

    as_of = as_of or _today()
    inserted = updated = 0
    with db.get_conn() as conn:
        db.ensure_thesis_receipts_table(conn)
        for domain in domains():
            action = _upsert_receipt(conn, {
                "receipt_key": f"frontier_domain:{domain['id']}",
                "source": "frontier_domains",
                "domain_id": domain["id"],
                "theme_id": domain["id"],
                "ticker": None,
                "company_name": domain.get("name", ""),
                "thesis": domain.get("thesis", ""),
                "status": "open",
                "discovered_at": as_of,
                "validation_events": domain.get("validation_signals", []),
                "validation_catalysts": domain.get("early_signal_queries", []),
                "death_signals": domain.get("death_signals", []),
            })
            inserted += action == "inserted"
            updated += action == "updated"
    return {"inserted": inserted, "updated": updated}


def sync_all(as_of: str | None = None) -> dict[str, dict[str, int]]:
    """Sync all configured thesis sources."""
    return {
        "chokepoints": sync_chokepoint_receipts(as_of=as_of),
        "frontier_domains": sync_frontier_domain_receipts(as_of=as_of),
    }


def refresh_price_outcomes() -> dict[str, int]:
    """Refresh outcome fields from locally stored price history."""
    updated = skipped = 0
    with db.get_conn() as conn:
        db.ensure_thesis_receipts_table(conn)
        rows = conn.execute("""
            SELECT * FROM thesis_receipts
            WHERE ticker IS NOT NULL AND ticker != ''
        """).fetchall()
        for row in rows:
            ticker = row["ticker"]
            first_price = row["first_price"]
            first_price_date = row["first_price_date"]
            if first_price is None:
                latest = _latest_price(conn, ticker)
                if not latest:
                    skipped += 1
                    continue
                first_price = latest["close"]
                first_price_date = latest["date"]

            stats = _price_stats(conn, ticker, float(first_price), row["discovered_at"])
            if not stats:
                skipped += 1
                continue
            conn.execute("""
                UPDATE thesis_receipts
                SET first_price = ?, first_price_date = ?, current_price = ?,
                    current_price_date = ?, max_gain_pct = ?, current_gain_pct = ?,
                    max_drawdown_pct = ?, updated_at = ?
                WHERE receipt_key = ?
            """, (
                first_price,
                first_price_date,
                stats.get("current_price"),
                stats.get("current_price_date"),
                stats.get("max_gain_pct"),
                stats.get("current_gain_pct"),
                stats.get("max_drawdown_pct"),
                time.time(),
                row["receipt_key"],
            ))
            updated += 1
    return {"updated": updated, "skipped": skipped}


def list_receipts(status: str | None = None, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List receipts for reporting."""
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if source:
        clauses.append("source = ?")
        params.append(source)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)

    with db.get_conn() as conn:
        db.ensure_thesis_receipts_table(conn)
        rows = conn.execute(f"""
            SELECT * FROM thesis_receipts
            {where}
            ORDER BY
              CASE WHEN current_gain_pct IS NULL THEN -999 ELSE current_gain_pct END DESC,
              updated_at DESC
            LIMIT ?
        """, params).fetchall()
    return [dict(row) for row in rows]


def receipt_tickers(status: str = "open", source: str | None = "chokepoints") -> list[str]:
    """Return distinct ticker receipts for watch-only price tracking."""
    clauses = ["ticker IS NOT NULL", "ticker != ''"]
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if source:
        clauses.append("source = ?")
        params.append(source)

    with db.get_conn() as conn:
        db.ensure_thesis_receipts_table(conn)
        rows = conn.execute(f"""
            SELECT DISTINCT ticker FROM thesis_receipts
            WHERE {' AND '.join(clauses)}
            ORDER BY ticker
        """, params).fetchall()
    return [row["ticker"] for row in rows]


def bootstrap_receipt_prices(period: str = "5d", limit: int | None = None) -> dict[str, Any]:
    """Fetch watch-only receipt ticker prices and refresh outcome stats."""
    tickers = receipt_tickers()
    if limit:
        tickers = tickers[:limit]
    if not tickers:
        return {"tickers": 0, "updated": 0, "skipped": 0}

    from src.price_fetcher import fetch_prices

    fetch_prices(tickers, period=period)
    refreshed = refresh_price_outcomes()
    return {"tickers": len(tickers), **refreshed}


def render_report(receipts: list[dict[str, Any]], limit: int | None = None) -> str:
    """Render a thesis receipt report."""
    rows = receipts[:limit] if limit else receipts
    lines = [
        "Thesis Receipt Report",
        "Policy: research accountability only; no direct trading integration.",
        "",
    ]
    if not rows:
        lines.append("No thesis receipts found.")
        return "\n".join(lines)

    for idx, row in enumerate(rows, start=1):
        ticker = row.get("ticker") or "-"
        gain = row.get("current_gain_pct")
        gain_text = "n/a" if gain is None else f"{gain:+.1f}%"
        lines.append(
            f"{idx:>2}. {ticker:<8} {row.get('source', ''):<16} "
            f"domain={row.get('domain_id') or '-'} status={row.get('status') or '-'} gain={gain_text}"
        )
        lines.append(f"    Thesis: {row.get('thesis') or ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Thesis receipt tracking")
    parser.add_argument("--sync", action="store_true", help="Sync receipts from configured sources")
    parser.add_argument("--refresh-prices", action="store_true", help="Refresh local price outcome stats")
    parser.add_argument("--bootstrap-prices", action="store_true", help="Fetch watch-only receipt ticker prices")
    parser.add_argument("--period", default="5d", help="Price period for --bootstrap-prices")
    parser.add_argument("--price-limit", type=int, help="Optional ticker cap for --bootstrap-prices")
    parser.add_argument("--report", action="store_true", help="Render receipt report")
    parser.add_argument("--status", help="Filter report by status")
    parser.add_argument("--source", help="Filter report by source")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="Render report rows as JSON")
    args = parser.parse_args(argv)

    if args.sync:
        print(json.dumps(sync_all(), indent=2, sort_keys=True))
    if args.refresh_prices:
        print(json.dumps(refresh_price_outcomes(), indent=2, sort_keys=True))
    if args.bootstrap_prices:
        print(json.dumps(bootstrap_receipt_prices(period=args.period, limit=args.price_limit), indent=2, sort_keys=True))

    if args.report or not (args.sync or args.refresh_prices or args.bootstrap_prices):
        rows = list_receipts(status=args.status, source=args.source, limit=args.limit)
        if args.json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            print(render_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
