"""Data Cleanup — prune stale data to keep DBs lean.

Run weekly (Sunday or as part of weekly report).
"""
import os
import sqlite3
import time
from datetime import datetime, timedelta
from src.utils import get_logger

logger = get_logger("data_cleanup")

RADAR_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ascr.sqlite")
ACTIVITY_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "activity_log.sqlite")


def cleanup_events(keep_days=90):
    """Remove events older than keep_days."""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(RADAR_DB)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.execute("DELETE FROM events WHERE date < ?", (cutoff,))
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.commit()
    conn.close()
    removed = before - after
    if removed:
        logger.info(f"Events: removed {removed} older than {keep_days}d ({before}→{after})")
    return removed


def cleanup_prices(keep_days=180):
    """Remove price data older than keep_days."""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(RADAR_DB)
    before = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    conn.execute("DELETE FROM prices WHERE date < ?", (cutoff,))
    after = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    conn.commit()
    conn.close()
    removed = before - after
    if removed:
        logger.info(f"Prices: removed {removed} older than {keep_days}d ({before}→{after})")
    return removed


def cleanup_activity_log(keep_days=90):
    """Remove activity log entries older than keep_days."""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(ACTIVITY_DB)
    try:
        before = conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
        conn.execute("DELETE FROM activity WHERE date < ?", (cutoff,))
        after = conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
        conn.commit()
        removed = before - after
        if removed:
            logger.info(f"Activity log: removed {removed} older than {keep_days}d")
        return removed
    except Exception:
        return 0
    finally:
        conn.close()


def cleanup_scores(keep_days=60):
    """Remove old scores, keep only recent."""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(RADAR_DB)
    before = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    conn.execute("DELETE FROM scores WHERE date < ?", (cutoff,))
    after = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    conn.commit()
    conn.close()
    removed = before - after
    if removed:
        logger.info(f"Scores: removed {removed} older than {keep_days}d")
    return removed


def vacuum_dbs():
    """Reclaim disk space after deletions."""
    for db_path in [RADAR_DB, ACTIVITY_DB]:
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("VACUUM")
            conn.close()
            size_mb = os.path.getsize(db_path) / 1024 / 1024
            logger.info(f"VACUUM {os.path.basename(db_path)}: {size_mb:.1f}MB")
        except Exception as e:
            logger.warning(f"VACUUM {db_path}: {e}")


def run_cleanup():
    """Full cleanup routine."""
    logger.info("=== Data Cleanup ===")

    results = {
        "events": cleanup_events(keep_days=90),
        "prices": cleanup_prices(keep_days=180),
        "scores": cleanup_scores(keep_days=60),
        "activity": cleanup_activity_log(keep_days=90),
    }

    vacuum_dbs()

    total = sum(results.values())
    logger.info(f"Cleanup done: {total} rows removed ({results})")

    # Log
    try:
        from src.activity_log import log as alog
        alog("cleanup", "cleanup_done", **results, total_removed=total)
    except Exception:
        pass

    return results


if __name__ == "__main__":
    results = run_cleanup()
    print(f"Removed: {results}")
