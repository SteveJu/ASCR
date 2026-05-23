"""Analysis Engine — analyze events for existing universe stocks.

Separate from Discovery Engine. Only works with known tickers.
Steps:
1. Fetch data (prices, SEC 8-K, news, earnings, insider, Reddit)
2. Pre-filter headlines (Python keywords)
3. Sonnet deep analysis (capped at 30/run)
4. Store events + update scores
5. Generate rankings via recommender
6. Push daily report

Does NOT discover new tickers — that's Discovery Engine's job.
"""
import os
import sys
from datetime import datetime
import hashlib

from src import config, db


def _alert_failure(component: str, error: str):
    """Send Telegram alert on critical system failure."""
    try:
        from src.telegram_notifier import send
        send(f"🚨 <b>SYSTEM FAILURE</b>\n\n"
             f"Component: {component}\n"
             f"Error: {str(error)}\n"
             f"Time: {__import__('datetime').datetime.now().strftime('%H:%M:%S')}\n\n"
             f"⚠️ Pipeline may have incomplete data. Check logs.")
    except Exception:
        pass  # Can't even send alert — truly broken
from src.utils import get_logger

logger = get_logger("analysis_engine")


def run_analysis(morning: bool = False) -> dict:
    """Run analysis pipeline for existing universe.
    Alerts via Telegram on critical failures."""
    try:
        return _run_analysis_inner(morning)
    except Exception as e:
        _alert_failure("Analysis Pipeline (TOTAL)", e)
        raise


def _run_analysis_inner(morning: bool = False) -> dict:
    """Run analysis pipeline for existing universe.

    morning=True: prices + scores only (skip Sonnet)
    morning=False: full pipeline with event analysis
    """
    logger.info(f"=== Analysis Engine ({'AM' if morning else 'PM'}) ===")
    try:
        config.assert_universe_sync()
    except Exception:
        pass
    tickers = config.all_tickers()
    logger.info(f"Universe: {len(tickers)} tickers")
    today = datetime.now().strftime("%Y-%m-%d")

    result = {
        "date": today,
        "tickers": len(tickers),
        "events": 0,
        "morning": morning,
    }

    # 0. Market regime
    regime = None
    try:
        from src.market_regime import detect_regime
        regime = detect_regime()
        result["regime"] = regime.get("regime", "unknown")
        logger.info(f"Regime: {regime.get('regime', '?')}")
    except Exception as e:
        logger.warning(f"Regime: {e}")

    # 1. Fetch prices
    logger.info("--- Fetching prices ---")
    try:
        from src.price_fetcher import fetch_prices
        fetch_prices(tickers)
    except Exception as e:
        logger.error(f"Prices: {e}")

    # 2. Score (old system, still updates DB)
    logger.info("--- Scoring ---")
    scores = []
    try:
        from src.scoring import compute_scores
        scores = compute_scores()
        result["scores"] = len(scores)
    except Exception as e:
        logger.error(f"Scoring: {e}")
        _alert_failure("Scoring Engine", e)

    # === MORNING STOPS HERE ===
    if morning:
        logger.info("Morning run complete (prices + scores only)")
        return result

    # 3. Event Pipeline (Sonnet analysis — the expensive part)
    logger.info("--- Event Pipeline ---")
    events = []
    try:
        from src.event_pipeline import run_pipeline
        events = run_pipeline()
        result["events"] = len(events)
        logger.info(f"Events: {len(events)} actionable")
    except Exception as e:
        logger.error(f"Event pipeline: {e}")
        _alert_failure("Event Pipeline", e)

    # 4. Score deltas + inflections
    inflections = []
    try:
        from src.score_delta import compute_deltas, detect_positive_inflections
        compute_deltas()
        inflections = detect_positive_inflections()
        result["inflections"] = len(inflections)
    except Exception as e:
        logger.warning(f"Score deltas: {e}")

    # 5. Shadow tracking
    try:
        from src.shadow_tracker import record_b_tier_shadows, update_shadow_returns
        record_b_tier_shadows(scores)
        update_shadow_returns()
    except Exception as e:
        logger.warning(f"Shadows: {e}")

    # 6. Bubble check
    bubble = None
    try:
        from src.bubble_detector import check_bubble_burst
        bubble = check_bubble_burst()
        if bubble and bubble.get("level"):
            result["bubble"] = bubble["level"]
            from src.telegram_notifier import send
            send(f"⚠️ <b>Bubble Alert: {bubble['level']}</b>\n{bubble.get('message', '')}")
    except Exception as e:
        logger.warning(f"Bubble: {e}")

    # 6b. Leveraged ETF monitor
    try:
        from src.leveraged_etf_monitor import generate_events as etf_generate
        etf_evts = etf_generate(tickers)
        if etf_evts:
            with db.get_conn() as conn:
                for ev in etf_evts:
                    h = hashlib.md5("{}{}{}".format(
                        ev["ticker"], today, ev["headline"]
                    ).encode()).hexdigest()
                    conn.execute(
                        "INSERT OR IGNORE INTO events (date, ticker, source, headline, event_type, "
                        "is_ai_related, evidence_delta, summary, confidence, hash, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (today, ev["ticker"], ev["source"], ev["headline"], ev["event_type"],
                         ev.get("is_ai_related", 1), ev["evidence_delta"], ev["summary"],
                         ev.get("confidence", 0.5), h, datetime.now().isoformat()))
                conn.commit()
            result["etf_events"] = len(etf_evts)
            logger.info(f"Leveraged ETF: {len(etf_evts)} events")
    except Exception as e:
        logger.warning(f"Leveraged ETF: {e}")

    # 7. Experience tracker
    try:
        from src.experience_tracker import daily_update as exp_update
        exp_result = exp_update()
        result["exp_signals"] = exp_result.get("signals_added", 0)
        result["exp_evaluated"] = exp_result.get("signals_evaluated", 0)
        logger.info(f"Experience: +{exp_result.get('signals_added',0)} signals, "
                    f"{exp_result.get('signals_evaluated',0)} evaluated")
    except Exception as e:
        logger.warning(f"Experience tracker: {e}")

    # 8. Positions + exits
    positions = []
    exit_signals = []
    try:
        from src.position_tracker import get_position_status, evaluate_exits
        positions = get_position_status()
        exit_signals = evaluate_exits(positions)
    except Exception as e:
        logger.warning(f"Positions: {e}")

    # 9. Push daily report (unified, recommender-based)
    logger.info("--- Telegram ---")
    try:
        from src.telegram_notifier import send_daily_summary, send_alert
        send_daily_summary(scores, exit_signals, positions)
        for sig in exit_signals:
            critical = [s for s in sig["signals"] if s["severity"] == "CRITICAL"]
            if critical:
                msg = "\n".join(f"🚨 {s['type']}: {s['message']}\n→ {s['action']}" for s in critical)
                send_alert("EXIT SIGNAL", sig["ticker"], msg)
    except Exception as e:
        logger.warning(f"Telegram: {e}")
        _alert_failure("Telegram Push", e)

    # 10. Activity log
    try:
        from src.activity_log import log as alog
        alog("analysis", "daily_done", events=result.get("events", 0),
             scores=result.get("scores", 0), regime=result.get("regime", ""))
    except Exception:
        pass

    logger.info(f"Analysis done: {result}")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--morning", action="store_true")
    args = parser.parse_args()
    result = run_analysis(morning=args.morning)
    print(f"Events: {result.get('events', 0)}, Scores: {result.get('scores', 0)}")
