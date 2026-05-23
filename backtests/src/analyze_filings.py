"""Analyze historical SEC filings with LLM to generate events."""
import os
import hashlib
import json
import time
import requests
from src import db
from src.config import MAX_LLM_CALLS_PER_DAY, GEMINI_MODEL

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "event_type": {"type": "string", "enum": [
            "contract", "earnings_beat", "earnings_miss", "shortage", "investment",
            "partnership", "expansion", "downgrade", "upgrade", "insider_buy",
            "insider_sell", "regulatory", "other"
        ]},
        "is_ai_related": {"type": "boolean"},
        "evidence_delta": {"type": "number"},
        "confidence": {"type": "number"},
        "verdict": {"type": "string", "enum": ["STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL"]},
        "conviction": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "thesis": {"type": "string"},
        "bull_case": {"type": "array", "items": {"type": "string"}},
        "bear_case": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["ticker", "verdict", "conviction", "evidence_delta", "summary", "thesis"]
}


def _call_gemini(prompt: str) -> dict | None:
    """Call Gemini with structured output."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 2000,
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:
        print(f"    Gemini error: {e}")
        return None


def analyze_batch(limit: int = None):
    """Analyze unprocessed 8-K filings with LLM."""
    conn = db.get_conn()
    conn.execute('PRAGMA busy_timeout=5000')

    # Get filings that haven't been analyzed yet
    analyzed_hashes = {r[0] for r in conn.execute(
        "SELECT hash FROM events WHERE source = 'sec_8k_backtest'").fetchall()}

    filings = conn.execute(
        "SELECT id, ticker, filing_date, form_type, title, url "
        "FROM sec_filings ORDER BY filing_date"
    ).fetchall()

    budget = limit or MAX_LLM_CALLS_PER_DAY
    calls = 0
    events_added = 0

    print(f"Analyzing {len(filings)} filings (budget: {budget} LLM calls)...")

    for f in filings:
        if calls >= budget:
            print(f"  Budget exhausted ({budget} calls)")
            break

        filing_hash = hashlib.md5(f"{f['ticker']}{f['filing_date']}{f['accession'] if 'accession' in f.keys() else f['title']}".encode()).hexdigest()
        if filing_hash in analyzed_hashes:
            continue

        prompt = (
            f"Analyze this SEC {f['form_type']} filing for AI supply chain investment impact.\n\n"
            f"Ticker: {f['ticker']}\n"
            f"Filing Date: {f['filing_date']}\n"
            f"Title: {f['title']}\n\n"
            f"This is a historical backtest. Analyze based ONLY on what was knowable at filing date.\n"
            f"Focus on: AI/data center relevance, revenue impact, competitive position.\n\n"
            f"Return JSON with these fields:\n"
            f"- ticker (string)\n"
            f"- event_type (one of: contract, earnings_beat, earnings_miss, investment, partnership, expansion, regulatory, other)\n"
            f"- is_ai_related (boolean)\n"
            f"- evidence_delta (0-10, how significant)\n"
            f"- confidence (0.0-1.0)\n"
            f"- verdict (STRONG_BUY, BUY, HOLD, AVOID, SELL)\n"
            f"- conviction (HIGH, MEDIUM, LOW)\n"
            f"- thesis (one sentence)\n"
            f"- summary (one sentence)\n"
            f"- bull_case (array of 2 strings)\n"
            f"- bear_case (array of 2 strings)\n"
        )

        result = _call_gemini(prompt)
        calls += 1

        if result and (result.get("evidence_delta") or 0) >= 3:
            conn.execute(
                "INSERT OR IGNORE INTO events "
                "(date, ticker, source, headline, event_type, evidence_delta, "
                "verdict, conviction, thesis, bull_case, bear_case, confidence, hash) "
                "VALUES (?, ?, 'sec_8k_backtest', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f['filing_date'], f['ticker'],
                 f"{f['form_type']}: {f['title']}",
                 result.get("event_type", "other"),
                 result.get("evidence_delta", 0),
                 result.get("verdict", "HOLD"),
                 result.get("conviction", "LOW"),
                 result.get("thesis", ""),
                 json.dumps(result.get("bull_case", [])),
                 json.dumps(result.get("bear_case", [])),
                 result.get("confidence", 0.5),
                 filing_hash))
            events_added += 1
            print(f"  ✓ {f['filing_date']} {f['ticker']}: {result.get('verdict')} ({result.get('conviction')}) ev={result.get('evidence_delta')}")
        elif result:
            print(f"  · {f['filing_date']} {f['ticker']}: ev={result.get('evidence_delta', 0)} (below threshold)")

        time.sleep(0.5)  # Rate limit

    conn.commit()
    conn.close()
    print(f"\nDone: {calls} LLM calls, {events_added} events added")


if __name__ == "__main__":
    analyze_batch()
