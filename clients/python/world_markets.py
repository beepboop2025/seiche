#!/usr/bin/env python3
"""Dependency-free example client for Seiche's public world-markets contract.

The client returns the server payload unchanged. ``contract_receipt`` is a
separate convenience projection so response, source, and evaluation clocks are
never silently collapsed into one timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.seiche.info"
ALLOWED_SECTIONS = frozenset(
    {
        "summary",
        "money_markets",
        "forex",
        "capital_markets",
        "china_macro",
        "sources",
        "methodology",
        "all",
    }
)
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "seiche-public-python-example/1.0 (+https://seiche.info/developers)"
CORE_CLOCK_DOMAINS = ("money_markets", "forex", "capital_markets")
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


class SeicheClientError(RuntimeError):
    """Raised for transport failures or a response outside the public contract."""


def fetch_world_markets(
    section: str = "sources",
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Fetch one bounded, anonymous REST projection.

    There are deliberately no credentials, hidden retries, or recompute flags.
    A 503 remains unavailable evidence and is never rewritten as an empty or
    calm result.
    """

    if section not in ALLOWED_SECTIONS:
        raise ValueError(
            "section must be one of: " + ", ".join(sorted(ALLOWED_SECTIONS))
        )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")

    query = urllib.parse.urlencode({"section": section})
    url = f"{base_url.rstrip('/')}/api/v2/world-markets?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After")
        suffix = f"; retry-after={retry_after}" if retry_after else ""
        raise SeicheClientError(f"Seiche returned HTTP {exc.code}{suffix}") from exc
    except urllib.error.URLError as exc:
        raise SeicheClientError(f"Seiche request failed: {exc.reason}") from exc

    if len(body) > max_response_bytes:
        raise SeicheClientError(
            f"response exceeded the {max_response_bytes}-byte client limit"
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeicheClientError("Seiche returned invalid UTF-8 JSON") from exc
    _validate_contract(payload, section)
    return payload


def _validate_contract(payload: Any, section: str) -> None:
    if not isinstance(payload, dict):
        raise SeicheClientError("world-markets response must be a JSON object")
    if payload.get("schema") != "seiche.world-markets.v1":
        raise SeicheClientError("unexpected world-markets schema")
    if payload.get("selection") != section:
        raise SeicheClientError("server selection does not match the request")
    if payload.get("context_only") is not True:
        raise SeicheClientError("context-only boundary is missing")
    clocks = payload.get("clocks")
    citation = payload.get("citation")
    scope = payload.get("scope")
    if not isinstance(clocks, dict) or not clocks.get("boundary"):
        raise SeicheClientError("clock boundary is missing")
    if not isinstance(citation, dict) or not citation.get("canonical_url"):
        raise SeicheClientError("citation block is missing")
    if not isinstance(scope, dict) or scope.get("coverage_claim") != (
        "curated_partial_non_exhaustive"
    ):
        raise SeicheClientError("partial-coverage boundary is missing")
    _validate_selector_shape(payload, section)
    _validate_clock_contract(payload, section, clocks, citation)
    if section in {"china_macro", "all"}:
        _validate_china_macro(payload["china_macro"])


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise SeicheClientError(f"{label} fields do not match schema v1")
    return value


def _validate_selector_shape(payload: dict[str, Any], section: str) -> None:
    present = frozenset(payload) & SECTION_CONTENT_KEYS
    if present != EXPECTED_SECTION_CONTENT[section]:
        raise SeicheClientError("response content does not match the requested section")


def _validate_clock_contract(
    payload: dict[str, Any],
    section: str,
    clocks: dict[str, Any],
    citation: dict[str, Any],
) -> None:
    domains = clocks.get("domains")
    if not isinstance(domains, dict) or set(domains) != set(CORE_CLOCK_DOMAINS):
        raise SeicheClientError(
            "world clock domains must contain only the core markets"
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
        raise SeicheClientError("required world clock paths are missing")
    if clocks.get("excluded_from_observation_clocks") != ["china_macro.knowledge_time"]:
        raise SeicheClientError("China knowledge time exclusion is missing")
    if any(
        value is not None and not isinstance(value, str) for value in domains.values()
    ):
        raise SeicheClientError("world clock domain values must be strings or null")
    latest = max(
        (value for value in domains.values() if value is not None), default=None
    )
    if clocks.get("latest_domain_as_of") != latest:
        raise SeicheClientError("latest world clock is inconsistent with core domains")
    if section in CORE_CLOCK_DOMAINS:
        selected = domains[section]
    elif section in {"summary", "all"}:
        selected = latest
    else:
        selected = None
    if (
        clocks.get("selected_evidence_as_of") != selected
        or payload.get("as_of") != selected
        or citation.get("evidence_as_of") != selected
    ):
        raise SeicheClientError("selected evidence clock is inconsistent")
    generated_at = payload.get("generated_at")
    if (
        clocks.get("snapshot_generated_at") != generated_at
        or citation.get("generated_at") != generated_at
    ):
        raise SeicheClientError("snapshot and citation clocks are inconsistent")
    if section == "china_macro" and generated_at is not None:
        raise SeicheClientError(
            "standalone China metadata cannot borrow a snapshot clock"
        )


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


def _validate_china_macro(china: Any) -> None:
    """Validate the exact metadata-only v1 state machine and nested allowlists."""

    if not isinstance(china, dict):
        raise SeicheClientError("China macro projection must be a JSON object")
    available = china.get("available")
    if not isinstance(available, bool):
        raise SeicheClientError("China macro availability state is inconsistent")
    expected_keys = CHINA_AVAILABLE_KEYS if available else CHINA_UNAVAILABLE_KEYS
    _require_exact_keys(china, expected_keys, "China macro")
    if (
        china.get("schema") != "seiche.nbs-macro-context.v1"
        or china.get("dataset") != "CN.NBS.MACRO_CONTEXT"
        or china.get("publisher") != "National Bureau of Statistics of China"
        or china.get("source_url")
        != "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData"
        or china.get("terms_url")
        != "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html"
    ):
        raise SeicheClientError("unexpected China macro identity or source contract")
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
        raise SeicheClientError("China macro metadata-only boundary is missing")
    if (
        china.get("as_of") is not None
        or china.get("public_distribution") != "metadata_only"
        or china.get("rights_status") != "redistribution_review_required"
    ):
        raise SeicheClientError("China macro rights or observation boundary is invalid")
    series = china.get("series_catalog")
    if (
        not isinstance(series, list)
        or isinstance(china.get("series_count"), bool)
        or china.get("series_count") != 4
        or len(series) != 4
    ):
        raise SeicheClientError("China macro series catalog is malformed")
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
            raise SeicheClientError("China macro semantic metadata is malformed")
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
        if any(not isinstance(row.get(field), str) for field in string_fields):
            raise SeicheClientError("China macro series metadata is malformed")
        if any(
            row.get(field) is not None and not isinstance(row.get(field), str)
            for field in ("dp_name", "source_unit_label_exact")
        ):
            raise SeicheClientError("China macro series metadata is malformed")
        if (
            not isinstance(row.get("source_unit_semantically_authoritative"), bool)
            or row.get("value_publication") != "withheld_pending_rights_review"
        ):
            raise SeicheClientError("China macro series publication gate is invalid")
        observed_ids.append(row["series_id"])
    if tuple(observed_ids) != CHINA_MACRO_SERIES_IDS:
        raise SeicheClientError("China macro series identities or order drifted")
    boundaries = china.get("boundaries")
    if (
        not isinstance(boundaries, list)
        or len(boundaries) != 3
        or any(not isinstance(item, str) or not item for item in boundaries)
        or not isinstance(china.get("reading"), str)
    ):
        raise SeicheClientError("China macro public boundaries are malformed")
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
        raise SeicheClientError("China macro availability state is inconsistent")
    if not available:
        if china.get("reason_code") != "signed_owner_export_required":
            raise SeicheClientError("China macro unavailable reason is invalid")
        return

    revision_id = china.get("revision_id")
    predecessor = china.get("predecessor_revision_id")
    knowledge_time = china.get("knowledge_time")
    knowledge_instant = _canonical_utc_instant(knowledge_time)
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
        raise SeicheClientError("available China macro revision metadata is malformed")
    provenance = _require_exact_keys(
        china.get("provenance"), CHINA_PROVENANCE_KEYS, "China macro provenance"
    )
    if (
        not _is_hex(provenance.get("manifest_sha256"), _HEX_64_RE)
        or provenance.get("owner_attestation") != "ed25519"
    ):
        raise SeicheClientError("China macro provenance is malformed")
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
        raise SeicheClientError("China macro attestation is malformed")


def contract_receipt(
    payload: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Return citation, clocks, coverage boundary, and effective client limits."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")

    return {
        "schema": payload["schema"],
        "selection": payload["selection"],
        "status": payload.get("status"),
        "clocks": payload["clocks"],
        "citation": payload["citation"],
        "scope": payload["scope"],
        "client_limits": {
            "timeout_seconds": timeout_seconds,
            "max_response_bytes": max_response_bytes,
            "automatic_retries": 0,
        },
    }


if __name__ == "__main__":
    print(json.dumps(contract_receipt(fetch_world_markets()), indent=2, sort_keys=True))
