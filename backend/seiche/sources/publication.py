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
NYFED_PD_SOURCE_URL = "https://www.newyorkfed.org/markets/counterparties/primary-dealers-statistics"
CFTC_RELEASE_SOURCE_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm"
CBUAE_FX_SOURCE_URL = "https://centralbank.ae/en/forex-eibor/exchange-rates/"
CBUAE_FX_REMOTE_IDS = frozenset(
    f"VAT/AED_PER_{currency}" for currency in ("USD", "INR", "EUR", "GBP", "JPY", "CHF", "SGD")
)
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


def pd_positions_refresh_due(
    asofs: list[object], fetched_at: object, *, now: datetime,
) -> bool:
    """Recheck a PD cache across the nominal Thursday 16:15 New York release.

    NY Fed publishes the previous week's statistics. The position histories
    have Wednesday observation dates, eight days before their normal release.
    This is a polling deadline, not evidence that publication occurred. The
    source does not document a holiday/special-release adjustment here, so do
    not infer one or change observed dates. If a successful post-deadline
    response still lacks the expected week, retain its actual observations
    and recheck hourly. Normal cache TTL and failure fallback still apply.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")
    local_now = now.astimezone(_NEW_YORK)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    due = datetime.combine(monday + timedelta(days=3), time(16, 15), _NEW_YORK)
    if local_now < due:
        due -= timedelta(weeks=1)
    expected = due.date() - timedelta(days=8)
    try:
        fetched = datetime.fromisoformat(fetched_at) if isinstance(fetched_at, str) else None
    except ValueError:
        fetched = None
    if fetched is None or fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now:
        return True
    try:
        observed = [date.fromisoformat(value) if isinstance(value, str) else None for value in asofs]
    except ValueError:
        observed = []
    if observed and all(value is not None and expected <= value <= local_now.date() for value in observed):
        return False
    return fetched < due or now - fetched >= timedelta(hours=1)


def cftc_positions_refresh_due(
    asofs: list[object], fetched_at: object, *, now: datetime,
) -> bool:
    """Poll COT at nominal Friday 15:30 New York for Tuesday positions.

    CFTC publishes a separate holiday/special release calendar. This nominal
    boundary only starts checking; it never certifies publication or invents
    a holiday adjustment. If a successful response still lacks a configured
    contract's expected week, recheck hourly while retaining its actual dates.
    The ordinary TTL and existing failure fallback remain in effect.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")
    local_now = now.astimezone(_NEW_YORK)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    due = datetime.combine(monday + timedelta(days=4), time(15, 30), _NEW_YORK)
    if local_now < due:
        due -= timedelta(weeks=1)
    expected = due.date() - timedelta(days=3)
    try:
        fetched = datetime.fromisoformat(fetched_at) if isinstance(fetched_at, str) else None
    except ValueError:
        fetched = None
    if fetched is None or fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now:
        return True
    try:
        observed = [date.fromisoformat(value) if isinstance(value, str) else None for value in asofs]
    except ValueError:
        observed = []
    if observed and all(value is not None and expected <= value <= local_now.date() for value in observed):
        return False
    return fetched < due or now - fetched >= timedelta(hours=1)


