"""Additional event/news sources beyond Google News.

Primary wires and curated X accounts catch material signals that aggregators
often delay, bury, or miss. This module returns article-shaped dicts compatible
with event_pipeline.fetch_news().
"""
import os
import re
import time

import feedparser
import requests

from src import config
from src.event_deduper import deduplicate_articles, enrich_article_identity
from src.utils import get_logger

logger = get_logger("news_sources")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ASCR/1.0; +https://github.com/SteveJu/ASCR-AI-Supply-Chain-Radar)"
}

DOMAIN_SOURCE_TERMS = [
    "ai", "artificial intelligence", "data center", "datacenter", "gpu",
    "nvidia", "blackwell", "rubin", "hbm", "dram", "nand", "memory",
    "semiconductor", "chip", "silicon", "photonics", "optical", "800g",
    "1.6t", "nvlink", "cpo", "networking", "switch", "liquid cooling",
    "power management", "capacity reservation", "hyperscaler", "cloud",
]

SOURCE_EXCLUDE_TERMS = [
    "class action",
    "securities fraud",
    "investor alert",
    "shareholder alert",
    "investigation initiated",
    "law firm",
    "rosen law",
    "kahn swick",
    "robbins geller",
]

SOURCE_MATERIAL_ONLY_KEYWORDS = {
    "capacity reservation",
    "multi-year agreement",
    "prepayment",
    "warrant",
    "strategic partnership",
}


def _text_blob(*parts) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _is_relevant_text(text: str, tickers: list = None, keywords: list = None) -> bool:
    text = f" {text.lower()} "
    if any(term in text for term in SOURCE_EXCLUDE_TERMS):
        return False
    tickers = tickers if tickers is not None else config.all_tickers(include_benchmarks=True)
    keywords = keywords if keywords is not None else config.discovery_keywords()

    for ticker in tickers:
        ticker = str(ticker).upper()
        upper = text.upper()
        if f"${ticker}" in upper:
            return True
        if len(ticker) > 2 and f" {ticker} " in upper:
            return True

    for keyword in keywords:
        keyword = str(keyword).lower()
        if keyword in SOURCE_MATERIAL_ONLY_KEYWORDS:
            continue
        if keyword in text:
            return True

    for term in DOMAIN_SOURCE_TERMS:
        term = term.lower()
        if len(term) <= 3 and term.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
                return True
        elif term in text:
            return True
    return False


def _entry_url(entry) -> str:
    if entry.get("link"):
        return entry.get("link")
    links = entry.get("links") or []
    if links:
        return links[0].get("href", "")
    return ""


def fetch_rss_sources(max_per_feed: int = 30, cfg: dict = None) -> list:
    """Fetch configured primary/newswire RSS feeds."""
    cfg = cfg or config.news_sources()
    feeds = cfg.get("rss_feeds", []) or []
    tickers = config.all_tickers(include_benchmarks=True)
    keywords = config.discovery_keywords()
    articles = []

    for feed_cfg in feeds:
        if not feed_cfg.get("enabled", True):
            continue
        feed_id = feed_cfg.get("id") or feed_cfg.get("source") or "rss"
        source = feed_cfg.get("source") or feed_id
        max_items = min(int(feed_cfg.get("max_items", max_per_feed)), max_per_feed)
        try:
            parsed = feedparser.parse(feed_cfg["url"])
            for entry in parsed.entries[:max_items]:
                title = (entry.get("title") or "").strip()
                summary = entry.get("summary") or entry.get("description") or ""
                if not title:
                    continue
                if not _is_relevant_text(_text_blob(title, summary), tickers=tickers, keywords=keywords):
                    continue

                articles.append(enrich_article_identity({
                    "title": title,
                    "url": _entry_url(entry),
                    "published": entry.get("published") or entry.get("updated") or "",
                    "source_query": source,
                    "source_name": feed_cfg.get("label", feed_id),
                    "source_type": "rss",
                    "summary": summary,
                }))
        except Exception as exc:
            logger.warning(f"RSS source failed {feed_id}: {exc}")
        time.sleep(float(feed_cfg.get("sleep_seconds", 0.15)))

    return deduplicate_articles(articles)


