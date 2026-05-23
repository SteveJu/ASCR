#!/usr/bin/env python3
"""View/modify the universe watchlist."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import config

u = config.universe()
for sector_key, sector in u.get("sectors", {}).items():
    print(f"\n{sector['name']}:")
    print(f"  {', '.join(sector['tickers'])}")
