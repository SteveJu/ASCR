"""Gemini API client with model fallback.

Primary: gemini-3.1-pro-preview (best quality, no thinking tokens)
Fallback: gemini-2.5-flash (cheap, fast, good enough)

Falls back automatically on 429/503 (overloaded/rate limit).
"""
import os
import json
import time
import requests
from dotenv import load_dotenv
from src.llm_usage import log_ai_call
from src.utils import get_logger

load_dotenv()
logger = get_logger("gemini")

API_KEY = os.getenv("GEMINI_API_KEY", "")
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

PRIMARY_MODEL = "gemini-3.1-pro-preview"
FALLBACK_MODEL = "gemini-2.5-flash"


def generate(prompt: str, max_tokens: int = 1000, temperature: float = 0.1,
             model: str = None, response_schema: dict = None,
             purpose: str = "gemini_generate", ticker: str = "") -> dict:
    """Call Gemini API. Returns {"text": ..., "model": ..., "usage": {...}}.

    Auto-falls back to FALLBACK_MODEL if primary is overloaded.
    """
    if not API_KEY:
        raise ValueError("GEMINI_API_KEY not set")

    models_to_try = [model or PRIMARY_MODEL, FALLBACK_MODEL]
    last_error = None

    for m in models_to_try:
        url = f"{BASE_URL}/models/{m}:generateContent?key={API_KEY}"
        gen_config = {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            }
        if response_schema:
            gen_config["responseMimeType"] = "application/json"
            gen_config["responseSchema"] = response_schema

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }

        for attempt in range(2):
            try:
                resp = requests.post(url, json=payload, timeout=60)
                data = resp.json()

                if "error" in data:
                    error_msg = data["error"].get("message", "")
                    # Overloaded or rate limited → try fallback
                    if "high demand" in error_msg or resp.status_code in (429, 503):
                        logger.warning(f"{m} overloaded, trying fallback")
                        last_error = error_msg
                        break  # try next model
                    # Other error → retry once
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    raise Exception(f"Gemini API error: {error_msg}")

                # Extract response
                cand = data.get("candidates", [{}])[0]
                parts = cand.get("content", {}).get("parts", [])
                text = ""
                for p in parts:
                    if "text" in p and not p.get("thought"):
                        text += p["text"]

                if not text and cand.get("finishReason") == "MAX_TOKENS":
                    # Thinking model used all tokens on thinking
                    if attempt == 0 and max_tokens < 2000:
                        payload["generationConfig"]["maxOutputTokens"] = 2000
                        continue
                    break  # try fallback

                usage = data.get("usageMetadata", {})
                input_tokens = usage.get("promptTokenCount", 0)
                output_tokens = usage.get("candidatesTokenCount", 0)
                thinking_tokens = usage.get("thoughtsTokenCount", 0)
                billable_output_tokens = output_tokens + thinking_tokens
                cost = log_ai_call(
                    model=m,
                    purpose=purpose,
                    ticker=ticker,
                    input_tokens=input_tokens,
                    output_tokens=billable_output_tokens,
                )
                result = {
                    "text": text.strip(),
                    "model": m,
                    "usage": {
                        "input": input_tokens,
                        "output": output_tokens,
                        "thinking": thinking_tokens,
                        "billable_output": billable_output_tokens,
                        "cost_usd": cost,
                    },
                }
                if m != PRIMARY_MODEL:
                    logger.info(f"Used fallback model: {m}")
                return result

            except requests.exceptions.Timeout:
                if attempt == 0:
                    time.sleep(3)
                    continue
                last_error = "timeout"
                break
            except Exception as e:
                last_error = str(e)
                break

    raise Exception(f"All Gemini models failed. Last error: {last_error}")


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "event_type": {"type": "string", "enum": [
            "contract", "earnings_beat", "earnings_miss", "shortage", "investment",
            "partnership", "expansion", "downgrade", "upgrade", "insider_buy",
            "insider_sell", "price_surge", "price_crash", "regulatory", "other"
        ]},
        "counterparty": {"type": "string"},
        "is_ai_related": {"type": "boolean"},
        "supply_chain_area": {"type": "string", "enum": [
            "memory", "networking", "optical", "power_cooling", "semicap",
            "eda_ip", "compute", "data_center", "energy", "other"
        ]},
        "revenue_impact": {"type": "string", "enum": ["high", "medium", "low"]},
        "time_horizon": {"type": "string", "enum": ["immediate", "short_term", "long_term"]},
        "evidence_delta": {"type": "number"},
        "asymmetry_delta": {"type": "number"},
        "risk_delta": {"type": "number"},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "verdict": {"type": "string", "enum": ["STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL"]},
        "conviction": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "upside_potential": {"type": "string"},
        "investment_thesis": {"type": "string"},
        "moat": {"type": "string"},
        "catalyst": {"type": "string"},
        "priced_in_pct": {"type": "number"},
        "bull_case": {"type": "array", "items": {"type": "string"}},
        "bear_case": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ticker", "verdict", "conviction", "evidence_delta", "summary",
                  "investment_thesis", "moat", "catalyst", "bull_case", "bear_case"]
}


