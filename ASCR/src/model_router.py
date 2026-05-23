"""LLM model router V2 — routes tasks to appropriate model tier.

Tiers:
  no_model:      prices, technical indicators, scoring math, stop loss — NO LLM
  cheap_filter:  news headline relevance, dedup, low-value text screening
  main_model:    event extraction, SEC filing summary, earnings call tone analysis
  deep_model:    ONLY on high_impact_event — S-tier review, complex multi-signal, position exit review

Rules:
  - Cron jobs CANNOT call deep_model unless high_impact_event
  - All LLM output MUST be JSON
  - LLM must NOT decide buy/sell — only extract, summarize, classify
  - Cost tracked per call
"""
import json
import os
from src import config, db
from src.utils import get_logger

logger = get_logger("model_router")

# Pricing estimates (per 1M tokens)
COST_PER_1M = {
    "cheap": {"input": 0.25, "output": 1.25},       # Haiku
    "standard": {"input": 3.0, "output": 15.0},      # Sonnet
    "premium": {"input": 15.0, "output": 75.0},      # Opus
}

class ModelRouter:
    def __init__(self):
        self.cfg = config.models()
        self.daily_budget = self.cfg.get("daily_budget_usd", 2.0)

    def _check_budget(self, tier: str) -> bool:
        """Check if we're within daily budget."""
        spent = db.get_daily_llm_cost()
        if spent >= self.daily_budget:
            logger.warning(f"Daily LLM budget exhausted (${spent:.2f} / ${self.daily_budget:.2f})")
            return False
        return True

    def _estimate_cost(self, tier: str, input_tokens: int, output_tokens: int) -> float:
        costs = COST_PER_1M.get(tier, COST_PER_1M["cheap"])
        return (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000

    def call(self, tier: str, purpose: str, prompt: str, ticker: str = "",
             require_high_impact: bool = False, is_high_impact: bool = False) -> dict:
        """Route a call to the appropriate model tier.

        Returns: {"success": bool, "result": dict|None, "tier": str, "reason": str}
        """
        # Enforce deep_model restrictions
        if tier == "premium" and require_high_impact and not is_high_impact:
            logger.info(f"Skipping premium call for {ticker}/{purpose} — not high impact")
            return {"success": False, "result": None, "tier": tier, "reason": "not_high_impact"}

        if not self._check_budget(tier):
            return {"success": False, "result": None, "tier": tier, "reason": "budget_exhausted"}

        # TODO: Actual LLM API call integration (Phase 2)
        # For now, return placeholder
        logger.info(f"[PLACEHOLDER] Would call {tier} model for {purpose} ({ticker})")

        # Log the call (even placeholder)
        db.log_llm_call(tier, f"placeholder-{tier}", purpose, ticker, 0, 0, 0)

        return {
            "success": False,
            "result": None,
            "tier": tier,
            "reason": "llm_not_integrated",
        }

    def classify_news_batch(self, headlines: list) -> list:
        """Batch classify news headlines (cheap tier)."""
        # MVP: use keyword classifier, no LLM
        from src.event_classifier import classify_headline
        return [classify_headline(h.get("headline", ""), h.get("ticker", "")) for h in headlines]

    def analyze_filing(self, filing_text: str, ticker: str, is_position_held: bool = False) -> dict:
        """Analyze SEC filing (standard or premium tier)."""
        tier = "premium" if is_position_held else "standard"
        return self.call(tier, "filing_analysis", filing_text[:1000], ticker,
                        require_high_impact=(tier == "premium"),
                        is_high_impact=is_position_held)

    def earnings_call_analysis(self, transcript: str, ticker: str, is_position_held: bool = False) -> dict:
        """Analyze earnings call (standard tier, premium for positions)."""
        tier = "premium" if is_position_held else "standard"
        return self.call(tier, "earnings_analysis", transcript[:1000], ticker,
                        require_high_impact=(tier == "premium"),
                        is_high_impact=is_position_held)

# Singleton
_router = None

def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
