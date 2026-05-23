"""Discovery Engine — find new AI supply chain stocks.

Separate from Analysis Engine. Runs independently.
Sources:
1. Google News (broad AI supply chain queries)
2. SEC 13F (what are big funds buying?)
3. Reddit mentions (emerging names)
4. Counterparties from existing events (supplier X mentioned → add X)

Output: validates and adds to universe.yaml, notifies via Telegram.
"""
import os
import re
import sys
import time
import hashlib
import feedparser
import yfinance as yf
import yaml
from datetime import datetime
from urllib.parse import quote

from src import config, db
from src.telegram_notifier import send
from src.utils import get_logger

logger = get_logger("discovery_engine")

# Broad discovery queries (not ticker-specific)
DISCOVERY_QUERIES = [
    "AI supply chain new company 2026",
    "AI infrastructure emerging stock",
    "NVIDIA new supplier partner",
    "AI data center new entrant",
    "AI semiconductor startup IPO",
    "AI optical networking new company",
    "AI chip cooling power new stock",
    "AI custom silicon ASIC new company",
    "hyperscaler infrastructure supplier",
    "AI server supply chain",
]

# Sectors for classification
AI_SECTORS = {"Technology", "Industrials", "Utilities", "Communication Services", "Energy"}

AI_INDUSTRY_KEYWORDS = {
    "semiconductor", "chip", "electronic", "communication equipment",
    "data processing", "scientific instrument", "electrical equipment",
    "software", "networking", "optical", "photonic", "cloud",
    "information technology", "computer hardware",
}

SECTOR_MAP = {
    "semiconductor": "memory",
    "electronic": "optical",
    "communication": "networking",
    "electrical": "power_cooling",
    "software": "eda_ip",
    "utilities": "power_cooling",
    "energy": "power_cooling",
}


def _current_universe() -> set:
    """Get all tickers currently in universe."""
    u = config.universe()
    tickers = set()
    for sval in u.get("sectors", {}).values():
        if isinstance(sval, dict):
            tickers.update(sval.get("tickers", []))
    return tickers


def scan_news_for_candidates() -> dict:
    """Scan Google News for potential new tickers. Returns {ticker: context}."""
    candidates = {}
    current = _current_universe()
    seen_titles = set()

    for q in DISCOVERY_QUERIES:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                key = title.lower()
                if key in seen_titles:
                    continue
                seen_titles.add(key)

                # Extract potential tickers (1-5 uppercase letters in parentheses or after $)
                found = re.findall(r'\b([A-Z]{1,5})\b', title)
                found += re.findall(r'\$([A-Z]{1,5})\b', title)
                for t in set(found):
                    if t not in current and t not in candidates and len(t) >= 2:
                        # Skip common words that look like tickers
                        skip = {"AI", "CEO", "SEC", "IPO", "NYSE", "US", "UK", "EU",
                                "GDP", "CPI", "ETF", "EPS", "PE", "FY", "YOY", "QOQ",
                                "THE", "AND", "FOR", "NEW", "BIG", "TOP", "ALL", "HAS"}
                        if t not in skip:
                            candidates[t] = title
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"News scan '{q}': {e}")

    logger.info(f"News scan: {len(candidates)} candidate tickers from {len(DISCOVERY_QUERIES)} queries")
    return candidates


# Common company name → ticker mapping
COMPANY_TICKER_MAP = {
    "NVIDIA": "NVDA", "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
    "AMAZON": "AMZN", "MICROSOFT": "MSFT", "APPLE": "AAPL",
    "META": "META", "FACEBOOK": "META", "TESLA": "TSLA",
    "OPENAI": None, "SPACEX": None,  # private
    "SAMSUNG": None,  # Korean
    "SK HYNIX": None, "TSMC": "TSM",
    "BROADCOM": "AVGO", "INTEL": "INTC", "AMD": "AMD",
    "QUALCOMM": "QCOM", "MICRON": "MU", "ORACLE": "ORCL",
    "DELL": "DELL", "IBM": "IBM", "NTT": "NTT",
    "SUPERMICRO": "SMCI", "SUPER MICRO": "SMCI",
}


