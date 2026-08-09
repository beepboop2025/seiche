from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from seiche import config
from seiche.markets.base import (
    CalendarUnavailableError,
    PackSupportStatus,
    ValidationCheck,
    ValidationOutcome,
    ValidationResult,
)
from seiche.markets.registry import default_registry
from seiche.markets.us_usd import legacy as us_legacy


def test_reference_registry_is_monetary_area_aware() -> None:
    registry = default_registry()
    assert {pack.market_id for pack in registry.list()} == {
        "US-USD",
        "EA-EUR",
        "UK-GBP",
        "JP-JPY",
        "CN-CNY",
        "HK-HKD",
        "IN-INR",
        "AU-AUD",
        "NZ-NZD",
        "SG-SGD",
    }
    euro = registry.get("EA-EUR")
    assert euro.monetary_area_id == "EA"
    assert len(euro.jurisdiction_codes) == 21
    assert "BG" in euro.jurisdiction_codes
    assert euro.support_status is PackSupportStatus.REFERENCE
    assert registry.get("CN-CNY").monetary_area_id != registry.get("HK-HKD").monetary_area_id


def test_pack_calendars_replace_generic_monday_friday() -> None:
    registry = default_registry()
    assert not registry.get("US-USD").settlement_calendar.is_business_day(date(2026, 7, 3))
    assert not registry.get("EA-EUR").settlement_calendar.is_business_day(date(2026, 4, 3))
    assert not registry.get("UK-GBP").settlement_calendar.is_business_day(date(2026, 12, 28))
    assert not registry.get("JP-JPY").settlement_calendar.is_business_day(date(2026, 9, 22))

    assert not registry.get("IN-INR").settlement_calendar.is_business_day(
        date(2026, 1, 26)
    )
    # Mainland China publishes working-weekend overrides; a generic weekday
    # calendar cannot represent this Saturday settlement day.
    assert registry.get("CN-CNY").settlement_calendar.is_business_day(
        date(2026, 2, 14)
    )
    with pytest.raises(CalendarUnavailableError):
        registry.get("CN-CNY").settlement_calendar.is_business_day(
            date(2027, 1, 4)
        )


def test_publication_clocks_roll_over_local_holidays() -> None:
    registry = default_registry()
    euro = registry.get("EA-EUR")
    euro_clock = euro.adapter_map["ecb_benchmark"].publication_clock
    sterling = registry.get("UK-GBP")
    sterling_clock = sterling.adapter_map["boe_sonia"].publication_clock

    assert euro_clock.resolve(date(2026, 4, 2), euro.settlement_calendar) == datetime(
        2026, 4, 7, 6, tzinfo=UTC
    )
    assert sterling_clock.resolve(
        date(2026, 12, 24), sterling.settlement_calendar
    ) == datetime(2026, 12, 29, 9, tzinfo=UTC)


def test_us_legacy_config_aliases_preserve_v1_series_contract() -> None:
    assert config.FRED_SERIES is us_legacy.FRED_SERIES
    assert config.MARKET_SERIES is us_legacy.MARKET_SERIES
    assert [spec.mnemonic for spec in config.FRED_SERIES] == [
        "WALCL",
        "WRESBAL",
        "WTREGEN",
        "RRPONTSYD",
        "IORB",
        "SRF_CEILING",
        "WCURCIR",
        "IOER",
        "EFFR",
        "SOFR",
        "GDP",
        "DISCOUNT_WINDOW",
    ]


def test_pack_imports_are_declarative_not_collector_imports() -> None:
    markets_dir = Path(__file__).parents[1] / "seiche" / "markets"
    for path in markets_dir.rglob("*.py"):
        source = path.read_text()
        assert "from seiche.sources" not in source
        assert "import seiche.sources" not in source


def test_universal_kernel_contains_no_local_vocabulary() -> None:
    kernel_dir = Path(__file__).parents[1] / "seiche" / "kernel"
    source = "\n".join(path.read_text() for path in kernel_dir.rglob("*.py"))
    for forbidden in ("SOFR", "IORB", "RBI", "ECB", "United States", "India", "Japan", "China"):
        assert forbidden not in source


def test_pack_cannot_claim_supported_without_every_validation() -> None:
    pack = default_registry().get("US-USD")
    with pytest.raises(ValueError, match="lacks passing validations"):
        replace(pack, support_status=PackSupportStatus.SUPPORTED)


def test_validation_results_require_content_addressed_evidence() -> None:
    with pytest.raises(ValueError, match="content-addressed"):
        ValidationResult(
            check=ValidationCheck.SCHEMA_AND_UNITS,
            outcome=ValidationOutcome.PASS,
            evidence="validation-report.json",
        )

    result = ValidationResult(
        check=ValidationCheck.SCHEMA_AND_UNITS,
        outcome=ValidationOutcome.PASS,
        evidence=f"sha256:{'a' * 64}",
    )
    assert result.evidence == f"sha256:{'a' * 64}"
