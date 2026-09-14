"""Read-only publication policies for specifically identified source series.

H.10 publishes daily observations weekly. Its schedule is an expectation, never
an observed publication/availability timestamp or evidence of trading authority.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from pandas.tseries.holiday import USFederalHolidayCalendar


H10_SOURCE_URL = "https://www.federalreserve.gov/releases/h10/"
# Explicit FRED identities, including the three daily dollar indexes. Mnemonic
# aliases share their source identity; unrelated daily series keep their policy.
H10_DAILY_REMOTE_IDS = frozenset(
    {
        "DEXUSUK",
        "DEXUSAL",
        "DEXCAUS",
        "DEXSZUS",
        "DEXMXUS",
        "DEXBZUS",
        "DEXSFUS",
        "DEXUSNZ",
        "DEXDNUS",
        "DEXHKUS",
        "DEXMAUS",
        "DEXNOUS",
        "DEXSDUS",
        "DEXSIUS",
        "DEXTAUS",
        "DEXTHUS",
        "DEXSLUS",
        "DEXCHUS",
        "DEXJPUS",
        "DEXKOUS",
        "DEXUSEU",
        "DEXINUS",
        "DTWEXBGS",
        "DTWEXAFEGS",
        "DTWEXEMEGS",
    }
)
_NEW_YORK = ZoneInfo("America/New_York")


@lru_cache(maxsize=8)
def _federal_holidays(year: int) -> frozenset[date]:
    return frozenset(
        USFederalHolidayCalendar()
        .holidays(start=f"{year}-01-01", end=f"{year}-12-31")
        .date
    )


@lru_cache(maxsize=64)
def _release_for_week(monday: date) -> datetime:
    """Scheduled Monday 16:15 New York, shifted past federal holidays."""
    release_day = monday
    while release_day.weekday() >= 5 or release_day in _federal_holidays(
        release_day.year
    ):
        release_day += timedelta(days=1)
    return datetime.combine(release_day, time(16, 15), tzinfo=_NEW_YORK)


def publication_freshness(
    source: object,
    remote_id: object,
    freq: object,
    asof: object,
    *,
    now: datetime,
) -> dict | None:
    """Return the H.10 policy projection, or None for all other identities.

    Compare whole observation weeks: Friday holidays and short source weeks
    must not require a nonexistent Friday print. Zero missed releases is fresh,
    one is aging, two through five are stale, and six or more are dead. A fresh
    week does not attest completeness of daily prints or their publication time.
    """
    if (
        source != "fred"
        or freq != "D"
        or not isinstance(remote_id, str)
        or remote_id not in H10_DAILY_REMOTE_IDS
    ):
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")
    local_now = now.astimezone(_NEW_YORK)
    release_week = local_now.date() - timedelta(days=local_now.weekday())
    due = _release_for_week(release_week)
    if local_now < due:
        release_week -= timedelta(weeks=1)
        due = _release_for_week(release_week)
    expected_week = release_week - timedelta(weeks=1)
    missed = None
    status = "dead" if asof is None else "unknown"
    try:
        observed = date.fromisoformat(asof) if isinstance(asof, str) else None
    except ValueError:
        observed = None
    if observed is not None and observed <= local_now.date():
        observed_week = observed - timedelta(days=observed.weekday())
        missed = max(0, (expected_week - observed_week).days // 7)
        status = (
            "fresh"
            if missed == 0
            else "aging"
            if missed == 1
            else "stale"
            if missed < 6
            else "dead"
        )
    return {
        "staleness": status,
        "publication_frequency": "W",
        "freshness_policy": "fred-h10-weekly-v1",
        "freshness_grace_days": None,
        "freshness_basis": "observation week versus scheduled H.10 releases; schedule is not a publication receipt",
        "publication_schedule": {
            "source_url": H10_SOURCE_URL,
            "timezone": "America/New_York",
            "rule": "Monday 16:15; federal holiday moves release to the following business day",
            "clock_precision": "scheduled",
            "latest_due_at": due.isoformat(),
            "actual_published_at": None,
            "expected_observation_week_start": expected_week.isoformat(),
            "expected_observation_week_end": (
                expected_week + timedelta(days=4)
            ).isoformat(),
            "missed_release_weeks": missed,
        },
    }
