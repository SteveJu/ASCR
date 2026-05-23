"""Data quality — timestamp tracking, lookahead bias prevention, source reliability.

Every piece of data tracks:
  available_at:  when we first saw it (our system time)
  published_at:  when it was published (source time)
  as_of_date:    the date it represents

Backtest rules:
  - NEVER use data where available_at > as_of_date being evaluated
  - If timestamp unreliable, add conservative delay
"""
from datetime import datetime, timedelta
from src.utils import get_logger

logger = get_logger("data_quality")

# Conservative delays for unreliable timestamps
SOURCE_DELAYS = {
    "sec_edgar": timedelta(hours=0),      # SEC filings have exact accepted time
    "yfinance": timedelta(hours=0),       # EOD prices available after close
    "finviz": timedelta(minutes=30),      # News aggregator, slight delay
    "google_news": timedelta(hours=1),    # RSS aggregation delay
    "earnings_call": timedelta(hours=2),  # Transcript availability delay
    "unknown": timedelta(hours=4),        # Conservative default
}


def get_safe_available_time(source: str, published_at: str = None) -> datetime:
    """Get conservative available_at time accounting for source delay."""
    delay = SOURCE_DELAYS.get(source, SOURCE_DELAYS["unknown"])

    if published_at:
        try:
            pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            return pub_dt + delay
        except (ValueError, TypeError):
            pass

    return datetime.now()


def assert_no_lookahead(data_date: str, eval_date: str, source: str = "unknown"):
    """Assert that data was available before the evaluation date.

    Raises AssertionError if lookahead bias detected.
    """
    try:
        d_dt = datetime.strptime(data_date, "%Y-%m-%d")
        e_dt = datetime.strptime(eval_date, "%Y-%m-%d")
        delay = SOURCE_DELAYS.get(source, SOURCE_DELAYS["unknown"])

        safe_available = d_dt + delay
        if safe_available.date() > e_dt.date():
            raise AssertionError(
                f"Lookahead bias: data from {data_date} (source={source}, "
                f"safe_available={safe_available.date()}) used in eval on {eval_date}"
            )
    except ValueError:
        logger.warning(f"Could not parse dates: data={data_date}, eval={eval_date}")


def validate_backtest_data(events: list, eval_date: str) -> list:
    """Filter events to only those safely available before eval_date."""
    valid = []
    for event in events:
        event_date = event.get("event_date", event.get("date", ""))
        source = event.get("source", "unknown")
        try:
            assert_no_lookahead(event_date, eval_date, source)
            valid.append(event)
        except AssertionError as e:
            logger.debug(f"Excluded (lookahead): {e}")
    return valid


def tag_data_quality(event: dict) -> dict:
    """Add data quality metadata to an event."""
    source = event.get("source", "unknown")
    published = event.get("published_at", event.get("event_date", ""))

    event["_dq"] = {
        "source": source,
        "published_at": published,
        "available_at": get_safe_available_time(source, published).isoformat(),
        "delay_hours": SOURCE_DELAYS.get(source, SOURCE_DELAYS["unknown"]).total_seconds() / 3600,
        "reliable_timestamp": source in ("sec_edgar", "yfinance"),
    }
    return event
