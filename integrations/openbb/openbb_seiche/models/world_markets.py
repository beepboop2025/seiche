"""Seiche world-markets evidence domains for OpenBB."""

from __future__ import annotations

from datetime import UTC, datetime
import re
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
    "china_macro",
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
SECTION_CONTENT_KEYS = frozenset(
    {
        "summary",
        "money_markets",
        "forex",
        "capital_markets",
        "china_macro",
        "sources",
        "methodology",
    }
)
EXPECTED_SECTION_CONTENT = {
    "summary": frozenset({"summary"}),
    "money_markets": frozenset({"money_markets"}),
    "forex": frozenset({"forex"}),
    "capital_markets": frozenset({"capital_markets"}),
    "china_macro": frozenset({"china_macro"}),
    "sources": frozenset({"sources"}),
    "methodology": frozenset({"methodology"}),
    "all": frozenset(
        {
            "money_markets",
            "forex",
            "capital_markets",
            "china_macro",
            "sources",
            "methodology",
        }
    ),
}
CHINA_MACRO_SERIES_IDS = (
    "CN.NBS.CPI_INDEX",
    "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
    "CN.NBS.MANUFACTURING_PMI",
    "CN.NBS.PPI_INDEX",
)
CHINA_COMMON_KEYS = frozenset(
    {
        "status",
        "evidence_status",
        "as_of",
        "schema",
        "available",
        "dataset",
        "publisher",
        "source_url",
        "context_only",
        "scoring_eligible",
        "cn_cny_gauge_eligible",
        "values_published",
        "raw_evidence_included",
        "history_included",
        "public_distribution",
        "rights_status",
        "terms_url",
        "series_catalog",
        "series_count",
        "reading",
        "boundaries",
    }
)
CHINA_AVAILABLE_KEYS = CHINA_COMMON_KEYS | frozenset(
    {
        "source_registry_ids",
        "revision_id",
        "predecessor_revision_id",
        "knowledge_time",
        "provenance",
        "attestation",
    }
)
CHINA_UNAVAILABLE_KEYS = CHINA_COMMON_KEYS | frozenset({"reason_code"})
CHINA_SERIES_KEYS = frozenset(
    {
        "series_id",
        "catalogid",
        "catalog_label",
        "row_id",
        "i",
        "ek",
        "ek_dp",
        "dp",
        "dp_name",
        "label",
        "reference_release_url",
        "release_url",
        "source_unit_label_exact",
        "source_unit_semantically_authoritative",
        "semantic_contract",
        "value_publication",
    }
)
CHINA_SEMANTIC_KEYS = frozenset(
    {"value_kind", "canonical_unit", "comparison_base", "transform", "threshold"}
)
CHINA_PROVENANCE_KEYS = frozenset({"manifest_sha256", "owner_attestation"})
CHINA_ATTESTATION_KEYS = frozenset(
    {
        "schema",
        "algorithm",
        "domain",
        "export_id",
        "signer_key_id",
        "signed_at",
        "manifest_sha256",
        "public_projection_sha256",
        "signature",
    }
)
_HEX_64_RE = re.compile(r"[0-9a-f]{64}")
_HEX_128_RE = re.compile(r"[0-9a-f]{128}")
_EXPORT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


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
    generated_at: str | None = Field(
        description=(
            "Snapshot generation clock; absent only when the standalone China "
            "metadata projection is served without a world-market snapshot."
        )
    )
    evaluated_at: str | None = Field(
        default=None, description="Time when freshness/status was evaluated."
    )
    selected_evidence_as_of: str | None = Field(
        default=None,
        description="Top-level evidence clock for the selected projection.",
    )
    knowledge_time: str | None = Field(
        default=None,
        description=(
            "When an owner-attested China export became knowable; never an "
            "observation or market as-of clock."
        ),
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


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise OpenBBError(f"Seiche {label} fields do not match schema v1.")
    return value


def _canonical_utc_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)
    except (OverflowError, ValueError):
        return None
    timespec = "microseconds" if parsed.microsecond else "seconds"
    canonical = parsed.isoformat(timespec=timespec).replace("+00:00", "Z")
    return parsed if canonical == value else None


