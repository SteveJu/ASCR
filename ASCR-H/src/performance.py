"""Performance analytics — compute trading statistics."""
from src import config, db
from src.utils import get_logger

logger = get_logger("performance")


def compute_stats() -> dict:
    """Compute full performance report."""
    account = db.get_account()
    if not account:
        return {"error": "no account"}

    initial = config.load().get("initial_cash", 10000)
    orders = db.get_orders()
    open_pos = db.get_all_positions("open")
    closed_pos = db.get_all_positions("closed")

    # Basic stats
    total_equity = account["cash"]
    for p in open_pos:
        total_equity += p.get("current_value", 0)

    total_return = (total_equity - initial) / initial * 100
    peak = account.get("peak_equity", total_equity)
    max_drawdown = (total_equity - peak) / peak * 100 if peak > 0 else 0

    # Win/loss from closed positions
    wins = [p for p in closed_pos if p.get("realized_pnl", 0) > 0]
    losses = [p for p in closed_pos if p.get("realized_pnl", 0) <= 0]
    win_rate = len(wins) / max(len(closed_pos), 1) * 100

    avg_win = sum(p["realized_pnl"] for p in wins) / max(len(wins), 1)
    avg_loss = sum(p["realized_pnl"] for p in losses) / max(len(losses), 1)
    total_wins = sum(p["realized_pnl"] for p in wins)
    total_losses = abs(sum(p["realized_pnl"] for p in losses))
    profit_factor = total_wins / max(total_losses, 1)

    # Holding days (from orders)
    buy_orders = [o for o in orders if o["side"] == "BUY"]
    sell_orders = [o for o in orders if o["side"] == "SELL"]

    # By rating
    by_rating = {}
    for p in closed_pos:
        r = p.get("rating_at_entry", "?")
        if r not in by_rating:
            by_rating[r] = {"count": 0, "total_pnl": 0, "wins": 0}
        by_rating[r]["count"] += 1
        by_rating[r]["total_pnl"] += p.get("realized_pnl", 0)
        if p.get("realized_pnl", 0) > 0:
            by_rating[r]["wins"] += 1

    # Shadow tracking stats
    shadows = db.get_pending_shadows(max_days=999)
    all_shadows = []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM shadow_tracks WHERE return_20d IS NOT NULL").fetchall()
        all_shadows = [dict(r) for r in rows]

    shadow_avg_20d = 0
    shadow_positive_20d = 0
    if all_shadows:
        returns_20d = [s["return_20d"] for s in all_shadows if s["return_20d"] is not None]
        if returns_20d:
            shadow_avg_20d = sum(returns_20d) / len(returns_20d)
            shadow_positive_20d = sum(1 for r in returns_20d if r > 0) / len(returns_20d) * 100

    # Top winners/losers
    all_closed_sorted = sorted(closed_pos, key=lambda p: p.get("realized_pnl", 0), reverse=True)
    top_winners = all_closed_sorted[:5]
    top_losers = all_closed_sorted[-5:] if len(all_closed_sorted) > 5 else []

    return {
        "total_equity": total_equity,
        "initial_cash": initial,
        "total_return_pct": total_return,
        "max_drawdown_pct": max_drawdown,
        "peak_equity": peak,
        "cash": account["cash"],
        "num_open": len(open_pos),
        "num_closed": len(closed_pos),
        "num_orders": len(orders),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "by_rating": by_rating,
        "shadow_avg_20d": shadow_avg_20d,
        "shadow_positive_20d_pct": shadow_positive_20d,
        "shadow_total": len(all_shadows),
        "top_winners": top_winners,
        "top_losers": top_losers,
    }
