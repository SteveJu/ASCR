"""Backtest configuration — isolated from live ASCR."""
import yaml
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

UNIVERSE_FILE = ROOT / "config" / "universe.yaml"
DB_PATH = ROOT / "data" / "backtest.sqlite"

# Backtest period
START_DATE = "2025-05-13"
END_DATE = "2026-05-13"

# LLM budget
MAX_LLM_CALLS_PER_DAY = 50
GEMINI_MODEL = "gemini-2.5-flash"  # Cheaper for bulk historical analysis


def all_tickers() -> list:
    with open(UNIVERSE_FILE) as f:
        u = yaml.safe_load(f)
    tickers = []
    for s in u.get("sectors", {}).values():
        if isinstance(s, dict):
            tickers.extend(s.get("tickers", []))
    return sorted(set(tickers))


def benchmarks() -> list:
    return ["QQQ", "SPY"]
