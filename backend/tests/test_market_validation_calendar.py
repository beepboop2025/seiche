from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from seiche.markets.registry import default_registry
from seiche.markets.validation_calendar import (
    REPRESENTATIVE_FIXTURES,
    BusinessDayFixture,
    PublicationClockFixture,
    assess_calendar_and_timezone,
)


AS_OF = datetime(2026, 8, 9, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "market_id",
    [
        "AU-AUD",
        "EA-EUR",
        "HK-HKD",
        "IN-INR",
        "JP-JPY",
        "NZ-NZD",
        "SG-SGD",
        "UK-GBP",
        "US-USD",
    ],
)
def test_built_in_pack_calendar_gate_passes_when_horizon_is_available(
    market_id: str,
) -> None:
    result = assess_calendar_and_timezone(
        default_registry().get(market_id),
        as_of=AS_OF,
    )

    assert result["status"] == "PASS"
    assert result["reasons"] == []
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["required_years"] == [2026, 2027]
    assert metrics["year_checks_available"] == 4
    assert metrics["failed_fixture_ids"] == []


def test_china_is_pending_when_next_official_calendar_is_not_published() -> None:
    result = assess_calendar_and_timezone(
        default_registry().get("CN-CNY"),
        as_of=AS_OF,
    )

    assert result["status"] == "PENDING"
    assert result["reasons"] == ["NEXT_YEAR_CALENDAR_UNAVAILABLE"]
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["year_checks_available"] == 2
    assert metrics["unavailable_years"] == [
        {"calendar_role": "holiday", "year": 2027},
        {"calendar_role": "settlement", "year": 2027},
    ]
    assert metrics["calendar_day_fixtures_checked"] == 2


def test_known_china_working_weekend_and_ecb_boe_clocks_are_versioned() -> None:
    china = REPRESENTATIVE_FIXTURES["CN-CNY"]
    assert any(
        isinstance(item, BusinessDayFixture)
        and item.fixture_id == "cn-spring-festival-working-weekend-2026"
        and item.expected_business_day
        for item in china
    )

    for market_id, adapter_id in (
        ("EA-EUR", "ecb_benchmark"),
        ("UK-GBP", "boe_sonia"),
    ):
        fixture = next(
            item
            for item in REPRESENTATIVE_FIXTURES[market_id]
            if isinstance(item, PublicationClockFixture)
        )
        assert fixture.adapter_id == adapter_id
        assert fixture.fixture_version == "market-calendar-2026-v1"


def test_malformed_fixture_is_a_deterministic_failure() -> None:
    pack = default_registry().get("US-USD")
    fixture = REPRESENTATIVE_FIXTURES[pack.market_id][0]
    malformed = replace(fixture, calendar_id="NOT-THE-PACK-CALENDAR")

    result = assess_calendar_and_timezone(
        pack,
        as_of=AS_OF,
        fixtures=(malformed,),
    )

    assert result["status"] == "FAIL"
    assert result["reasons"] == ["FIXTURE_CALENDAR_MISMATCH"]
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["failed_fixture_ids"] == [fixture.fixture_id]


def test_naive_validation_cutoff_fails_closed() -> None:
    result = assess_calendar_and_timezone(
        default_registry().get("US-USD"),
        as_of=datetime(2026, 8, 9, 12),
    )

    assert result["status"] == "FAIL"
    assert result["reasons"] == ["AS_OF_TIMESTAMP_NAIVE"]


def test_unknown_serialized_pack_timezone_returns_a_failure_not_an_exception() -> None:
    pack = copy.copy(default_registry().get("US-USD"))
    object.__setattr__(pack, "local_timezone", "Invalid/Serialized_Zone")

    result = assess_calendar_and_timezone(pack, as_of=AS_OF)

    assert result["status"] == "FAIL"
    assert result["reasons"] == ["PACK_TIMEZONE_UNKNOWN"]
