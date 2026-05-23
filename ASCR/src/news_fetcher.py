"""News fetcher — RSS/free news sources for stock headlines."""
import requests
import re
import time
from datetime import datetime
from src import config, db
from src.utils import get_logger

logger = get_logger("news_fetcher")

# Free news sources
FINVIZ_NEWS = "https://finviz.com/quote.ashx?t={ticker}"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

def _fetch_finviz_news(ticker: str) -> list:
    """Scrape news headlines from Finviz."""
    url = FINVIZ_NEWS.format(ticker=ticker)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        # Simple regex to extract news table rows
        # Finviz news is in <table id="news-table">
        pattern = r'<a[^>]+href="([^"]+)"[^>]*class="tab-link-news"[^>]*>([^<]+)</a>'
        matches = re.findall(pattern, resp.text)

        results = []
        for url, headline in matches[:10]:
            if not db.is_news_seen(url):
                results.append({
                    "ticker": ticker,
                    "headline": headline.strip(),
                    "url": url,
                    "source": "finviz",
                    "published_at": datetime.now().strftime("%Y-%m-%d"),
                })
        return results
    except Exception as e:
        logger.warning(f"Finviz fetch failed for {ticker}: {e}")
        return []

def _fetch_google_news(query: str, ticker: str = "") -> list:
    """Fetch news from Google News RSS."""
    import xml.etree.ElementTree as ET

    url = GOOGLE_NEWS_RSS.format(query=query.replace(" ", "+"))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        results = []
        for item in root.findall(".//item")[:10]:
            title = item.find("title")
            link = item.find("link")
            pub_date = item.find("pubDate")

            if title is not None and link is not None:
                news_url = link.text
                if not db.is_news_seen(news_url):
                    results.append({
                        "ticker": ticker,
                        "headline": title.text,
                        "url": news_url,
                        "source": "google_news",
                        "published_at": pub_date.text if pub_date is not None else "",
                    })
        return results
    except Exception as e:
        logger.warning(f"Google News fetch failed for {query}: {e}")
        return []

def _keyword_relevance(headline: str) -> float:
    """Score headline relevance based on discovery keywords."""
    keywords = config.discovery_keywords()
    headline_lower = headline.lower()
    score = 0
    for kw in keywords:
        if kw.lower() in headline_lower:
            score += 10
    return min(score, 100)

def fetch_news(tickers: list = None) -> list:
    """Fetch news for all tickers."""
    if tickers is None:
        tickers = config.all_tickers()

    logger.info(f"Fetching news for {len(tickers)} tickers...")
    all_news = []

    for ticker in tickers:
        # Finviz news
        news = _fetch_finviz_news(ticker)
        for item in news:
            item["relevance_score"] = _keyword_relevance(item["headline"])
            db.add_news(ticker, item["headline"], item["url"], item["source"],
                       item["published_at"], item["relevance_score"])
            all_news.append(item)

        time.sleep(0.5)  # Rate limit

    # Also search for broad AI supply chain news
    broad_queries = [
        "AI data center infrastructure stock",
        "HBM DRAM NAND AI supply chain",
        "optical transceiver 800G 1.6T",
        "data center power cooling stock",
    ]
    for q in broad_queries:
        news = _fetch_google_news(q)
        for item in news:
            item["relevance_score"] = _keyword_relevance(item["headline"])
            if item["relevance_score"] > 0:
                db.add_news("", item["headline"], item["url"], item["source"],
                           item["published_at"], item["relevance_score"])
                all_news.append(item)
        time.sleep(1)

    logger.info(f"Fetched {len(all_news)} news items.")
    return all_news

if __name__ == "__main__":
    news = fetch_news()
    for n in news[:20]:
        print(f"[{n['ticker']}] {n['headline']} (relevance: {n['relevance_score']})")
