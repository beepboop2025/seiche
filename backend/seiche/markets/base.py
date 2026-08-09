"""Types shared by monetary-area packs.

Packs are data: they describe instruments, clocks, calendars, licensing and
engine bindings. They do not fetch data and therefore cannot make another
market unavailable merely by being imported or registered.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    RATE_ROLES,
    RateCompounding,
    RedistributionStatus,
    SemanticRole,
)


class PolicyRegime(StrEnum):
    FLOOR = "floor"
    CORRIDOR = "corridor"
    TIERED = "tiered"
    QUANTITY_TARGETING = "quantity_targeting"
    EXCHANGE_RATE_TARGETING = "exchange_rate_targeting"
    CURRENCY_BOARD = "currency_board"


class CapabilityStatus(StrEnum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    FORWARD_ONLY = "FORWARD_ONLY"


class PackSupportStatus(StrEnum):
    REFERENCE = "REFERENCE"
    VALIDATING = "VALIDATING"
    SUPPORTED = "SUPPORTED"


class ValidationCheck(StrEnum):
    SCHEMA_AND_UNITS = "schema_and_units"
    CALENDAR_AND_TIMEZONE = "calendar_and_timezone"
    TRUNCATION_INVARIANCE = "truncation_invariance"
    EXTRA_REPORTING_LAG = "extra_reporting_lag"
    REVISION_VINTAGE_LEAKAGE = "revision_vintage_leakage"
    LABEL_SHUFFLE = "label_shuffle"
    MISSING_SOURCE_FAILURE_INJECTION = "missing_source_failure_injection"
    LOCAL_TEMPORAL_HOLDOUT = "local_temporal_holdout"
    LEAVE_ONE_MARKET_OUT = "leave_one_market_out"
    FORWARD_PAPER_RECORD = "forward_paper_record"
    US_OUTPUT_PARITY = "us_output_parity"


class ValidationOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


REQUIRED_VALIDATION_CHECKS = frozenset(ValidationCheck)


class PublicationClockPrecision(StrEnum):
    EXACT = "exact"
    SCHEDULED = "scheduled"
    UPSTREAM_NATIVE = "upstream_native"
    UNKNOWN = "unknown"


class PublicationClockUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceSeriesSpec:
    """Compatibility shape for legacy collectors during pack extraction."""

    mnemonic: str
    source: str
    remote_id: str
    label: str
    unit: str
    freq: str = "D"
    ttl_minutes: int = 360
    start: str = "2017-01-01"


HolidayProvider = Callable[[int], Iterable[date]]


class CalendarUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    calendar_id: str
    timezone_name: str
    weekend_days: frozenset[int] = frozenset({5, 6})
    holiday_provider: HolidayProvider | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {self.timezone_name!r}") from exc
        if not self.calendar_id.strip():
            raise ValueError("calendar_id is required")
        if not self.weekend_days or any(day not in range(7) for day in self.weekend_days):
            raise ValueError("weekend_days must use Python weekday numbers 0..6")

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def holidays(self, year: int) -> frozenset[date]:
        if self.holiday_provider is None:
            raise CalendarUnavailableError(
                f"calendar {self.calendar_id!r} has no validated holiday set for {year}"
            )
        return frozenset(self.holiday_provider(year))

    def is_business_day(self, day: date) -> bool:
        return day.weekday() not in self.weekend_days and day not in self.holidays(day.year)

    def roll_forward(self, day: date) -> date:
        current = day
        while not self.is_business_day(current):
            current += timedelta(days=1)
        return current

    def roll_backward(self, day: date) -> date:
        current = day
        while not self.is_business_day(current):
            current -= timedelta(days=1)
        return current

    def add_business_days(self, day: date, count: int) -> date:
        if count == 0:
            return self.roll_forward(day)
        step = 1 if count > 0 else -1
        remaining = abs(count)
        current = day
        while remaining:
            current += timedelta(days=step)
            if self.is_business_day(current):
                remaining -= 1
        return current


@dataclass(frozen=True, slots=True)
class PublicationClock:
    timezone_name: str
    local_time: time | None
    business_day_lag: int
    precision: PublicationClockPrecision
    calendar_id: str

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {self.timezone_name!r}") from exc
        if self.business_day_lag < 0:
            raise ValueError("business_day_lag cannot be negative")
        if self.precision in {
            PublicationClockPrecision.EXACT,
            PublicationClockPrecision.SCHEDULED,
        } and self.local_time is None:
            raise ValueError("an exact or scheduled publication clock needs local_time")

    def resolve(self, event_day: date, calendar: BusinessCalendar) -> datetime:
        """Resolve a declared schedule; native/unknown clocks must come from evidence."""

        if calendar.calendar_id != self.calendar_id:
            raise ValueError("publication clock and calendar IDs do not match")
        if self.local_time is None:
            raise PublicationClockUnavailableError(
                "adapter must parse source_publication_time from upstream evidence"
            )
        publication_day = calendar.add_business_days(event_day, self.business_day_lag)
        local = datetime.combine(
            publication_day,
            self.local_time,
            tzinfo=ZoneInfo(self.timezone_name),
        )
        return local.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SourceAdapterSpec:
    adapter_id: str
    classification: ConnectorClassification
    expected_cadence: str
    publication_clock: PublicationClock
    redistribution_status: RedistributionStatus
    retry_limit: int = 4
    backoff_seconds: float = 1.5
    circuit_breaker_failures: int = 5
    circuit_breaker_cooldown_seconds: int = 900

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", self.adapter_id):
            raise ValueError("adapter_id must be a lowercase path-safe identifier")
        if not re.fullmatch(r"P(?:T\d+[HMS]|\d+D|\d+W)", self.expected_cadence):
            raise ValueError("expected_cadence must be a simple ISO-8601 duration")
        if self.retry_limit < 0 or self.backoff_seconds < 0:
            raise ValueError("retry settings cannot be negative")
        if self.circuit_breaker_failures < 1:
            raise ValueError("circuit_breaker_failures must be positive")
        if self.circuit_breaker_cooldown_seconds < 1:
            raise ValueError("circuit-breaker cooldown must be positive")
        if (
            self.classification is ConnectorClassification.LICENSED
            and self.redistribution_status is RedistributionStatus.ALLOWED
        ):
            raise ValueError("licensed adapters need a restricted redistribution policy")


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    instrument_id: str
    mnemonic: str
    semantic_role: SemanticRole
    source_adapter_id: str
    source_unit: str
    canonical_unit: CanonicalUnit
    value_multiplier: Decimal | int | float | str = Decimal("1")
    rate_compounding: RateCompounding | None = None
    day_count: DayCountConvention | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not self.mnemonic.strip():
            raise ValueError("instrument_id and mnemonic are required")
        multiplier = (
            self.value_multiplier
            if isinstance(self.value_multiplier, Decimal)
            else Decimal(str(self.value_multiplier))
        )
        if not multiplier.is_finite() or multiplier == 0:
            raise ValueError("value_multiplier must be finite and non-zero")
        object.__setattr__(self, "value_multiplier", multiplier)
        if (self.rate_compounding is None) != (self.day_count is None):
            raise ValueError("rate convention fields must be supplied together")
        if self.semantic_role in RATE_ROLES:
            if self.canonical_unit is not CanonicalUnit.BASIS_POINTS:
                raise ValueError("rate instruments must normalize to basis points")
            if self.rate_compounding is None:
                raise ValueError("rate instruments require a rate convention")
        elif self.rate_compounding is not None:
            raise ValueError("non-rate instruments cannot carry a rate convention")

    def normalize(self, raw_value: Decimal | int | float | str) -> Decimal:
        """Perform the pack-declared unit conversion, with no market logic."""

        value = raw_value if isinstance(raw_value, Decimal) else Decimal(str(raw_value))
        normalized = value * self.value_multiplier
        if not normalized.is_finite():
            raise ValueError("normalized value must be finite")
        return normalized


@dataclass(frozen=True, slots=True)
class ReserveMaintenanceSpec:
    rule_id: str
    description: str
    period_days: int | None
    calendar_id: str

    def __post_init__(self) -> None:
        if self.period_days is not None and self.period_days < 1:
            raise ValueError("period_days must be positive when present")


@dataclass(frozen=True, slots=True)
class EventSpec:
    event_id: str
    label: str
    affected_roles: frozenset[SemanticRole]


@dataclass(frozen=True, slots=True)
class MinimumHistory:
    observations: int
    span_days: int

    def __post_init__(self) -> None:
        if self.observations < 1 or self.span_days < 1:
            raise ValueError("minimum history must be positive")


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    status: CapabilityStatus
    required_roles: frozenset[SemanticRole] = frozenset()
    reason: str | None = None
    minimum_history: MinimumHistory | None = None

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id is required")
        if self.status is CapabilityStatus.READY and not self.required_roles:
            raise ValueError("READY capabilities must declare their required roles")
        if self.status is not CapabilityStatus.READY and not self.reason:
            raise ValueError("non-READY capabilities must explain why")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    check: ValidationCheck
    outcome: ValidationOutcome
    evidence: str

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            raise ValueError("validation results require an evidence reference")


@dataclass(frozen=True, slots=True)
class MarketPack:
    market_id: str
    monetary_area_id: str
    display_name: str
    jurisdiction_codes: tuple[str, ...]
    currency: str
    local_timezone: str
    holiday_calendar: BusinessCalendar
    settlement_calendar: BusinessCalendar
    reserve_maintenance: tuple[ReserveMaintenanceSpec, ...]
    policy_regime: PolicyRegime
    instruments: tuple[InstrumentSpec, ...]
    source_adapters: tuple[SourceAdapterSpec, ...]
    capabilities: tuple[Capability, ...]
    events: tuple[EventSpec, ...]
    calibration_id: str
    minimum_history: MinimumHistory
    support_status: PackSupportStatus = PackSupportStatus.VALIDATING
    validation_results: tuple[ValidationResult, ...] = ()

    def __post_init__(self) -> None:
        market_id = self.market_id.upper()
        area_id = self.monetary_area_id.upper()
        currency = self.currency.upper()
        jurisdictions = tuple(dict.fromkeys(code.upper() for code in self.jurisdiction_codes))
        object.__setattr__(self, "market_id", market_id)
        object.__setattr__(self, "monetary_area_id", area_id)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "jurisdiction_codes", jurisdictions)
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]{3}", market_id):
            raise ValueError("market_id must look like 'US-USD' or 'EA-EUR'")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("currency must be an ISO 4217 alpha-3 code")
        if not jurisdictions or any(not re.fullmatch(r"[A-Z]{2}", c) for c in jurisdictions):
            raise ValueError("jurisdiction_codes must use ISO alpha-2 codes")
        if ZoneInfo(self.local_timezone) != self.settlement_calendar.timezone:
            raise ValueError("settlement calendar timezone must match local_timezone")
        if not self.calibration_id.strip():
            raise ValueError("calibration_id is required")

        instrument_ids = [item.instrument_id for item in self.instruments]
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("instrument_id values must be unique within a pack")
        adapter_ids = [item.adapter_id for item in self.source_adapters]
        if len(adapter_ids) != len(set(adapter_ids)):
            raise ValueError("source adapter IDs must be unique within a pack")
        unknown_adapters = {
            item.source_adapter_id for item in self.instruments
        } - set(adapter_ids)
        if unknown_adapters:
            raise ValueError(f"instruments reference unknown adapters: {sorted(unknown_adapters)}")
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability IDs must be unique within a pack")
        mapped_roles = {item.semantic_role for item in self.instruments}
        for capability in self.capabilities:
            if capability.status is CapabilityStatus.READY:
                missing = capability.required_roles - mapped_roles
                if missing:
                    names = sorted(role.value for role in missing)
                    raise ValueError(
                        f"READY capability {capability.capability_id!r} lacks roles {names}"
                    )
        validation_checks = [item.check for item in self.validation_results]
        if len(validation_checks) != len(set(validation_checks)):
            raise ValueError("validation checks must be unique within a pack")
        if self.support_status is PackSupportStatus.SUPPORTED:
            passed = {
                item.check
                for item in self.validation_results
                if item.outcome is ValidationOutcome.PASS
            }
            missing = REQUIRED_VALIDATION_CHECKS - passed
            if missing:
                names = sorted(item.value for item in missing)
                raise ValueError(
                    f"SUPPORTED pack {self.market_id!r} lacks passing validations: {names}"
                )

    @property
    def instrument_map(self) -> Mapping[str, InstrumentSpec]:
        return MappingProxyType({item.instrument_id: item for item in self.instruments})

    @property
    def capability_map(self) -> Mapping[str, Capability]:
        return MappingProxyType({item.capability_id: item for item in self.capabilities})

    @property
    def adapter_map(self) -> Mapping[str, SourceAdapterSpec]:
        return MappingProxyType({item.adapter_id: item for item in self.source_adapters})

    def instruments_for_role(self, role: SemanticRole) -> tuple[InstrumentSpec, ...]:
        return tuple(item for item in self.instruments if item.semantic_role is role)

    def summary(self) -> dict[str, object]:
        missing = [
            item.capability_id
            for item in self.capabilities
            if item.status is not CapabilityStatus.READY
        ]
        return {
            "market_id": self.market_id,
            "monetary_area_id": self.monetary_area_id,
            "display_name": self.display_name,
            "jurisdiction_codes": list(self.jurisdiction_codes),
            "currency": self.currency,
            "local_timezone": self.local_timezone,
            "policy_regime": self.policy_regime.value,
            "calibration_id": self.calibration_id,
            "support_status": self.support_status.value,
            "holiday_calendar": self.holiday_calendar.calendar_id,
            "settlement_calendar": self.settlement_calendar.calendar_id,
            "calendar_status": (
                "READY"
                if self.settlement_calendar.holiday_provider is not None
                else "UNAVAILABLE"
            ),
            "validation": {
                "passed": sum(
                    item.outcome is ValidationOutcome.PASS
                    for item in self.validation_results
                ),
                "required": len(REQUIRED_VALIDATION_CHECKS),
            },
            "capabilities": {
                item.capability_id: item.status.value for item in self.capabilities
            },
            "missing_capabilities": missing,
        }
