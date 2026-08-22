"""Seiche world-markets evidence domains for OpenBB."""

from __future__ import annotations

from typing import Any, Literal

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.abstract.data import Data
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.abstract.query_params import QueryParams
from pydantic import Field

from openbb_seiche.models._client import get_json

Selector = Literal[
    "summary",
    "money_markets",
    "forex",
    "capital_markets",
    "sources",
    "methodology",
    "all",
]
EvidenceStatus = Literal[
    "observed", "derived", "structural", "restricted", "unavailable"
]
EVIDENCE_STATUSES = frozenset(
    {"observed", "derived", "structural", "restricted", "unavailable"}
)
DOMAIN_SELECTORS = ("money_markets", "forex", "capital_markets")
COVERAGE_CLAIM = "curated_partial_non_exhaustive"


class SeicheWorldMarketsQueryParams(QueryParams):
    """Select a bounded world-markets projection from a completed snapshot."""

    selector: Selector = Field(
        default="summary", description="Public projection selector."
    )


class SeicheWorldMarketsData(Data):
    """One evidence domain with its own clock and status."""

    domain: str = Field(
        description="Selected evidence domain, source, or methodology identifier."
    )
    status: EvidenceStatus = Field(
        description="Observed, derived, structural, restricted, or unavailable."
    )
    as_of: str | None = Field(
        default=None, description="Latest evidence clock for this domain."
    )
    reading: str | None = Field(
        default=None, description="Bounded plain-language domain reading."
    )
    coverage: dict[str, Any] = Field(
        default_factory=dict, description="Domain-specific coverage counts."
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded selector-specific evidence, source, or methodology fields.",
    )
    generated_at: str = Field(description="Snapshot generation clock.")
    evaluated_at: str | None = Field(
        default=None, description="Time when freshness/status was evaluated."
    )
    selected_evidence_as_of: str | None = Field(
        default=None,
        description="Top-level evidence clock for the selected projection.",
    )
    clock_boundary: str = Field(
        description="Statement separating response/evaluation time from evidence clocks."
    )
    selection: str = Field(description="Requested public projection selector.")
    canonical_url: str = Field(
        description="Canonical citation page for the world-markets view."
    )
    api_url: str = Field(description="Canonical public API URL for this projection.")
    coverage_claim: str = Field(description="Declared scope of world-markets coverage.")
    evidence_boundary: str = Field(
        description="Research and scope boundary attached to this view."
    )


def _domain_summaries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domains = (payload.get("summary") or {}).get("domains")
    if not isinstance(domains, list):
        domains = (payload.get("coverage") or {}).get("domains")
    if not isinstance(domains, list):
        raise OpenBBError("Seiche world-markets response does not contain domain rows.")
    return [row for row in domains if isinstance(row, dict)]


def _selected_rows(payload: dict[str, Any], selector: Selector) -> list[dict[str, Any]]:
    """Honor the server selector instead of silently returning every summary."""

    domains = _domain_summaries(payload)
    if selector in DOMAIN_SELECTORS:
        selected = [row for row in domains if row.get("id") == selector]
        if len(selected) != 1 or not isinstance(payload.get(selector), dict):
            raise OpenBBError(
                f"Seiche world-markets response is missing {selector!r} evidence."
            )
        return [{**selected[0], "details": payload[selector]}]
    if selector == "sources":
        sources = payload.get("sources")
        if not isinstance(sources, list):
            raise OpenBBError("Seiche world-markets response is missing source rows.")
        selected = []
        for source in sources:
            if not isinstance(source, dict) or not source.get("id"):
                continue
            selected.append(
                {
                    "id": f"source:{source['id']}",
                    "status": source.get("status", "structural"),
                    "as_of": None,
                    "reading": source.get("publisher"),
                    "coverage": {
                        "catalog_role": source.get("catalog_role"),
                        "domains": source.get("domains") or [],
                        "used_in_snapshot": bool(source.get("used_in_snapshot")),
                        "projection_paths": source.get("projection_paths") or [],
                    },
                    "details": source,
                }
            )
        if not selected:
            raise OpenBBError("Seiche world-markets source registry is empty.")
        return selected
    if selector == "methodology":
        methodology = payload.get("methodology")
        if not isinstance(methodology, dict):
            raise OpenBBError("Seiche world-markets response is missing methodology.")
        return [
            {
                "id": "methodology",
                "status": methodology.get("status", "structural"),
                "as_of": payload.get("as_of"),
                "reading": methodology.get("projection"),
                "coverage": methodology.get("boundedness") or {},
                "details": methodology,
            }
        ]
    return [
        {
            **row,
            "details": payload.get(str(row.get("id")), {}) if selector == "all" else {},
        }
        for row in domains
    ]


