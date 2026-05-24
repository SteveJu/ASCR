"""US market calendar helpers."""
from datetime import date, timedelta
from functools import lru_cache


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _easter_date(year: int) -> date:
    """Return Western Easter date using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _fallback_market_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),       # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),       # Presidents Day
        _easter_date(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),         # Memorial Day
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),       # Labor Day
        _nth_weekday(year, 11, 3, 4),      # Thanksgiving
        _observed_fixed_holiday(year, 12, 25),
    }
    return {holiday for holiday in holidays if holiday.year == year}


@lru_cache(maxsize=16)
def us_market_holidays(year: int) -> set[date]:
    """Return full-day US equity market holidays for a calendar year.

    Uses pandas holiday rules when available, with a small built-in fallback so
    market-hour gates still work in minimal environments.
    """
    try:
        from pandas.tseries.holiday import (
            AbstractHolidayCalendar,
            GoodFriday,
            Holiday,
            USLaborDay,
            USMartinLutherKingJr,
            USMemorialDay,
            USPresidentsDay,
            USThanksgivingDay,
            nearest_workday,
        )

        class USMarketHolidayCalendar(AbstractHolidayCalendar):
            rules = [
                Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
                USMartinLutherKingJr,
                USPresidentsDay,
                GoodFriday,
                USMemorialDay,
                Holiday(
                    "Juneteenth National Independence Day",
                    month=6,
                    day=19,
                    start_date="2022-06-19",
                    observance=nearest_workday,
                ),
                Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
                USLaborDay,
                USThanksgivingDay,
                Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
            ]

        calendar = USMarketHolidayCalendar()
        holidays = calendar.holidays(start=f"{year}-01-01", end=f"{year}-12-31")
        return {holiday.date() for holiday in holidays}
    except Exception:
        return _fallback_market_holidays(year)


def is_us_market_holiday(value: date) -> bool:
    return value in us_market_holidays(value.year)
