"""Event Pipeline — fetch news + SEC filings, analyze with Claude, output structured events.

Architecture:
  1. Google News RSS → headlines per ticker/sector
  2. SEC EDGAR → 8-K, Form 4 filings
  3. Claude Sonnet → classify & extract supply chain signals
  4. Output: structured events → evidence scoring → buy signals

Cost control:
  - Haiku for headline filtering (cheap, high volume)
  - Sonnet for deep analysis (only on relevant articles)
  - ~$0.10-0.30/day estimated
"""
import os
import json
import time
import hashlib
import sqlite3
import feedparser
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from anthropic import Anthropic
from src import config, db
from src.llm_usage import log_ai_call
from src.utils import get_logger

logger = get_logger("event_pipeline")

LLM_RETRY_ATTEMPTS = max(1, int(os.getenv("ASCR_LLM_RETRIES", "3")))
LLM_RETRY_BASE_SECONDS = max(
    0.0,
    float(os.getenv("ASCR_LLM_RETRY_BASE_SECONDS", "1.5")),
)
LLM_RETRY_MAX_SECONDS = max(
    LLM_RETRY_BASE_SECONDS,
    float(os.getenv("ASCR_LLM_RETRY_MAX_SECONDS", "12")),
)

# Load API key
def _get_client():
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    return Anthropic(api_key=key)


def _with_retry(label: str, fn, attempts: int = None):
    """Run a transient external call with exponential backoff."""
    attempts = attempts or LLM_RETRY_ATTEMPTS
    last_error = None
    for attempt in range(attempts):
        try:
            result = fn()
            if result is None:
                raise RuntimeError(f"{label} returned no result")
            return result
        except Exception as exc:
            last_error = exc
            if attempt >= attempts - 1:
                break
            sleep_s = min(LLM_RETRY_MAX_SECONDS, LLM_RETRY_BASE_SECONDS * (2 ** attempt))
            logger.warning(
                f"{label} failed (attempt {attempt + 1}/{attempts}); "
                f"retrying in {sleep_s:.1f}s: {exc}"
            )
            if sleep_s > 0:
                time.sleep(sleep_s)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")

# ─── Database ───

def init_events_db():
    with db.get_conn() as conn:
        db.ensure_events_table(conn)

# ─── News Fetching ───

# AI supply chain search queries - broader than individual tickers
SECTOR_QUERIES = [
    # === Deals & Contracts ===
    "NVIDIA contract deal partnership supply",
    "AI data center deal contract billion",
    "HBM memory supply shortage capacity",
    "AI chip supply chain investment",
    # === Earnings & Guidance ===
    "Micron earnings beat guidance raised AI",
    "Broadcom earnings AI revenue segment",
    "ASML earnings order backlog EUV",
    "Vertiv earnings data center demand",
    "Coherent Lumentum earnings optical AI",
    "semiconductor earnings AI demand outlook",
    "data center REIT earnings FFO guidance",
    # === Institutional Holdings / 13F ===
    "13F filing AI semiconductor holdings",
    "Berkshire Hathaway portfolio AI stocks",
    "ARK Invest buy sell AI semiconductor",
    "Soros fund AI technology holdings",
    "hedge fund top picks AI infrastructure",
    "institutional buying semiconductor AI",
    "insider buying AI chip data center",
    # === Sector-specific ===
    "optical networking AI data center demand",
    "power cooling data center infrastructure",
    "semiconductor equipment AI capex",
    "AI server demand hyperscaler order",
    "HBM DRAM NAND price forecast shortage",
    "AI inference chip demand deployment",
    "copper power grid data center bottleneck",
    # === Key companies (our universe) ===
    "Micron HBM AI memory",
    "Broadcom AI networking custom chip",
    "Marvell AI custom silicon",
    "Arista Cisco AI networking switch",
    "Vertiv Eaton data center power cooling",
    "ASML EUV lithography AI demand",
    "Corning Coherent Lumentum optical AI",
    "ARM CDNS SNPS chip design AI",
    "Equinix Digital Realty data center AI",
    # === NVIDIA focus (supply chain signals) ===
    "NVIDIA investment stake acquisition minority",
    "NVIDIA strategic partnership supply agreement",
    "NVIDIA NVLink optical interconnect supplier",
    "NVIDIA Blackwell Rubin supply chain",
    "NVIDIA capex spending infrastructure billion",
    "Jensen Huang investment partnership announce",
]

def fetch_news(max_per_query=20) -> list:
    """Fetch news from Google News RSS."""
    all_articles = []
    seen_titles = set()

    for query in SECTOR_QUERIES:
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ASCR/1.0)"},
                timeout=10,
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:max_per_query]:
                title = entry.get("title", "").strip()
                # Dedup by title similarity
                title_key = title.lower()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

                all_articles.append({
                    "title": title,
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "source_query": query,
                })
            time.sleep(0.3)  # rate limit
        except Exception as e:
            logger.warning(f"RSS fetch failed for '{query}': {e}")

    logger.info(f"Fetched {len(all_articles)} unique articles from {len(SECTOR_QUERIES)} queries")
    return all_articles


