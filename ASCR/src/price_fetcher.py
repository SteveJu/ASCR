"""Fetch and store price/volume data from yfinance."""
import time
import yfinance as yf
from src import config, db
from src.utils import get_logger

logger = get_logger("price_fetcher")


def fetch_prices(tickers: list = None, period: str = "3mo"):
    """Fetch price history for all tickers with retry."""
    if tickers is None:
        tickers = config.all_tickers()

    logger.info(f"Fetching prices for {len(tickers)} tickers (period={period})...")

    batch_str = " ".join(tickers)
    data = None

    for attempt in range(3):
        try:
            data = yf.download(batch_str, period=period, interval="1d",
                               progress=False, threads=True)
            if data is not None and not data.empty:
                break
            logger.warning(f"yfinance returned empty data (attempt {attempt+1}/3)")
        except Exception as e:
            logger.error(f"Batch download failed (attempt {attempt+1}/3): {e}")
        if attempt < 2:
            time.sleep(5)

    if data is None or data.empty:
        logger.warning("No price data returned after 3 attempts")
        return

    count = 0
    for ticker in tickers:
        try:
            if len(tickers) > 1:
                close = data["Close"][ticker].dropna()
                open_ = data["Open"][ticker].dropna()
                high = data["High"][ticker].dropna()
                low = data["Low"][ticker].dropna()
                vol = data["Volume"][ticker].dropna()
            else:
                close = data["Close"].dropna()
                open_ = data["Open"].dropna()
                high = data["High"].dropna()
                low = data["Low"].dropna()
                vol = data["Volume"].dropna()

            for date in close.index:
                d = date.strftime("%Y-%m-%d")
                db.upsert_price(
                    ticker, d,
                    float(open_.get(date, 0)),
                    float(high.get(date, 0)),
                    float(low.get(date, 0)),
                    float(close[date]),
                    int(vol.get(date, 0)),
                )
                count += 1
        except Exception as e:
            logger.warning(f"Error processing {ticker}: {e}")

    logger.info(f"Stored {count} price records for {len(tickers)} tickers.")


def get_ticker_info(ticker: str) -> dict:
    """Get fundamental info for a ticker (market cap, PE, etc.)."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        return {
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "revenue": info.get("totalRevenue"),
            "revenue_growth": info.get("revenueGrowth"),
            "gross_margin": info.get("grossMargins"),
            "profit_margin": info.get("profitMargins"),
            "debt_to_equity": info.get("debtToEquity"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "short_ratio": info.get("shortRatio"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "name": info.get("shortName") or info.get("longName") or ticker,
        }
    except Exception as e:
        logger.warning(f"Failed to get info for {ticker}: {e}")
        return {"name": ticker}


if __name__ == "__main__":
    fetch_prices()