class SeicheWorldMarketsFetcher(
    Fetcher[SeicheWorldMarketsQueryParams, list[SeicheWorldMarketsData]]
):
    """Fetch source-clocked world-markets evidence from Seiche."""

    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> SeicheWorldMarketsQueryParams:
        return SeicheWorldMarketsQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: SeicheWorldMarketsQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        payload = await get_json(
            "/api/v2/world-markets",
            params={"section": query.selector},
            client=kwargs.get("client"),
        )
        if payload.get("schema") != "seiche.world-markets.v1":
            raise OpenBBError(
                "Seiche world-markets response has an unsupported schema."
            )
        if payload.get("selection") != query.selector:
            raise OpenBBError(
                "Seiche world-markets response selection does not match the query."
            )
        if payload.get("context_only") is not True:
            raise OpenBBError(
                "Seiche world-markets response is missing its context-only boundary."
            )
        clocks = payload.get("clocks")
        citation = payload.get("citation")
        scope = payload.get("scope")
        if not isinstance(clocks, dict) or not clocks.get("boundary"):
            raise OpenBBError(
                "Seiche world-markets response is missing its clock boundary."
            )
        if not isinstance(citation, dict) or not citation.get("canonical_url"):
            raise OpenBBError(
                "Seiche world-markets response is missing its citation block."
            )
        if not isinstance(scope, dict) or scope.get("coverage_claim") != COVERAGE_CLAIM:
            raise OpenBBError(
                "Seiche world-markets response has an unsupported coverage claim."
            )
        generated_at = clocks.get("snapshot_generated_at") or payload.get(
            "generated_at"
        )
        if not isinstance(generated_at, str) or not generated_at:
            raise OpenBBError(
                "Seiche world-markets response is missing its snapshot clock."
            )
        rows = _selected_rows(payload, query.selector)
        unsupported = sorted(
            {
                str(row.get("status"))
                for row in rows
                if row.get("status") not in EVIDENCE_STATUSES
            }
        )
        if unsupported:
            raise OpenBBError(
                f"Seiche world-markets response has unsupported status: {unsupported}."
            )
        return [
            {
                "domain": row.get("id", "unknown"),
                "status": row.get("status", "unavailable"),
                "as_of": row.get("as_of"),
                "reading": row.get("reading"),
                "coverage": row.get("coverage") or {},
                "details": row.get("details") or {},
                "generated_at": generated_at,
                "evaluated_at": clocks.get("evaluation_at"),
                "selected_evidence_as_of": clocks.get("selected_evidence_as_of"),
                "clock_boundary": clocks["boundary"],
                "selection": payload["selection"],
                "canonical_url": citation["canonical_url"],
                "api_url": citation.get("api_url")
                or "https://api.seiche.info/api/v2/world-markets",
                "coverage_claim": scope["coverage_claim"],
                "evidence_boundary": payload.get("disclaimer")
                or "Research context, not investment advice.",
            }
            for row in rows
        ]

    @staticmethod
    def transform_data(
        query: SeicheWorldMarketsQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[SeicheWorldMarketsData]:
        return [SeicheWorldMarketsData.model_validate(item) for item in data]