# 8-K item codes that matter for AI supply chain investing
IMPORTANT_8K_ITEMS = {
    "1.01": "Entry into Material Agreement",      # 🔥 contracts, partnerships
    "1.02": "Termination of Material Agreement",   # 🔴 lost deal
    "2.01": "Completion of Acquisition",           # 🔥 M&A
    "2.02": "Results of Operations",               # 📊 earnings
    "2.05": "Costs of Restructuring",              # ⚠️ layoffs
    "2.06": "Material Impairment",                 # 🔴 writedown
    "3.02": "Unregistered Sales of Equity",        # ⚠️ dilution
    "4.01": "Changes in Registrant's Auditor",     # 🔴 red flag
    "5.02": "Departure of Directors/Officers",     # ⚠️ management change
    "7.01": "Regulation FD Disclosure",            # 🔥 material nonpublic info
    "8.01": "Other Events",                        # varies
}

# CIK mapping for our universe
TICKER_CIK = {
    "MU": "0000723125", "SNDK": "0001000180", "STX": "0001137789", "WDC": "0000106040",
    "CIEN": "0000936395", "COHR": "0000820318", "GLW": "0000024741", "LITE": "0001434524",
    "ANET": "0001313545", "AVGO": "0001649338", "CSCO": "0000858877", "MRVL": "0001058057",
    "CEG": "0001868275", "ETN": "0000031462", "NEE": "0000753308", "VRT": "0001674101",
    "VST": "0001862068", "DLR": "0001365135", "EQIX": "0001101239", "IREN": "0001878848",
    "NBIS": "0001877787", "AMAT": "0000006951", "ASML": "0000937966", "KLAC": "0000319201",
    "LRCX": "0000707549", "TER": "0000097210", "ARM": "0001973239", "CDNS": "0000813672",
    "SNPS": "0000883241", "AAOI": "0001158838", "CRWV": "0001822359", "MOD": "0000806468",
    "PWR": "0001040971",
    "NVDA": "0001045810",
}


def fetch_sec_filings(tickers: list, days=7) -> list:
    """Fetch recent 8-K filings with item-level detail from SEC EDGAR."""
    filings = []
    email = os.environ.get("SEC_USER_AGENT_EMAIL", "research@example.com")
    headers = {"User-Agent": f"ASCR {email}"}

    for ticker in tickers:
        cik = TICKER_CIK.get(ticker)
        if not cik:
            continue
        try:
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue

            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            items_list = recent.get("items", [])
            primary_docs = recent.get("primaryDocument", [])
            accessions = recent.get("accessionNumber", [])

            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            for i in range(min(30, len(forms))):
                if "8-K" not in forms[i]:
                    continue
                if dates[i] < cutoff:
                    break

                items_str = items_list[i] if i < len(items_list) else ""
                items = [it.strip() for it in items_str.split(",") if it.strip()]

                # Check if any important items
                important = [it for it in items if it in IMPORTANT_8K_ITEMS]
                if not important:
                    continue

                acc_clean = accessions[i].replace("-", "") if i < len(accessions) else ""
                doc = primary_docs[i] if i < len(primary_docs) else ""
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_clean}/{doc}"

                item_desc = "; ".join(f"{it}: {IMPORTANT_8K_ITEMS.get(it, 'unknown')}" for it in important)
                filings.append({
                    "ticker": ticker,
                    "form": forms[i],
                    "date": dates[i],
                    "items": items_str,
                    "important_items": important,
                    "item_descriptions": item_desc,
                    "url": doc_url,
                    "doc": doc,
                })

            time.sleep(0.15)  # SEC rate limit: 10 req/sec
        except Exception as e:
            logger.warning(f"SEC fetch failed for {ticker}: {e}")

    logger.info(f"Fetched {len(filings)} important 8-K filings for {len(tickers)} tickers")
    return filings


def fetch_13f_filings(days=30) -> list:
    """Fetch recent 13F filings from major AI-focused funds."""
    major_filers = {
        "1067983": "Berkshire Hathaway",
        "1350694": "ARK Invest",
        "1029160": "Renaissance Technologies",
        "1061768": "Citadel",
        "1336528": "Bridgewater",
        "1649553": "Tiger Global",
    }
    filings = []
    email = os.environ.get("SEC_USER_AGENT_EMAIL", "research@example.com")
    headers = {"User-Agent": f"ASCR {email}"}
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    for cik, name in major_filers.items():
        try:
            url = (f"https://efts.sec.gov/LATEST/search-index?"
                   f"q=%22{cik}%22&dateRange=custom&startdt={start}"
                   f"&enddt={datetime.now().strftime('%Y-%m-%d')}&forms=13F-HR")
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for hit in data.get("hits", {}).get("hits", [])[:3]:
                    src = hit.get("_source", {})
                    filings.append({
                        "filer": name,
                        "cik": cik,
                        "form": "13F-HR",
                        "date": src.get("file_date", ""),
                        "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR",
                    })
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"13F fetch failed for {name}: {e}")

    logger.info(f"Fetched {len(filings)} 13F filings from {len(major_filers)} major filers")
    return filings


