"""Universe management — fixed + dynamic candidate discovery."""
from src import config, db
from src.utils import get_logger

logger = get_logger("universe")

def get_full_universe() -> list:
    """Get all tickers: fixed universe + promoted discovered candidates."""
    fixed = config.all_tickers()
    # Could add promoted candidates here in future
    return sorted(set(fixed))

def add_candidate(ticker: str, source: str, reason: str):
    """Add a dynamically discovered candidate."""
    db.add_discovered_candidate(ticker, source, reason)
    logger.info(f"New candidate discovered: {ticker} (source: {source}, reason: {reason})")
