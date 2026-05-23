"""Insider Trading Tracker — Form 4 filings from SEC EDGAR.

CEO/CFO buying their own stock = strongest bullish signal.
Large insider sells = potential warning (but often just comp plans).
"""
import os
import re
import time
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from src import config, db
from src.utils import get_logger

logger = get_logger("insider")

HEADERS = {"User-Agent": "StockRadar research@example.com"}

# CIK map (reuse from event_pipeline)
TICKER_CIK = {
    "MU": "0000723125", "AMAT": "0000006951", "MRVL": "0001058057",
    "AVGO": "0001649338", "LRCX": "0000707549", "KLAC": "0000319201",
    "SNPS": "0000883241", "CDNS": "0000813672", "ANET": "0001313545",
    "VRT": "0001674101", "GLW": "0000024741", "COHR": "0000820318",
    "LITE": "0001704715", "CIEN": "0000936395", "VST": "0001692819",
    "CEG": "0001868275", "NEE": "0000753308", "TER": "0000097210",
    "STX": "0001137789", "WDC": "0000106040", "ASML": "0000937966",
    "NVDA": "0001045810",
}


def fetch_insider_trades(days=7):
    """Fetch recent Form 4 filings for tracked tickers."""
    trades = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    for ticker, cik in TICKER_CIK.items():
        try:
            url = (f"https://efts.sec.gov/LATEST/search-index?"
                   f"q=%22{cik}%22&forms=4&dateRange=custom&startdt={cutoff}")
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])

            for h in hits:
                src = h.get("_source", {})
                names = src.get("display_names", [])
                file_date = src.get("file_date", "")

                # Parse insider name (first non-company name)
                insider = ""
                for n in names:
                    if ticker not in n.upper() and "INC" not in n.upper() and "CORP" not in n.upper():
                        insider = re.sub(r'\s*\(CIK.*\)', '', n).strip()
                        break

                if insider and file_date:
                    trades.append({
                        "ticker": ticker,
                        "insider": insider,
                        "file_date": file_date,
                        "form": "4",
                    })
            time.sleep(0.3)  # SEC rate limit
        except Exception as e:
            logger.warning(f"Form 4 {ticker}: {e}")

    logger.info(f"Found {len(trades)} insider filings in last {days} days")
    return trades


def fetch_analyst_changes():
    """Fetch analyst rating changes from Yahoo Finance."""
    import yfinance as yf
    changes = []

    for ticker in config.all_tickers():
        try:
            t = yf.Ticker(ticker)
            recs = t.recommendations
            if recs is None or len(recs) < 2:
                continue

            current = recs.iloc[0]
            prev = recs.iloc[1]

            # Detect upgrade/downgrade
            curr_buy = (current.get("strongBuy", 0) or 0) + (current.get("buy", 0) or 0)
            prev_buy = (prev.get("strongBuy", 0) or 0) + (prev.get("buy", 0) or 0)
            curr_sell = (current.get("sell", 0) or 0) + (current.get("strongSell", 0) or 0)
            prev_sell = (prev.get("sell", 0) or 0) + (prev.get("strongSell", 0) or 0)

            delta_buy = curr_buy - prev_buy
            delta_sell = curr_sell - prev_sell

            if abs(delta_buy) >= 2 or abs(delta_sell) >= 2:
                changes.append({
                    "ticker": ticker,
                    "buy_change": delta_buy,
                    "sell_change": delta_sell,
                    "total_buy": curr_buy,
                    "total_sell": curr_sell,
                    "total_hold": current.get("hold", 0) or 0,
                })
            time.sleep(0.3)
        except Exception:
            pass

    logger.info(f"Found {len(changes)} analyst rating changes")
    return changes


def fetch_reddit_sentiment(tickers=None, subreddits=None):
    """Fetch Reddit mentions for tracked tickers."""
    if not tickers:
        tickers = config.all_tickers()
    if not subreddits:
        subreddits = ["wallstreetbets", "stocks", "investing"]

    mentions = {}
    headers = {"User-Agent": "StockRadar/1.0"}

    for sub in subreddits:
        try:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit=100"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            posts = resp.json().get("data", {}).get("children", [])

            for p in posts:
                d = p.get("data", {})
                title = d.get("title", "").upper()
                text = d.get("selftext", "").upper()
                ups = d.get("ups", 0)
                combined = f"{title} {text}"

                for ticker in tickers:
                    if f"${ticker}" in combined or f" {ticker} " in combined:
                        if ticker not in mentions:
                            mentions[ticker] = {"count": 0, "ups": 0, "posts": []}
                        mentions[ticker]["count"] += 1
                        mentions[ticker]["ups"] += ups
                        if len(mentions[ticker]["posts"]) < 3:
                            mentions[ticker]["posts"].append(d.get("title", ""))

            time.sleep(1)  # Reddit rate limit
        except Exception as e:
            logger.warning(f"Reddit {sub}: {e}")

    logger.info(f"Reddit: {len(mentions)} tickers mentioned")
    return mentions


if __name__ == "__main__":
    print("=== Insider Trades ===")
    trades = fetch_insider_trades(days=30)
    for t in trades[:10]:
        print(f"  {t['file_date']} {t['ticker']:6s} {t['insider']}")

    print("\n=== Analyst Changes ===")
    changes = fetch_analyst_changes()
    for c in changes:
        print(f"  {c['ticker']:6s} buy{c['buy_change']:+d} sell{c['sell_change']:+d} "
              f"(now {c['total_buy']}B/{c['total_hold']}H/{c['total_sell']}S)")

    print("\n=== Reddit ===")
    mentions = fetch_reddit_sentiment()
    for t, m in sorted(mentions.items(), key=lambda x: -x[1]["ups"])[:10]:
        print(f"  {t:6s} {m['count']}mentions ↑{m['ups']} | {m['posts'][0] if m['posts'] else ''}")
