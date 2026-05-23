"""Utility functions."""
import logging
import os
from datetime import datetime

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def fmt_number(n, decimals=2) -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1e9:
        return f"${n/1e9:.{decimals}f}B"
    elif abs(n) >= 1e6:
        return f"${n/1e6:.{decimals}f}M"
    elif abs(n) >= 1e3:
        return f"${n/1e3:.{decimals}f}K"
    return f"${n:.{decimals}f}"

def pct(value, decimals=1) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{decimals}f}%"
