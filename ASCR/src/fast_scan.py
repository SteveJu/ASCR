"""Fast scan — lightweight mid-day check for breaking signals.

Runs at 12:30 PM ET. Only checks:
1. New 8-K filings (material agreements, FD disclosure)
2. Breaking news headlines
3. Does NOT re-run full Sonnet analysis — just flags urgents

If something urgent found → push to Telegram immediately.
"""
import os
import sys
import json
import time
import hashlib
import feedparser
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from src import config, db
from src.telegram_notifier import send
from src.utils import get_logger

logger = get_logger("fast_scan")

# SEC 8-K item codes -> human-readable summaries
ITEM_SUMMARY = {
    "1.01": "Material agreement",
    "1.02": "Termination of material agreement",
    "1.03": "Bankruptcy or receivership",
    "2.01": "Asset acquisition or disposition",
    "2.02": "Results of operations",
    "2.03": "Material financial obligation",
    "2.04": "Off-balance-sheet obligation trigger",
    "2.05": "Restructuring or impairment costs",
    "2.06": "Material impairment",
    "3.01": "Delisting or listing standard issue",
    "3.02": "Unregistered securities sale",
    "3.03": "Security holder rights change",
    "4.01": "Auditor change",
    "4.02": "Financial statements not reliable",
    "5.01": "Change in control",
    "5.02": "Officer or director change",
    "5.03": "Charter or bylaws amendment",
    "5.07": "Shareholder voting results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}


def summarize_8k_items(items_str: str, ticker: str) -> str:
    """Generate one-line summary from 8-K item codes."""
    items = [i.strip() for i in items_str.split(";") if i.strip()]
    summaries = []
    for item in items:
        # Extract item code (e.g. "2.02: Results of Operations" → "2.02")
        code = item.split(":")[0].strip()
        if code in ITEM_SUMMARY:
            summaries.append(ITEM_SUMMARY[code])
        else:
            summaries.append(item)

    if not summaries:
        return f"{ticker} filed an 8-K"
    return f"{ticker}: {', '.join(summaries)}"

# High-priority breaking news queries
BREAKING_QUERIES = [
    "NVIDIA deal investment partnership today",
    "AI chip contract signed billion",
    "semiconductor AI deal announced today",
    "data center AI infrastructure deal breaking",
]


def scan_8k_urgent():
    """Check for new 8-K filings with material items since last scan."""
    from src.event_pipeline import fetch_sec_filings, TICKER_CIK
    tickers = list(TICKER_CIK.keys())
    filings = fetch_sec_filings(tickers, days=1)

    # Only care about material items
    urgent = [f for f in filings if any(
        it in ("1.01", "2.01", "7.01") for it in f.get("important_items", [])
    )]

    # Dedup against already-processed
    new_urgent = []
    for f in urgent:
        h = hashlib.md5(f"{f['ticker']}{f['date']}{f['items']}".encode()).hexdigest()
        with db.get_conn() as conn:
            existing = conn.execute("SELECT id FROM events WHERE hash=?", (h,)).fetchone()
        if not existing:
            new_urgent.append(f)

    return new_urgent


def scan_breaking_news():
    """Quick scan for breaking AI supply chain news."""
    articles = []
    seen = set()
    for q in BREAKING_QUERIES:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                # Only care about today's news
                published = entry.get("published", "")
                articles.append({"title": title, "published": published})
            time.sleep(0.3)
        except Exception:
            pass
    return articles


def run_fast_scan():
    """Run fast mid-day scan."""
    logger.info("=== Fast Scan ===")

    # 1. Check 8-K filings
    urgent_filings = scan_8k_urgent()
    if urgent_filings:
        lines = [f"🚨 <b>Intraday 8-K Alerts ({len(urgent_filings)})</b>\n"]
        for f in urgent_filings:
            summary = summarize_8k_items(f.get("item_descriptions", ""), f["ticker"])
            lines.append(f"📋 <b>{f['ticker']}</b> — {summary}")
            lines.append(f"   {f['item_descriptions']}")
            lines.append(f"   Filed: {f['date']} | <a href=\"{f.get('url','')}\">view</a>")
            lines.append("")
        send("\n".join(lines))
        logger.info(f"Sent {len(urgent_filings)} urgent 8-K alerts")

    # 2. Breaking news (just log, don't burn Sonnet tokens mid-day)
    breaking = scan_breaking_news()
    logger.info(f"Breaking news: {len(breaking)} articles scanned")

    # If very high-signal keywords appear, alert
    hot_keywords = ["billion deal", "signed contract", "strategic investment",
                    "NVIDIA invest", "NVIDIA partner", "acquisition"]
    hot_news = []
    for a in breaking:
        title_lower = a["title"].lower()
        if any(kw in title_lower for kw in hot_keywords):
            hot_news.append(a)

    if hot_news:
        lines = [f"🔥 <b>Intraday Hot News ({len(hot_news)})</b>\n"]
        for n in hot_news[:5]:
            lines.append(f"  📰 {n['title']}")
        send("\n".join(lines))
        logger.info(f"Sent {len(hot_news)} breaking news alerts")

    # 3. Bubble burst check (position monitoring moved to ASCR-H)
    try:
        from src.bubble_detector import check_bubble_burst
        bubble = check_bubble_burst(days=1)
        if bubble["level"]:
            send(f"<b>{bubble['message']}</b>")
            if bubble["action"] == "liquidate":
                send("💀 <b>MELTDOWN: next daily run will liquidate positions</b>")
            logger.info(f"Bubble: {bubble['level']}")
    except Exception as e:
        logger.warning(f"Bubble check: {e}")

    return {"urgent_filings": len(urgent_filings), "hot_news": len(hot_news)}


if __name__ == "__main__":
    run_fast_scan()
