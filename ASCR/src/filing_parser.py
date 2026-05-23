"""SEC filing parser — placeholder for Phase 2 LLM-powered parsing."""
from src.utils import get_logger

logger = get_logger("filing_parser")

def parse_8k(filing: dict) -> dict:
    """Parse 8-K filing. MVP: return metadata only."""
    return {
        "ticker": filing.get("ticker"),
        "form": filing.get("form"),
        "date": filing.get("date"),
        "description": filing.get("description", ""),
        "events": [],
        "note": "Full 8-K parsing requires LLM (Phase 2)",
    }
