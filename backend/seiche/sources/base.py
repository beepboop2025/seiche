"""Provenance-carrying series envelope shared by all collectors.

Principle: no naked numbers. Every series that leaves this layer knows where
it came from, when it was observed, when it was fetched, and how stale it is
relative to its own expected cadence.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

import pandas as pd

from seiche.config import STALENESS_GRACE_DAYS


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


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
