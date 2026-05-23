"""AI Bubble Burst Detector — circuit breaker for systemic collapse.

Monitors the entire AI universe for synchronized crashes.
Three levels: WARNING → DANGER → MELTDOWN (auto-liquidate).

This runs BEFORE any buy/sell decisions in recommender.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from src import config
from src.utils import get_logger

logger = get_logger("bubble_detector")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "ascr.sqlite")

# Thresholds
LEVELS = {
    "WARNING": {
        "pct_declining": 0.80,   # >80% of universe declining
        "pct_crash": 0.50,       # >50% down more than crash_threshold
        "crash_threshold": -0.05, # -5%
        "action": "alert",
    },
    "DANGER": {
        "pct_declining": 0.90,
        "pct_crash": 0.60,
        "crash_threshold": -0.08,
        "action": "alert_urgent",
    },
    "MELTDOWN": {
        "pct_declining": 0.95,
        "pct_crash": 0.80,
        "crash_threshold": -0.10,
        "action": "liquidate",
    },
}


def check_bubble_burst(days=1) -> dict:
    """Check if AI bubble is bursting.

    Returns:
        {
            level: None | "WARNING" | "DANGER" | "MELTDOWN",
            action: "none" | "alert" | "alert_urgent" | "liquidate",
            stats: {total, declining, crash_count, pct_declining, pct_crash,
                    avg_change, worst_ticker, worst_change},
            message: str,
        }
    """
    tickers = config.all_tickers(include_benchmarks=False)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    changes = []
    for ticker in tickers:
        rows = conn.execute("""
            SELECT close FROM prices WHERE ticker=?
            ORDER BY date DESC LIMIT ?
        """, (ticker, days + 1)).fetchall()

        if len(rows) < 2:
            continue

        current = rows[0]["close"]
        previous = rows[days]["close"] if len(rows) > days else rows[-1]["close"]

        if previous and previous > 0:
            change = (current - previous) / previous
            changes.append({"ticker": ticker, "change": change})

    conn.close()

    if not changes:
        return {"level": None, "action": "none", "stats": {}, "message": "No price data"}

    total = len(changes)
    declining = sum(1 for c in changes if c["change"] < 0)
    pct_declining = declining / total

    # Check each level (highest first)
    result_level = None
    result_action = "none"

    for level_name in ["MELTDOWN", "DANGER", "WARNING"]:
        lvl = LEVELS[level_name]
        crash_count = sum(1 for c in changes if c["change"] <= lvl["crash_threshold"])
        pct_crash = crash_count / total

        if pct_declining >= lvl["pct_declining"] and pct_crash >= lvl["pct_crash"]:
            result_level = level_name
            result_action = lvl["action"]
            break

    avg_change = sum(c["change"] for c in changes) / total
    worst = min(changes, key=lambda c: c["change"])
    best = max(changes, key=lambda c: c["change"])

    crash_5 = sum(1 for c in changes if c["change"] <= -0.05)
    crash_10 = sum(1 for c in changes if c["change"] <= -0.10)

    stats = {
        "total": total,
        "declining": declining,
        "pct_declining": round(pct_declining * 100, 1),
        "crash_5pct": crash_5,
        "crash_10pct": crash_10,
        "avg_change": round(avg_change * 100, 2),
        "worst_ticker": worst["ticker"],
        "worst_change": round(worst["change"] * 100, 1),
        "best_ticker": best["ticker"],
        "best_change": round(best["change"] * 100, 1),
    }

    if result_level:
        emoji = {"WARNING": "⚠️", "DANGER": "🔴", "MELTDOWN": "💀"}[result_level]
        message = (f"{emoji} AI BUBBLE {result_level}\n"
                   f"Declining: {declining}/{total} ({pct_declining:.0%})\n"
                   f"Down >5%: {crash_5} | down >10%: {crash_10}\n"
                   f"Average: {avg_change:+.1%} | worst: {worst['ticker']} {worst['change']:+.1%}")
    else:
        message = (f"🟢 Normal - {declining}/{total} declining ({pct_declining:.0%}), "
                   f"avg {avg_change:+.1%}")

    # Log
    try:
        from src.activity_log import log as alog
        alog("bubble_detector", f"check_{result_level or 'normal'}",
             level=result_level, action=result_action,
             pct_declining=stats["pct_declining"],
             crash_10pct=crash_10, avg_change=stats["avg_change"])
    except Exception:
        pass


    # Leveraged ETF bubble signals
    etf_danger_signals = []
    try:
        from src.leveraged_etf_monitor import get_bubble_signals
        etf_signals = get_bubble_signals()
        for s in etf_signals:
            if s["severity"] == "DANGER":
                etf_danger_signals.append(f"LEVERAGED_ETF: {s['detail']}")
    except Exception:
        pass

    if etf_danger_signals:
        message += "\n" + "\n".join(etf_danger_signals)
        stats["etf_danger_signals"] = etf_danger_signals

    return {
        "level": result_level,
        "action": result_action,
        "stats": stats,
        "message": message,
    }


if __name__ == "__main__":
    result = check_bubble_burst(days=1)
    print(result["message"])
    print(f"Level: {result['level']}")
    print(f"Action: {result['action']}")
    s = result["stats"]
    if s:
        print(f"Stats: {s['declining']}/{s['total']} declining, "
              f"{s['crash_5pct']} >5%, {s['crash_10pct']} >10%")
