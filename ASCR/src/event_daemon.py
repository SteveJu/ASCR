"""Continuous Event Monitor Daemon — real-time signal detection.

Runs during market hours, polls every 15min for new events.
Only calls LLM when genuinely new items found (hash dedup).
Triggers ASCR-H execution on actionable signals.

Cost model:
- RSS/SEC fetch: free (pure Python)
- Haiku filter: ~$0.001 per article
- Gemini analysis: ~$0.005 per event
- Expected: 0-5 new events per cycle, ~$0.03/cycle max
"""
import os
import sys
import time
import logging
import signal as _signal
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, db
from src.event_deduper import (
    article_hash,
    deduplicate_articles,
    enrich_article_identity,
    find_duplicate_article,
    stable_hash,
)
from src.market_calendar import is_us_market_holiday
from src.utils import get_logger

logger = get_logger("event_daemon")

# Configuration
POLL_INTERVAL = 900    # 15 minutes
MARKET_OPEN_H = 9
MARKET_OPEN_M = 30
MARKET_CLOSE_H = 16
MARKET_CLOSE_M = 0
PRE_MARKET_BUFFER = 30  # Start 30min before open
POST_MARKET_BUFFER = 30 # Continue 30min after close

_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    logger.info(f"Signal {sig} received, shutting down...")
    _shutdown = True

_signal.signal(_signal.SIGTERM, _handle_signal)
_signal.signal(_signal.SIGINT, _handle_signal)


def _is_active_hours(now=None) -> bool:
    """Check if we should be polling (market hours +/- buffer)."""
    now = now or datetime.now()
    # Skip weekends
    if now.weekday() >= 5:
        return False
    # Skip holidays
    if is_us_market_holiday(now.date()):
        return False
    # Active window: 9:00 AM - 4:30 PM ET
    start_min = (MARKET_OPEN_H * 60 + MARKET_OPEN_M) - PRE_MARKET_BUFFER
    end_min = (MARKET_CLOSE_H * 60 + MARKET_CLOSE_M) + POST_MARKET_BUFFER
    now_min = now.hour * 60 + now.minute
    return start_min <= now_min <= end_min


def _get_seen_hashes(conn, today: str) -> set:
    """Get all event hashes for today."""
    rows = conn.execute(
        "SELECT hash FROM events WHERE date = ? AND hash IS NOT NULL", (today,)
    ).fetchall()
    return {r[0] for r in rows}


def _fetch_new_articles() -> list:
    """Fetch news articles, return list with hash for each."""
    from src.event_pipeline import fetch_news
    articles = fetch_news(max_per_query=10)  # Smaller batch for frequent polling
    return [enrich_article_identity(a) for a in articles]


def _fetch_new_filings(tickers: list) -> list:
    """Fetch recent SEC 8-K filings."""
    from src.event_pipeline import fetch_sec_filings
    filings = fetch_sec_filings(tickers, days=1)  # Only today

    result = []
    for f in filings:
        h = stable_hash(f"{f['ticker']}{f['date']}{f.get('items','')}")
        high_priority = any(it in ("1.01", "2.01", "7.01") for it in f.get("important_items", []))
        result.append({
            "title": f"SEC 8-K: {f['ticker']} filed {f['date']} — {f['item_descriptions']}",
            "url": f.get("url", ""),
            "published": f.get("date", ""),
            "source_query": "sec_8k",
            "_hash": h,
            "_priority": "high" if high_priority else "normal",
        })

    return result


def _filter_and_analyze(new_articles: list) -> list:
    """Run Haiku filter + Gemini/Sonnet analysis on new articles only."""
    if not new_articles:
        return []

    from src.event_pipeline import filter_headlines, analyze_article, init_events_db
    import re as _re

    init_events_db()
    today = datetime.now().strftime("%Y-%m-%d")

    # Step 1: quant headline filter. This keeps only material, incremental news
    # and orders it before scarce LLM calls are spent.
    relevant = deduplicate_articles(filter_headlines(new_articles))
    if not relevant:
        return []

    logger.info(f"Filter: {len(new_articles)} → {len(relevant)} quant-relevant")

    # Step 2: Deep analysis per article (capped at 10 per cycle to control cost)
    MAX_PER_CYCLE = 10
    events = []
    for article in relevant[:MAX_PER_CYCLE]:
        article = enrich_article_identity(article)
        h = article.get("_hash") or article_hash(article)
        with db.get_conn() as conn:
            duplicate = find_duplicate_article(conn, article)
            if duplicate:
                logger.info(
                    "Skip duplicate article (%s): %s",
                    duplicate.get("reason", "duplicate"),
                    article["title"][:120],
                )
                continue

        result = analyze_article(article)
        if not result:
            continue

        ticker = result.get("ticker", "")
        if not ticker or not _re.match(r"^[A-Z]{1,5}$", ticker):
            continue

        if result.get("confidence", 0) < 0.5 or abs(result.get("evidence_delta", 0)) < 3:
            continue
        if result.get("priced_in_pct", 0) > 80:
            continue

        result["headline"] = article["title"]
        result["source"] = article.get("source_query", "news")
        result["url"] = article.get("url", "")
        result["_hash"] = h
        events.append(result)

    return events


