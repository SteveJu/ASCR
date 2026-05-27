"""Reconcile position snapshots against the order ledger."""
from src import config, db


EPS = 1e-6


def _load_orders():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM paper_orders ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _load_positions():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM paper_positions ORDER BY ticker").fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def _ledger_from_orders(orders):
    ledger = {}
    cash_delta = 0.0
    issues = []

    for order in orders:
        ticker = order["ticker"]
        side = (order["side"] or "").upper()
        qty = float(order["quantity"] or 0)
        price = float(order["price"] or 0)
        notional = qty * price

        pos = ledger.setdefault(ticker, {
            "ticker": ticker,
            "quantity": 0.0,
            "cost_basis": 0.0,
            "realized_pnl": 0.0,
            "entry_date": None,
            "avg_entry_price": 0.0,
            "status": "closed",
        })

        if side == "BUY":
            if pos["quantity"] <= EPS:
                pos["entry_date"] = order["date"]
            pos["quantity"] += qty
            pos["cost_basis"] += notional
            pos["avg_entry_price"] = (
                pos["cost_basis"] / pos["quantity"] if pos["quantity"] > EPS else 0.0
            )
            pos["status"] = "open"
            cash_delta -= notional
        elif side == "SELL":
            cash_delta += notional
            if pos["quantity"] <= EPS:
                issues.append(f"{ticker}: SELL {qty:.6f} with no ledger position")
                continue

            avg_entry = pos["cost_basis"] / pos["quantity"]
            sell_qty = min(qty, pos["quantity"])
            if qty - pos["quantity"] > EPS:
                issues.append(
                    f"{ticker}: SELL {qty:.6f} exceeds ledger qty {pos['quantity']:.6f}"
                )

            pos["realized_pnl"] += (price - avg_entry) * sell_qty
            pos["quantity"] -= sell_qty
            pos["cost_basis"] -= avg_entry * sell_qty
            if pos["quantity"] <= EPS:
                pos["quantity"] = 0.0
                pos["cost_basis"] = 0.0
                pos["avg_entry_price"] = 0.0
                pos["status"] = "closed"
            else:
                pos["avg_entry_price"] = pos["cost_basis"] / pos["quantity"]
                pos["status"] = "open"
        else:
            issues.append(f"{ticker}: unknown side {side!r}")

    return ledger, cash_delta, issues


def audit_positions(initial_cash=None):
    """Return reconciliation data for current DB state."""
    if initial_cash is None:
        try:
            initial_cash = config.load().get("initial_cash", 10000)
        except FileNotFoundError:
            initial_cash = 10000
    orders = _load_orders()
    positions = _load_positions()
    ledger, cash_delta, issues = _ledger_from_orders(orders)
    account = db.get_account() or {"cash": 0}

    mismatches = []
    for ticker in sorted(set(positions) | set(ledger)):
        recorded = positions.get(ticker, {})
        expected = ledger.get(ticker, {
            "ticker": ticker,
            "quantity": 0.0,
            "cost_basis": 0.0,
            "realized_pnl": 0.0,
            "status": "closed",
        })

        rec_qty = float(recorded.get("quantity") or 0)
        exp_qty = float(expected.get("quantity") or 0)
        rec_cost = float(recorded.get("cost_basis") or 0)
        exp_cost = float(expected.get("cost_basis") or 0)
        rec_realized = float(recorded.get("realized_pnl") or 0)
        exp_realized = float(expected.get("realized_pnl") or 0)

        qty_diff = rec_qty - exp_qty
        cost_diff = rec_cost - exp_cost
        realized_diff = rec_realized - exp_realized

        has_mismatch = abs(qty_diff) > EPS or abs(realized_diff) > 0.01
        if exp_qty > EPS or rec_qty > EPS:
            has_mismatch = has_mismatch or abs(cost_diff) > 0.01

        if has_mismatch:
            mismatches.append({
                "ticker": ticker,
                "recorded_status": recorded.get("status", "missing"),
                "expected_status": expected.get("status", "closed"),
                "recorded_qty": rec_qty,
                "expected_qty": exp_qty,
                "qty_diff": qty_diff,
                "recorded_cost": rec_cost,
                "expected_cost": exp_cost,
                "cost_diff": cost_diff,
                "recorded_realized": rec_realized,
                "expected_realized": exp_realized,
                "realized_diff": realized_diff,
            })

    expected_cash = initial_cash + cash_delta
    return {
        "orders_count": len(orders),
        "positions_count": len(positions),
        "recorded_cash": float(account.get("cash") or 0),
        "expected_cash": expected_cash,
        "cash_diff": float(account.get("cash") or 0) - expected_cash,
        "mismatches": mismatches,
        "issues": issues,
    }


def format_audit_report(result):
    lines = [
        "Position Audit",
        f"orders={result['orders_count']} positions={result['positions_count']}",
        (
            f"cash recorded=${result['recorded_cash']:,.2f} "
            f"ledger=${result['expected_cash']:,.2f} "
            f"diff=${result['cash_diff']:+,.2f}"
        ),
    ]

    if result["issues"]:
        lines.append("")
        lines.append("Ledger issues:")
        for issue in result["issues"][:20]:
            lines.append(f"- {issue}")

    if result["mismatches"]:
        lines.append("")
        lines.append("Position mismatches:")
        for m in result["mismatches"][:50]:
            lines.append(
                "- {ticker}: qty {recorded_qty:.6f} vs {expected_qty:.6f} "
                "(diff {qty_diff:+.6f}), cost ${recorded_cost:,.2f} vs "
                "${expected_cost:,.2f}, realized ${recorded_realized:+,.2f} vs "
                "${expected_realized:+,.2f}".format(**m)
            )
    else:
        lines.append("")
        lines.append("No position mismatches.")

    return "\n".join(lines)