def analyze_headline(headline: str, source: str = "", extra: str = "") -> dict | None:
    """Analyze a news headline for AI supply chain impact.
    Uses Gemini structured output (response_schema) for reliable JSON.
    Returns parsed dict or None on failure.
    """
    prompt = (
        f"Analyze this headline for AI supply chain investment impact.\n\n"
        f"Headline: {headline}\n"
        f"Source: {source}\n"
        f"{extra}\n\n"
        f"SCORING CALIBRATION for evidence_delta (be strict, most news is 3-5):\n"
        f"  1-2: Routine/recycled info, analyst repeats consensus, minor personnel change\n"
        f"  3-4: Incremental positive (beat estimates slightly, new product variant, small partnership)\n"
        f"  5-6: Meaningful catalyst (significant earnings beat >10%, major contract, new customer win)\n"
        f"  7-8: Game-changing (multi-billion deal, new market entry, supply chain shift, strategic pivot)\n"
        f"  9-10: Extremely rare — industry-reshaping (think: NVDA announces $20B fab, major M&A, regulatory shift)\n"
        f"  Negative: -3 to -8 for genuinely bad news (contract loss, earnings miss, downgrade with substance)\n\n"
        f"IMPORTANT: Do NOT default to 6-8. Most news is 3-5. Reserve 7+ for truly exceptional events.\n"
        f"If the headline is just restating known information or analyst commentary, score 2-3.\n\n"
        f"Also provide risk_delta 0-10, confidence 0.0-1.0, priced_in_pct 0-100.\n"
        f"Include 2-3 bull_case points and 2-3 bear_case points."
    )

    try:
        result = generate(prompt, max_tokens=4096, temperature=0.1,
                         model=os.getenv("ASCR_EVENT_MODEL", "gemini-3.1-flash-lite"),
                         response_schema=ANALYSIS_SCHEMA,
                         purpose="event_analysis")
        text = result["text"]

        # With structured output, response should be valid JSON directly
        parsed = json.loads(text)
        parsed["_model"] = result["model"]
        parsed["_usage"] = result["usage"]
        return parsed
    except json.JSONDecodeError:
        # Fallback: try cleaning
        text = result.get("text", "")
        if "```" in text:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                if block.startswith("{"):
                    text = block
                    break
        if not text.startswith("{"):
            start = text.find("{")
            if start >= 0:
                text = text[start:]
        if not text.endswith("}"):
            end = text.rfind("}")
            if end >= 0:
                text = text[:end+1]
        try:
            parsed = json.loads(text)
            parsed["_model"] = result["model"]
            parsed["_usage"] = result["usage"]
            return parsed
        except Exception:
            logger.warning(f"JSON parse error: {text[:300]}")
            return None
    except Exception as e:
        logger.error(f"Gemini analyze error: {e}")
        return None


def call_gemini(prompt: str, model: str = None, max_tokens: int = 2000,
                temperature: float = 0.1, purpose: str = "gemini_call",
                ticker: str = "") -> str:
    """Compatibility wrapper for older modules expecting a text response."""
    result = generate(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model,
        purpose=purpose,
        ticker=ticker,
    )
    return result.get("text", "")
