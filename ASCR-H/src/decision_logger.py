"""Decision logger — records every BUY/SELL/TRIM/HOLD/NO_BUY decision with full context.

Every decision gets a unique ID and captures the complete state at decision time:
scores, prices, benchmarks, reasons, mode (historical vs live).
"""
import time
import sqlite3
from datetime import datetime
from src import db
from src.utils import get_logger

logger = get_logger("decision_logger")

# Benchmark
BENCHMARK_TICKER = "QQQ"


def init_decision_tables():
    """Create decision tracking tables."""
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                rating TEXT,
                evidence_score REAL,
                asymmetry_score REAL,
                momentum_score REAL,
                risk_score REAL,
                opportunity_score REAL,
                tracking_priority TEXT,
                price_at_decision REAL,
                benchmark_ticker TEXT DEFAULT 'QQQ',
                benchmark_price_at_decision REAL,
                reason TEXT,
                signal_id TEXT,
                expected_holding_days INTEGER,
                mode TEXT DEFAULT 'live_paper',
                created_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dec_ticker ON decisions(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dec_date ON decisions(decision_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dec_mode ON decisions(mode)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_outcomes (
                outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL,
                evaluation_date TEXT,
                horizon_days INTEGER NOT NULL,
                price_at_horizon REAL,
                benchmark_price_at_horizon REAL,
                forward_return REAL,
                benchmark_return REAL,
                alpha_return REAL,
                max_gain REAL,
                max_drawdown REAL,
                thesis_confirmed INTEGER,
                exit_triggered INTEGER,
                outcome_notes TEXT,
                FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcome_dec ON decision_outcomes(decision_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_quality_scores (
                score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL,
                evaluation_date TEXT,
                horizon_days INTEGER NOT NULL,
                decision_type TEXT,
                quality_score REAL,
                return_score REAL,
                benchmark_score REAL,
                drawdown_score REAL,
                timing_score REAL,
                thesis_score REAL,
                opportunity_cost_score REAL,
                rule_consistency_score REAL,
                explanation TEXT,
                FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                mode TEXT NOT NULL,
                overall_dqs REAL,
                buy_dqs REAL,
                sell_dqs REAL,
                trim_dqs REAL,
                hold_dqs REAL,
                no_buy_dqs REAL,
                rating_quality_score REAL,
                exit_quality_score REAL,
                missed_opportunity_score REAL,
                false_positive_rate REAL,
                false_negative_rate REAL,
                stability_score REAL,
                warning_level TEXT DEFAULT 'normal',
                notes TEXT,
                UNIQUE(date, mode)
            )
        """)

    logger.info("Decision tables initialized")


def get_benchmark_price(date: str) -> float:
    """Get QQQ price at a given date from radar DB."""
    try:
        with db.radar_conn() as conn:
            row = conn.execute(
                "SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (BENCHMARK_TICKER, date)
            ).fetchone()
            if row:
                return row[0]
            logger.warning(f"benchmark_price_missing ticker={BENCHMARK_TICKER} date={date}")
            return 0.0
    except Exception as e:
        logger.warning(f"benchmark_price_failed ticker={BENCHMARK_TICKER} date={date} error={e}")
        return 0.0


def log_decision(ticker: str, decision_type: str, price: float,
                  scores: dict = None, reason: str = "", mode: str = "live_paper",
                  signal_id: str = "", expected_holding_days: int = 0) -> int:
    """Log a decision and return decision_id."""
    today = datetime.now().strftime("%Y-%m-%d")
    benchmark_price = get_benchmark_price(today)

    scores = scores or {}

    with db.get_conn() as conn:
        conn.execute("""
            INSERT INTO decisions (decision_date, ticker, decision_type, rating,
                evidence_score, asymmetry_score, momentum_score, risk_score,
                opportunity_score, tracking_priority, price_at_decision,
                benchmark_ticker, benchmark_price_at_decision,
                reason, signal_id, expected_holding_days, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, ticker, decision_type,
              scores.get("rating", ""), scores.get("evidence", 0),
              scores.get("asymmetry", 0), scores.get("momentum", 0),
              scores.get("risk", 0), scores.get("opportunity", 0),
              scores.get("tracking_priority", ""),
              price, BENCHMARK_TICKER, benchmark_price,
              reason, signal_id, expected_holding_days, mode, time.time()))
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        decision_id = row[0]

    logger.info(f"Decision #{decision_id}: {decision_type} {ticker} @ ${price:.2f} [{mode}] — {reason}")
    return decision_id


def log_historical_decision(ticker: str, decision_type: str, price: float,
                             date: str, scores: dict = None, reason: str = "",
                             benchmark_price: float = 0) -> int:
    """Log a historical backtest decision."""
    scores = scores or {}

    with db.get_conn() as conn:
        conn.execute("""
            INSERT INTO decisions (decision_date, ticker, decision_type, rating,
                evidence_score, asymmetry_score, momentum_score, risk_score,
                opportunity_score, tracking_priority, price_at_decision,
                benchmark_ticker, benchmark_price_at_decision,
                reason, signal_id, expected_holding_days, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, 'historical_backtest', ?)
        """, (date, ticker, decision_type,
              scores.get("rating", ""), scores.get("evidence", 0),
              scores.get("asymmetry", 0), scores.get("momentum", 0),
              scores.get("risk", 0), scores.get("opportunity", 0),
              scores.get("tracking_priority", ""),
              price, BENCHMARK_TICKER, benchmark_price,
              reason, time.time()))
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        return row[0]


def get_pending_decisions(mode: str = None, horizons_needed: list = None) -> list:
    """Get decisions that still need outcome evaluation."""
    if horizons_needed is None:
        horizons_needed = [5, 10, 20, 60]

    with db.get_conn() as conn:
        if mode:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE mode=? ORDER BY decision_date", (mode,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM decisions ORDER BY decision_date").fetchall()

    decisions = [dict(r) for r in rows]

    # Filter to those missing outcomes
    pending = []
    for d in decisions:
        with db.get_conn() as conn:
            existing = conn.execute(
                "SELECT horizon_days FROM decision_outcomes WHERE decision_id=?",
                (d["decision_id"],)
            ).fetchall()
        existing_horizons = {r[0] for r in existing}
        missing = [h for h in horizons_needed if h not in existing_horizons]
        if missing:
            d["_missing_horizons"] = missing
            pending.append(d)

    return pending


# Initialize on import
init_decision_tables()
