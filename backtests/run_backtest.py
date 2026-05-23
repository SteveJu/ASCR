"""Main entry point for the backtest.

Usage:
  python run_backtest.py fetch    # Step 1: Download historical data
  python run_backtest.py analyze  # Step 2: Analyze filings with LLM
  python run_backtest.py sim      # Step 3: Run trading simulation
  python run_backtest.py all      # Run all steps
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from src import db


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    # Always init DB
    db.init_db()

    if cmd in ("fetch", "all"):
        print("=" * 60)
        print("STEP 1: Fetching historical data")
        print("=" * 60)

        from src.fetch_prices import fetch_all
        fetch_all()

        from src.fetch_sec import fetch_8k_filings, fetch_insider_trades
        fetch_8k_filings()
        fetch_insider_trades()

    if cmd in ("analyze", "all"):
        print("\n" + "=" * 60)
        print("STEP 2: Analyzing filings with LLM")
        print("=" * 60)

        from src.analyze_filings import analyze_batch
        analyze_batch()

    if cmd in ("sim", "all"):
        print("\n" + "=" * 60)
        print("STEP 3: Running trading simulation")
        print("=" * 60)

        from src.simulator import Simulator
        sim = Simulator()
        sim.run()

    if cmd == "v2":
        print("\n" + "=" * 60)
        print("BACKTEST V2: Sector-Aware + Dynamic Universe")
        print("=" * 60)

        from src.simulator_v2 import SimulatorV2
        sim = SimulatorV2()
        sim.run()

    if cmd == "status":
        conn = db.get_conn()
        prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        filings = conn.execute("SELECT COUNT(*) FROM sec_filings").fetchone()[0]
        insider = conn.execute("SELECT COUNT(*) FROM insider_trades").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        trades = conn.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0]
        print(f"Prices:  {prices:,} rows")
        print(f"8-K:     {filings:,} filings")
        print(f"Form 4:  {insider:,} trades")
        print(f"Events:  {events:,} (analyzed)")
        print(f"Trades:  {trades:,} (simulated)")
        conn.close()


if __name__ == "__main__":
    main()
