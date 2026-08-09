"""Reusable calendar rules; packs select the rules that their venue validates."""

from __future__ import annotations

from datetime import date, timedelta

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


_JAPAN_BANK_HOLIDAYS = {
    2026: {
        (1, 1), (1, 2), (1, 3), (1, 12), (2, 11), (2, 23), (3, 20),
        (4, 29), (5, 3), (5, 4), (5, 5), (5, 6), (7, 20), (8, 11),
        (9, 21), (9, 22), (9, 23), (10, 12), (11, 3), (11, 23), (12, 31),
    },
    2027: {
        (1, 1), (1, 2), (1, 3), (1, 11), (2, 11), (2, 23), (3, 21),
        (3, 22), (4, 29), (5, 3), (5, 4), (5, 5), (7, 19), (8, 11),
        (9, 20), (9, 23), (10, 11), (11, 3), (11, 23), (12, 31),
    },
}


def japan_bank_holidays(year: int) -> frozenset[date]:
    try:
        values = _JAPAN_BANK_HOLIDAYS[year]
    except KeyError as exc:
        raise CalendarUnavailableError(
            f"Japan settlement holidays have not been loaded for {year}"
        ) from exc
    return frozenset(date(year, month, day) for month, day in values)
