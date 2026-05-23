"""Price Event Detector — turns abnormal price moves into events.

A stock surging 10%+ in a week or 20%+ in a month IS an event.
A stock crashing 15%+ is also an event (negative).

This replaces momentum as a scoring dimension — price action
is just another signal, not a separate axis.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from src import config
from src.utils import get_logger

logger = get_logger("price_events")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "ascr.sqlite")

# Thresholds
SURGE_1W = 0.10   # +10% in 1 week
SURGE_1M = 0.20   # +20% in 1 month
CRASH_1W = -0.10  # -10% in 1 week
CRASH_1M = -0.20  # -20% in 1 month
VOLUME_SPIKE = 2.0  # 2x average volume


def detect_price_events(tickers=None) -> list:
    """Detect abnormal price moves for tracked tickers."""
    if not tickers:
        tickers = config.all_tickers(include_benchmarks=False)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    events = []

    for ticker in tickers:
        try:
            rows = conn.execute("""
                SELECT date, close, volume FROM prices
                WHERE ticker=? ORDER BY date DESC LIMIT 25
            """, (ticker,)).fetchall()

            if len(rows) < 5:
                continue

            latest = rows[0]["close"]
            latest_date = rows[0]["date"]
            latest_vol = rows[0]["volume"] or 0

            # 1-week change (5 trading days)
            if len(rows) >= 5:
                week_ago = rows[4]["close"]
                if week_ago and week_ago > 0:
                    change_1w = (latest - week_ago) / week_ago
                    if change_1w >= SURGE_1W:
                        events.append({
                            "ticker": ticker,
                            "headline": f"PRICE SURGE: {ticker} up {change_1w:.0%} in 1 week (${week_ago:.2f}→${latest:.2f})",
                            "evidence_delta": min(int(change_1w * 50), 15),  # +10%→5, +20%→10, +30%→15
                            "event_type": "price_surge_1w",
                            "date": latest_date,
                        })
                    elif change_1w <= CRASH_1W:
                        events.append({
                            "ticker": ticker,
                            "headline": f"PRICE DROP: {ticker} down {change_1w:.0%} in 1 week (${week_ago:.2f}→${latest:.2f})",
                            "evidence_delta": max(int(change_1w * 50), -15),
                            "event_type": "price_drop_1w",
                            "date": latest_date,
                        })

            # 1-month change (21 trading days)
            if len(rows) >= 21:
                month_ago = rows[20]["close"]
                if month_ago and month_ago > 0:
                    change_1m = (latest - month_ago) / month_ago
                    if change_1m >= SURGE_1M:
                        events.append({
                            "ticker": ticker,
                            "headline": f"PRICE SURGE: {ticker} up {change_1m:.0%} in 1 month (${month_ago:.2f}→${latest:.2f})",
                            "evidence_delta": min(int(change_1m * 30), 15),
                            "event_type": "price_surge_1m",
                            "date": latest_date,
                        })
                    elif change_1m <= CRASH_1M:
                        events.append({
                            "ticker": ticker,
                            "headline": f"PRICE DROP: {ticker} down {change_1m:.0%} in 1 month (${month_ago:.2f}→${latest:.2f})",
                            "evidence_delta": max(int(change_1m * 30), -15),
                            "event_type": "price_drop_1m",
                            "date": latest_date,
                        })

            # Volume spike (vs 20-day average)
            if len(rows) >= 20 and latest_vol:
                avg_vol = sum(r["volume"] or 0 for r in rows[1:21]) / 20
                if avg_vol > 0 and latest_vol / avg_vol >= VOLUME_SPIKE:
                    vol_ratio = latest_vol / avg_vol
                    events.append({
                        "ticker": ticker,
                        "headline": f"VOLUME SPIKE: {ticker} volume {vol_ratio:.1f}x average",
                        "evidence_delta": min(int(vol_ratio * 2), 8),  # 2x→4, 3x→6, 4x→8
                        "event_type": "volume_spike",
                        "date": latest_date,
                    })

        except Exception as e:
            logger.warning(f"{ticker}: {e}")

    conn.close()
    logger.info(f"Detected {len(events)} price events across {len(tickers)} tickers")
    return events


if __name__ == "__main__":
    events = detect_price_events()
    for e in sorted(events, key=lambda x: -abs(x["evidence_delta"])):
        print(f"  {e['ticker']:6s} ev={e['evidence_delta']:+3d} {e['headline']}")