def scan_events_for_counterparties() -> dict:
    """Find counterparties mentioned in events that aren't in universe."""
    candidates = {}
    current = _current_universe()

    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT counterparty, ticker, headline
            FROM events
            WHERE counterparty IS NOT NULL AND counterparty != ''
            AND date >= date('now', '-30 days')
        """).fetchall()

    for r in rows:
        cp = r["counterparty"].strip()
        # Try direct ticker match
        cp_upper = cp.upper()
        if re.match(r'^[A-Z]{1,5}$', cp_upper):
            resolved = cp_upper
        else:
            # Try name→ticker mapping
            resolved = COMPANY_TICKER_MAP.get(cp_upper)
            if resolved is None:
                # Try partial match
                for name, ticker in COMPANY_TICKER_MAP.items():
                    if name in cp_upper or cp_upper in name:
                        resolved = ticker
                        break

        if resolved and resolved not in current and resolved not in candidates:
            candidates[resolved] = f"Counterparty of {r['ticker']}: {r['headline']}"

    logger.info(f"Counterparty scan: {len(candidates)} candidates")
    return candidates


def validate_candidate(ticker: str) -> dict | None:
    """Validate a ticker: real stock, AI-related, >$1B market cap."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("previousClose")
        mcap = info.get("marketCap", 0) or 0
        sector = info.get("sector", "")
        industry = (info.get("industry", "") or "").lower()
        name = info.get("shortName", ticker)

        if not price or price < 1:
            return None
        if mcap < 1e9:
            return None
        if sector not in AI_SECTORS:
            return None

        # Industry keyword match
        industry_match = any(kw in industry for kw in AI_INDUSTRY_KEYWORDS)
        name_match = any(kw in name.lower() for kw in
                        ["semi", "chip", "data", "cloud", "network", "optical",
                         "power", "energy", "cooling", "silicon", "compute", "ai"])

        if not industry_match and not name_match:
            return None

        # Determine sector
        target_sector = "new_additions"
        for kw, sect in SECTOR_MAP.items():
            if kw in industry:
                target_sector = sect
                break

        return {
            "ticker": ticker,
            "name": name,
            "sector": sector,
            "industry": industry,
            "market_cap": mcap,
            "price": price,
            "target_sector": target_sector,
        }
    except Exception:
        return None


def add_to_universe(validated: dict) -> bool:
    """Add a validated ticker to universe.yaml."""
    ticker = validated["ticker"]
    target = validated["target_sector"]

    upath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "universe.yaml")
    with open(upath) as f:
        u = yaml.safe_load(f)

    if target not in u.get("sectors", {}):
        u.setdefault("sectors", {})["new_additions"] = {"name": "New Additions", "tickers": []}
        target = "new_additions"

    sect = u["sectors"][target]
    if isinstance(sect, dict) and ticker not in sect.get("tickers", []):
        sect.setdefault("tickers", []).append(ticker)
        with open(upath, "w") as f:
            yaml.dump(u, f, default_flow_style=False)
        return True
    return False


def run_discovery() -> dict:
    """Full discovery run. Returns summary."""
    logger.info("=== Discovery Engine ===")

    current = _current_universe()
    logger.info(f"Current universe: {len(current)} tickers")

    # Gather candidates from all sources
    all_candidates = {}
    all_candidates.update(scan_news_for_candidates())
    all_candidates.update(scan_events_for_counterparties())

    # Remove already-known
    new_candidates = {k: v for k, v in all_candidates.items() if k not in current}
    logger.info(f"Total new candidates: {len(new_candidates)}")

    # Validate top candidates (limit to avoid hammering yfinance)
    added = []
    checked = 0
    for ticker, context in list(new_candidates.items())[:20]:
        checked += 1
        info = validate_candidate(ticker)
        if info:
            if add_to_universe(info):
                info["context"] = context
                added.append(info)
                logger.info(f"  ✅ Added {ticker}: {info['name']} "
                           f"(${info['market_cap']/1e9:.1f}B, {info['industry']})")
        time.sleep(0.5)

    # Fetch prices for new tickers
    if added:
        try:
            from src.price_fetcher import fetch_prices
            fetch_prices([a["ticker"] for a in added], period="3mo")
        except Exception as e:
            logger.warning(f"Price fetch for new: {e}")

        # Notify via Telegram
        lines = [f"🔍 <b>Discovery Engine: {len(added)} new additions</b>\n"]
        for a in added:
            lines.append(f"  ✅ <b>{a['ticker']}</b> — {a['name']}")
            lines.append(f"     ${a['market_cap']/1e9:.1f}B | {a['industry']}")
            lines.append(f"     Source: {a['context']}")
            lines.append("")
        send("\n".join(lines))

    # Daily mention scan for anomaly detection (sector_discovery)
    try:
        from src.sector_discovery import run_daily_scan
        run_daily_scan()
    except Exception as e:
        logger.warning(f"Sector discovery daily scan: {e}")

    result = {
        "candidates_found": len(new_candidates),
        "checked": checked,
        "added": len(added),
        "tickers_added": [a["ticker"] for a in added],
    }
    logger.info(f"Discovery done: {result}")
    return result


if __name__ == "__main__":
    result = run_discovery()
    print(f"Found {result['candidates_found']} candidates, added {result['added']}")