def cbuae_fx_freshness(asof: object, *, now: datetime) -> dict:
    """Conservative daily table age; no unverified UAE release calendar.

    The publisher describes daily reference rates, but its table-update label
    is not a fixing clock or a guaranteed publication deadline. Do not exempt
    holidays or invent missed-release counts from a polling schedule.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")
    local_day = now.astimezone(ZoneInfo("Asia/Dubai")).date()
    age = None
    status = "dead" if asof is None else "unknown"
    try:
        observed = date.fromisoformat(asof) if isinstance(asof, str) else None
    except ValueError:
        observed = None
    if observed is not None and date(2018, 1, 1) <= observed <= local_day:
        age = (local_day - observed).days
        status = "fresh" if age == 0 else "aging" if age == 1 else "stale"
    return {
        "staleness": status,
        "age_days": age,
        "publication_frequency": "D",
        "freshness_policy": "cbuae-vat-fx-calendar-age-v1",
        "freshness_grace_days": 0,
        "freshness_basis": "Publisher table date: same Dubai calendar day fresh, one day aging, older stale; no verified publication calendar.",
        "publication_schedule": {
            "source_url": CBUAE_FX_SOURCE_URL,
            "timezone": "Asia/Dubai",
            "rule": "Publisher describes daily updates; exact publication time and holiday calendar are not verified.",
            "clock_precision": "unknown",
            "latest_due_at": None,
            "actual_published_at": None,
            "missed_publication_opportunities": None,
        },
    }


_DUBAI = ZoneInfo("Asia/Dubai")
_DONIA_PUBLICATION_AIM = time(9, 30)
_DONIA_COLLECTOR_GRACE = timedelta(minutes=90)
_DONIA_EARLIEST = date(2018, 1, 1)


def _uae_weekday(day: date) -> bool:
    """Monday to Friday. Public holidays are not verified and are not skipped."""
    return day.weekday() < 5


def _previous_uae_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not _uae_weekday(cursor):
        cursor -= timedelta(days=1)
    return cursor


def _donia_expected_chart_date(local_now: datetime) -> date:
    day = local_now.date()
    opens = datetime.combine(day, _DONIA_PUBLICATION_AIM, _DUBAI) + _DONIA_COLLECTOR_GRACE
    if _uae_weekday(day) and local_now >= opens:
        return day
    return _previous_uae_weekday(day)


def _donia_latest_aim(expected: date) -> datetime:
    return datetime.combine(expected, _DONIA_PUBLICATION_AIM, _DUBAI)


def _missed_uae_weekdays(observed: date, expected: date) -> int:
    if observed >= expected:
        return 0
    missed = 0
    cursor = observed
    while cursor < expected:
        cursor += timedelta(days=1)
        if _uae_weekday(cursor):
            missed += 1
    return missed


def cbuae_donia_freshness(asof: object, *, now: datetime) -> dict:
    """Keep the latest chart date fresh until the next aimed DONIA publication.

    CBUAE aims to publish by 09:30 Dubai time on Monday to Friday. The chart
    has no actual publication timestamp. The hourly collector has 90 minutes
    after that aim before a missing weekday print is aging. One missed weekday
    is aging. Two or more are stale. Weekend gaps stay fresh because no print
    is due. Public holidays are not excused.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("publication evaluation time must be timezone-aware")
    local_now = now.astimezone(_DUBAI)
    local_day = local_now.date()
    age = None
    status = "dead" if asof is None else "unknown"
    expected = _donia_expected_chart_date(local_now)
    try:
        observed = date.fromisoformat(asof) if isinstance(asof, str) else None
    except ValueError:
        observed = None
    if observed is not None and _DONIA_EARLIEST <= observed <= local_day:
        age = (local_day - observed).days
        missed = _missed_uae_weekdays(observed, expected)
        status = "fresh" if missed == 0 else "aging" if missed == 1 else "stale"
    return {
        "staleness": status,
        "age_days": age,
        "publication_frequency": "D",
        "freshness_policy": "cbuae-donia-publication-aim-v1",
        "freshness_grace_days": None,
        "freshness_basis": (
            "Latest publisher chart date stays fresh until 09:30 Asia/Dubai on the next "
            "Monday to Friday, plus 90 minutes for the hourly collector. Public holidays "
            "are not verified. This aim is not an observed publication timestamp."
        ),
        "publication_schedule": {
            "source_url": "https://centralbank.ae/en/our-operations/monetary-policy-and-domestic-markets/",
            "timezone": "Asia/Dubai",
            "rule": "CBUAE aims to publish DONIA by 09:30 on UAE business days; this is not an observed publication timestamp.",
            "clock_precision": "unknown",
            "latest_due_at": _donia_latest_aim(expected).isoformat(),
            "actual_published_at": None,
            "missed_publication_opportunities": None,
        },
    }


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
    if source == "cbuae_fx" and freq == "D" and isinstance(remote_id, str) and remote_id in CBUAE_FX_REMOTE_IDS:
        return cbuae_fx_freshness(asof, now=now)
    if source == "cbuae_donia" and freq == "D" and remote_id == "UAE_INTEREST_RATES/DONIA":
        return cbuae_donia_freshness(asof, now=now)
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
