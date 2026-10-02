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
H41_SOURCE_URL = "https://www.federalreserve.gov/releases/h41/"
# Only H.4.1 series with Wednesday observation dates. Match the upstream
# identity so short and long history aliases use the same release clock.
H41_WEEKLY_REMOTE_IDS = frozenset({
    "WALCL", "WRESBAL", "WTREGEN", "WCURCIR", "WLCFLPCL", "SWPT",
    "WLRRAFOIAL", "WMTSECL1", "H41RESPPALGTRFNWW",
})
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
DTS_SOURCE_URL = "https://home.treasury.gov/policy-issues/financial-markets-financial-institutions-and-fiscal-service/cash-and-debt-forecasting"


@lru_cache(maxsize=8)
def _federal_holidays(year: int) -> frozenset[date]:
    return frozenset(
        USFederalHolidayCalendar()
        .holidays(start=f"{year}-01-01", end=f"{year}-12-31")
        .date
    )


def treasury_dts_freshness(asof: str, *, now: datetime) -> dict:
    """DTS is due by 16:00 New York on the following federal business day.

    The deadline is an expectation, never an observed publication timestamp.
    Preserve weekends, holidays and the intraday cutoff in read-time checks.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")

    def business(day: date) -> bool:
        return day.weekday() < 5 and day not in _federal_holidays(day.year)

    def previous(day: date) -> date:
        day -= timedelta(days=1)
        while not business(day):
            day -= timedelta(days=1)
        return day

    local_now = now.astimezone(_NEW_YORK)
    release_day = local_now.date()
    if not business(release_day) or local_now.time() < time(16):
        release_day = previous(release_day)
    expected_day = previous(release_day)
    observed = date.fromisoformat(asof)
    missed = 0
    cursor = expected_day
    while cursor > observed:
        missed += 1
        cursor = previous(cursor)
    return {
        "freshness": "fresh" if missed == 0 else "aging" if missed == 1 else "stale",
        "freshness_policy": "treasury-dts-next-business-day-v1",
        "publication_schedule": {
            "source_url": DTS_SOURCE_URL,
            "timezone": "America/New_York",
            "rule": "16:00 on the following federal business day",
            "clock_precision": "scheduled",
            "latest_due_at": datetime.combine(release_day, time(16), _NEW_YORK).isoformat(),
            "actual_published_at": None,
            "expected_observation_date": expected_day.isoformat(),
            "missed_publication_opportunities": missed,
        },
    }


@lru_cache(maxsize=64)
def _release_for_week(monday: date) -> datetime:
    """Scheduled Monday 16:15 New York, shifted past federal holidays."""
    release_day = monday
    while release_day.weekday() >= 5 or release_day in _federal_holidays(
        release_day.year
    ):
        release_day += timedelta(days=1)
    return datetime.combine(release_day, time(16, 15), tzinfo=_NEW_YORK)


@lru_cache(maxsize=64)
def _h41_release_for_week(monday: date) -> datetime:
    release_day = monday + timedelta(days=3)
    while release_day.weekday() >= 5 or release_day in _federal_holidays(release_day.year):
        release_day += timedelta(days=1)
    return datetime.combine(release_day, time(16, 30), tzinfo=_NEW_YORK)


def _h41_freshness(asof: object, *, now: datetime) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")
    local_now = now.astimezone(_NEW_YORK)
    release_week = local_now.date() - timedelta(days=local_now.weekday())
    due = _h41_release_for_week(release_week)
    if local_now < due:
        release_week -= timedelta(weeks=1)
        due = _h41_release_for_week(release_week)
    expected = release_week + timedelta(days=2)
    missed = None
    status = "dead" if asof is None else "unknown"
    try:
        observed = date.fromisoformat(asof) if isinstance(asof, str) else None
    except ValueError:
        observed = None
    if observed is not None and observed <= local_now.date():
        # A late/missing Wednesday is a missed release, even when its raw age
        # still fits the generic ten-day grace period for weekly series.
        missed = max(0, (expected - observed).days + 6) // 7
        status = "fresh" if missed == 0 else "aging" if missed == 1 else "stale" if missed < 6 else "dead"
    return {
        "staleness": status,
        "publication_frequency": "W",
        "freshness_policy": "fred-h41-weekly-v1",
        "freshness_grace_days": None,
        "freshness_basis": "observation date versus scheduled H.4.1 releases; schedule is not a publication receipt",
        "publication_schedule": {
            "source_url": H41_SOURCE_URL,
            "timezone": "America/New_York",
            "rule": "Thursday 16:30; federal holiday moves release to the following business day",
            "clock_precision": "scheduled",
            "latest_due_at": due.isoformat(),
            "actual_published_at": None,
            "expected_observation_date": expected.isoformat(),
            "missed_release_weeks": missed,
        },
    }


def publication_refresh_due(
    source: object, remote_id: object, freq: object, asof: object,
    fetched_at: str, *, now: datetime,
) -> bool:
    """Expire a pre-release H.4.1 cache and poll missing releases hourly.

    The normal TTL still applies outside this override. A post-release fetch
    is not proof of new observations; preserve those clocks when the mirror
    has not caught up. Hourly retries avoid hammering FRED on every sweep.
    """
    if source != "fred" or freq != "W" or not isinstance(remote_id, str) or remote_id not in H41_WEEKLY_REMOTE_IDS:
        return False
    policy = _h41_freshness(asof, now=now)
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return True
    if fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now:
        return True
    if policy["staleness"] == "fresh":
        return False
    due = datetime.fromisoformat(policy["publication_schedule"]["latest_due_at"])
    return fetched < due or now - fetched >= timedelta(hours=1)


def publication_freshness(
    source: object,
    remote_id: object,
    freq: object,
    asof: object,
    *,
    now: datetime,
) -> dict | None:
    """Return a source-specific policy, or None for unrelated identities.

    Compare whole observation weeks: Friday holidays and short source weeks
    must not require a nonexistent Friday print. Zero missed releases is fresh,
    one is aging, two through five are stale, and six or more are dead. A fresh
    week does not attest completeness of daily prints or their publication time.
    """
    if source == "fred" and freq == "W" and isinstance(remote_id, str) and remote_id in H41_WEEKLY_REMOTE_IDS:
        return _h41_freshness(asof, now=now)
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
