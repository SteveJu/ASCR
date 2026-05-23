"""Event cooldown — prevent repeated rating boosts from duplicate news cycles.

Rules:
- Same ticker, same event_type within 10 days → cooldown (no re-boost)
- UNLESS the new event contains incremental info (new $, warrant, guidance, customer)
- Incremental bypass: first try Python keywords, then LLM if ambiguous
"""
import time
from src import db
from src.utils import get_logger

logger = get_logger("cooldown")

COOLDOWN_DAYS = 10
COOLDOWN_TABLE = "event_cooldowns"

def init_cooldown_table():
    """Create cooldown tracking table if needed."""
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_cooldowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                event_type TEXT NOT NULL,
                triggered_at REAL NOT NULL,
                rating_at_trigger TEXT,
                source_event_id TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cooldown_ticker ON event_cooldowns(ticker)")

init_cooldown_table()


def is_on_cooldown(ticker: str, event_type: str) -> bool:
    """Check if this ticker+event_type is on cooldown."""
    cutoff = time.time() - (COOLDOWN_DAYS * 86400)
    with db.get_conn() as conn:
        row = conn.execute("""
            SELECT 1 FROM event_cooldowns
            WHERE ticker=? AND event_type=? AND triggered_at > ?
        """, (ticker, event_type, cutoff)).fetchone()
    return row is not None


def set_cooldown(ticker: str, event_type: str, rating: str = "", event_id: str = ""):
    """Mark cooldown for a ticker+event_type."""
    with db.get_conn() as conn:
        conn.execute("""
            INSERT INTO event_cooldowns (ticker, event_type, triggered_at, rating_at_trigger, source_event_id)
            VALUES (?, ?, ?, ?, ?)
        """, (ticker, event_type, time.time(), rating, event_id))
    logger.debug(f"Cooldown set: {ticker}/{event_type}")


def should_apply_event(ticker: str, event: dict) -> dict:
    """Decide whether an event should affect scoring or is on cooldown.

    Returns:
        {
            "apply": bool,
            "reason": str,
            "needs_llm_check": bool,
        }
    """
    event_type = event.get("event_type", "other")
    is_incremental = event.get("is_incremental", False)

    # Not on cooldown → always apply
    if not is_on_cooldown(ticker, event_type):
        return {"apply": True, "reason": "no_cooldown", "needs_llm_check": False}

    # On cooldown but has incremental info → bypass
    if is_incremental:
        return {"apply": True, "reason": "incremental_bypass", "needs_llm_check": False}

    # On cooldown, check if event has keywords that suggest genuinely new info
    from src.event_deduper import _has_incremental_info
    title = event.get("title", "") or event.get("headline", "")
    if _has_incremental_info(title):
        return {"apply": True, "reason": "keyword_incremental_bypass", "needs_llm_check": False}

    # Ambiguous — might need LLM to decide
    # For now, check if the event seems materially different
    has_value = event.get("has_contract_value", False)
    has_counterparty = event.get("counterparty") not in (None, "")
    if has_value or has_counterparty:
        return {"apply": True, "reason": "possible_new_info", "needs_llm_check": True}

    # Default: skip (on cooldown, no new info detected)
    return {"apply": False, "reason": "cooldown_active", "needs_llm_check": False}
