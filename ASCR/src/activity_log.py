"""Activity Logger — structured daily log for analysis.

Every pipeline run, every decision, every push gets logged to:
  data/activity_log.sqlite — structured, queryable
  logs/YYYY-MM-DD.log — human-readable daily file

This is the audit trail. Every row has a timestamp, action type,
and JSON payload.
"""
import os
import json
import sqlite3
import time
from datetime import datetime
from src.utils import get_logger

logger = get_logger("activity_log")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "activity_log.sqlite")
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def _init():
    os.makedirs(LOG_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            ticker TEXT,
            details TEXT,
            created_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_action ON activity(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_ticker ON activity(ticker)")
    conn.commit()
    conn.close()


def log(source: str, action: str, ticker: str = None, **kwargs):
    """Log an activity.

    Args:
        source: "radar", "pipeline", "trader", "bot", "scanner", "fast_scan"
        action: "pipeline_start", "pipeline_end", "event_found", "pick_generated",
                "buy", "sell", "rotation", "alert_sent", "scan_start", "scan_end",
                "ticker_added", "ticker_removed", "error", etc.
        ticker: optional ticker symbol
        **kwargs: any additional data → stored as JSON
    """
    _init()
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    date = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    details = json.dumps(kwargs, ensure_ascii=False, default=str) if kwargs else "{}"

    # DB
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO activity (timestamp, date, time, source, action, ticker, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ts, date, time_str, source, action, ticker, details, time.time()))
    conn.commit()
    conn.close()

    # Daily log file
    log_path = os.path.join(LOG_DIR, f"{date}.log")
    line = f"[{time_str}] [{source}] {action}"
    if ticker:
        line += f" | {ticker}"
    if kwargs:
        # Compact one-liner
        summary_parts = []
        for k, v in kwargs.items():
            if isinstance(v, str) and len(v) > 80:
                v = v
            summary_parts.append(f"{k}={v}")
        line += f" | {', '.join(summary_parts)}"
    line += "\n"

    with open(log_path, "a") as f:
        f.write(line)


def query(date=None, source=None, action=None, ticker=None, days=7, limit=100) -> list:
    """Query activity log."""
    _init()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    where = []
    params = []
    if date:
        where.append("date = ?")
        params.append(date)
    elif days:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where.append("date >= ?")
        params.append(cutoff)
    if source:
        where.append("source = ?")
        params.append(source)
    if action:
        where.append("action = ?")
        params.append(action)
    if ticker:
        where.append("ticker = ?")
        params.append(ticker)

    sql = "SELECT * FROM activity"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    sql += f" LIMIT {limit}"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    log("test", "test_entry", "MU", note="testing activity log", score=42)
    rows = query(days=1)
    for r in rows:
        print(f"  {r['timestamp']} [{r['source']}] {r['action']} {r['ticker'] or ''}")
