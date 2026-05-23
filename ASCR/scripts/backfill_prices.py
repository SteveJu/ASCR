#!/usr/bin/env python3
"""Backfill price history."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.price_fetcher import fetch_prices
from src import config

period = sys.argv[1] if len(sys.argv) > 1 else "1y"
tickers = sys.argv[2:] if len(sys.argv) > 2 else config.all_tickers()
fetch_prices(tickers, period=period)
