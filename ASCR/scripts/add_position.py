#!/usr/bin/env python3
"""Add a position with trade card generation.

Usage: add_position.py TICKER PRICE SHARES [OPTIONS]
  --date DATE             Entry date (default: today)
  --thesis "TEXT"         Investment thesis
  --horizon "TEXT"        Expected holding period
  --max-loss PCT          Max loss % (default: 25)
  --trailing PCT          Trailing stop % (default: 15)
  --notes "TEXT"          Additional notes
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import db
from src.exit_rules import generate_trade_card
from src.price_fetcher import get_ticker_info
from datetime import datetime

parser = argparse.ArgumentParser(description="Add a stock position")
parser.add_argument("ticker", type=str)
parser.add_argument("price", type=float)
parser.add_argument("shares", type=float)
parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
parser.add_argument("--thesis", default="")
parser.add_argument("--horizon", default="1-3 months")
parser.add_argument("--max-loss", type=float, default=25)
parser.add_argument("--trailing", type=float, default=15)
parser.add_argument("--notes", default="")

args = parser.parse_args()
ticker = args.ticker.upper()

# Get current price for trade card
info = get_ticker_info(ticker)
current_price = None
prices = db.get_prices(ticker, days=5)
if prices:
    current_price = prices[0]["close"]

# Add position
pos_id = db.add_position(
    ticker, args.price, args.shares, args.date,
    thesis=args.thesis, expected_holding=args.horizon,
    max_loss_pct=args.max_loss, trailing_stop_pct=args.trailing,
    notes=args.notes
)

# Get position back for trade card
position = db.get_position(pos_id)

# Print trade card
card = generate_trade_card(position, current_price)
print(card)
print(f"\nPosition #{pos_id} added successfully.")
print(f"Company: {info.get('name', ticker)}")
if current_price:
    pnl_pct = (current_price - args.price) / args.price * 100
    print(f"Current: ${current_price:.2f} ({pnl_pct:+.1f}% from entry)")