def fetch_earnings_calendar(tickers: list) -> list:
    """Fetch upcoming/recent earnings dates and surprises."""
    import yfinance as yf
    earnings = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is not None and not (hasattr(cal, 'empty') and cal.empty):
                # Calendar might be dict or DataFrame
                if isinstance(cal, dict):
                    date = cal.get("Earnings Date", [None])
                    if isinstance(date, list) and date:
                        date = str(date[0])[:10]
                    else:
                        date = str(date)[:10] if date else None
                    earnings.append({
                        "ticker": ticker,
                        "earnings_date": date,
                        "source": "calendar",
                    })
                elif hasattr(cal, 'iloc'):
                    earnings.append({
                        "ticker": ticker,
                        "earnings_date": str(cal.iloc[0, 0])[:10] if len(cal) > 0 else None,
                        "source": "calendar",
                    })
            # Also check recent earnings surprise
            hist = t.earnings_dates
            if hist is not None and not hist.empty:
                recent = hist.head(1)
                if "Surprise(%)" in recent.columns:
                    surprise = recent["Surprise(%)"].iloc[0]
                    if surprise is not None:
                        earnings.append({
                            "ticker": ticker,
                            "surprise_pct": float(surprise) if str(surprise) != 'nan' else None,
                            "eps_estimate": float(recent["EPS Estimate"].iloc[0]) if "EPS Estimate" in recent.columns else None,
                            "eps_actual": float(recent["Reported EPS"].iloc[0]) if "Reported EPS" in recent.columns else None,
                            "earnings_date": str(recent.index[0])[:10],
                            "source": "earnings_history",
                        })
            time.sleep(0.3)
        except Exception as e:
            pass  # yfinance can be flaky

    actual = [e for e in earnings if e.get("surprise_pct") is not None or e.get("earnings_date")]
    logger.info(f"Fetched earnings data for {len(actual)}/{len(tickers)} tickers")
    return earnings

# ─── LLM Analysis ───

FILTER_PROMPT = """You are an AI supply chain investment analyst.
Given these news headlines, identify which ones are relevant to AI supply chain investing.

Our universe: memory/HBM (MU, SNDK, STX, WDC), optical (CIEN, COHR, GLW, LITE),
networking (ANET, AVGO, CSCO, MRVL), power/cooling (CEG, ETN, NEE, VRT, VST),
data centers (DLR, EQIX, IREN, NBIS), semicap (AMAT, ASML, KLAC, LRCX, TER),
EDA/IP (ARM, CDNS, SNPS), infrastructure (AAOI, CRWV, MOD, PWR).

RELEVANT signals (mark relevant=true):
- Earnings beats/misses with AI segment detail
- Revenue guidance changes (raised/lowered)
- New contracts, partnerships, investments
- Supply chain disruptions or shortages
- Major institutional buys/sells (13F, insider trades)
- Capacity expansion or new facility announcements
- Customer wins (hyperscaler orders)
- Price changes in HBM/DRAM/NAND/optical components

NOT relevant (mark relevant=false):
- Generic market commentary, opinion pieces
- News >7 days old that's already priced in
- Analyst price target changes (unless dramatically different)
- General AI hype without specific company impact

For each headline, respond with a JSON array. Each element:
{{"index": N, "relevant": true/false, "ticker": "most affected ticker or null", "new_company": "ticker of company NOT in our universe but should be tracked, or null", "reason": "one line why"}}

IMPORTANT: If a headline mentions a company that benefits from AI supply chain but is NOT in our universe above, put its ticker in "new_company". This is how we discover new stocks to track.

Headlines:
{headlines}"""

ANALYSIS_PROMPT = """You are a senior equity analyst specializing in AI infrastructure. Your job is to analyze events and give a clear BUY/HOLD/AVOID verdict with price conviction.

Context: We track AI-related stocks. We want EARLY signals — before the market prices them in.
Our edge: speed of discovery + depth of analysis. We need to understand WHY something matters, not just THAT it happened.

CRITICAL RULES:
1. NVIDIA investments/deals → identify the SUPPLIER that benefits (not NVIDIA itself)
2. Think about SECOND-ORDER effects (NVIDIA buys memory → memory suppliers benefit → memory equipment makers benefit)
3. Ask: "Is this already priced in?" — if a stock already ran 50%, the news might be late
4. Ask: "What's the MOAT?" — can competitors steal this opportunity?
5. Ask: "What's the MAGNITUDE?" — a $100M deal for a $50B company is noise; for a $2B company it's transformative

Headline: {headline}
Source: {source}
{extra_context}

Respond with ONLY valid JSON:
{{
    "ticker": "most affected ticker from our universe (or null)",
    "secondary_tickers": ["other affected tickers"],
    "new_company": "ticker NOT in our universe but should be tracked (or null)",
    "event_type": "contract|partnership|earnings_beat|earnings_miss|guidance_raise|guidance_lower|capacity|shortage|expansion|regulation|product|investment|13f_buy|13f_sell|insider_buy|insider_sell|price_surge|price_drop|analyst_upgrade|analyst_downgrade|other",
    "counterparty": "e.g. NVIDIA, Microsoft, or null",
    "is_ai_related": true/false,
    "supply_chain_area": "HBM|NAND|optical|networking|power|cooling|data_center|semicap|EDA|infrastructure|software|robotics|energy",

    "investment_thesis": "3-5 sentences: WHY does this matter for the stock? What's the revenue impact? How does it change the competitive position? What's the bull case vs bear case?",
    "verdict": "STRONG_BUY|BUY|HOLD|AVOID|SELL",
    "conviction": "HIGH|MEDIUM|LOW",
    "upside_potential": "10-20%|20-50%|50-100%|100%+|unclear",
    "time_horizon": "days|weeks|1-2Q|12-24M",
    "already_priced_in": true/false,
    "priced_in_pct": 0-100 (how much of this news is already reflected in the price),
    "moat": "strong|moderate|weak|none",
    "moat_reason": "one line: why does this company have/lack a moat",
    "catalyst": "what specific upcoming event could move the stock (earnings, product launch, etc.)",

    "evidence_delta": -10 to +10,
    "asymmetry_delta": -10 to +10 (big upside vs limited downside?),
    "risk_delta": -10 to +10 (dilution, competition, regulatory risk?),
    "risk_factors": ["list of specific risks"],

    "summary": "1-2 line TL;DR for a busy investor",
    "confidence": 0.0 to 1.0
}}"""


