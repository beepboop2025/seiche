"""Signed, content-bound evidence for historical Seiche inputs."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

VINTAGE_SAFE_STATUSES = frozenset(
    {"alfred_vintage", "as_published_capture", "effectively_unrevised_print"}
)


class VintageCutVerificationError(ValueError):
    """The evidence cut is unsigned, altered, unbound, or temporally invalid."""


def _utc(value: datetime | pd.Timestamp, field: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        if field == "event_time":
            timestamp = timestamp.tz_localize("UTC")
        else:
            raise ValueError(f"{field} must be timezone-aware")
    return timestamp.tz_convert("UTC").to_pydatetime()


def _value_token(value: float) -> str:
    number = float(value)
    if np.isnan(number):
        return "nan"
    if not np.isfinite(number):
        raise ValueError("vintage series values must be finite or NaN")
    return number.hex()


@dataclass(frozen=True, slots=True)
class VintageObservation:
    input_name: str
    event_time: datetime
    knowledge_time: datetime
    value: float
    vintage_status: str


def _row_payload(row: VintageObservation) -> dict[str, str]:
    return {
        "input_name": row.input_name,
        "event_time": row.event_time.isoformat().replace("+00:00", "Z"),
        "knowledge_time": row.knowledge_time.isoformat().replace("+00:00", "Z"),
        "value": _value_token(row.value),
        "vintage_status": row.vintage_status,
    }


def _cut_payload(issuer_id: str, as_of: datetime,
                 rows: Sequence[VintageObservation]) -> bytes:
    payload = {
        "schema": "seiche.vintage-data-cut.v1",
        "issuer_id": issuer_id,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "rows": [_row_payload(row) for row in rows],
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class VintageDataCut:
    schema: str
    cut_id: str
    issuer_id: str
    as_of: datetime
    rows: tuple[VintageObservation, ...]
    signature: str


_VERIFIED_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class VerifiedVintageDataCut:
    data_cut: VintageDataCut

    def __init__(self, data_cut: VintageDataCut, token: object) -> None:
        if token is not _VERIFIED_TOKEN:
            raise VintageCutVerificationError(
                "VerifiedVintageDataCut can only be created by VintageEvidenceStore"
            )
        object.__setattr__(self, "data_cut", data_cut)

    @property
    def cut_id(self) -> str:
        return self.data_cut.cut_id

    @property
    def issuer_id(self) -> str:
        return self.data_cut.issuer_id

    @property
    def as_of(self) -> datetime:
        return self.data_cut.as_of

    @property
    def rows(self) -> tuple[VintageObservation, ...]:
        return self.data_cut.rows


class VintageEvidenceStore:
    """Bitemporal loader and signer for exact historical series revisions."""

    def __init__(
        self,
        series: Mapping[str, pd.Series],
        *,
        knowledge_times: Mapping[str, datetime | pd.Series],
        vintage_statuses: Mapping[str, str],
        signing_key: bytes | None = None,
        issuer_id: str | None = None,
    ) -> None:
        key = secrets.token_bytes(32) if signing_key is None else bytes(signing_key)
        if len(key) < 32:
            raise ValueError("signing_key must contain at least 32 bytes")
        self._signing_key = key
        self.issuer_id = issuer_id or f"seiche_pit_{hashlib.sha256(key).hexdigest()[:24]}"
        if not isinstance(self.issuer_id, str) or not self.issuer_id.strip():
            raise ValueError("issuer_id must be a non-blank string")
        names = set(series)
        if not names or names != set(knowledge_times) or names != set(vintage_statuses):
            raise ValueError(
                "series, knowledge_times, and vintage_statuses must have the same non-empty keys"
            )
        self._rows = self._build_rows(series, knowledge_times, vintage_statuses)

    @staticmethod
    def _build_rows(
        series: Mapping[str, pd.Series],
        knowledge_times: Mapping[str, datetime | pd.Series],
        statuses: Mapping[str, str],
    ) -> tuple[VintageObservation, ...]:
        rows: list[VintageObservation] = []
        for name in sorted(series):
            values = series[name]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("vintage input names must be non-blank strings")
            if not isinstance(values, pd.Series) or values.empty:
                raise ValueError(f"vintage input {name!r} must be a non-empty Series")
            if values.index.has_duplicates or not values.index.is_monotonic_increasing:
                raise ValueError(f"vintage input {name!r} index must be unique and sorted")
            status = statuses[name]
            if not isinstance(status, str) or not status.strip():
                raise ValueError(f"vintage status for {name!r} must be non-blank")

            raw_knowledge = knowledge_times[name]
            if isinstance(raw_knowledge, pd.Series):
                if not raw_knowledge.index.equals(values.index):
                    raise ValueError(
                        f"knowledge-time index for {name!r} must exactly match its values"
                    )
                clocks = list(raw_knowledge)
            else:
                clocks = [raw_knowledge] * len(values)

            for (event_raw, value), knowledge_raw in zip(
                values.items(), clocks, strict=True
            ):
                event = _utc(event_raw, "event_time")
                knowledge = _utc(knowledge_raw, "knowledge_time")
                if knowledge < event:
                    raise ValueError(
                        f"knowledge_time precedes event_time for {name!r} at {event_raw}"
                    )
                number = float(value)
                _value_token(number)
                rows.append(VintageObservation(
                    input_name=name,
                    event_time=event,
                    knowledge_time=knowledge,
                    value=number,
                    vintage_status=status,
                ))
        return tuple(sorted(rows, key=lambda row: (row.input_name, row.event_time)))

    def issue_cut(self, as_of: datetime) -> VintageDataCut:
        cutoff = _utc(as_of, "as_of")
        rows = tuple(
            row for row in self._rows
            if row.event_time <= cutoff and row.knowledge_time <= cutoff
        )
        payload = _cut_payload(self.issuer_id, cutoff, rows)
        digest = hashlib.sha256(payload).hexdigest()
        return VintageDataCut(
            schema="seiche.vintage-data-cut.v1",
            cut_id=f"vintagecut_{digest[:32]}",
            issuer_id=self.issuer_id,
            as_of=cutoff,
            rows=rows,
            signature=hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest(),
        )

    def verify_cut(self, data_cut: VintageDataCut) -> VerifiedVintageDataCut:
        if not isinstance(data_cut, VintageDataCut):
            raise TypeError("data_cut must be a VintageDataCut")
        if data_cut.schema != "seiche.vintage-data-cut.v1":
            raise VintageCutVerificationError("unsupported vintage data-cut schema")
        if data_cut.issuer_id != self.issuer_id:
            raise VintageCutVerificationError("vintage data cut was issued by another store")
        payload = _cut_payload(data_cut.issuer_id, data_cut.as_of, data_cut.rows)
        digest = hashlib.sha256(payload).hexdigest()
        if data_cut.cut_id != f"vintagecut_{digest[:32]}":
            raise VintageCutVerificationError("vintage data-cut content identity mismatch")
        expected = hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, data_cut.signature):
            raise VintageCutVerificationError("vintage data-cut signature mismatch")
        if any(
            row.event_time > data_cut.as_of or row.knowledge_time > data_cut.as_of
            for row in data_cut.rows
        ):
            raise VintageCutVerificationError("vintage data cut contains future information")
        return VerifiedVintageDataCut(data_cut, _VERIFIED_TOKEN)


def assert_cut_binds_series(
    data_cut: VerifiedVintageDataCut,
    series: Mapping[str, pd.Series],
) -> None:
    """Require exact name, date order, and numeric value equality to the cut."""
    if not isinstance(data_cut, VerifiedVintageDataCut):
        raise TypeError("validated history requires a VerifiedVintageDataCut")
    grouped: dict[str, list[VintageObservation]] = {}
    for row in data_cut.rows:
        grouped.setdefault(row.input_name, []).append(row)
    if set(grouped) != set(series):
        raise VintageCutVerificationError(
            "vintage data-cut inputs do not exactly match consumed series"
        )
    for name, values in series.items():
        expected = grouped[name]
        actual = [
            (_utc(event, "event_time"), _value_token(value))
            for event, value in values.items()
        ]
        bound = [(row.event_time, _value_token(row.value)) for row in expected]
        if actual != bound:
            raise VintageCutVerificationError(
                f"vintage data cut does not bind exact series {name!r}"
            )


def verified_cut_evidence(
    data_cut: VerifiedVintageDataCut,
    required_inputs: Sequence[str],
) -> dict:
    if not isinstance(data_cut, VerifiedVintageDataCut):
        raise TypeError("data_cut must be a VerifiedVintageDataCut")
    statuses: dict[str, set[str]] = {}
    for row in data_cut.rows:
        statuses.setdefault(row.input_name, set()).add(row.vintage_status)
    missing = [name for name in required_inputs if name not in statuses]
    unsafe = {
        name: sorted(statuses.get(name, {"missing"}))
        for name in required_inputs
        if statuses.get(name, set()) - VINTAGE_SAFE_STATUSES or name not in statuses
    }
    eligible = not missing and not unsafe and bool(data_cut.rows)
    return {
        "status": "VINTAGE_PIT" if eligible else "UNSAFE_VERIFIED_DATA_CUT",
        "validated_backtest_eligible": eligible,
        "real_money_eligible": False,
        "reason": (
            "signed cut binds every consumed series to accepted as-published revisions; "
            "a forward live record is still required before capital use"
            if eligible else "signed cut is incomplete or contains an unsafe vintage status"
        ),
        "required_inputs": list(required_inputs),
        "manifest": {name: sorted(values) for name, values in statuses.items()},
        "missing": missing,
        "unsafe": unsafe,
        "accepted_statuses": sorted(VINTAGE_SAFE_STATUSES),
        "cut_id": data_cut.cut_id,
        "issuer_id": data_cut.issuer_id,
        "as_of": data_cut.as_of.isoformat(),
    }