def _x_headers(bearer_token: str) -> dict:
    return {"Authorization": f"Bearer {bearer_token}", "User-Agent": HEADERS["User-Agent"]}


def _x_get(url: str, bearer_token: str, params: dict = None) -> dict:
    resp = requests.get(url, headers=_x_headers(bearer_token), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_x_accounts(cfg: dict = None) -> list:
    """Fetch configured X accounts using the official X API v2.

    Requires the bearer token env var configured in config/news_sources.yaml.
    If no token is present, this source is skipped cleanly.
    """
    cfg = cfg or config.news_sources()
    x_cfg = cfg.get("x_accounts", {}) or {}
    if not x_cfg.get("enabled", False):
        return []

    token_env = x_cfg.get("bearer_token_env", "X_BEARER_TOKEN")
    bearer_token = os.getenv(token_env, "")
    if not bearer_token:
        logger.info(f"X source skipped: {token_env} not set")
        return []

    tickers = config.all_tickers(include_benchmarks=True)
    keywords = config.discovery_keywords()
    max_items = max(5, min(int(x_cfg.get("max_items_per_user", 10)), 100))
    min_likes = int(x_cfg.get("min_like_count", 0))
    articles = []

    for user_cfg in x_cfg.get("users", []) or []:
        username = str(user_cfg.get("username", "")).lstrip("@")
        if not username:
            continue
        source = user_cfg.get("source") or "x_fintwit"
        label = user_cfg.get("label") or username
        try:
            user = _x_get(
                f"https://api.x.com/2/users/by/username/{username}",
                bearer_token,
                params={"user.fields": "verified,public_metrics"},
            ).get("data", {})
            user_id = user.get("id")
            if not user_id:
                continue

            timeline = _x_get(
                f"https://api.x.com/2/users/{user_id}/tweets",
                bearer_token,
                params={
                    "max_results": max_items,
                    "exclude": "replies,retweets",
                    "tweet.fields": "created_at,public_metrics,entities,lang",
                },
            )
            for tweet in timeline.get("data", []) or []:
                text = (tweet.get("text") or "").strip()
                metrics = tweet.get("public_metrics") or {}
                if metrics.get("like_count", 0) < min_likes:
                    continue
                if not _is_relevant_text(text, tickers=tickers, keywords=keywords):
                    continue

                tweet_id = tweet.get("id", "")
                articles.append(enrich_article_identity({
                    "title": f"X @{username}: {text}",
                    "url": f"https://x.com/{username}/status/{tweet_id}" if tweet_id else f"https://x.com/{username}",
                    "published": tweet.get("created_at", ""),
                    "source_query": source,
                    "source_name": f"X / {label}",
                    "source_type": "x",
                    "author": username,
                    "public_metrics": metrics,
                }))
        except Exception as exc:
            logger.warning(f"X source failed @{username}: {exc}")
        time.sleep(float(x_cfg.get("sleep_seconds", 0.25)))

    return deduplicate_articles(articles)


def fetch_additional_sources(max_per_feed: int = 30, include_x: bool = True, cfg: dict = None) -> list:
    """Fetch all configured non-Google sources."""
    cfg = cfg or config.news_sources()
    articles = []
    articles.extend(fetch_rss_sources(max_per_feed=max_per_feed, cfg=cfg))
    if include_x:
        articles.extend(fetch_x_accounts(cfg=cfg))
    articles = deduplicate_articles(articles)
    if articles:
        by_source = {}
        for article in articles:
            source = article.get("source_query", "unknown")
            by_source[source] = by_source.get(source, 0) + 1
        logger.info(f"Additional sources: {len(articles)} articles {by_source}")
    return articles