def _python_prefilter(articles: list) -> list:
    """Fast Python keyword filter — eliminates obviously irrelevant articles.
    Passes borderline cases through (better to over-include than miss).
    """
    # Keywords that indicate AI/semiconductor/supply chain relevance
    INCLUDE_KEYWORDS = {
        # Companies
        "nvidia", "nvda", "amd", "intel", "tsmc", "samsung", "hynix", "micron",
        "broadcom", "marvell", "asml", "applied materials", "lam research",
        "synopsys", "cadence", "arista", "vertiv", "corning", "coherent",
        "lumentum", "ciena", "vistra", "constellation energy", "quanta",
        # Tech
        "ai chip", "ai server", "gpu", "hbm", "dram", "nand", "data center",
        "datacenter", "semiconductor", "chip", "wafer", "fab", "foundry",
        "optical", "photonic", "networking", "switch", "router",
        "cooling", "power", "transformer", "infrastructure",
        # Events
        "deal", "contract", "partnership", "investment", "acquire", "merger",
        "supply", "shortage", "capacity", "expansion", "billion",
        "earnings", "revenue", "guidance", "forecast", "upgrade", "downgrade",
        "insider", "form 4", "sec filing", "8-k",
        # AI broad
        "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "large language", "llm", "training", "inference",
        "hyperscale", "cloud computing",
    }

    # Keywords that indicate irrelevance
    EXCLUDE_KEYWORDS = {
        "crypto", "bitcoin", "ethereum", "nft", "meme stock", "sports betting",
        "cannabis", "marijuana", "covid vaccine", "real estate investment trust",
    }

    filtered = []
    for a in articles:
        title_lower = a["title"].lower()

        # Exclude obvious noise
        if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
            continue

        # Include if any keyword matches
        if any(kw in title_lower for kw in INCLUDE_KEYWORDS):
            filtered.append(a)
            continue

        # Check if any tracked ticker is mentioned
        from src import config
        for ticker in config.all_tickers():
            if f" {ticker.lower()} " in f" {title_lower} " or f"${ticker.lower()}" in title_lower:
                filtered.append(a)
                break

    return filtered


