"""Telegram notification sender — dual bot support.

Primary (ASCR Bot): All radar signals, events, analysis
Paper (ASCR-H Bot): Paper trading simulation only
"""
import requests
from src import config
from src.utils import get_logger

logger = get_logger("telegram")


def _send_to(bot_key: str, message: str, parse_mode: str = "HTML") -> bool:
    """Send message to a specific bot (primary or paper)."""
    cfg = config.telegram()
    bot = cfg.get(bot_key, {})
    if not bot.get("enabled", False):
        return False
    token = bot.get("bot_token", "")
    chat_id = bot.get("chat_id", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if len(message) > 4000:
        message = message[:3997] + "..."
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id, "text": message,
            "parse_mode": parse_mode, "disable_web_page_preview": True,
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send ({bot_key}) failed: {e}")
        return False


def send(message: str, parse_mode: str = "HTML") -> bool:
    """Send to primary bot (ASCR Bot)."""
    return _send_to("primary", message, parse_mode)


def send_paper(message: str, parse_mode: str = "HTML") -> bool:
    """Send to paper trading bot (ASCR-H Bot)."""
    return _send_to("paper", message, parse_mode)


def send_both(message: str, parse_mode: str = "HTML") -> bool:
    """Send to both bots."""
    r1 = _send_to("primary", message, parse_mode)
    r2 = _send_to("paper", message, parse_mode)
    return r1 or r2


def send_daily_summary(scores: list = None, exit_signals: list = None, positions: list = None):
    """Send unified daily report using recommender rankings."""
    from src.recommender import get_top_picks

    msg = "📊 <b>ASCR Daily Report</b>\n\n"

    # Top picks from recommender (event-driven)
    top = get_top_picks(10)
    if top:
        msg += "<b>🎯 Top 10 Picks:</b>\n"
        for i, s in enumerate(top, 1):
            ev = s["ev_score"]
            count = s["ev_count"]
            price_str = f"${s['price']:.0f}" if s["price"] else "N/A"
            heat = "🔥" * min(int(count / 5), 3) if count else ""
            # Verdict if available
            verdict = s.get("verdict", "")
            verdict_str = f" [{verdict}]" if verdict else ""
            msg += f"  {i}. <b>{s['ticker']}</b> ev={ev:+.0f} ({count} events) {price_str}{verdict_str} {heat}\n"
            if s.get("event_types"):
                msg += f"     {s['event_types']}\n"
        msg += "\n"

    # Exit signals
    if exit_signals:
        msg += "<b>⚠️ Position Alerts:</b>\n"
        for sig in exit_signals:
            msg += f"<b>{sig['ticker']}</b> ({sig['pnl_pct']:+.1f}%)\n"
            for s in sig["signals"]:
                severity_emoji = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "⚡", "INFO": "ℹ️"}.get(s["severity"], "")
                msg += f"  {severity_emoji} {s['type']}: {s['message']}\n"
        msg += "\n"

    # Positions
    if positions:
        msg += "<b>💼 Positions:</b>\n"
        total_pnl = 0
        for p in positions:
            emoji = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            msg += f"  {emoji} {p['ticker']}: {p['pnl_pct']:+.1f}%\n"
            total_pnl += p["pnl"]
        msg += f"  Total P&L: ${total_pnl:+,.0f}\n"

    return send(msg)


def send_alert(alert_type: str, ticker: str, message: str):
    """Send high-priority alert to primary bot."""
    msg = f"🚨 <b>[{alert_type}]</b> ${ticker}\n\n{message}"
    return send(msg)


def send_event_signals(events_text: str):
    """Send event pipeline results to primary bot."""
    return send(events_text)
