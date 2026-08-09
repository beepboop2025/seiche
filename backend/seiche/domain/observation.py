"""Canonical, market-neutral observation contract.

Collectors may speak CSV, SDMX, vendor schemas, or tenant-specific formats.
Everything crossing into Seiche's observation store must first become an
``Observation``.  In particular, ``knowledge_time`` belongs to each row; it
is not a batch-level retrieval timestamp retroactively applied to history.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping


class SemanticRole(StrEnum):
    POLICY_FLOOR = "POLICY_FLOOR"
    POLICY_TARGET = "POLICY_TARGET"
    POLICY_CEILING = "POLICY_CEILING"
    UNSECURED_OVERNIGHT = "UNSECURED_OVERNIGHT"
    SECURED_OVERNIGHT = "SECURED_OVERNIGHT"
    TERM_1W = "TERM_1W"
    TERM_1M = "TERM_1M"
    TERM_3M = "TERM_3M"
    TBILL_3M = "TBILL_3M"
    CP_3M = "CP_3M"
    CD_3M = "CD_3M"
    RESERVE_BALANCES = "RESERVE_BALANCES"
    SYSTEM_LIQUIDITY = "SYSTEM_LIQUIDITY"
    CENTRAL_BANK_FACILITY_RATE = "CENTRAL_BANK_FACILITY_RATE"
    CENTRAL_BANK_FACILITY_TAKEUP = "CENTRAL_BANK_FACILITY_TAKEUP"
    GOVERNMENT_CASH_BALANCE = "GOVERNMENT_CASH_BALANCE"
    REPO_VOLUME = "REPO_VOLUME"
    RATE_MEDIAN = "RATE_MEDIAN"
    RATE_P99 = "RATE_P99"
    FX_SWAP_BASIS = "FX_SWAP_BASIS"
    COLLATERAL_HAIRCUT = "COLLATERAL_HAIRCUT"


class CanonicalUnit(StrEnum):
    """Small unit vocabulary understood by universal engines.

    Rate adapters normalize to basis points. Monetary stocks and volumes are
    expressed in millions of the observation's ``currency`` so a raw number
    can never be mistaken for a cross-currency comparable quantity.
    """

    BASIS_POINTS = "basis_points"
    LOCAL_CURRENCY_MILLIONS = "local_currency_millions"
    RATIO = "ratio"
    INDEX_POINTS = "index_points"
    COUNT = "count"
    CONTRACTS = "contracts"


class RateCompounding(StrEnum):
    SIMPLE = "simple"
    COMPOUNDED = "compounded"


class DayCountConvention(StrEnum):
    ACT_360 = "ACT/360"
    ACT_365 = "ACT/365"


class ConnectorClassification(StrEnum):
    OFFICIAL_OPEN = "official_open"
    LICENSED = "licensed"
    TENANT_PROVIDED = "tenant_provided"
    MANUAL_SIGNED_EVIDENCE = "manual_signed_evidence"
    UNAVAILABLE = "unavailable"


class RedistributionStatus(StrEnum):
    ALLOWED = "allowed"
    DERIVED_ONLY = "derived_only"
    METADATA_ONLY = "metadata_only"
    PROHIBITED = "prohibited"


class QualityState(StrEnum):
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    REVISED = "revised"
    ESTIMATED = "estimated"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class StalenessState(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    DEAD = "dead"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


RATE_ROLES = frozenset(
    {
        SemanticRole.POLICY_FLOOR,
        SemanticRole.POLICY_TARGET,
        SemanticRole.POLICY_CEILING,
        SemanticRole.UNSECURED_OVERNIGHT,
        SemanticRole.SECURED_OVERNIGHT,
        SemanticRole.TERM_1W,
        SemanticRole.TERM_1M,
        SemanticRole.TERM_3M,
        SemanticRole.TBILL_3M,
        SemanticRole.CP_3M,
        SemanticRole.CD_3M,
        SemanticRole.CENTRAL_BANK_FACILITY_RATE,
        SemanticRole.RATE_MEDIAN,
        SemanticRole.RATE_P99,
        SemanticRole.FX_SWAP_BASIS,
        SemanticRole.COLLATERAL_HAIRCUT,
    }
)

_MARKET_ID_RE = re.compile(r"^[A-Z0-9]+-[A-Z]{3}$")
_AREA_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
_JURISDICTION_RE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    # SQLite stores the canonical ISO form lexically; second precision keeps
    # ordering stable across adapters that expose different subsecond detail.
    return value.astimezone(UTC).replace(microsecond=0)


def _decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("value must be finite")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError("value must be finite")
    return parsed


def evidence_sha256(payload: bytes | str) -> str:
    """Hash the exact evidence bytes before parsing or unit conversion."""

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class Observation:
    market_id: str
    monetary_area_id: str
    jurisdiction_codes: tuple[str, ...]
    currency: str
    instrument_id: str
    semantic_role: SemanticRole
    value: Decimal | int | float | str | None
    canonical_unit: CanonicalUnit
    rate_compounding: RateCompounding | None
    day_count: DayCountConvention | None
    event_time: datetime
    knowledge_time: datetime
    source_publication_time: datetime
    revision_id: str
    source: str
    evidence_hash: str
    connector_classification: ConnectorClassification
    redistribution_status: RedistributionStatus
    quality: QualityState
    staleness: StalenessState

    def __post_init__(self) -> None:
        market_id = self.market_id.upper()
        area_id = self.monetary_area_id.upper()
        currency = self.currency.upper()
        jurisdictions = tuple(dict.fromkeys(code.upper() for code in self.jurisdiction_codes))
        object.__setattr__(self, "market_id", market_id)
        object.__setattr__(self, "monetary_area_id", area_id)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "jurisdiction_codes", jurisdictions)
        object.__setattr__(self, "value", _decimal(self.value))
        object.__setattr__(self, "event_time", _aware_utc(self.event_time, "event_time"))
        object.__setattr__(
            self,
            "knowledge_time",
            _aware_utc(self.knowledge_time, "knowledge_time"),
        )
        object.__setattr__(
            self,
            "source_publication_time",
            _aware_utc(self.source_publication_time, "source_publication_time"),
        )

        if not _MARKET_ID_RE.fullmatch(market_id):
            raise ValueError("market_id must look like 'US-USD' or 'EA-EUR'")
        if not _AREA_ID_RE.fullmatch(area_id):
            raise ValueError("monetary_area_id must be a stable uppercase identifier")
        if not jurisdictions or any(
            not _JURISDICTION_RE.fullmatch(code) for code in jurisdictions
        ):
            raise ValueError("jurisdiction_codes must contain ISO 3166-1 alpha-2 codes")
        if not _CURRENCY_RE.fullmatch(currency):
            raise ValueError("currency must be an ISO 4217 alpha-3 code")
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if not self.revision_id.strip():
            raise ValueError("revision_id is required")
        if not self.source.strip():
            raise ValueError("source is required")
        if not _SHA256_RE.fullmatch(self.evidence_hash):
            raise ValueError("evidence_hash must be a lowercase SHA-256 hex digest")
        if self.knowledge_time < self.source_publication_time:
            raise ValueError("knowledge_time cannot precede source_publication_time")

        is_unavailable = self.quality is QualityState.UNAVAILABLE
        if is_unavailable != (self.value is None):
            raise ValueError("only UNAVAILABLE observations may have a null value")
        if is_unavailable and self.staleness is not StalenessState.UNAVAILABLE:
            raise ValueError("an UNAVAILABLE observation needs UNAVAILABLE staleness")

        rate_fields = (self.rate_compounding, self.day_count)
        if (rate_fields[0] is None) != (rate_fields[1] is None):
            raise ValueError("rate_compounding and day_count must be supplied together")
        if self.semantic_role in RATE_ROLES and not is_unavailable:
            if None in rate_fields:
                raise ValueError("rate observations require compounding and day-count conventions")
            if self.canonical_unit is not CanonicalUnit.BASIS_POINTS:
                raise ValueError("rate observations must be normalized to basis points")
        elif self.semantic_role not in RATE_ROLES and any(v is not None for v in rate_fields):
            raise ValueError("non-rate observations cannot carry rate conventions")

        if (
            self.connector_classification is ConnectorClassification.UNAVAILABLE
            and not is_unavailable
        ):
            raise ValueError("an unavailable connector cannot produce a numeric observation")
        if (
            self.connector_classification is ConnectorClassification.LICENSED
            and self.redistribution_status is RedistributionStatus.ALLOWED
        ):
            raise ValueError("licensed inputs cannot default to unrestricted redistribution")

    @property
    def identity(self) -> tuple[str, str, datetime, datetime, str, str]:
        """Immutable row identity; revisions never overwrite one another."""

        return (
            self.market_id,
            self.instrument_id,
            self.event_time,
            self.knowledge_time,
            self.source,
            self.revision_id,
        )

    @property
    def usable(self) -> bool:
        return (
            self.value is not None
            and self.quality not in {QualityState.REJECTED, QualityState.UNAVAILABLE}
            and self.staleness not in {StalenessState.DEAD, StalenessState.UNAVAILABLE}
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "monetary_area_id": self.monetary_area_id,
            "jurisdiction_codes": list(self.jurisdiction_codes),
            "currency": self.currency,
            "instrument_id": self.instrument_id,
            "semantic_role": self.semantic_role.value,
            "value": str(self.value) if self.value is not None else None,
            "canonical_unit": self.canonical_unit.value,
            "rate_compounding": self.rate_compounding.value if self.rate_compounding else None,
            "day_count": self.day_count.value if self.day_count else None,
            "event_time": self.event_time.isoformat(),
            "knowledge_time": self.knowledge_time.isoformat(),
            "source_publication_time": self.source_publication_time.isoformat(),
            "revision_id": self.revision_id,
            "source": self.source,
            "evidence_hash": self.evidence_hash,
            "connector_classification": self.connector_classification.value,
            "redistribution_status": self.redistribution_status.value,
            "quality": self.quality.value,
            "staleness": self.staleness.value,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Observation:
        def timestamp(name: str) -> datetime:
            value = record[name]
            return value if isinstance(value, datetime) else datetime.fromisoformat(value)

        jurisdictions = record["jurisdiction_codes"]
        if isinstance(jurisdictions, str):
            jurisdictions = tuple(code for code in jurisdictions.split(",") if code)
        return cls(
            market_id=record["market_id"],
            monetary_area_id=record["monetary_area_id"],
            jurisdiction_codes=tuple(jurisdictions),
            currency=record["currency"],
            instrument_id=record["instrument_id"],
            semantic_role=SemanticRole(record["semantic_role"]),
            value=record["value"],
            canonical_unit=CanonicalUnit(record["canonical_unit"]),
            rate_compounding=(
                RateCompounding(record["rate_compounding"])
                if record.get("rate_compounding")
                else None
            ),
            day_count=(DayCountConvention(record["day_count"]) if record.get("day_count") else None),
            event_time=timestamp("event_time"),
            knowledge_time=timestamp("knowledge_time"),
            source_publication_time=timestamp("source_publication_time"),
            revision_id=record["revision_id"],
            source=record["source"],
            evidence_hash=record["evidence_hash"],
            connector_classification=ConnectorClassification(record["connector_classification"]),
            redistribution_status=RedistributionStatus(record["redistribution_status"]),
            quality=QualityState(record["quality"]),
            staleness=StalenessState(record["staleness"]),
        )
