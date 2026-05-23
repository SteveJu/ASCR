"""Universe scanner — weekly discovery of new AI supply chain tickers.

Runs Friday 10 PM ET:
1. Scan recent events for tickers NOT in our universe
2. Search news for emerging AI supply chain companies
3. Ask Claude to evaluate candidates
4. Push recommendations to Telegram (human decides)
"""
import os
import json
import time
import feedparser
import requests
from datetime import datetime
from urllib.parse import quote
from anthropic import Anthropic
from src import config, db
from src.llm_usage import log_ai_call
from src.telegram_notifier import send
from src.utils import get_logger

logger = get_logger("universe_scanner")

DISCOVERY_QUERIES = [
    "new AI supply chain company IPO",
    "AI infrastructure startup funding series",
    "NVIDIA new supplier partner investment 2026",
    "AI data center new entrant company",
    "AI chip cooling power startup",
    "AI optical networking emerging company",
    "AI semiconductor new player",
    "hyperscaler supply chain new vendor",
]

EVAL_PROMPT = """You are an AI supply chain investment analyst.

Our current universe has {n_tickers} tickers across these sectors:
{sectors}

Current tickers: {tickers}

Below are candidate companies/tickers that appeared in recent AI supply chain news but are NOT in our universe.
For each, evaluate if we should ADD it to our tracking universe.

Criteria for inclusion:
1. Directly in AI supply chain (not just "uses AI")
2. Publicly traded US stock (has ticker)
3. Material revenue exposure to AI infrastructure
4. Not too large/diversified (AAPL, MSFT, GOOGL too broad)
5. Fills a gap in our current coverage

Candidates:
{candidates}

Respond with a JSON array. Each element:
{{
    "ticker": "SYMBOL",
    "company": "Full Name",
    "sector": "which sector it fits (or new sector name)",
    "reason": "why add — specific AI supply chain relevance",
    "confidence": 0.0 to 1.0,
    "add": true/false
}}

Only recommend add=true if confidence >= 0.7 and the company clearly belongs."""


def _get_client():
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    return Anthropic(api_key=key)


def _current_universe():
    u = config.universe()
    tickers = set()
    sectors = {}
    for sname, sval in u.get("sectors", {}).items():
        if isinstance(sval, dict):
            t_list = sval.get("tickers", [])
            sectors[sname] = t_list
            tickers.update(t_list)
    return tickers, sectors


def scan_news_for_new_tickers():
    """Search news for companies not in our universe."""
    current_tickers, _ = _current_universe()
    candidates = {}

    for query in DISCOVERY_QUERIES:
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                candidates[title] = {"title": title, "source": query}
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"RSS scan failed for '{query}': {e}")

    logger.info(f"Scanned {len(candidates)} headlines from {len(DISCOVERY_QUERIES)} queries")
    return list(candidates.values())


def scan_events_for_unknown():
    """Check events table for tickers not in our universe."""
    current_tickers, _ = _current_universe()
    unknown = []
    # Also check common large-cap AI names to exclude
    exclude = {"NVDA", "AMD", "INTC", "TSM", "AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN"}
    with db.get_conn() as conn:
        try:
            rows = conn.execute("""
                SELECT DISTINCT ticker, COUNT(*) as cnt, SUM(evidence_delta) as total_ev
                FROM events
                WHERE ticker IS NOT NULL AND ticker != ''
                GROUP BY ticker
                ORDER BY total_ev DESC
            """).fetchall()
            for r in rows:
                t = r["ticker"]
                if t and t not in current_tickers and t not in exclude:
                    unknown.append({"ticker": t, "count": r["cnt"], "total_ev": r["total_ev"]})
        except Exception:
            pass
    return unknown


def evaluate_candidates(headlines, unknown_tickers):
    """Use Claude to evaluate potential additions."""
    current_tickers, sectors = _current_universe()
    client = _get_client()

    # Build candidate list
    candidate_text = ""
    if unknown_tickers:
        candidate_text += "From event analysis (tickers seen but not tracked):\n"
        for t in unknown_tickers[:20]:
            candidate_text += f"  - {t}\n"
        candidate_text += "\n"

    if headlines:
        candidate_text += "From recent news headlines:\n"
        for h in headlines[:30]:
            candidate_text += f"  - {h['title']}\n"

    if not candidate_text.strip():
        return []

    sectors_text = "\n".join(f"  {s}: {', '.join(t)}" for s, t in sectors.items())

    try:
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": EVAL_PROMPT.format(
                n_tickers=len(current_tickers),
                sectors=sectors_text,
                tickers=", ".join(sorted(current_tickers)),
                candidates=candidate_text,
            )}]
        )
        log_ai_call(
            model="claude-opus-4-6",
            purpose="universe_candidate_eval",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
        text = resp.content[0].text.strip()
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
        logger.info(f"Evaluated {len(results)} candidates, "
                    f"usage: {resp.usage.input_tokens}in/{resp.usage.output_tokens}out")
        return [r for r in results if isinstance(r, dict)]
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        return []


def run_scan():
    """Full weekly scan."""
    logger.info("=== Universe Scanner — Weekly Scan ===")

    # 1. Scan news
    headlines = scan_news_for_new_tickers()

    # 2. Check events for unknown tickers
    unknown = scan_events_for_unknown()
    logger.info(f"Unknown tickers from events: {unknown}")

    # 3. Evaluate with Claude
    candidates = evaluate_candidates(headlines, unknown)

    # 4. Filter recommendations
    recommended = [c for c in candidates if c.get("add") and c.get("confidence", 0) >= 0.7]
    rejected = [c for c in candidates if not c.get("add") or c.get("confidence", 0) < 0.7]

    # 5. Push to Telegram
    if recommended:
        lines = [f"🔍 <b>Universe Scanner - {len(recommended)} suggested additions</b>\n"]
        for r in recommended:
            lines.append(
                f"✅ <b>{r['ticker']}</b> ({r.get('company', '')})\n"
                f"   Sector: {r.get('sector', '?')}\n"
                f"   Confidence: {r.get('confidence', 0):.0%}\n"
                f"   {r.get('reason', '')}\n"
            )
        lines.append("Use /add TICKER to add one.")
        send("\n".join(lines))
    else:
        send("🔍 <b>Universe Scanner</b> - no new suggestions this week. Current {n} tickers look covered.".format(
            n=len(_current_universe()[0])))

    if rejected:
        logger.info(f"Rejected {len(rejected)} candidates: "
                    f"{[r.get('ticker','?') for r in rejected]}")

    # 4. Prune unhealthy tickers
    try:
        from src.universe_pruner import auto_prune, format_telegram_report
        from src.telegram_notifier import send
        result = auto_prune(dry_run=True)  # Report only, don't auto-remove

        if result["remove"] or result["watch"]:
            report = format_telegram_report(result)
            send(report)
            logger.info(f"Pruner: {len(result['remove'])} to remove, {len(result['watch'])} to watch")
        else:
            logger.info("Pruner: universe healthy, no removals needed")
    except Exception as e:
        logger.warning(f"Pruner: {e}")

    return {"recommended": recommended, "rejected": rejected, "unknown": unknown}


if __name__ == "__main__":
    run_scan()
