"""Current Seiche funding-stress regime for OpenBB."""

from __future__ import annotations

from typing import Any

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.abstract.data import Data
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.abstract.query_params import QueryParams
from pydantic import Field

from openbb_seiche.models._client import canonical_url, get_json


class SeicheFundingStressQueryParams(QueryParams):
    """Query the latest completed public Seiche funding-stress snapshot."""


class SeicheFundingStressData(Data):
    """A source-clocked funding-stress reading, not a tradeable quote."""

    generated_at: str = Field(
        description="UTC generation clock for the completed snapshot."
    )
    stress_index: float = Field(
        description="Seiche composite index on its published scale."
    )
    regime: str = Field(description="Published categorical regime.")
    coverage_pct: float = Field(
        description="Input coverage percentage for the reading."
    )
    tell: float | None = Field(
        default=None, description="Plumbing stress minus priced stress."
    )
    event_probability_5bd: float | None = Field(
        default=None,
        description="Published ensemble probability of an event within five business days.",
    )
    ensemble_dispersion: float | None = Field(
        default=None,
        description="Cross-member dispersion for the five-day probability.",
    )
    ensemble_members: dict[str, float] | None = Field(
        default=None,
        description="Named public member probabilities behind the five-day ensemble.",
    )
    next_turn_date: str | None = Field(
        default=None, description="Next published turn date."
    )
    next_turn_forecast_bp: float | None = Field(
        default=None,
        description="Published forecast for the next turn, in basis points.",
    )
    crunch_window_count: int = Field(
        description="Number of published forward pressure windows."
    )
    fault_count: int = Field(description="Number of faults attached to the snapshot.")
    source_url: str = Field(description="Canonical public API URL.")
    disclaimer: str = Field(
        description="Research-use boundary attached to the reading."
    )


class SeicheFundingStressFetcher(
    Fetcher[SeicheFundingStressQueryParams, list[SeicheFundingStressData]]
):
    """Fetch the latest public funding-stress regime from Seiche."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> SeicheFundingStressQueryParams:
        return SeicheFundingStressQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: SeicheFundingStressQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        payload = await get_json("/api/gauge", client=kwargs.get("client"))
        if payload.get("schema") != "seiche.gauge.v1":
            raise OpenBBError("Seiche gauge response has an unsupported schema.")
        required = {"generated_at", "index", "regime", "coverage_pct"}
        missing = sorted(required - payload.keys())
        if missing:
            raise OpenBBError(
                f"Seiche gauge contract is missing required fields: {missing}"
            )
        next_turn = payload.get("next_turn") or {}
        windows = payload.get("crunch_windows") or []
        return [
            {
                "generated_at": payload["generated_at"],
                "stress_index": payload["index"],
                "regime": payload["regime"],
                "coverage_pct": payload["coverage_pct"],
                "tell": payload.get("tell"),
                "event_probability_5bd": payload.get("p_event_5bd"),
                "ensemble_dispersion": payload.get("p_event_5bd_dispersion"),
                "ensemble_members": payload.get("p_event_5bd_members"),
                "next_turn_date": next_turn.get("date"),
                "next_turn_forecast_bp": next_turn.get("forecast_bp"),
                "crunch_window_count": len(windows),
                "fault_count": int(payload.get("faults") or 0),
                "source_url": canonical_url("/api/gauge"),
                "disclaimer": payload.get("notes")
                or "Research context, not investment advice.",
            }
        ]

    @staticmethod
    def transform_data(
        query: SeicheFundingStressQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[SeicheFundingStressData]:
        return [SeicheFundingStressData.model_validate(item) for item in data]
