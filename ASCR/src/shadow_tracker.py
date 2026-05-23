"""Shadow tracker for ascr — tracks B-tier and High priority candidates.

Tracks forward returns without buying. Answers:
- Do B-High stocks often rally in 20/60 days?
- What signals precede B → A/S upgrades?
- Should we allow small B-tier positions?
"""
from datetime import datetime, timedelta
from src import db, config
from src.utils import get_logger

logger = get_logger("shadow_tracker")


def init_shadow_table():
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT,
                ticker TEXT,
                rating TEXT,
                tracking_priority TEXT,
                evidence_score REAL,
                asymmetry_score REAL,
                momentum_score REAL,
                risk_score REAL,
                opportunity_score REAL,
                entry_reference_price REAL,
                return_5d REAL,
                return_10d REAL,
                return_20d REAL,
                return_60d REAL,
                max_gain_60d REAL,
                max_drawdown_60d REAL,
                later_upgraded_to TEXT,
                upgrade_date TEXT,
                sector TEXT,
                created_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_ticker_sr ON shadow_tracks(ticker)")

init_shadow_table()


def record_b_tier_shadows(scores: list):
    """Record B-tier and High tracking priority stocks for shadow tracking."""
    today = datetime.now().strftime("%Y-%m-%d")

    for s in scores:
        if s.get("rating") not in ("B",):
            continue

        ticker = s["ticker"]
        prices = db.get_prices(ticker, days=3)
        if not prices:
            continue

        price = prices[0]["close"]

        # Check if already tracked recently (within 7 days)
        with db.get_conn() as conn:
            existing = conn.execute("""
                SELECT 1 FROM shadow_tracks WHERE ticker=? AND signal_date > date(?, '-7 days')
            """, (ticker, today)).fetchone()
            if existing:
                continue

        import time
        with db.get_conn() as conn:
            conn.execute("""
                INSERT INTO shadow_tracks (signal_date, ticker, rating, tracking_priority,
                    evidence_score, asymmetry_score, momentum_score, risk_score, opportunity_score,
                    entry_reference_price, sector, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (today, ticker, s["rating"], s.get("tracking_priority", ""),
                  s.get("evidence", 0), s.get("asymmetry", 0),
                  s.get("momentum", 0), s.get("risk", 0),
                  s.get("opportunity", 0), price, "", time.time()))

    logger.info(f"Shadow tracking recorded for B-tier stocks")


def update_shadow_returns():
    """Update forward returns for all pending shadow tracks."""
    today = datetime.now()

    with db.get_conn() as conn:
        pending = conn.execute("SELECT * FROM shadow_tracks WHERE return_60d IS NULL").fetchall()

    for shadow in pending:
        shadow = dict(shadow)
        ticker = shadow["ticker"]
        entry_price = shadow["entry_reference_price"]
        signal_date = shadow["signal_date"]

        if not entry_price or entry_price <= 0:
            continue

        try:
            sig_dt = datetime.strptime(signal_date, "%Y-%m-%d")
        except ValueError:
            continue

        days_elapsed = (today - sig_dt).days
        prices = db.get_prices(ticker, days=min(days_elapsed + 10, 70))
        prices_since = sorted([p for p in prices if p["date"] >= signal_date], key=lambda x: x["date"])
        closes = [p["close"] for p in prices_since if p["close"] and p["close"] > 0]

        if not closes:
            continue

        updates = {}
        current = closes[-1]

        if len(closes) >= 5 and shadow.get("return_5d") is None and days_elapsed >= 5:
            updates["return_5d"] = (closes[min(4, len(closes)-1)] - entry_price) / entry_price * 100
        if len(closes) >= 10 and shadow.get("return_10d") is None and days_elapsed >= 10:
            updates["return_10d"] = (closes[min(9, len(closes)-1)] - entry_price) / entry_price * 100
        if len(closes) >= 20 and shadow.get("return_20d") is None and days_elapsed >= 20:
            updates["return_20d"] = (closes[min(19, len(closes)-1)] - entry_price) / entry_price * 100
        if days_elapsed >= 60 and shadow.get("return_60d") is None:
            updates["return_60d"] = (current - entry_price) / entry_price * 100
            updates["max_gain_60d"] = (max(closes) - entry_price) / entry_price * 100
            updates["max_drawdown_60d"] = (min(closes) - entry_price) / entry_price * 100

        # Check if later upgraded
        latest_scores = db.get_score_history(ticker, days=1)
        if latest_scores:
            new_rating = latest_scores[0].get("rating", "")
            if new_rating in ("S", "A") and shadow["rating"] == "B":
                updates["later_upgraded_to"] = new_rating
                updates["upgrade_date"] = today.strftime("%Y-%m-%d")

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [shadow["id"]]
            with db.get_conn() as conn:
                conn.execute(f"UPDATE shadow_tracks SET {set_clause} WHERE id=?", values)

    logger.info(f"Updated {len(pending)} shadow tracks")


def get_shadow_report() -> dict:
    """Generate shadow tracking summary."""
    with db.get_conn() as conn:
        completed = conn.execute("SELECT * FROM shadow_tracks WHERE return_20d IS NOT NULL").fetchall()
        upgraded = conn.execute("SELECT * FROM shadow_tracks WHERE later_upgraded_to IS NOT NULL").fetchall()

    completed = [dict(r) for r in completed]
    upgraded = [dict(r) for r in upgraded]

    if not completed:
        return {"message": "No completed shadow tracks yet"}

    returns_20d = [s["return_20d"] for s in completed if s["return_20d"] is not None]
    returns_60d = [s["return_60d"] for s in completed if s.get("return_60d") is not None]

    return {
        "total_tracked": len(completed),
        "avg_return_20d": sum(returns_20d) / len(returns_20d) if returns_20d else 0,
        "positive_20d_pct": sum(1 for r in returns_20d if r > 0) / len(returns_20d) * 100 if returns_20d else 0,
        "avg_return_60d": sum(returns_60d) / len(returns_60d) if returns_60d else 0,
        "upgrades": len(upgraded),
        "upgrade_tickers": [u["ticker"] for u in upgraded],
        "big_winners": [{"ticker": s["ticker"], "return_60d": s.get("return_60d", 0)}
                        for s in completed if s.get("return_60d", 0) and s["return_60d"] > 30],
    }
