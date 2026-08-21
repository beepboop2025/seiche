"""Seiche source health and provenance rows for OpenBB."""

from __future__ import annotations

from typing import Any, Literal

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.abstract.data import Data
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.abstract.query_params import QueryParams
from pydantic import Field

from openbb_seiche.models._client import canonical_url, get_json

Staleness = Literal["all", "fresh", "aging", "stale", "dead", "unknown"]
HealthState = Literal["fresh", "aging", "stale", "dead", "unknown"]
HEALTH_STATES = frozenset({"fresh", "aging", "stale", "dead", "unknown"})


class SeicheDataHealthQueryParams(QueryParams):
    """Filter Seiche's public per-source freshness ledger."""

    source: str | None = Field(
        default=None, description="Optional exact source identifier."
    )
    staleness: Staleness = Field(
        default="all", description="Optional freshness-state filter."
    )


class SeicheDataHealthData(Data):
    """One source row from Seiche's public provenance ledger."""

    mnemonic: str = Field(description="Stable Seiche series or feed identifier.")
    source: str = Field(description="Upstream source identifier.")
    remote_id: str | None = Field(
        default=None, description="Upstream series identifier, when public."
    )
    label: str | None = Field(default=None, description="Human-readable source label.")
    unit: str | None = Field(default=None, description="Native unit, when applicable.")
    frequency: str | None = Field(
        default=None, description="Native publication frequency."
    )
    as_of: str | None = Field(default=None, description="Latest observation clock.")
    fetched_at: str | None = Field(default=None, description="Collection clock.")
    staleness: HealthState = Field(description="Fresh, aging, stale, dead, or unknown.")
    age_days: int | None = Field(
        default=None, description="Observation age in calendar days."
    )
    observation_count: int | None = Field(
        default=None, description="Locally retained observation count."
    )
    freshness_basis: str | None = Field(
        default=None, description="Cadence-aware freshness rule."
    )
    snapshot_generated_at: str = Field(
        description="Clock for the completed health snapshot."
    )
    seiche_version: str | None = Field(
        default=None, description="Seiche service version label."
    )
    snapshot_fault_count: int = Field(
        description="Faults attached to the completed snapshot."
    )
    source_url: str = Field(description="Canonical public health API URL.")


class SeicheDataHealthFetcher(
    Fetcher[SeicheDataHealthQueryParams, list[SeicheDataHealthData]]
):
    """Fetch the public provenance ledger without hiding stale or dead inputs."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> SeicheDataHealthQueryParams:
        return SeicheDataHealthQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: SeicheDataHealthQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        payload = await get_json("/api/health", client=kwargs.get("client"))
        rows = payload.get("provenance")
        if not isinstance(rows, list):
            raise OpenBBError(
                "Seiche health response does not contain provenance rows."
            )
        generated_at = payload.get("generated_at")
        if not isinstance(generated_at, str) or not generated_at:
            raise OpenBBError(
                "Seiche health response does not contain a snapshot clock."
            )
        faults = payload.get("faults")
        if not isinstance(faults, list):
            raise OpenBBError("Seiche health response does not contain a fault ledger.")
        selected = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            state = str(row.get("staleness") or "unknown")
            if state not in HEALTH_STATES:
                raise OpenBBError(
                    f"Seiche health response has an unsupported state: {state!r}."
                )
            if query.source and row.get("source") != query.source:
                continue
            if query.staleness != "all" and state != query.staleness:
                continue
            selected.append(
                {
                    "mnemonic": row.get("mnemonic", "unknown"),
                    "source": row.get("source", "unknown"),
                    "remote_id": row.get("remote_id"),
                    "label": row.get("label"),
                    "unit": row.get("unit"),
                    "frequency": row.get("freq"),
                    "as_of": row.get("asof"),
                    "fetched_at": row.get("fetched_at"),
                    "staleness": state,
                    "age_days": row.get("age_days"),
                    "observation_count": row.get("n_obs"),
                    "freshness_basis": row.get("freshness_basis"),
                    "snapshot_generated_at": generated_at,
                    "seiche_version": payload.get("version"),
                    "snapshot_fault_count": len(faults),
                    "source_url": canonical_url("/api/health"),
                }
            )
        return selected

    @staticmethod
    def transform_data(
        query: SeicheDataHealthQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[SeicheDataHealthData]:
        return [SeicheDataHealthData.model_validate(item) for item in data]
