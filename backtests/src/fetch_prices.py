"""Fetch 1 year of historical prices for all universe tickers + benchmarks."""
import yfinance as yf
from datetime import datetime
from src.config import all_tickers, benchmarks, START_DATE, END_DATE
from src import db


def fetch_all():
    """Download historical prices for entire backtest period."""
    conn = db.get_conn()
    tickers = all_tickers() + benchmarks()

    print(f"Fetching prices for {len(tickers)} tickers: {START_DATE} → {END_DATE}")

    data = yf.download(tickers, start=START_DATE, end=END_DATE,
                       progress=True, threads=True)

    if data.empty:
        print("ERROR: No data returned")
        return

    count = 0
    for ticker in tickers:
        try:
            if len(tickers) > 1:
                close = data["Close"][ticker].dropna()
                opens = data["Open"][ticker].dropna()
                high = data["High"][ticker].dropna()
                low = data["Low"][ticker].dropna()
                vol = data["Volume"][ticker].dropna()
            else:
                close = data["Close"].dropna()
                opens = data["Open"].dropna()
                high = data["High"].dropna()
                low = data["Low"].dropna()
                vol = data["Volume"].dropna()

            for date in close.index:
                d = date.strftime("%Y-%m-%d")
                conn.execute(
                    "INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ticker, d,
                     float(opens.get(date, 0)), float(high.get(date, 0)),
                     float(low.get(date, 0)), float(close[date]),
                     int(vol.get(date, 0))))
                count += 1
        except Exception as e:
            print(f"  {ticker}: {e}")

    conn.commit()
    conn.close()
    print(f"Stored {count} price rows for {len(tickers)} tickers")


if __name__ == "__main__":
    db.init_db()
    fetch_all()