def _is_hex(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _validate_selector_shape(payload: dict[str, Any], selector: Selector) -> None:
    present = frozenset(payload) & SECTION_CONTENT_KEYS
    if present != EXPECTED_SECTION_CONTENT[selector]:
        raise OpenBBError(
            "Seiche world-markets content does not match the requested selector."
        )


def _validate_clock_contract(
    payload: dict[str, Any],
    selector: Selector,
    clocks: dict[str, Any],
    citation: dict[str, Any],
) -> None:
    domains = clocks.get("domains")
    if not isinstance(domains, dict) or set(domains) != set(DOMAIN_SELECTORS):
        raise OpenBBError(
            "Seiche world-market clocks must contain only the core markets."
        )
    if (
        "generated_at" not in payload
        or "as_of" not in payload
        or "snapshot_generated_at" not in clocks
        or "latest_domain_as_of" not in clocks
        or "selected_evidence_as_of" not in clocks
        or "generated_at" not in citation
        or "evidence_as_of" not in citation
    ):
        raise OpenBBError("Seiche world-markets required clock paths are missing.")
    if clocks.get("excluded_from_observation_clocks") != ["china_macro.knowledge_time"]:
        raise OpenBBError("Seiche China knowledge-time clock exclusion is missing.")
    if any(
        value is not None and not isinstance(value, str) for value in domains.values()
    ):
        raise OpenBBError("Seiche world-market domain clocks are malformed.")
    latest = max(
        (value for value in domains.values() if value is not None), default=None
    )
    if clocks.get("latest_domain_as_of") != latest:
        raise OpenBBError("Seiche latest world-market clock is inconsistent.")
    if selector in DOMAIN_SELECTORS:
        selected = domains[selector]
    elif selector in {"summary", "all"}:
        selected = latest
    else:
        selected = None
    if (
        clocks.get("selected_evidence_as_of") != selected
        or payload.get("as_of") != selected
        or citation.get("evidence_as_of") != selected
    ):
        raise OpenBBError("Seiche selected evidence clock is inconsistent.")
    generated_at = payload.get("generated_at")
    if (
        clocks.get("snapshot_generated_at") != generated_at
        or citation.get("generated_at") != generated_at
    ):
        raise OpenBBError("Seiche snapshot and citation clocks are inconsistent.")
    if selector == "china_macro" and generated_at is not None:
        raise OpenBBError(
            "Seiche standalone China metadata cannot borrow a snapshot clock."
        )


def _validate_china_macro(china: Any) -> dict[str, Any]:
    if not isinstance(china, dict):
        raise OpenBBError("Seiche world-markets response is missing China metadata.")
    available = china.get("available")
    if not isinstance(available, bool):
        raise OpenBBError("Seiche China macro availability state is inconsistent.")
    _require_exact_keys(
        china,
        CHINA_AVAILABLE_KEYS if available else CHINA_UNAVAILABLE_KEYS,
        "China macro",
    )
    if (
        china.get("schema") != "seiche.nbs-macro-context.v1"
        or china.get("dataset") != "CN.NBS.MACRO_CONTEXT"
        or china.get("publisher") != "National Bureau of Statistics of China"
        or china.get("source_url")
        != "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData"
        or china.get("terms_url")
        != "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html"
    ):
        raise OpenBBError("Seiche China macro identity or source contract drifted.")
    required_false = (
        "cn_cny_gauge_eligible",
        "history_included",
        "raw_evidence_included",
        "scoring_eligible",
        "values_published",
    )
    if china.get("context_only") is not True or any(
        china.get(field) is not False for field in required_false
    ):
        raise OpenBBError(
            "Seiche China macro response is missing its metadata-only boundary."
        )
    if (
        china.get("as_of") is not None
        or china.get("public_distribution") != "metadata_only"
        or china.get("rights_status") != "redistribution_review_required"
    ):
        raise OpenBBError(
            "Seiche China macro response has an invalid rights or observation boundary."
        )
    series = china.get("series_catalog")
    if (
        not isinstance(series, list)
        or isinstance(china.get("series_count"), bool)
        or china.get("series_count") != 4
        or len(series) != 4
    ):
        raise OpenBBError("Seiche China macro series catalog is malformed.")
    observed_ids: list[str] = []
    for row in series:
        row = _require_exact_keys(row, CHINA_SERIES_KEYS, "China macro series")
        semantic = _require_exact_keys(
            row.get("semantic_contract"),
            CHINA_SEMANTIC_KEYS,
            "China macro semantic contract",
        )
        if any(
            value is not None and not isinstance(value, str)
            for value in semantic.values()
        ):
            raise OpenBBError("Seiche China macro semantic metadata is malformed.")
        string_fields = (
            "series_id",
            "catalogid",
            "catalog_label",
            "row_id",
            "i",
            "ek",
            "ek_dp",
            "dp",
            "label",
            "reference_release_url",
            "release_url",
        )
        if any(not isinstance(row.get(field), str) for field in string_fields) or any(
            row.get(field) is not None and not isinstance(row.get(field), str)
            for field in ("dp_name", "source_unit_label_exact")
        ):
            raise OpenBBError("Seiche China macro series metadata is malformed.")
        if (
            not isinstance(row.get("source_unit_semantically_authoritative"), bool)
            or row.get("value_publication") != "withheld_pending_rights_review"
        ):
            raise OpenBBError("Seiche China macro publication gate is invalid.")
        observed_ids.append(row["series_id"])
    if tuple(observed_ids) != CHINA_MACRO_SERIES_IDS:
        raise OpenBBError("Seiche China macro series identities or order drifted.")
    boundaries = china.get("boundaries")
    if (
        not isinstance(boundaries, list)
        or len(boundaries) != 3
        or any(not isinstance(item, str) or not item for item in boundaries)
        or not isinstance(china.get("reading"), str)
    ):
        raise OpenBBError("Seiche China macro public boundaries are malformed.")
    if (
        available
        and (
            china.get("status") != "restricted"
            or china.get("evidence_status") != "restricted"
        )
    ) or (
        not available
        and (
            china.get("status") != "structural"
            or china.get("evidence_status") != "unavailable"
        )
    ):
        raise OpenBBError("Seiche China macro availability state is inconsistent.")
    if not available:
        if china.get("reason_code") != "signed_owner_export_required":
            raise OpenBBError("Seiche China macro unavailable reason is invalid.")
        return china

    revision_id = china.get("revision_id")
    predecessor = china.get("predecessor_revision_id")
    knowledge_instant = _canonical_utc_instant(china.get("knowledge_time"))
    if (
        not isinstance(revision_id, str)
        or _EXPORT_ID_RE.fullmatch(revision_id) is None
        or (
            predecessor is not None
            and (
                not isinstance(predecessor, str)
                or _EXPORT_ID_RE.fullmatch(predecessor) is None
            )
        )
        or knowledge_instant is None
        or china.get("source_registry_ids")
        != ["nbs_monthly_data_browser", "nbs_terms_of_service"]
    ):
        raise OpenBBError("Seiche available China revision metadata is malformed.")
    provenance = _require_exact_keys(
        china.get("provenance"), CHINA_PROVENANCE_KEYS, "China macro provenance"
    )
    if (
        not _is_hex(provenance.get("manifest_sha256"), _HEX_64_RE)
        or provenance.get("owner_attestation") != "ed25519"
    ):
        raise OpenBBError("Seiche China macro provenance is malformed.")
    attestation = _require_exact_keys(
        china.get("attestation"), CHINA_ATTESTATION_KEYS, "China macro attestation"
    )
    signed_instant = _canonical_utc_instant(attestation.get("signed_at"))
    if (
        attestation.get("schema") != "seiche.nbs-owner-export-signature.v1"
        or attestation.get("algorithm") != "ed25519"
        or attestation.get("domain") != "seiche-nbs-owner-export-v1"
        or attestation.get("export_id") != revision_id
        or attestation.get("manifest_sha256") != provenance["manifest_sha256"]
        or not _is_hex(attestation.get("signer_key_id"), _HEX_64_RE)
        or not _is_hex(attestation.get("public_projection_sha256"), _HEX_64_RE)
        or not _is_hex(attestation.get("signature"), _HEX_128_RE)
        or signed_instant is None
        or signed_instant < knowledge_instant
    ):
        raise OpenBBError("Seiche China macro attestation is malformed.")
    return china


def _domain_summaries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domains = (payload.get("summary") or {}).get("domains")
    if not isinstance(domains, list):
        domains = (payload.get("coverage") or {}).get("domains")
    if not isinstance(domains, list):
        raise OpenBBError("Seiche world-markets response does not contain domain rows.")
    return [row for row in domains if isinstance(row, dict)]


def _china_macro_row(payload: dict[str, Any]) -> dict[str, Any]:
    china = _validate_china_macro(payload.get("china_macro"))
    return {
        "id": "china_macro",
        "status": china["status"],
        "as_of": None,
        "knowledge_time": china.get("knowledge_time"),
        "reading": china["reading"],
        "coverage": {
            "available": china["available"],
            "evidence_status": china["evidence_status"],
            "public_distribution": china["public_distribution"],
            "rights_status": china["rights_status"],
            "series_count": china["series_count"],
            "values_published": False,
        },
        "details": china,
    }


def _selected_rows(payload: dict[str, Any], selector: Selector) -> list[dict[str, Any]]:
    """Honor the server selector instead of silently returning every summary."""

    if selector == "china_macro":
        return [_china_macro_row(payload)]
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
    selected = [
        {
            **row,
            "details": payload.get(str(row.get("id")), {}) if selector == "all" else {},
        }
        for row in domains
    ]
    if selector == "all":
        selected.append(_china_macro_row(payload))
    return selected


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
        _validate_selector_shape(payload, query.selector)
        _validate_clock_contract(payload, query.selector, clocks, citation)
        generated_at = payload.get("generated_at")
        if query.selector != "china_macro" and (
            not isinstance(generated_at, str) or not generated_at
        ):
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
                "selected_evidence_as_of": (
                    None
                    if row.get("id") == "china_macro"
                    else clocks.get("selected_evidence_as_of")
                ),
                "knowledge_time": row.get("knowledge_time"),
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