def filter_headlines(articles: list) -> list:
    """Two-stage filter: fast Python pre-filter → Haiku for borderline cases.

    Python filter handles ~80% of filtering (free, instant).
    Haiku only called if >50 articles pass pre-filter (rare).
    """
    if not articles:
        return []

    # Stage 1: Python keyword filter (free)
    prefiltered = _python_prefilter(articles)
    logger.info(f"Python pre-filter: {len(articles)}→{len(prefiltered)} articles")

    # If pre-filter is selective enough, skip Haiku entirely
    if len(prefiltered) <= 50:
        # Assign filter_ticker from headline for each article
        from src import config
        tickers = config.all_tickers()
        for a in prefiltered:
            title_upper = a["title"].upper()
            for t in tickers:
                if t in title_upper:
                    a["filter_ticker"] = t
                    a["filter_reason"] = "keyword_match"
                    break
        return prefiltered

    # Stage 2: Too many passed — use Haiku to narrow down (rare)
    logger.info(f"Pre-filter passed {len(prefiltered)} — using Haiku to narrow")
    client = _get_client()
    headlines = "\n".join(f"{i}. {a['title']}" for i, a in enumerate(prefiltered))

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": FILTER_PROMPT.format(headlines=headlines)}]
        )
        log_ai_call(
            model="claude-haiku-4-5",
            purpose="headline_filter",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
        text = resp.content[0].text.strip()
        # Extract JSON from response
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    text = part
                    break
        results = json.loads(text)
        if not isinstance(results, list):
            results = [results]

        relevant = []
        for r in results:
            if not isinstance(r, dict):
                continue
            if r.get("relevant"):
                idx = r.get("index", -1)
                if isinstance(idx, int) and 0 <= idx < len(articles):
                    articles[idx]["filter_ticker"] = r.get("ticker")
                    articles[idx]["filter_reason"] = r.get("reason", "")
                    relevant.append(articles[idx])

        logger.info(f"Haiku filter: {len(relevant)}/{len(articles)} relevant "
                    f"(usage: {resp.usage.input_tokens}in/{resp.usage.output_tokens}out)")
        return relevant
    except Exception as e:
        logger.error(f"Haiku filter failed: {e}")
        return articles[:10]  # fallback: pass top 10 through


def analyze_article(article: dict) -> dict | None:
    """Use Gemini to deeply analyze a relevant article (was Sonnet)."""
    extra = ""
    if article.get("filter_ticker"):
        extra = f"Pre-filter suggested ticker: {article['filter_ticker']}\nReason: {article.get('filter_reason','')}"

    def _call_gemini():
        from src.gemini_client import analyze_headline

        result = analyze_headline(
            headline=article["title"],
            source=article.get("source_query", ""),
            extra=extra,
        )
        if result:
            usage = result.get("_usage", {})
            result["_usage"] = {
                "input": usage.get("input", 0),
                "output": usage.get("output", 0),
            }
        return result

    try:
        return _with_retry("Gemini analysis", _call_gemini)
    except Exception as e:
        logger.warning(f"Gemini analysis failed after retries; trying Sonnet fallback: {e}")

    try:
        return _with_retry("Sonnet fallback", lambda: _analyze_article_sonnet(article))
    except Exception as e:
        logger.error(f"Both Gemini and Sonnet failed after retries: {e}")
        return None


def _analyze_article_sonnet(article: dict) -> dict | None:
    """Fallback: use Anthropic Sonnet for article analysis."""
    client = _get_client()
    extra = ""
    if article.get("filter_ticker"):
        extra = f"Pre-filter suggested ticker: {article['filter_ticker']}\nReason: {article.get('filter_reason','')}"

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": ANALYSIS_PROMPT.format(
                headline=article["title"],
                source=article.get("source_query", ""),
                extra_context=extra
            )}]
        )
        log_ai_call(
            model="claude-sonnet-4-6",
            purpose="event_analysis_fallback",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            ticker=article.get("filter_ticker", "") or "",
        )
        text = resp.content[0].text.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    text = part
                    break
        result = json.loads(text)
        result["_usage"] = {
            "input": resp.usage.input_tokens,
            "output": resp.usage.output_tokens,
        }
        return result
    except Exception as e:
        logger.warning(f"Sonnet fallback error: {e}")
        return None



