"""Telegram notifier for ASCR-H — uses ASCR-H Bot bot."""
import requests
from src import config
from src.utils import get_logger

logger = get_logger("telegram")

def _send(message: str, parse_mode: str = "HTML") -> bool:
    cfg = config.load()
    tg = cfg.get("telegram", {})
    if not tg.get("enabled", False):
        logger.info("Telegram send skipped: disabled")
        return False

    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")
    if not token or not chat_id:
        logger.warning("Telegram send skipped: missing bot_token or chat_id")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Split long messages (Telegram limit: 4096)
    MAX = 4000
    if len(message) <= MAX:
        chunks = [message]
    else:
        chunks = []
        current = ""
        for line in message.split("\n"):
            if len(current) + len(line) + 1 > MAX:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)

    ok = True
    for i, chunk in enumerate(chunks, 1):
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=10)
            resp.raise_for_status()
            logger.info(
                f"Telegram sent chunk={i}/{len(chunks)} chars={len(chunk)} "
                f"parse_mode={parse_mode}"
            )
        except Exception as e:
            logger.error(f"Telegram send failed chunk={i}/{len(chunks)} chars={len(chunk)} error={e}")
            ok = False
    return ok

def send_paper(message: str):
    """Send a generic ASCR-H notification."""
    return _send(message)

def send_trade(side: str, ticker: str, shares: float, price: float,
               amount: float, reason: str, rating: str = ""):
    """Send trade execution notification."""
    emoji = "🟢 BUY" if side == "BUY" else "🔴 SELL"
    msg = (
        f"📝 <b>Paper Trade</b>\n\n"
        f"{emoji} <b>${ticker}</b>\n"
        f"Shares: {shares:.2f} @ ${price:.2f}\n"
        f"Amount: ${amount:,.0f}\n"
    )
    if rating:
        msg += f"Rating: {rating}\n"
    if reason:
        msg += f"Reason: {reason}\n"
    return _send(msg)

def send_daily_summary(cash: float, positions_value: float, total_equity: float,
                        num_positions: int, daily_return: float,
                        today_buys: list = None, today_sells: list = None):
    """Send end-of-day summary."""
    msg = (
        f"📊 <b>ASCR-H Daily Summary</b>\n\n"
        f"💰 Cash: ${cash:,.0f}\n"
        f"📈 Positions: ${positions_value:,.0f} ({num_positions} open)\n"
        f"💎 Total: ${total_equity:,.0f}\n"
        f"📉 Daily: {daily_return:+.1f}%\n"
    )
    if today_buys:
        msg += "\n<b>Buys:</b>\n"
        for b in today_buys:
            msg += f"  🟢 ${b['ticker']} {b['shares']:.1f}sh @ ${b['price']:.2f}\n"
    if today_sells:
        msg += "\n<b>Sells:</b>\n"
        for s in today_sells:
            msg += f"  🔴 ${s['ticker']} {s['shares']:.1f}sh @ ${s['price']:.2f} ({s.get('reason','')})\n"
    return _send(msg)

def send_weekly_performance(total_return: float, max_drawdown: float,
                             win_rate: float, num_open: int, top_positions: list = None):
    """Send weekly performance report."""
    msg = (
        f"📈 <b>ASCR-H Weekly Report</b>\n\n"
        f"Total Return: {total_return:+.1f}%\n"
        f"Max Drawdown: {max_drawdown:.1f}%\n"
        f"Win Rate: {win_rate:.0f}%\n"
        f"Open Positions: {num_open}\n"
    )
    if top_positions:
        msg += "\n<b>Top Positions:</b>\n"
        for p in top_positions[:5]:
            emoji = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            msg += f"  {emoji} ${p['ticker']}: {p['pnl_pct']:+.1f}%\n"
    return _send(msg)


def send_strategy_health(health: dict):
    """Send strategy health summary to Telegram."""
    w = health.get("warning_level", "unknown")
    emoji = {"healthy": "🟢", "monitoring": "🟡", "unstable": "🟠", "broken": "🔴"}.get(w, "⚪")
    mode = health.get("mode", "unknown")

    msg = (
        f"🔬 <b>Strategy Health — {mode}</b>\n\n"
        f"{emoji} <b>DQS: {health['overall_dqs']:.1f}/100 ({w.upper()})</b>\n\n"
        f"Buy: {health.get('buy_dqs',0):.1f} | Sell: {health.get('sell_dqs',0):.1f} | "
        f"Trim: {health.get('trim_dqs',0):.1f}\n"
        f"Hold: {health.get('hold_dqs',0):.1f} | NoBuy: {health.get('no_buy_dqs',0):.1f}\n"
        f"Ranking: {health.get('rating_quality_score',0):.1f} | "
        f"Stability: {health.get('stability_score',0):.1f}\n\n"
        f"FP: {health.get('false_positive_rate',0):.1f}% | "
        f"FN: {health.get('false_negative_rate',0):.1f}% | "
        f"Exit: {health.get('exit_quality_score',0):.1f}%\n"
        f"Sample: {health.get('sample_size', 0)}"
    )

    missed = health.get("missed_opportunities", [])
    if missed:
        msg += f"\n\n🎯 <b>Missed: {len(missed)} stocks</b>"
        for m in missed[:5]:
            msg += f"\n  {m['ticker']} [{m.get('rating','')}] +{m.get('max_gain',0):.0f}%"
        if len(missed) > 5:
            msg += f"\n  ... and {len(missed)-5} more"

    return _send(msg)


def send_validation_alert(alerts: list):
    """Send degradation alerts to Telegram."""
    if not alerts:
        return False
    msg = "⚠️ <b>Strategy Degradation Alerts</b>\n\n"
    for a in alerts:
        e = "🚨" if a["level"] == "critical" else "⚠️"
        msg += f"{e} <b>{a['type']}</b>\n{a['message']}\n→ {a['action']}\n\n"
    return _send(msg)


def send_regime_alert(report_text: str):
    """Send regime monitor alert."""
    return _send(report_text)
