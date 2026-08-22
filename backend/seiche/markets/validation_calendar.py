"""Pure, offline validation for market-pack calendars and publication clocks.

The validator deliberately distinguishes an incorrect known calendar fact
(``FAIL``) from a future calendar which has not yet been published
(``PENDING``).  It performs no network access and returns JSON-compatible data
which can be wrapped by the validation evidence layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seiche.markets.base import (
    CalendarUnavailableError,
    MarketPack,
    PublicationClockUnavailableError,
)


FIXTURE_SET_VERSION = "market-calendar-2026-v2"


@dataclass(frozen=True, slots=True)
class BusinessDayFixture:
    """One authoritative settlement-day expectation."""

    fixture_id: str
    fixture_version: str
    market_id: str
    calendar_id: str
    day: date
    expected_business_day: bool


@dataclass(frozen=True, slots=True)
class PublicationClockFixture:
    """One publication schedule expectation, expressed in UTC."""

    fixture_id: str
    fixture_version: str
    market_id: str
    calendar_id: str
    adapter_id: str
    event_day: date
    expected_publication_time: datetime


CalendarFixture = BusinessDayFixture | PublicationClockFixture


def _business_fixture(
    fixture_id: str,
    market_id: str,
    calendar_id: str,
    day: date,
    expected: bool,
) -> BusinessDayFixture:
    return BusinessDayFixture(
        fixture_id=fixture_id,
        fixture_version=FIXTURE_SET_VERSION,
        market_id=market_id,
        calendar_id=calendar_id,
        day=day,
        expected_business_day=expected,
    )


def _clock_fixture(
    fixture_id: str,
    market_id: str,
    calendar_id: str,
    adapter_id: str,
    event_day: date,
    expected: datetime,
) -> PublicationClockFixture:
    return PublicationClockFixture(
        fixture_id=fixture_id,
        fixture_version=FIXTURE_SET_VERSION,
        market_id=market_id,
        calendar_id=calendar_id,
        adapter_id=adapter_id,
        event_day=event_day,
        expected_publication_time=expected,
    )


# These are deliberately small, reviewable regression fixtures rather than a
# second calendar database.  Every date is backed by the official source URI
# declared on its pack; the provider remains responsible for complete years.
REPRESENTATIVE_FIXTURES: Mapping[str, tuple[CalendarFixture, ...]] = MappingProxyType(
    {
        "AU-AUD": (
            _business_fixture(
                "au-australia-day-2026",
                "AU-AUD",
                "AU-RITS",
                date(2026, 1, 26),
                False,
            ),
        ),
        "CN-CNY": (
            _business_fixture(
                "cn-spring-festival-working-weekend-2026",
                "CN-CNY",
                "CN-CFETS-SETTLEMENT",
                date(2026, 2, 14),
                True,
            ),
            _business_fixture(
                "cn-national-day-2026",
                "CN-CNY",
                "CN-CFETS-SETTLEMENT",
                date(2026, 10, 1),
                False,
            ),
        ),
        "EA-EUR": (
            _business_fixture(
                "target-good-friday-2026",
                "EA-EUR",
                "TARGET",
                date(2026, 4, 3),
                False,
            ),
            _clock_fixture(
                "ecb-benchmark-easter-roll-2026",
                "EA-EUR",
                "TARGET",
                "ecb_benchmark",
                date(2026, 4, 2),
                datetime(2026, 4, 7, 6, tzinfo=UTC),
            ),
        ),
        "HK-HKD": (
            _business_fixture(
                "hk-national-day-2026",
                "HK-HKD",
                "HK-HKD-CHATS",
                date(2026, 10, 1),
                False,
            ),
        ),
        "IN-INR": (
            _business_fixture(
                "in-republic-day-2026",
                "IN-INR",
                "IN-MUMBAI-MONEY-MARKET",
                date(2026, 1, 26),
                False,
            ),
        ),
        "JP-JPY": (
            _business_fixture(
                "jp-autumnal-equinox-2026",
                "JP-JPY",
                "JP-BOJ-NET",
                date(2026, 9, 22),
                False,
            ),
        ),
        "KR-KRW": (
            # The Bank of Korea's 2026 Holiday Schedule explicitly lists the
            # regional election on June 3; no 2027 schedule is inferred.
            _business_fixture(
                "kr-regional-election-2026",
                "KR-KRW",
                "KR-BOK-WIRE",
                date(2026, 6, 3),
                False,
            ),
        ),
        "NZ-NZD": (
            _business_fixture(
                "nz-waitangi-day-2026",
                "NZ-NZD",
                "NZ-ESAS",
                date(2026, 2, 6),
                False,
            ),
        ),
        "SG-SGD": (
            _business_fixture(
                "sg-national-day-observed-2026",
                "SG-SGD",
                "SG-MEPS-PLUS",
                date(2026, 8, 10),
                False,
            ),
        ),
        "UK-GBP": (
            _business_fixture(
                "uk-boxing-day-substitute-2026",
                "UK-GBP",
                "GB-STERLING-SETTLEMENT",
                date(2026, 12, 28),
                False,
            ),
            _clock_fixture(
                "boe-sonia-christmas-roll-2026",
                "UK-GBP",
                "GB-STERLING-SETTLEMENT",
                "boe_sonia",
                date(2026, 12, 24),
                datetime(2026, 12, 29, 9, tzinfo=UTC),
            ),
        ),
        "US-USD": (
            _business_fixture(
                "us-independence-day-observed-2026",
                "US-USD",
                "US-FEDWIRE",
                date(2026, 7, 3),
                False,
            ),
        ),
    }
)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def assess_calendar_and_timezone(
    pack: MarketPack,
    *,
    as_of: datetime,
    fixtures: Iterable[CalendarFixture] | None = None,
) -> dict[str, object]:
    """Assess one pack's calendar and publication-clock declarations.

    ``as_of`` controls the two-year availability horizon.  Supplying
    ``fixtures`` is useful for testing or an independently reviewed fixture
    release; omitting it selects this module's fixture set for ``pack``.
    """

    failed: set[str] = set()
    pending: set[str] = set()
    details: set[str] = set()
    unavailable_years: set[tuple[str, int]] = set()
    failed_fixture_ids: set[str] = set()

    try:
        pack_timezone = ZoneInfo(pack.local_timezone)
    except ZoneInfoNotFoundError:
        # MarketPack normally prevents this; retain a total result for data
        # loaded from less trusted serialized pack definitions.
        failed.add("PACK_TIMEZONE_UNKNOWN")
        pack_timezone = None

    if not _is_aware(as_of):
        failed.add("AS_OF_TIMESTAMP_NAIVE")
        current_year = as_of.year
    elif pack_timezone is None:
        current_year = as_of.astimezone(UTC).year
    else:
        current_year = as_of.astimezone(pack_timezone).year
    required_years = (current_year, current_year + 1)

    selected_fixtures = tuple(
        REPRESENTATIVE_FIXTURES.get(pack.market_id, ())
        if fixtures is None
        else fixtures
    )
    if not selected_fixtures:
        failed.add("REPRESENTATIVE_FIXTURES_MISSING")

    calendar_roles = (
        ("holiday", pack.holiday_calendar),
        ("settlement", pack.settlement_calendar),
    )
    year_checks_available = 0
    year_checks_attempted = 0

    for role, calendar in calendar_roles:
        if calendar.valid_from_year is None or calendar.valid_to_year is None:
            failed.add("CALENDAR_VALIDITY_UNBOUNDED")
            details.add(f"{role}:{calendar.calendar_id}:unbounded")
        if not calendar.source_uri:
            failed.add("CALENDAR_SOURCE_MISSING")
            details.add(f"{role}:{calendar.calendar_id}:source")
        if calendar.holiday_provider is None:
            failed.add("CALENDAR_HOLIDAY_PROVIDER_MISSING")
            details.add(f"{role}:{calendar.calendar_id}:provider")
        if pack_timezone is not None and calendar.timezone != pack_timezone:
            failed.add("CALENDAR_TIMEZONE_MISMATCH")
            details.add(f"{role}:{calendar.calendar_id}:{calendar.timezone_name}")

        for year_index, year in enumerate(required_years):
            year_checks_attempted += 1
            try:
                holidays = calendar.holidays(year)
                working_days = calendar.working_days(year)
            except CalendarUnavailableError:
                unavailable_years.add((role, year))
                if year_index == 0:
                    failed.add("CURRENT_YEAR_CALENDAR_UNAVAILABLE")
                else:
                    pending.add("NEXT_YEAR_CALENDAR_UNAVAILABLE")
                continue
            except Exception as exc:  # provider failures are validation output
                failed.add("CALENDAR_PROVIDER_ERROR")
                details.add(
                    f"{role}:{calendar.calendar_id}:{year}:{type(exc).__name__}"
                )
                continue

            year_checks_available += 1
            if any(type(day) is not date or day.year != year for day in holidays):
                failed.add("HOLIDAY_PROVIDER_YEAR_MISMATCH")
                details.add(f"{role}:{calendar.calendar_id}:{year}:holidays")
            if any(type(day) is not date or day.year != year for day in working_days):
                failed.add("WORKING_DAY_PROVIDER_YEAR_MISMATCH")
                details.add(f"{role}:{calendar.calendar_id}:{year}:working-days")

    publication_clocks_checked = 0
    resolvable_publication_clocks_checked = 0
    timezone_aware_resolutions = 0
    for adapter_id in sorted(pack.adapter_map):
        publication_clocks_checked += 1
        clock = pack.adapter_map[adapter_id].publication_clock
        try:
            clock_timezone = ZoneInfo(clock.timezone_name)
        except ZoneInfoNotFoundError:
            failed.add("PUBLICATION_CLOCK_TIMEZONE_UNKNOWN")
            details.add(f"{adapter_id}:{clock.timezone_name}")
            continue
        if pack_timezone is not None and clock_timezone != pack_timezone:
            failed.add("PUBLICATION_CLOCK_TIMEZONE_MISMATCH")
            details.add(f"{adapter_id}:{clock.timezone_name}")
        if clock.calendar_id != pack.settlement_calendar.calendar_id:
            failed.add("PUBLICATION_CLOCK_CALENDAR_MISMATCH")
            details.add(f"{adapter_id}:{clock.calendar_id}")
        if clock.local_time is None:
            continue
        try:
            resolved = clock.resolve(date(current_year, 6, 15), pack.settlement_calendar)
        except (CalendarUnavailableError, ValueError) as exc:
            failed.add("PUBLICATION_CLOCK_RESOLUTION_ERROR")
            details.add(f"{adapter_id}:{type(exc).__name__}")
            continue
        resolvable_publication_clocks_checked += 1
        if _is_aware(resolved) and resolved.utcoffset() == UTC.utcoffset(resolved):
            timezone_aware_resolutions += 1
        else:
            failed.add("PUBLICATION_TIMESTAMP_NOT_UTC_AWARE")
            details.add(adapter_id)

    calendar_day_fixtures_checked = 0
    publication_clock_fixtures_checked = 0
    fixture_ids: set[str] = set()
    for fixture in selected_fixtures:
        if fixture.fixture_id in fixture_ids:
            failed.add("FIXTURE_ID_DUPLICATE")
            failed_fixture_ids.add(fixture.fixture_id)
        fixture_ids.add(fixture.fixture_id)
        if fixture.fixture_version != FIXTURE_SET_VERSION:
            failed.add("FIXTURE_VERSION_UNSUPPORTED")
            failed_fixture_ids.add(fixture.fixture_id)
        if fixture.market_id != pack.market_id:
            failed.add("FIXTURE_MARKET_MISMATCH")
            failed_fixture_ids.add(fixture.fixture_id)
        if fixture.calendar_id != pack.settlement_calendar.calendar_id:
            failed.add("FIXTURE_CALENDAR_MISMATCH")
            failed_fixture_ids.add(fixture.fixture_id)

        if isinstance(fixture, BusinessDayFixture):
            calendar_day_fixtures_checked += 1
            try:
                actual = pack.settlement_calendar.is_business_day(fixture.day)
            except CalendarUnavailableError:
                failed.add("FIXTURE_CALENDAR_UNAVAILABLE")
                failed_fixture_ids.add(fixture.fixture_id)
                continue
            except Exception as exc:
                failed.add("FIXTURE_CALENDAR_ERROR")
                failed_fixture_ids.add(fixture.fixture_id)
                details.add(f"{fixture.fixture_id}:{type(exc).__name__}")
                continue
            if actual is not fixture.expected_business_day:
                failed.add("BUSINESS_DAY_FIXTURE_MISMATCH")
                failed_fixture_ids.add(fixture.fixture_id)
            continue

        publication_clock_fixtures_checked += 1
        if not _is_aware(fixture.expected_publication_time):
            failed.add("PUBLICATION_FIXTURE_EXPECTED_TIMESTAMP_NAIVE")
            failed_fixture_ids.add(fixture.fixture_id)
            continue
        adapter = pack.adapter_map.get(fixture.adapter_id)
        if adapter is None:
            failed.add("PUBLICATION_FIXTURE_ADAPTER_UNKNOWN")
            failed_fixture_ids.add(fixture.fixture_id)
            continue
        try:
            actual_publication = adapter.publication_clock.resolve(
                fixture.event_day,
                pack.settlement_calendar,
            )
        except (
            CalendarUnavailableError,
            PublicationClockUnavailableError,
            ValueError,
        ) as exc:
            failed.add("PUBLICATION_FIXTURE_RESOLUTION_ERROR")
            failed_fixture_ids.add(fixture.fixture_id)
            details.add(f"{fixture.fixture_id}:{type(exc).__name__}")
            continue
        if not _is_aware(actual_publication):
            failed.add("PUBLICATION_TIMESTAMP_NOT_UTC_AWARE")
            failed_fixture_ids.add(fixture.fixture_id)
        elif actual_publication != fixture.expected_publication_time.astimezone(UTC):
            failed.add("PUBLICATION_CLOCK_FIXTURE_MISMATCH")
            failed_fixture_ids.add(fixture.fixture_id)

    if failed:
        status = "FAIL"
    elif pending:
        status = "PENDING"
    else:
        status = "PASS"

    metrics: dict[str, object] = {
        "fixture_set_version": FIXTURE_SET_VERSION,
        "as_of_year": current_year,
        "required_years": list(required_years),
        "calendar_roles_checked": len(calendar_roles),
        "year_checks_attempted": year_checks_attempted,
        "year_checks_available": year_checks_available,
        "publication_clocks_checked": publication_clocks_checked,
        "resolvable_publication_clocks_checked": resolvable_publication_clocks_checked,
        "timezone_aware_resolutions": timezone_aware_resolutions,
        "calendar_day_fixtures_checked": calendar_day_fixtures_checked,
        "publication_clock_fixtures_checked": publication_clock_fixtures_checked,
        "unavailable_years": [
            {"calendar_role": role, "year": year}
            for role, year in sorted(unavailable_years)
        ],
        "failed_fixture_ids": sorted(failed_fixture_ids),
        "assertion_details": sorted(details),
    }
    return {
        "status": status,
        "metrics": metrics,
        "reasons": sorted(failed | pending),
    }


__all__ = [
    "BusinessDayFixture",
    "CalendarFixture",
    "FIXTURE_SET_VERSION",
    "PublicationClockFixture",
    "REPRESENTATIVE_FIXTURES",
    "assess_calendar_and_timezone",
]