def run_pipeline(min_confidence=0.5, min_evidence_delta=3) -> list:
    """Full pipeline: fetch → filter → analyze → store."""
    today = datetime.now().strftime("%Y-%m-%d")
    init_events_db()  # CREATE IF NOT EXISTS, no drop

    from src.activity_log import log as alog
    alog("pipeline", "pipeline_start", articles_count=0)

    # 0. Idempotency: check if full pipeline already ran today (avoid double Sonnet cost)
    with db.get_conn() as conn:
        today_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE date=? AND source != 'price'",
            (today,)
        ).fetchone()[0]
    if today_events > 0:
        logger.info(f"Pipeline already ran today ({today_events} events). Skipping LLM analysis.")
        logger.info("Re-running price events only.")
        # Still run price events
        try:
            from src.price_events import detect_price_events
            price_evts = detect_price_events()
            for pe in price_evts:
                h = hashlib.md5(f"{pe['ticker']}{pe['date']}{pe['event_type']}".encode()).hexdigest()
                with db.get_conn() as conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO events
                        (hash, ticker, date, headline, url, event_type, evidence_delta,
                         asymmetry_delta, risk_delta, summary, confidence, source, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (h, pe["ticker"], pe["date"], pe["headline"], "",
                          pe["event_type"], pe["evidence_delta"], 0, 0,
                          pe["headline"], 1.0, "price", time.time()))
            logger.info(f"Price events: {len(price_evts)}")
        except Exception as e:
            logger.warning(f"Price events: {e}")
        return []

    # 1. Fetch
    articles = fetch_news()
    universe = config.universe()
    tickers = [t for sector in universe.get("sectors", {}).values()
               for t in (sector.get("tickers", []) if isinstance(sector, dict) else [])]
    # Include NVIDIA in 8-K scan — its filings reveal supply chain partners
    sec_tickers = tickers + (["NVDA"] if "NVDA" not in tickers else [])
    sec_filings = fetch_sec_filings(sec_tickers, days=7)

    # Convert 8-K filings to articles for LLM analysis
    for f in sec_filings:
        high_priority = any(it in ("1.01", "2.01", "7.01") for it in f.get("important_items", []))
        emoji = "🔥" if high_priority else "📋"
        articles.append({
            "title": f"{emoji} SEC 8-K: {f['ticker']} filed {f['date']} — {f['item_descriptions']}",
            "url": f.get("url", ""),
            "published": f.get("date", ""),
            "source_query": "sec_8k",
        })

    # 1b. Fetch earnings data
    try:
        earnings = fetch_earnings_calendar(tickers)
        # Convert earnings surprises to "articles" for filtering
        for e in earnings:
            if e.get("source") == "earnings_history" and e.get("surprise_pct") is not None:
                surprise = e["surprise_pct"]
                if abs(surprise) >= 5:  # Only significant surprises
                    direction = "beats" if surprise > 0 else "misses"
                    articles.append({
                        "title": f"{e['ticker']} earnings {direction} estimates by {abs(surprise):.1f}% "
                                f"(EPS ${e.get('eps_actual','?')} vs ${e.get('eps_estimate','?')}) on {e.get('earnings_date','')}",
                        "url": "",
                        "published": e.get("earnings_date", ""),
                        "source_query": "earnings",
                    })
    except Exception as e:
        logger.warning(f"Earnings fetch: {e}")

    # 1c. Fetch 13F filings from major funds
    try:
        thirteenf = fetch_13f_filings(days=30)
        for f in thirteenf:
            articles.append({
                "title": f"13F filing: {f['filer']} updated holdings (filed {f['date']})",
                "url": f.get("url", ""),
                "published": f.get("date", ""),
                "source_query": "13f_institutional",
            })
    except Exception as e:
        logger.warning(f"13F fetch: {e}")

    logger.info(f"Total articles after earnings+13F: {len(articles)}")

    # 2. Filter with Haiku (batch)
    # Process in batches of 50 to fit context
    relevant = []
    batch_size = 50
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i+batch_size]
        relevant.extend(filter_headlines(batch))
        time.sleep(0.5)

    logger.info(f"After filtering: {len(relevant)} relevant articles")
    alog("pipeline", "filter_done", total=len(articles), relevant=len(relevant))

    # 3. Analyze each relevant article with Sonnet (capped at 30 per run)
    MAX_LLM_CALLS_PER_RUN = 30
    events = []
    total_in, total_out = 0, 0
    llm_calls = 0
    for article in relevant:
        if llm_calls >= MAX_LLM_CALLS_PER_RUN:
            logger.warning(f"Hit LLM cap ({MAX_LLM_CALLS_PER_RUN}). "
                          f"Remaining {len(relevant) - llm_calls} articles skipped.")
            break
        # Dedup: check if we already have this headline
        h = hashlib.md5(article["title"].encode()).hexdigest()
        with db.get_conn() as conn:
            existing = conn.execute("SELECT id FROM events WHERE hash=?", (h,)).fetchone()
        if existing:
            continue

        result = analyze_article(article)
        if not result:
            continue

        # Validate ticker: must be 1-5 uppercase letters (valid US stock symbol)
        import re
        ticker = result.get("ticker", "")
        if not ticker or not re.match(r"^[A-Z]{1,5}$", ticker):
            logger.warning(f"Invalid ticker from LLM: '{ticker}' — skipping")
            continue

        llm_calls += 1
        total_in += result.get("_usage", {}).get("input", 0)
        total_out += result.get("_usage", {}).get("output", 0)

        # Filter: only keep high-confidence, material events
        if (result.get("confidence", 0) < min_confidence or
            abs(result.get("evidence_delta", 0)) < min_evidence_delta):
            continue
        # Skip if >80% priced in (still store if partially priced in)
        if result.get("priced_in_pct", 0) > 80:
            continue

        # Store with full analysis
        with db.get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO events
                (date, ticker, source, headline, url, event_type, counterparty,
                 is_ai_related, supply_chain_area, revenue_impact, time_horizon,
                 evidence_delta, asymmetry_delta, risk_delta, summary,
                 confidence, raw_llm_json, hash, created_at,
                 verdict, conviction, upside_potential, investment_thesis,
                 moat, catalyst, priced_in_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                today, result.get("ticker"), article.get("source_query", ""),
                article["title"], article.get("url", ""),
                result.get("event_type"), result.get("counterparty"),
                1 if result.get("is_ai_related") else 0,
                result.get("supply_chain_area"), result.get("revenue_impact"),
                result.get("time_horizon"),
                result.get("evidence_delta", 0), result.get("asymmetry_delta", 0),
                result.get("risk_delta", 0), result.get("summary"),
                result.get("confidence", 0), json.dumps(result), h,
                datetime.now().isoformat(),
                result.get("verdict", ""), result.get("conviction", ""),
                result.get("upside_potential", ""), result.get("investment_thesis", ""),
                result.get("moat", ""), result.get("catalyst", ""),
                result.get("priced_in_pct", 0),
            ))

        events.append({
            "ticker": result.get("ticker"),
            "headline": article["title"],
            "event_type": result.get("event_type"),
            "evidence_delta": result.get("evidence_delta", 0),
            "confidence": result.get("confidence", 0),
            "summary": result.get("summary", ""),
            "verdict": result.get("verdict", ""),
            "conviction": result.get("conviction", ""),
            "upside_potential": result.get("upside_potential", ""),
            "investment_thesis": result.get("investment_thesis", ""),
        })
        # Record in experience tracker
        try:
            from src.experience_tracker import record_signal, init_db
            from src.recommender import get_live_price
            init_db()
            record_signal(
                event_id=None,  # will be backfilled
                ticker=result.get("ticker", ""),
                signal_date=today,
                event_type=result.get("event_type", ""),
                source=article.get("source_query", ""),
                headline=article["title"],
                verdict=result.get("verdict", ""),
                conviction=result.get("conviction", ""),
                evidence_delta=result.get("evidence_delta", 0),
                priced_in_pct=result.get("priced_in_pct", 0),
                price_at_signal=get_live_price(result.get("ticker", "")),
            )
        except Exception:
            pass  # non-critical

        alog("pipeline", "event_found", result.get("ticker"),
             ev_delta=result.get("evidence_delta", 0),
             verdict=result.get("verdict", ""),
             conviction=result.get("conviction", ""),
             headline=article["title"])
        logger.info(f"  📰 {result.get('ticker','?')}: {article['title']} "
                    f"(ev={result.get('evidence_delta',0):+.0f}, conf={result.get('confidence',0):.1f})")

        time.sleep(0.3)  # rate limit

    alog("pipeline", "pipeline_end",
         events=len(events), tokens_in=total_in, tokens_out=total_out,
         cost_est=round(total_in * 3/1e6 + total_out * 15/1e6, 4))
    logger.info(f"Pipeline done: {len(events)} actionable events, "
                f"LLM usage: {total_in}in/{total_out}out tokens")

    # 4. Price events (surges, crashes, volume spikes — no LLM needed)
    logger.info("--- Price Events ---")
    try:
        from src.price_events import detect_price_events
        price_evts = detect_price_events()
        for pe in price_evts:
            h = hashlib.md5(f"{pe['ticker']}{pe['date']}{pe['event_type']}".encode()).hexdigest()
            with db.get_conn() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO events
                    (hash, ticker, date, headline, url, event_type, evidence_delta,
                     asymmetry_delta, risk_delta, summary, confidence, source, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (h, pe["ticker"], pe["date"], pe["headline"], "",
                      pe["event_type"], pe["evidence_delta"], 0, 0,
                      pe["headline"], 1.0, "price", time.time()))
        logger.info(f"Price events: {len(price_evts)} detected")
    except Exception as e:
        logger.warning(f"Price events: {e}")

    # Sector death signal detection
    try:
        from src.sector_strategy import classify_event_for_sector, get_sector_for_ticker
        death_alerts = []
        for ev in events:
            t = ev.get("ticker", "")
            sector = get_sector_for_ticker(t)
            if sector == "unknown":
                continue
            classification = classify_event_for_sector(ev.get("headline", ""), sector)
            if classification["is_death"]:
                death_alerts.append({
                    "ticker": t,
                    "sector": sector,
                    "matches": classification["death_matches"],
                    "severity": classification["severity"],
                    "action": classification["action"],
                })
                logger.warning(f"DEATH SIGNAL: {t} ({sector}): {classification['death_matches']}")

        if death_alerts:
            from src.telegram_notifier import send
            for da in death_alerts:
                emoji = "💀" if da["severity"] == "critical" else "⚠️"
                send(f"{emoji} <b>Sector Death Signal</b>\n"
                     f"Ticker: {da['ticker']} ({da['sector']})\n"
                     f"Signals: {', '.join(da['matches'])}\n"
                     f"Action: {da['action']}")
    except Exception as e:
        logger.warning(f"Sector strategy: {e}")

    # Emerging sector scan (check all headlines for new sector signals)
    try:
        from src.sector_strategy import scan_emerging_sectors
        all_headlines = [ev.get("headline", "") for ev in events]
        emerging = scan_emerging_sectors(all_headlines)
        if emerging:
            from src.telegram_notifier import send
            sectors_found = set(e["sector"] for e in emerging)
            for sector_name in sectors_found:
                hits = [e for e in emerging if e["sector"] == sector_name]
                candidates = hits[0].get("candidates", [])
                send(f"🔎 <b>Emerging Sector Signal: {sector_name}</b>\n"
                     f"Hits: {len(hits)} headlines\n"
                     f"Example: {hits[0]['headline']}\n"
                     f"Candidates: {', '.join(candidates) if candidates else 'TBD'}")
    except Exception as e:
        logger.warning(f"Emerging sector scan: {e}")

    # Supply chain propagation — derived signals for related tickers
    try:
        from src.supply_chain import propagate_event
        universe_set = set(tickers)
        derived_events = []
        for ev in events:
            derived = propagate_event(ev, universe_set)
            derived_events.extend(derived)

        if derived_events:
            with db.get_conn() as conn:
                for de in derived_events:
                    h = hashlib.md5("{}{}{}{}".format(
                        de["ticker"], today, de["event_type"], de["headline"]
                    ).encode()).hexdigest()
                    conn.execute("""
                        INSERT OR IGNORE INTO events
                        (date, ticker, source, headline, event_type, counterparty,
                         is_ai_related, supply_chain_area, evidence_delta,
                         asymmetry_delta, risk_delta, summary, confidence, hash,
                         created_at, verdict, conviction)
                        VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?)
                    """, (today, de["ticker"], de["source"], de["headline"],
                          de["event_type"], de.get("counterparty", ""),
                          de.get("is_ai_related", 1), de.get("supply_chain_area", ""),
                          de["evidence_delta"], de["summary"],
                          de.get("confidence", 0.5), h,
                          datetime.now().isoformat(),
                          de.get("verdict", ""), de.get("conviction", "LOW")))
                conn.commit()
            logger.info(f"Supply chain: {len(derived_events)} derived events")
    except Exception as e:
        logger.warning(f"Supply chain propagation: {e}")

    # Discovery moved to discovery_engine.py

        # Top picks now in unified daily report (telegram_notifier.send_daily_summary)

    # Push to Telegram
    try:
        from src.telegram_notifier import send, send_event_signals
        if events:
            send_event_signals(format_events_telegram(events))
    except Exception as e:
        logger.warning(f"Telegram push: {e}")

    return events


