"""Live quote helpers for ASCR-H display and execution-adjacent reporting."""
import time

from src import db
from src.utils import get_logger

logger = get_logger("quote_provider")

_quote_cache = {}
_CACHE_TTL = 60


def get_live_quote(ticker: str) -> dict:
    """Return current price plus previous session close when available.

    The previous close must come from the quote source, not the latest ASCR DB
    price row, because the local research DB can lag live market sessions.
    """
    now = time.time()
    if ticker in _quote_cache:
        cached_quote, cached_at = _quote_cache[ticker]
        if now - cached_at < _CACHE_TTL:
            return cached_quote

    quote = {"price": 0.0, "previous_close": None}
    try:
        import yfinance as yf

        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
        if not price or not prev_close:
            try:
                fast_info = yf_ticker.fast_info
                price = price or fast_info.get("lastPrice")
                prev_close = (
                    prev_close
                    or fast_info.get("regularMarketPreviousClose")
                    or fast_info.get("previousClose")
                )
            except Exception as exc:
                logger.warning(f"quote_fast_info_failed ticker={ticker} error={exc}")
        if not price or not prev_close:
            hist = yf_ticker.history(period="5d")
            if len(hist) > 0 and not price:
                price = float(hist["Close"].iloc[-1])
            if len(hist) >= 2 and not prev_close:
                prev_close = float(hist["Close"].iloc[-2])
        if price and price > 0:
            quote = {
                "price": float(price),
                "previous_close": float(prev_close) if prev_close and prev_close > 0 else None,
            }
            _quote_cache[ticker] = (quote, now)
            return quote
    except Exception as exc:
        logger.warning(f"quote_yfinance_failed ticker={ticker} error={exc}")

    try:
        prices = db.read_radar_prices(ticker, days=1)
    except Exception as exc:
        logger.warning(f"quote_fallback_db_failed ticker={ticker} error={exc}")
        prices = []
    price = prices[0]["close"] if prices else 0.0
    if price and price > 0:
        quote = {"price": float(price), "previous_close": None}
        _quote_cache[ticker] = (quote, now)
        logger.info(f"quote_fallback_db ticker={ticker} price=${price:.2f}")
    else:
        logger.warning(f"quote_missing ticker={ticker} sources=yfinance,ascr_db")
    return quote


def get_live_price(ticker: str) -> float:
    """Return current price, falling back to 0.0 when no source is available."""
    return get_live_quote(ticker)["price"]
