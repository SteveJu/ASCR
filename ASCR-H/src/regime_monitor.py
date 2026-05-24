"""Regime monitor — detect when AI trade is dying and system should be paused.

Three kill signals:
1. ALPHA DEATH: System stops beating random picks from same universe
2. SECTOR ROTATION: AI sector underperforms broad market (SPY) consistently
3. HEAT DEATH: Universe-wide returns go negative (the tide goes out)

Plus rolling performance trackers vs benchmarks.
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta
from collections import defaultdict
from src import db
from src.utils import get_logger

logger = get_logger("regime_monitor")

RADAR_BACKTEST_DB = os.environ.get("ASCR_BACKTEST_DB_PATH", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "data", "backtest.sqlite"))


def _radar_conn():
    """Read-only connection to ASCR live DB."""
    from src import config as pt_config; cfg = pt_config.load(); path = cfg["ascr"]["db_path"]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_universe_returns(lookback_days: int = 20) -> dict:
    """Get recent returns for all universe stocks."""
    cutoff = (datetime.now() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    try:
        with _radar_conn() as conn:
            tickers = conn.execute("SELECT DISTINCT ticker FROM prices WHERE date >= ?",
                                   (cutoff,)).fetchall()
            results = {}
            for row in tickers:
                ticker = row[0]
                prices = conn.execute("""
                    SELECT date, close FROM prices WHERE ticker=? AND date >= ?
                    ORDER BY date ASC
                """, (ticker, cutoff)).fetchall()
                if len(prices) >= lookback_days:
                    old_price = prices[0][1]
                    new_price = prices[-1][1]
                    if old_price and old_price > 0:
                        results[ticker] = (new_price - old_price) / old_price * 100
            return results
    except Exception as e:
        logger.warning(f"Could not get universe returns: {e}")
        return {}


def _get_benchmark_return(ticker: str, lookback_days: int = 20) -> float:
    """Get benchmark return over lookback period."""
    cutoff = (datetime.now() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    try:
        with _radar_conn() as conn:
            prices = conn.execute("""
                SELECT close FROM prices WHERE ticker=? AND date >= ?
                ORDER BY date ASC
            """, (ticker, cutoff)).fetchall()
            if len(prices) >= 2:
                return (prices[-1][0] - prices[0][0]) / prices[0][0] * 100
            logger.warning(f"benchmark_return_missing ticker={ticker} lookback_days={lookback_days} rows={len(prices)}")
    except Exception as e:
        logger.warning(f"benchmark_return_failed ticker={ticker} lookback_days={lookback_days} error={e}")
    return 0.0


def compute_regime_signals() -> dict:
    """Compute all regime/kill signals.

    Returns dict with:
    - kill_signals: list of triggered kill conditions
    - metrics: detailed numbers
    - recommendation: "continue" | "caution" | "pause" | "stop"
    """
    signals = []
    metrics = {}

    # === 1. ALPHA vs RANDOM PICKS ===
    # Compare system's actual buy returns vs random-N picks from universe
    with db.get_conn() as conn:
        # System's buy returns (last 30 days of evaluated decisions)
        cutoff_30d = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        system_buys = conn.execute("""
            SELECT d.ticker, o.forward_return, o.alpha_return
            FROM decisions d
            JOIN decision_outcomes o ON d.decision_id = o.decision_id
            WHERE d.decision_type = 'BUY' AND d.mode = 'live_paper'
              AND o.horizon_days = 20 AND d.decision_date >= ?
        """, (cutoff_30d,)).fetchall()

    system_returns = [r[1] for r in system_buys if r[1] is not None]

    # Universe average return
    universe_returns = _get_universe_returns(20)
    universe_avg = sum(universe_returns.values()) / len(universe_returns) if universe_returns else 0

    if system_returns:
        system_avg = sum(system_returns) / len(system_returns)
        alpha_vs_random = system_avg - universe_avg
        metrics["system_avg_return"] = round(system_avg, 2)
        metrics["universe_avg_return"] = round(universe_avg, 2)
        metrics["alpha_vs_random"] = round(alpha_vs_random, 2)
        metrics["system_buy_count"] = len(system_returns)

        if alpha_vs_random < -5:
            signals.append({
                "type": "ALPHA_DEATH",
                "severity": "critical",
                "message": f"System buys ({system_avg:+.1f}%) underperform random universe picks ({universe_avg:+.1f}%) by {alpha_vs_random:.1f}%",
                "action": "System adds no value. Pause and recalibrate.",
            })
        elif alpha_vs_random < 0:
            signals.append({
                "type": "ALPHA_FADING",
                "severity": "warning",
                "message": f"System buys ({system_avg:+.1f}%) slightly below universe average ({universe_avg:+.1f}%)",
                "action": "Monitor closely. System edge may be disappearing.",
            })
    else:
        metrics["system_avg_return"] = None
        metrics["alpha_vs_random"] = None

    # === 2. SECTOR ROTATION — AI universe vs SPY ===
    spy_return = _get_benchmark_return("SPY", 20)
    qqq_return = _get_benchmark_return("QQQ", 20)
    metrics["spy_20d_return"] = round(spy_return, 2)
    metrics["qqq_20d_return"] = round(qqq_return, 2)

    if universe_returns:
        sector_vs_spy = universe_avg - spy_return
        metrics["sector_vs_spy"] = round(sector_vs_spy, 2)

        if sector_vs_spy < -10:
            signals.append({
                "type": "SECTOR_ROTATION",
                "severity": "critical",
                "message": f"AI sector ({universe_avg:+.1f}%) massively underperforms SPY ({spy_return:+.1f}%) — delta {sector_vs_spy:.1f}%",
                "action": "AI trade is rotating out. Stop buying. Consider exiting all positions.",
            })
        elif sector_vs_spy < -5:
            signals.append({
                "type": "SECTOR_WEAKENING",
                "severity": "warning",
                "message": f"AI sector ({universe_avg:+.1f}%) underperforming SPY ({spy_return:+.1f}%) by {sector_vs_spy:.1f}%",
                "action": "AI momentum weakening. Tighten stops, no new positions.",
            })

    # === 3. HEAT DEATH — universe-wide negative returns ===
    if universe_returns:
        negative_pct = sum(1 for r in universe_returns.values() if r < 0) / len(universe_returns) * 100
        avg_return = universe_avg
        metrics["universe_negative_pct"] = round(negative_pct, 1)
        metrics["universe_count"] = len(universe_returns)

        if negative_pct > 70 and avg_return < -5:
            signals.append({
                "type": "HEAT_DEATH",
                "severity": "critical",
                "message": f"{negative_pct:.0f}% of AI universe is negative, avg return {avg_return:+.1f}%",
                "action": "The tide has gone out. Exit all. This is not a dip, it's a regime change.",
            })
        elif negative_pct > 50:
            signals.append({
                "type": "HEAT_COOLING",
                "severity": "warning",
                "message": f"{negative_pct:.0f}% of AI universe in red, avg return {avg_return:+.1f}%",
                "action": "Market cooling. Reduce position sizes, raise stop levels.",
            })

    # === 4. ROLLING PERFORMANCE vs QQQ ===
    with db.get_conn() as conn:
        # Get portfolio equity history
        equity_rows = conn.execute("""
            SELECT date, total_equity FROM (
                SELECT decision_date as date,
                       SUM(CASE WHEN decision_type='BUY' THEN -price_at_decision
                                WHEN decision_type='SELL' THEN price_at_decision
                                ELSE 0 END) as total_equity
                FROM decisions WHERE mode='live_paper'
                GROUP BY decision_date
            ) ORDER BY date DESC LIMIT 20
        """).fetchall()

    # === 5. CONSECUTIVE LOSSES ===
    with db.get_conn() as conn:
        recent_sells = conn.execute("""
            SELECT d.ticker, o.forward_return
            FROM decisions d
            JOIN decision_outcomes o ON d.decision_id = o.decision_id
            WHERE d.decision_type IN ('SELL') AND d.mode = 'live_paper'
              AND o.horizon_days = 20
            ORDER BY d.decision_date DESC LIMIT 10
        """).fetchall()

    consecutive_losses = 0
    for sell in recent_sells:
        if sell[1] is not None and sell[1] < 0:
            consecutive_losses += 1
        else:
            break

    metrics["consecutive_losses"] = consecutive_losses
    if consecutive_losses >= 3:
        signals.append({
            "type": "LOSING_STREAK",
            "severity": "warning",
            "message": f"{consecutive_losses} consecutive losing trades",
            "action": "Pause new buys for 1 week. Review what changed.",
        })

    # === RECOMMENDATION ===
    critical = sum(1 for s in signals if s["severity"] == "critical")
    warnings = sum(1 for s in signals if s["severity"] == "warning")

    if critical >= 2:
        recommendation = "stop"
        rec_msg = "🔴 STOP: Multiple critical signals. Exit all positions. System is broken or market has changed."
    elif critical >= 1:
        recommendation = "pause"
        rec_msg = "🟠 PAUSE: Critical signal detected. No new buys. Tighten all stops."
    elif warnings >= 2:
        recommendation = "caution"
        rec_msg = "🟡 CAUTION: Multiple warnings. Reduce position sizes. Monitor daily."
    elif warnings >= 1:
        recommendation = "caution"
        rec_msg = "🟡 CAUTION: Watch closely. System may be losing edge."
    else:
        recommendation = "continue"
        rec_msg = "🟢 CONTINUE: No kill signals detected. System operating normally."

    metrics["recommendation"] = recommendation
    metrics["recommendation_message"] = rec_msg

    # Save to DB
    _save_regime_check(signals, metrics)

    return {
        "signals": signals,
        "metrics": metrics,
        "recommendation": recommendation,
        "recommendation_message": rec_msg,
    }


def _save_regime_check(signals, metrics):
    """Save regime check to strategy_health table."""
    try:
        with db.get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_health
                    (date, mode, overall_dqs, warning_level, notes)
                VALUES (?, 'regime_monitor', ?, ?, ?)
            """, (
                datetime.now().strftime("%Y-%m-%d"),
                metrics.get("alpha_vs_random", 0) or 0,
                metrics.get("recommendation", "unknown"),
                str(signals),
            ))
    except Exception as e:
        logger.warning(f"Could not save regime check: {e}")


