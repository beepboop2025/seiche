"""Provenance-carrying series envelope shared by all collectors.

Principle: no naked numbers. Every series that leaves this layer knows where
it came from, when it was observed, when it was fetched, and how stale it is
relative to its own expected cadence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Protocol

import pandas as pd

from seiche.config import STALENESS_GRACE_DAYS
from seiche.domain.observation import Observation, evidence_sha256


@dataclass
class Series:
    mnemonic: str
    source: str
    remote_id: str
    label: str
    unit: str
    freq: str
    fetched_at: str                      # ISO UTC
    points: pd.Series                    # DatetimeIndex -> float

    @property
    def asof(self) -> str | None:
        if self.points.empty:
            return None
        return self.points.index[-1].date().isoformat()

    @property
    def staleness(self) -> str:
        """fresh | aging | stale | dead — measured against expected cadence."""
        if self.points.empty:
            return "dead"
        grace = STALENESS_GRACE_DAYS.get(self.freq, 7)
        age = (datetime.now(timezone.utc).date() - self.points.index[-1].date()).days
        if age <= grace:
            return "fresh"
        if age <= grace * 2:
            return "aging"
        if age <= grace * 6:
            return "stale"
        return "dead"

    def provenance(self) -> dict:
        grace = STALENESS_GRACE_DAYS.get(self.freq, 7)
        age_days = None
        if self.asof is not None:
            age_days = max(
                0,
                (datetime.now(timezone.utc).date() - self.points.index[-1].date()).days,
            )
        return {
            "mnemonic": self.mnemonic,
            "source": self.source,
            "remote_id": self.remote_id,
            "label": self.label,
            "unit": self.unit,
            "freq": self.freq,
            "asof": self.asof,
            "fetched_at": self.fetched_at,
            "staleness": self.staleness,
            "age_days": age_days,
            "freshness_grace_days": grace,
            "freshness_basis": "age of latest observation versus this series' native publication cadence",
            "n_obs": int(len(self.points)),
        }

    def tail_records(self, n: int = 500) -> list[list]:
        pts = self.points.dropna().tail(n)
        return [[idx.date().isoformat(), round(float(v), 6)] for idx, v in pts.items()]


class SourceFault(Exception):
    """Raised when an upstream fails; carried into API output fail-loud."""

    def __init__(self, source: str, detail: str):
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


class SourcePolicyUnavailableError(RuntimeError):
    """A source request was deliberately withheld by an access policy."""


@dataclass(frozen=True, slots=True)
class RawCapture:
    """Exact immutable response bytes retained before parsing."""

    market_id: str
    adapter_id: str
    captured_at: datetime
    source_uri: str
    media_type: str
    payload: bytes
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        object.__setattr__(self, "market_id", self.market_id.upper())
        object.__setattr__(self, "captured_at", self.captured_at.astimezone(UTC))
        if evidence_sha256(self.payload) != self.evidence_hash:
            raise ValueError("raw capture evidence_hash does not match payload bytes")


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    """One collector result; row clocks remain on the observations themselves."""

    market_id: str
    adapter_id: str
    captured_at: datetime
    observations: tuple[Observation, ...]
    raw_capture: RawCapture | None = None

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        market_id = self.market_id.upper()
        captured_at = self.captured_at.astimezone(UTC).replace(microsecond=0)
        object.__setattr__(self, "market_id", market_id)
        object.__setattr__(self, "captured_at", captured_at)
        if any(item.market_id != market_id for item in self.observations):
            raise ValueError("an observation batch cannot mix markets")
        if any(item.knowledge_time > captured_at for item in self.observations):
            raise ValueError("observation knowledge_time cannot follow batch capture time")
        if self.raw_capture is not None and (
            self.raw_capture.market_id != market_id
            or self.raw_capture.adapter_id != self.adapter_id
        ):
            raise ValueError("raw capture scope must match its observation batch")


class CanonicalSourceAdapter(Protocol):
    """I/O adapter contract used by the independent collector supervisor."""

    market_id: str
    adapter_id: str

    async def collect(self) -> ObservationBatch: ...


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