def _validate_and_add(candidate_tickers: list) -> list:
    """Validate and auto-add new tickers using Python checks (no LLM).

    Criteria (all must pass):
    1. Real US stock with price > $1
    2. Market cap > $1B
    3. Sector is tech/industrials/utilities (not finance/healthcare/consumer)
    4. Industry keywords suggest AI relevance
    """
    import yaml
    import yfinance as yf

    current_tickers, sectors = set(), {}
    u = config.universe()
    for sname, sval in u.get("sectors", {}).items():
        if isinstance(sval, dict):
            t_list = sval.get("tickers", [])
            sectors[sname] = t_list
            current_tickers.update(t_list)

    AI_INDUSTRIES = {
        "semiconductors", "semiconductor equipment", "electronic components",
        "communication equipment", "computer hardware", "data processing",
        "scientific & technical instruments", "electrical equipment",
        "information technology", "software", "internet services",
        "cloud computing", "networking", "optical", "photonics",
    }

    AI_SECTORS = {"Technology", "Industrials", "Utilities", "Communication Services"}

    SECTOR_MAP = {
        "semiconductors": "memory", "semiconductor equipment": "semicap",
        "communication equipment": "networking", "electronic components": "optical",
        "electrical equipment": "power_cooling", "software": "eda_ip",
    }

    added = []
    upath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "universe.yaml")
    with open(upath) as f:
        u_data = yaml.safe_load(f)

    for ticker in candidate_tickers[:10]:
        if ticker in current_tickers:
            continue
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            price = info.get("regularMarketPrice") or info.get("previousClose")
            mcap = info.get("marketCap", 0) or 0
            sector = info.get("sector", "")
            industry = (info.get("industry", "") or "").lower()
            name = info.get("shortName", ticker)

            # Filter checks
            if not price or price < 1:
                continue
            if mcap < 1e9:  # < $1B
                continue
            if sector not in AI_SECTORS:
                continue

            # Industry keyword match (fuzzy)
            industry_match = any(kw in industry for kw in AI_INDUSTRIES)
            name_match = any(kw in name.lower() for kw in
                           ["semi", "chip", "data", "cloud", "network", "optical",
                            "power", "energy", "cooling", "silicon", "compute"])

            if not industry_match and not name_match:
                continue

            # Determine target sector
            target_sector = "new_additions"
            for kw, sect in SECTOR_MAP.items():
                if kw in industry:
                    target_sector = sect
                    break

            if target_sector not in u_data.get("sectors", {}):
                u_data.setdefault("sectors", {})["new_additions"] = {"name": "New Additions", "tickers": []}
                target_sector = "new_additions"

            sect = u_data["sectors"].get(target_sector, {})
            if isinstance(sect, dict) and ticker not in sect.get("tickers", []):
                sect.setdefault("tickers", []).append(ticker)
                added.append({
                    "ticker": ticker, "sector": target_sector,
                    "reason": f"{name} ({sector}/{industry}, mcap=${mcap/1e9:.1f}B)",
                    "added": True,
                })
                logger.info(f"Auto-added {ticker} to {target_sector}: {name}")

        except Exception as e:
            logger.warning(f"Validate {ticker}: {e}")
        time.sleep(0.3)

    if added:
        with open(upath, "w") as f:
            yaml.dump(u_data, f, default_flow_style=False)
        try:
            from src.price_fetcher import fetch_prices
            fetch_prices([a["ticker"] for a in added], period="3mo")
        except Exception as e:
            logger.warning(f"Price fetch for new tickers: {e}")

    return added


def get_recent_events(ticker=None, days=7) -> list:
    """Get recent events, optionally filtered by ticker."""
    with db.get_conn() as conn:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if ticker:
            rows = conn.execute("""
                SELECT * FROM events WHERE ticker=? AND date>=?
                ORDER BY evidence_delta DESC
            """, (ticker, cutoff)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM events WHERE date>=?
                ORDER BY evidence_delta DESC
            """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def format_events_telegram(events: list) -> str:
    """Format events for Telegram notification."""
    if not events:
        return "📰 No new actionable events today."

    lines = [f"📰 <b>Event Pipeline — {len(events)} signals</b>\n"]
    for e in events:
        emoji = "🔥" if e.get("evidence_delta", 0) >= 7 else "📊" if e.get("evidence_delta", 0) >= 5 else "📰"
        lines.append(f"{emoji} <b>{e['ticker']}</b>: {e['headline']}")
        lines.append(f"   ev={e.get('evidence_delta',0):+.0f} | "
                    f"{e.get('event_type','')} | conf={e.get('confidence',0):.0%}")
        if e.get("summary"):
            lines.append(f"   <i>{e['summary']}</i>")
        lines.append("")

    return "\n".join(lines)