def format_regime_report(result: dict) -> str:
    """Format regime monitor output for Telegram/CLI."""
    m = result["metrics"]
    lines = ["🌡️ <b>AI Trade Regime Monitor</b>\n"]

    # Recommendation
    lines.append(f"{result['recommendation_message']}\n")

    # Metrics
    if m.get("system_avg_return") is not None:
        lines.append(f"📊 System buys: {m['system_avg_return']:+.1f}% ({m.get('system_buy_count',0)} trades)")
    if m.get("universe_avg_return") is not None:
        lines.append(f"🌐 Universe avg: {m['universe_avg_return']:+.1f}%")
    if m.get("alpha_vs_random") is not None:
        emoji = "✅" if m["alpha_vs_random"] > 0 else "⚠️"
        lines.append(f"{emoji} Alpha vs random: {m['alpha_vs_random']:+.1f}%")
    if m.get("sector_vs_spy") is not None:
        emoji = "✅" if m["sector_vs_spy"] > 0 else "⚠️"
        lines.append(f"{emoji} AI sector vs SPY: {m['sector_vs_spy']:+.1f}%")
    if m.get("spy_20d_return") is not None:
        lines.append(f"📈 SPY 20d: {m['spy_20d_return']:+.1f}% | QQQ: {m['qqq_20d_return']:+.1f}%")
    if m.get("universe_negative_pct") is not None:
        lines.append(f"🔻 Universe red: {m['universe_negative_pct']:.0f}% ({m.get('universe_count',0)} stocks)")
    if m.get("consecutive_losses", 0) > 0:
        lines.append(f"💀 Losing streak: {m['consecutive_losses']}")

    # Signals
    if result["signals"]:
        lines.append("\n<b>⚠️ Signals:</b>")
        for s in result["signals"]:
            emoji = "🚨" if s["severity"] == "critical" else "⚠️"
            lines.append(f"{emoji} <b>{s['type']}</b>: {s['message']}")
            lines.append(f"  → {s['action']}")

    return "\n".join(lines)
