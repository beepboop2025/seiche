"""Reusable dated calendar datasets selected by monetary-area packs.

The :mod:`holidays` dependency supplies versioned civil-holiday calculations;
pack-specific wrappers add settlement-system closures and weekend overrides.
Every deployed calendar is bounded, so an unreviewed future year fails loud.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Callable

import holidays as holiday_data

from seiche.markets.base import CalendarUnavailableError


def western_easter_sunday(year: int) -> date:
    """Gregorian computus (Meeus/Jones/Butcher), returned as a civil date."""

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
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def next_weekday(day: date) -> date:
    current = day
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


def nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return date(
        year,
        month,
        1 + (weekday - first.weekday()) % 7 + (occurrence - 1) * 7,
    )


def last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def target_holidays(year: int) -> frozenset[date]:
    easter = western_easter_sunday(year)
    return frozenset(
        {
            date(year, 1, 1),
            easter - timedelta(days=2),
            easter + timedelta(days=1),
            date(year, 5, 1),
            date(year, 12, 25),
            date(year, 12, 26),
        }
    )


def country_holiday_provider(
    country: str,
    *,
    subdiv: str | None = None,
    extra: Callable[[int], set[date] | frozenset[date]] | None = None,
) -> Callable[[int], frozenset[date]]:
    """Build a cached provider from the maintained ``holidays`` dataset."""

    @lru_cache(maxsize=None)
    def provider(year: int) -> frozenset[date]:
        calendar = holiday_data.country_holidays(country, subdiv=subdiv, years=[year])
        values = set(calendar.keys())
        if extra is not None:
            values.update(extra(year))
        return frozenset(values)

    return provider


def country_working_day_provider(
    country: str,
    *,
    subdiv: str | None = None,
) -> Callable[[int], frozenset[date]]:
    """Return officially designated weekend workdays when the dataset has them."""

    @lru_cache(maxsize=None)
    def provider(year: int) -> frozenset[date]:
        calendar = holiday_data.country_holidays(country, subdiv=subdiv, years=[year])
        return frozenset(
            day for day in calendar.weekend_workdays if day.year == year
        )

    return provider


def england_wales_bank_holidays(year: int) -> frozenset[date]:
    easter = western_easter_sunday(year)
    holidays = {
        next_weekday(date(year, 1, 1)),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        nth_weekday(year, 5, 0, 1),
        last_weekday(year, 5, 0),
        last_weekday(year, 8, 0),
    }
    # Christmas and Boxing Day substitutions consume the next two free
    # weekdays in order when either fixed date falls on a weekend.
    for fixed in (date(year, 12, 25), date(year, 12, 26)):
        observed = fixed if fixed.weekday() < 5 else next_weekday(fixed)
        while observed in holidays:
            observed += timedelta(days=1)
            observed = next_weekday(observed)
        holidays.add(observed)
    return frozenset(holidays)


def _japan_bank_closures(year: int) -> frozenset[date]:
    return frozenset(
        {
            date(year, 1, 2),
            date(year, 1, 3),
            date(year, 12, 31),
        }
    )


japan_bank_holidays = country_holiday_provider(
    "JP",
    extra=_japan_bank_closures,
)

# Public names keep pack declarations compact and make the jurisdictional
# choice (for example Maharashtra rather than a synthetic all-India calendar)
# reviewable in one place.
australia_nsw_holidays = country_holiday_provider("AU", subdiv="NSW")
china_public_holidays = country_holiday_provider("CN")
china_working_weekends = country_working_day_provider("CN")
hong_kong_holidays = country_holiday_provider("HK")
india_maharashtra_holidays = country_holiday_provider("IN", subdiv="MH")
new_zealand_wellington_holidays = country_holiday_provider("NZ", subdiv="WGN")
singapore_holidays = country_holiday_provider("SG")
uk_england_holidays = country_holiday_provider("GB", subdiv="ENG")
