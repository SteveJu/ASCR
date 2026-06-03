"""Weekly performance report — generated Friday after market close."""
import os
from datetime import datetime, timedelta
from src import db
from src.decision_logger import get_benchmark_price, BENCHMARK_TICKER
from src.utils import get_logger

logger = get_logger("weekly_report")


def _get_live_price(ticker: str) -> float:
    """Get live price with ASCR DB fallback."""
    try:
        import yfinance as yf
        quote = yf.Ticker(ticker)
        info = quote.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if not price:
            hist = quote.history(period="1d")
            if len(hist) > 0:
                price = float(hist["Close"].iloc[-1])
        if price and price > 0:
            return float(price)
    except Exception as exc:
        logger.warning(f"Weekly price fetch failed ticker={ticker}: {exc}")

    prices = db.read_radar_prices(ticker, days=1)
    return float(prices[0]["close"]) if prices else 0.0


def _get_latest_scores() -> list[dict]:
    """Latest ASCR scores, sorted by event/research opportunity."""
    return db.read_radar_scores()


def generate_weekly_report() -> str:
    """Generate comprehensive weekly report."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📈 <b>Event Radar Weekly Report — {today}</b>\n"]

    # Account status
    account = db.get_account()
    positions = db.get_all_positions("open")
    pos_value = sum(p["quantity"] * _get_live_price(p["ticker"]) for p in positions)
    cash = account["cash"]
    total = cash + pos_value
    ret = (total / 10000 - 1) * 100
    peak = account.get("peak_equity", 10000)
    dd = (total - peak) / peak * 100 if peak > 0 else 0

    lines.append(f"💎 <b>${total:,.0f}</b> ({ret:+.1f}% total)")
    lines.append(f"💰 Cash: ${cash:,.0f} | Positions: ${pos_value:,.0f}")
    lines.append(f"📉 Max DD: {dd:.1f}% from peak ${peak:,.0f}\n")

    # QQQ benchmark from paper-trading inception.
    qqq_price = _get_live_price("QQQ")
    with db.get_conn() as conn:
        inception_row = conn.execute("SELECT MIN(date) FROM paper_orders").fetchone()
    inception_date = inception_row[0] if inception_row and inception_row[0] else today
    with db.radar_conn() as conn:
        qqq_start = conn.execute("""
            SELECT close FROM prices
            WHERE ticker='QQQ' AND date<=?
            ORDER BY date DESC LIMIT 1
        """, (inception_date,)).fetchone()
    if qqq_start and qqq_price:
        qqq_ret = (qqq_price / qqq_start[0] - 1) * 100
        alpha = ret - qqq_ret
        lines.append(f"📊 QQQ: {qqq_ret:+.1f}% | <b>Alpha: {alpha:+.1f}%</b>\n")

    # Holdings detail
    if positions:
        lines.append("<b>📋 Holdings:</b>")
        sorted_pos = sorted(positions, key=lambda p:
            (_get_live_price(p["ticker"]) - p["avg_entry_price"]) / p["avg_entry_price"] * 100
            if p["avg_entry_price"] > 0 else 0, reverse=True)
        for p in sorted_pos:
            price = _get_live_price(p["ticker"])
            pnl = (price - p["avg_entry_price"]) / p["avg_entry_price"] * 100 if p["avg_entry_price"] > 0 else 0
            val = p["quantity"] * price
            weight = val / total * 100 if total > 0 else 0
            peak_p = p.get("max_price_since_entry") or price
            off_peak = (price - peak_p) / peak_p * 100 if peak_p > 0 else 0
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {emoji} <b>{p['ticker']}</b>: {pnl:+.1f}% "
                        f"(${val:,.0f}, {weight:.0f}% of port)")
            lines.append(f"    Entry ${p['avg_entry_price']:.2f} → ${price:.2f} "
                        f"| Peak ${peak_p:.2f} ({off_peak:+.1f}%)")
        lines.append("")

    # This week's trades
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    orders = db.get_orders(days=7)
    if orders:
        lines.append("<b>📝 This Week's Trades:</b>")
        for o in orders:
            emoji = "🟢" if o["side"] == "BUY" else "🔴"
            lines.append(f"  {emoji} {o['side']} {o['ticker']} "
                        f"{o['quantity']:.1f}sh @ ${o['price']:.2f} — {o.get('reason','')}")
        lines.append("")
    else:
        lines.append("<i>No trades this week.</i>\n")

    # ASCR ranking snapshot
    scores = _get_latest_scores()
    if scores:
        lines.append("<b>🏆 Top-10 Radar Scores:</b>")
        held = {p["ticker"] for p in positions}
        for i, s in enumerate(scores[:10]):
            marker = "⭐" if s["ticker"] in held else "  "
            opp = s.get("opportunity_score", s.get("opportunity", 0)) or 0
            evidence = s.get("evidence_score", s.get("evidence", 0)) or 0
            asymmetry = s.get("asymmetry_score", s.get("asymmetry", 0)) or 0
            lines.append(f"  {i+1}. {marker}{s['ticker']:6s} opp={opp:.0f} "
                        f"ev={evidence:.0f} asym={asymmetry:.0f} [{s['rating']}]")
        lines.append("")

    # Regime check
    try:
        from src.regime_monitor import compute_regime_signals
        regime = compute_regime_signals()
        msg = regime["recommendation_message"]
        lines.append(f"<b>🌡️ Regime:</b> {msg}")
    except Exception as e:
        logger.warning(f"Weekly report regime section failed: {e}")

    # Universe scan results (Friday only)
    try:
        import sys
        sys.path.insert(0, os.environ.get("ASCR_PROJECT_DIR", "../ASCR"))
        from src.universe_scanner import scan_events_for_unknown
        unknown = scan_events_for_unknown()
        if unknown:
            lines.append(f"\n<b>🔍 Tickers found outside the universe:</b>")
            lines.append(f"  {', '.join(unknown[:10])}")
            lines.append(f"  (use /add TICKER to add)")
    except Exception as e:
        logger.warning(f"Weekly report universe scan section failed: {e}")

    # Upcoming earnings for holdings
    try:
        from src.event_pipeline import fetch_earnings_calendar
        held_tickers = [p["ticker"] for p in positions]
        earnings = fetch_earnings_calendar(held_tickers)
        upcoming = [e for e in earnings if e.get("earnings_date") and e.get("source") == "calendar"]
        if upcoming:
            lines.append(f"\n<b>📅 Upcoming earnings for holdings:</b>")
            for e in sorted(upcoming, key=lambda x: x.get("earnings_date", "Z")):
                lines.append(f"  {e['ticker']}: {e['earnings_date']}")
    except Exception as e:
        logger.warning(f"Weekly report earnings section failed: {e}")

    # Weekly data cleanup
    try:
        import sys
        sys.path.insert(0, os.environ.get("ASCR_PROJECT_DIR", "../ASCR"))
        from src.data_cleanup import run_cleanup
        cleanup = run_cleanup()
        if sum(cleanup.values()) > 0:
            lines.append(f"\n🧹 Data cleanup: removed {sum(cleanup.values())} rows")
    except Exception as e:
        logger.warning(f"Weekly report data cleanup section failed: {e}")

    report = "\n".join(lines)
    logger.info(f"Weekly report generated")
    return report