def _store_events(events: list) -> list:
    """Store events to DB, return only newly inserted ones."""
    if not events:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    stored = []

    with db.get_conn() as conn:
        for ev in events:
            h = ev.get("_hash") or article_hash({
                "title": ev.get("headline", ""),
                "url": ev.get("url", ""),
                "source_query": ev.get("source", "news"),
            })

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO events
                    (hash, date, ticker, source, headline, event_type, counterparty,
                     is_ai_related, supply_chain_area, evidence_delta,
                     asymmetry_delta, risk_delta, summary, confidence,
                     created_at, verdict, conviction, investment_thesis,
                     upside_potential, moat, catalyst)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (h, today, ev["ticker"], ev.get("source", "news"),
                      ev.get("headline", ""), ev.get("event_type", ""),
                      ev.get("counterparty", ""),
                      ev.get("is_ai_related", 1), ev.get("supply_chain_area", ""),
                      ev.get("evidence_delta", 0), ev.get("asymmetry_delta", 0),
                      ev.get("risk_delta", 0), ev.get("summary", ""),
                      ev.get("confidence", 0.5), datetime.now().isoformat(),
                      ev.get("verdict", ""), ev.get("conviction", ""),
                      ev.get("investment_thesis", ""), ev.get("upside_potential", ""),
                      ev.get("moat", ""), ev.get("catalyst", "")))

                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    stored.append(ev)
            except Exception as e:
                logger.warning(f"Store event {ev.get('ticker','?')}: {e}")

        conn.commit()

    return stored


def _trigger_ascr_h(new_events: list):
    """Signal ASCR-H to check for trades based on new events."""
    if not new_events:
        return

    tickers = list(set(ev.get("ticker", "") for ev in new_events))
    logger.info(f"New actionable events for: {tickers}")

    # Alert on Telegram (English + Chinese)
    try:
        from src.telegram_notifier import send
        from src.event_alerts import filter_explosive_events

        alert_events = filter_explosive_events(new_events)
        if not alert_events:
            logger.info(f"Telegram real-time alert skipped: 0/{len(new_events)} explosive events")
        else:
            en_lines = ["💥 <b>Real-Time Explosive Event</b>\n"]
            for ev in alert_events:
                verdict = ev.get("verdict", "")
                ticker = ev.get("ticker", "?")
                headline = ev.get("headline", "")
                evidence = ev.get("evidence_delta", 0)
                conviction = ev.get("conviction", "")
                thesis = ev.get("investment_thesis", "") or ev.get("summary", "")
                alert_score = ev.get("_alert_score", 0)
                en_lines.append(
                    f"<b>{ticker}</b> [{verdict}|{conviction}] "
                    f"ev={evidence:+.0f} alert={alert_score:.0f}"
                )
                en_lines.append(f"  {headline}")
                if thesis:
                    en_lines.append(f"  -> {thesis}")
                en_lines.append("")
            send("\n".join(en_lines))


    except Exception as e:
        logger.warning(f"Telegram alert: {e}")

    # Trigger ASCR-H execution via subprocess (clean isolation)
    try:
        import subprocess
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", "-c",
             "from src.intraday_monitor import run_intraday_trades; import json; r = run_intraday_trades(); print(json.dumps(r))"],
            capture_output=True, text=True, timeout=120,
            cwd=os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H"),
            env={**os.environ, "PYTHONPATH": os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H")},
        )
        if result.returncode == 0:
            logger.info(f"Paper trader triggered: {result.stdout.strip()}")
        else:
            logger.warning(f"Paper trader error: {result.stderr.strip()}")
    except Exception as e:
        logger.warning(f"Paper trader trigger failed: {e}")


def run_cycle() -> dict:
    """Single poll cycle: fetch → dedup → analyze → store → trigger."""
    today = datetime.now().strftime("%Y-%m-%d")

    universe = config.universe()
    tickers = [t for sector in universe.get("sectors", {}).values()
               for t in (sector.get("tickers", []) if isinstance(sector, dict) else [])]

    # Fetch
    articles = _fetch_new_articles()
    filings = _fetch_new_filings(tickers)
    all_items = articles + filings

    if not all_items:
        return {"fetched": 0, "new": 0, "events": 0}

    # Dedup against DB events
    with db.get_conn() as conn:
        seen = _get_seen_hashes(conn, today)

    # Also dedup against article-level cache (tracks what we've already fetched today)
    cache_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", f"seen_articles_{today}.txt")
    seen_articles = set()
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            seen_articles = set(line.strip() for line in f)

    new_items = [a for a in all_items if a.get("_hash") not in seen and a.get("_hash") not in seen_articles]

    if not new_items:
        return {"fetched": len(all_items), "new": 0, "events": 0}

    logger.info(f"Cycle: {len(all_items)} fetched, {len(new_items)} new")

    # Analyze only new items
    events = _filter_and_analyze(new_items)

    # Mark analyzed items after processing so transient analysis failures can retry.
    with open(cache_path, "a") as f:
        for a in new_items:
            item_hash = a.get("_hash", "")
            if item_hash:
                f.write(item_hash + "\n")

    # Store
    stored = _store_events(events)

    # Trigger ASCR-H if actionable
    actionable = [e for e in stored if e.get("evidence_delta", 0) >= 3]
    if actionable:
        _trigger_ascr_h(actionable)

    return {
        "fetched": len(all_items),
        "new": len(new_items),
        "analyzed": len(events),
        "stored": len(stored),
        "actionable": len(actionable),
    }


def run_daemon():
    """Main daemon loop — runs during market hours."""
    logger.info("Event daemon starting...")

    while not _shutdown:
        if _is_active_hours():
            try:
                result = run_cycle()
                if result.get("new", 0) > 0:
                    logger.info(f"Cycle result: {result}")
                else:
                    logger.info(f"Cycle: {result['fetched']} items, nothing new")
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                try:
                    from src.telegram_notifier import send
                    send(f"🚨 Event daemon error: {str(e)}")
                except Exception:
                    pass
        else:
            logger.debug("Outside active hours, sleeping...")

        # Sleep with shutdown check
        for _ in range(POLL_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Event daemon stopped.")


if __name__ == "__main__":
    run_daemon()
