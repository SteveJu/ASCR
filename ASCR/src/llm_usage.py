"""AI API usage logging and cost estimation."""
from src.utils import get_logger

logger = get_logger("llm_usage")

# Prices are USD per 1M tokens. Output includes billed reasoning/thinking tokens.
MODEL_PRICING = {
    # Google Gemini
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    # Anthropic Claude
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
}

TIER_BY_MODEL = {
    "gemini-3.1-pro-preview": "standard",
    "gemini-2.5-flash": "standard",
    "gemini-2.5-flash-lite": "cheap",
    "claude-haiku-4-5": "cheap",
    "claude-sonnet-4-6": "standard",
    "claude-opus-4-6": "premium",
}


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0) -> float:
    """Estimate USD cost for a model call."""
    prices = MODEL_PRICING.get(model)
    if not prices:
        return 0.0
    return (
        input_tokens * prices["input"] +
        output_tokens * prices["output"]
    ) / 1_000_000


def log_ai_call(model: str, purpose: str, input_tokens: int = 0,
                output_tokens: int = 0, ticker: str = "") -> float:
    """Log an AI API call to ascr.sqlite; return estimated cost."""
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    cost = estimate_cost(model, input_tokens, output_tokens)
    tier = TIER_BY_MODEL.get(model, "unknown")

    try:
        from src import db
        db.log_llm_call(
            tier=tier,
            model=model,
            purpose=purpose,
            ticker=ticker or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
    except Exception as e:
        logger.warning(f"Failed to log AI call for {model}/{purpose}: {e}")

    return cost
