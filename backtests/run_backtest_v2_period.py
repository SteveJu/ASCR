"""
Backtest May 2024 → May 2025 (Year 1)
Same logic as V3-Daily, but earlier period.
Separate DB: data/backtest_y1.sqlite

Usage:
  python3 run_backtest_v2_period.py fetch
  python3 run_backtest_v2_period.py enrich
  python3 run_backtest_v2_period.py analyze
  python3 run_backtest_v2_period.py sim
  python3 run_backtest_v2_period.py all
  python3 run_backtest_v2_period.py status
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Override config BEFORE importing anything else
import src.config as config
config.START_DATE = "2024-05-13"
config.END_DATE = "2025-05-13"
config.DB_PATH = config.ROOT / "data" / "backtest_y1.sqlite"
config.GEMINI_MODEL = "gemini-2.5-flash-lite"
config.MAX_LLM_CALLS_PER_DAY = 999  # cheapest

from src import db

# Monkey-patch db module to use new path
import importlib
importlib.reload(db)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    db.init_db()

    print(f"Backtest Y1: {config.START_DATE} → {config.END_DATE}")
    print(f"DB: {config.DB_PATH}")
    print(f"Model: {config.GEMINI_MODEL}")
    print()

    if cmd in ("fetch", "all"):
        print("=" * 60)
        print("STEP 1: Fetching historical data (prices + SEC)")
        print("=" * 60)

        from src.fetch_prices import fetch_all
        fetch_all()

        from src.fetch_sec import fetch_8k_filings, fetch_insider_trades
        fetch_8k_filings()
        fetch_insider_trades()

    if cmd in ("enrich", "all"):
        print("\n" + "=" * 60)
        print("STEP 2: Enriching 8-K item details + insider events")
        print("=" * 60)

        from src.enrich_data import enrich_8k_items, generate_insider_events
        enrich_8k_items()
        generate_insider_events()

    if cmd in ("analyze", "all"):
        print("\n" + "=" * 60)
        print("STEP 3: Analyzing filings with Gemini (cheapest model)")
        print("=" * 60)

        from src.analyze_filings import analyze_batch
        analyze_batch()

    if cmd in ("sim", "all"):
        print("\n" + "=" * 60)
        print("STEP 4: Running V3-Daily simulation")
        print("=" * 60)

        from src.simulator_v2 import SimulatorV2
        sim = SimulatorV2()
        # Monkey-patch to daily rebalance
        sim.rebalance_interval = 1
        sim.run()

    if cmd == "status":
        conn = db.get_conn()
        for t in ['prices', 'sec_filings', 'insider_trades', 'events', 'backtest_trades', 'backtest_daily']:
            try:
                c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                try:
                    date_col = 'date' if t != 'sec_filings' else 'filing_date'
                    if t == 'insider_trades':
                        date_col = 'filing_date'
                    mn = conn.execute(f"SELECT MIN({date_col}) FROM {t}").fetchone()[0]
                    mx = conn.execute(f"SELECT MAX({date_col}) FROM {t}").fetchone()[0]
                    print(f"  {t}: {c:,} rows ({mn} → {mx})")
                except:
                    print(f"  {t}: {c:,} rows")
            except:
                print(f"  {t}: (not created)")
        conn.close()


if __name__ == "__main__":
    main()
