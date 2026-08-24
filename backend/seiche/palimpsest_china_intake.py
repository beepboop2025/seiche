"""Strict offline intake for Palimpsest's China-economic export.

The intake deliberately does not fetch Palimpsest or any upstream publisher.
An operator supplies an exact manifest, its exact JSONL artifact, and a small
Seiche acceptance receipt which binds a trusted ``accepted_at`` clock to those
bytes.  Only the reviewed ``world_bank_wdi`` source may contribute values.

This is a context contract, not a market-observation adapter.  Nothing in this
module creates :class:`seiche.domain.observation.Observation`, changes the
``CN-CNY`` pack, or makes a row eligible for a gauge or score.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
from functools import lru_cache
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from seiche.china_economic_focus import featured_series
from seiche.nbs_trust import verify_trusted_palimpsest_china_signature

EXPORT_SCHEMA = "palimpsest.china-economic-export.v1"
LEGACY_MANIFEST_SCHEMA = "palimpsest.china-economic-export-manifest.v1"
REVIEW_MANIFEST_SCHEMA = "palimpsest.china-economic-export-manifest.v2"
MANIFEST_SCHEMA = "palimpsest.china-economic-export-manifest.v3"
PRODUCER_SCHEMA = "palimpsest.producer-receipt.v1"
POLICY_SCHEMA = "palimpsest.china-economic-source-policy.v1"
SERIES_REGISTRY_SCHEMA = "palimpsest-china-econ-wdi-series.v1"
AVAILABILITY_RECEIPT_SCHEMA = "palimpsest-china-econ-wdi-run.v3"
AVAILABILITY_SCHEMA = "palimpsest-china-econ-wdi-availability.v1"
INDICATOR_PROVENANCE_SCHEMA = "palimpsest-china-econ-wdi-indicator-provenance.v1"
COLLECTOR_ARTIFACT_SCHEMA = "palimpsest-collector-artifact/v1"
HANDOFF_SCHEMA = "palimpsest.china-economic-handoff-receipt.v3"
LINEAGE_CHAIN_SCHEMA = "palimpsest.china-economic-lineage-chain.v1"
LINEAGE_RECORD_SCHEMA = "palimpsest.china-economic-lineage-record.v1"
LINEAGE_EVIDENCE_SCHEMA = "palimpsest.china-economic-lineage-evidence.v1"
LINEAGE_EVIDENCE_RECORD_SCHEMA = "palimpsest.china-economic-lineage-evidence-record.v1"
ACCEPTANCE_SCHEMA = "seiche.palimpsest-china-economic-acceptance.v2"
ACCEPTANCE_DOMAIN = "seiche:palimpsest-china-economic-acceptance:v2"
CONTEXT_SCHEMA = "seiche.palimpsest-china-economic-context.v1"

ALLOWED_SOURCE_IDS = frozenset({"world_bank_wdi"})
MARKET_CHANNELS = ("capital_market", "money_market")
WDI_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
WDI_RIGHTS_EVIDENCE_URL = (
    "https://datacatalog.worldbank.org/search/dataset/0037712/"
    "world-development-indicators"
)
WDI_ATTRIBUTION = "World Bank, World Development Indicators"
WDI_DATASET = "World Development Indicators"
WDI_COLLECTOR_ID = "world-bank-wdi-china"
WDI_PUBLICATION_STATE = "public_context_only"
WDI_LINEAGE_MODE = "git_tracked_append_only"
WDI_LEDGER_PATH = "readings/china-econ-wdi-observations.jsonl"
WDI_AVAILABILITY_PATH = "readings/china-econ-wdi-latest.json"
WDI_REGISTRY_PATH = "config/china_econ_wdi_series.json"
HANDOFF_LINEAGE_MODE = "git_tracked_reviewed_merge_chain"
LINEAGE_CHAIN_PATH = "china-econ-wdi-lineage-chain.jsonl"
LINEAGE_EVIDENCE_PATH = "github-commit-lineage-evidence.jsonl"
WDI_UPSTREAM_ATTRIBUTION_STATE = "residual_gate"
WDI_UPSTREAM_ATTRIBUTION_REQUIREMENT = (
    "Pin and review a separate per-indicator upstream source and rights metadata "
    "registry before claiming complete upstream attribution; the WDI observation "
    "response does not carry that authority."
)
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_INPUT_LEDGER_BYTES = 128 * 1024 * 1024
MAX_AVAILABILITY_BYTES = 64 * 1024 * 1024
MAX_PRODUCER_COMMIT_EVIDENCE_BYTES = 256 * 1024
MAX_PRODUCER_MAIN_EVIDENCE_BYTES = 256 * 1024
MAX_HANDOFF_BYTES = 2 * 1024 * 1024
MAX_CHECKSUMS_BYTES = 64 * 1024
MAX_LINEAGE_CHAIN_BYTES = 8 * 1024 * 1024
MAX_LINEAGE_EVIDENCE_BYTES = 96 * 1024 * 1024
MAX_ACCEPTANCE_BYTES = 16 * 1024
MAX_SERIES_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_LINEAGE_RECORDS = 256
MAX_RECORDS = 100_000
MAX_SERIES = 512

# This process-local capability is deliberately not serializable and the
# dataclass field which carries it is not accepted by ``__init__``.  Parsing a
# valid Palimpsest manifest proves export integrity; only the signed Seiche
# acceptance path below may confer public-serving authority.
_OWNER_ATTESTED_AUTHORITY = object()

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ED25519_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_SOURCE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_]{1,79}")
_SERIES_ID_RE = re.compile(r"cn\.wdi\.[a-z0-9][a-z0-9_]{1,119}")
_SOURCE_SERIES_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,79}")
_FORBIDDEN_VALUE_SOURCE_MARKERS = ("cfets", "chinamoney", "china_money")

_MANIFEST_V1_KEYS = frozenset(
    {
        "schema_version",
        "generated_at",
        "context_only",
        "scoring_allowed",
        "artifact",
        "input_ledger",
        "policy",
        "series_registry",
        "source_decisions",
        "market_channel_mapping",
    }
)
_MANIFEST_V2_KEYS = frozenset({*_MANIFEST_V1_KEYS, "producer"})
_MANIFEST_KEYS = frozenset({*_MANIFEST_V2_KEYS, "availability_receipt"})
_PRODUCER_KEYS = frozenset(
    {"schema_version", "repository", "commit_sha", "workflow_run"}
)
_PRODUCER_WORKFLOW_KEYS = frozenset(
    {
        "provider",
        "workflow_file",
        "run_id",
        "run_attempt",
        "head_sha",
        "event",
        "conclusion",
        "url",
    }
)
_PRODUCER_REPOSITORY = "beepboop2025/palimpsest"
_PRODUCER_WORKFLOW_FILE = ".github/workflows/tests.yml"
_PRODUCER_AUTHOR_LOGIN = "beepboop2025"
_PRODUCER_COMMITTER_LOGIN = "web-flow"
_ARTIFACT_RECEIPT_KEYS = frozenset(
    {"schema_version", "path", "media_type", "sha256", "bytes", "records"}
)
_INPUT_LEDGER_KEYS = frozenset({"path", "sha256", "bytes", "records"})
_POLICY_RECEIPT_KEYS = frozenset({"path", "sha256", "schema_version", "evaluated_at"})
_SERIES_REGISTRY_RECEIPT_KEYS = frozenset({"path", "sha256", "bytes", "schema_version"})
_AVAILABILITY_RECEIPT_KEYS = frozenset(
    {
        "path",
        "sha256",
        "bytes",
        "schema_version",
        "generated_at",
        "batch_raw_sha256",
        "availability_schema_version",
        "current_numeric_identities_sha256",
        "current_numeric_identities_records",
        "current_projectable_series_sha256",
        "current_projectable_series_records",
        "current_projectable_source_indicators_sha256",
        "current_projectable_source_indicators_records",
        "withdrawn_numeric_identities_sha256",
        "withdrawn_numeric_identities_records",
    }
)
_RUN_RECEIPT_KEYS = frozenset(
    {
        "appended_observations",
        "availability",
        "batch_raw_sha256",
        "collector_artifact",
        "context_only",
        "dataset",
        "dataset_last_updated",
        "generated_at",
        "indicator_provenance",
        "ledger_after",
        "ledger_before",
        "ledger_coverage",
        "license",
        "license_url",
        "limitations",
        "publication_state",
        "redistribution_status",
        "response_coverage",
        "revision_lineage",
        "rights_evidence_url",
        "schema_version",
        "scoring_allowed",
        "source_id",
    }
)
_AVAILABILITY_KEYS = frozenset(
    {
        "coverage_semantics",
        "entries",
        "null_records",
        "records",
        "schema_version",
        "withdrawal_limitation",
        "withdrawal_state",
    }
)
_AVAILABILITY_ENTRY_KEYS = frozenset({"available", "footnote", "indicator_id", "year"})
_LEDGER_SNAPSHOT_KEYS = frozenset({"sha256", "bytes", "records"})
_RESPONSE_COVERAGE_KEYS = frozenset(
    {
        "coverage_semantics",
        "requested_start_year",
        "requested_end_year",
        "configured_indicators",
        "represented_indicators",
        "populated_indicators",
        "null_only_indicators",
        "source_rows",
        "populated_observations",
        "null_rows",
        "period_start",
        "period_end",
    }
)
_LEDGER_COVERAGE_KEYS = frozenset(
    {"coverage_semantics", "records", "series_count", "period_start", "period_end"}
)
_REVISION_LINEAGE_KEYS = frozenset({"mode", "durable_cross_run", "ledger_path"})
_INDICATOR_PROVENANCE_KEYS = frozenset(
    {
        "schema_version",
        "records",
        "entries",
        "upstream_attribution_state",
        "upstream_attribution_requirement",
    }
)
_INDICATOR_PROVENANCE_ENTRY_KEYS = frozenset(
    {"indicator_id", "reviewed_name", "source_title"}
)
_COLLECTOR_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "collector_id",
        "source_receipt",
        "freshness",
        "coverage",
        "abstention",
        "payload_sha256",
    }
)
_COLLECTOR_SOURCE_RECEIPT_KEYS = frozenset(
    {"url", "raw_sha256", "dataset_last_updated", "license"}
)
_COLLECTOR_FRESHNESS_KEYS = frozenset(
    {"evidence_state", "observed_at", "native_cadence", "dataset_age_days"}
)
_SOURCE_DECISION_KEYS = frozenset(
    {
        "source_id",
        "decision",
        "decision_sha256",
        "values_allowed",
        "seiche_export_allowed",
        "license",
        "license_url",
        "rights_evidence_url",
        "attribution",
        "reviewed_at",
        "expires_at",
        "reason",
        "input_records",
        "exported_records",
    }
)
_WRAPPER_KEYS = frozenset(
    {
        "schema_version",
        "context_only",
        "scoring_allowed",
        "market_channels",
        "observation",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "series_id",
        "value",
        "unit",
        "frequency",
        "period_start",
        "period_end",
        "released_at",
        "collected_at",
        "source_id",
        "evidence_url",
        "revision",
        "status",
        "geography",
        "sector",
        "firm_size",
        "ownership",
        "quality",
        "raw_sha256",
        "metadata",
        "observation_id",
    }
)
_WDI_METADATA_KEYS = frozenset(
    {
        "family",
        "source_series_id",
        "source_document_version",
        "parser_version",
        "release_time_semantics",
        "aggregation_window",
    }
)
_ACCEPTANCE_CLAIM_KEYS = frozenset(
    {
        "schema_version",
        "algorithm",
        "domain",
        "accepted_at",
        "manifest_sha256",
        "artifact_sha256",
        "producer_commit_evidence",
        "producer_main_evidence",
        "handoff_receipt",
        "checksum_subject",
        "governed_lineage",
        "operator_confirmations",
        "signer_key_id",
    }
)
_ACCEPTANCE_KEYS = frozenset({*_ACCEPTANCE_CLAIM_KEYS, "signature"})
_PRODUCER_COMMIT_EVIDENCE_KEYS = frozenset(
    {
        "path",
        "request_url",
        "sha256",
        "bytes",
        "sha",
        "author_login",
        "committer_login",
        "parent_shas",
        "verification",
    }
)
_PRODUCER_COMMIT_VERIFICATION_KEYS = frozenset({"verified", "reason", "verified_at"})
_PRODUCER_MAIN_EVIDENCE_KEYS = frozenset(
    {
        "path",
        "request_url",
        "sha256",
        "bytes",
        "observed_at",
        "name",
        "commit",
        "protected",
    }
)
_PRODUCER_MAIN_COMMIT_KEYS = frozenset({"sha"})
_ACCEPTANCE_HANDOFF_KEYS = frozenset({"path", "schema_version", "sha256", "bytes"})
_ACCEPTANCE_CHECKSUM_KEYS = frozenset({"path", "sha256", "bytes", "records"})
_OPERATOR_CONFIRMATION_INPUT_KEYS = frozenset(
    {
        "github_attestation_verified",
        "exact_checksum_subject_set_verified",
        "producer_raw_identity_verified",
        "detached_first_parent_lineage_rebuild_verified",
        "current_main_branch_evidence_verified",
        "rights_and_freshness_reviewed",
    }
)
_OPERATOR_CONFIRMATION_KEYS = frozenset(
    {
        *_OPERATOR_CONFIRMATION_INPUT_KEYS,
        "github_attestation_subject_sha256",
        "checksum_subject_sha256",
        "producer_commit_evidence_sha256",
        "lineage_chain_sha256",
        "lineage_evidence_sha256",
        "lineage_evaluated_at_commit_sha",
        "producer_main_evidence_sha256",
        "manifest_sha256",
        "availability_receipt_sha256",
        "rights_expires_at",
    }
)
_LINEAGE_CHAIN_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "path",
        "sha256",
        "bytes",
        "records",
        "root_commit_sha",
        "tip_commit_sha",
        "evaluated_at_commit_sha",
        "governed_paths",
        "evidence",
    }
)
_LINEAGE_EVIDENCE_RECEIPT_KEYS = frozenset(
    {"schema_version", "path", "sha256", "bytes", "records"}
)
_LINEAGE_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "commit",
        "previous_change_sha",
        "git_tree_entries",
        "registry_transition",
        "ledger",
        "availability_receipt",
        "ledger_transition",
    }
)
_LINEAGE_COMMIT_KEYS = frozenset(
    {
        "sha",
        "request_url",
        "api_url",
        "author_login",
        "committer_login",
        "parent_shas",
        "verification",
        "raw_sha256",
        "raw_bytes",
    }
)
_LINEAGE_EVIDENCE_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "commit_sha",
        "raw_sha256",
        "raw_bytes",
        "encoding",
        "payload_base64",
    }
)
_LINEAGE_TREE_ENTRY_KEYS = frozenset({"mode", "type", "object_sha"})
_LINEAGE_REGISTRY_TRANSITION_KEYS = frozenset(
    {"state", "previous", "current", "added_source_indicators"}
)
_LINEAGE_REGISTRY_RECEIPT_KEYS = frozenset(
    {"path", "schema_version", "sha256", "bytes", "series_records"}
)
_LINEAGE_LEDGER_KEYS = frozenset({"path", "sha256", "bytes", "records"})
_LINEAGE_AVAILABILITY_KEYS = frozenset(
    {"path", "schema_version", "sha256", "bytes", "generated_at"}
)
_LINEAGE_LEDGER_TRANSITION_KEYS = frozenset(
    {"state", "prefix_bytes", "appended_records", "receipt_appended_observations"}
)
_HANDOFF_KEYS = frozenset(
    {
        "schema_version",
        "producer",
        "producer_commit_evidence",
        "revision_lineage",
        "artifact",
        "input_ledger",
        "reviewed_availability_receipt",
        "live_verification",
        "live_raw_response",
        "files",
    }
)
_HANDOFF_PRODUCER_COMMIT_KEYS = frozenset(
    {
        "path",
        "sha",
        "request_url",
        "api_url",
        "author_login",
        "committer_login",
        "parent_shas",
        "verification",
        "sha256",
        "bytes",
    }
)
_HANDOFF_LINEAGE_KEYS = frozenset(
    {
        "mode",
        "chain",
        "cross_run_revision_authority",
        "live_check_new_vintages_appended",
    }
)
_HANDOFF_LIVE_VERIFICATION_KEYS = frozenset(
    {
        "path",
        "sha256",
        "bytes",
        "batch_raw_sha256",
        "current_availability_sha256",
    }
)
_HANDOFF_LIVE_RAW_KEYS = frozenset({"path", "sha256"})
_HANDOFF_FILE_KEYS = frozenset({"path", "sha256", "bytes"})

_CHECKSUM_SUBJECT_NAMES = frozenset(
    {
        "china-econ-wdi-latest.json",
        "china-econ-wdi-live-check.json",
        "china-econ-wdi-lineage-chain.jsonl",
        "china-econ-wdi-observations.jsonl",
        "china_econ_source_policy.json",
        "china_econ_wdi_series.json",
        "github-commit.json",
        "github-commit-lineage-evidence.jsonl",
        "handoff-receipt.json",
        "palimpsest-china-economic-export-v1.jsonl",
        "palimpsest-china-economic-export-v3-manifest.json",
        "world-bank-wdi-response.json",
    }
)
_HANDOFF_CORE_NAMES = _CHECKSUM_SUBJECT_NAMES - {"handoff-receipt.json"}


class PalimpsestChinaIntakeError(ValueError):
    """The offline export or its Seiche acceptance receipt failed closed."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _freeze_json(value: Any) -> Any:
    """Recursively freeze validated JSON before placing it in the process cache."""

    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(child) for child in value)
    return value


def _copy_json(value: Any) -> Any:
    """Return ordinary JSON containers from a recursively frozen value."""

    if isinstance(value, Mapping):
        return {key: _copy_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_copy_json(child) for child in value]
    return value


def _canonical_json_line(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PalimpsestChinaIntakeError("document is not canonical JSON data") from exc
    return encoded + b"\n"


def _ledger_json_line(value: object) -> bytes:
    """Encode Palimpsest's durable ledger wire format exactly."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PalimpsestChinaIntakeError("input ledger row is not JSON data") from exc
    return encoded + b"\n"


def _strict_json(raw: bytes, *, label: str) -> Any:
    if type(raw) is not bytes or not raw:
        raise PalimpsestChinaIntakeError(f"{label} is empty")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaIntakeError(f"{label} is not strict UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise PalimpsestChinaIntakeError(
            f"{label} contains non-finite JSON number {value}"
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise PalimpsestChinaIntakeError(
                    f"{label} contains duplicate JSON key {key!r}"
                )
            out[key] = value
        return out

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaIntakeError(f"{label} is not valid JSON") from exc


def _exact_keys(
    value: object, expected: frozenset[str], *, label: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise PalimpsestChinaIntakeError(f"{label} must be an object")
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise PalimpsestChinaIntakeError(
            f"{label} keys changed (missing={missing}, unknown={unknown})"
        )
    return value


def _required_string(value: object, *, label: str, maximum: int = 8192) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise PalimpsestChinaIntakeError(f"{label} must be a bounded non-empty string")
    return value


def _sha(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise PalimpsestChinaIntakeError(f"{label} must be lowercase SHA-256")
    return value


def _count(value: object, *, label: str, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PalimpsestChinaIntakeError(f"{label} must be an integer >= {minimum}")
    return value


def _git_sha(value: object, *, label: str) -> str:
    if type(value) is not str or _GIT_SHA_RE.fullmatch(value) is None:
        raise PalimpsestChinaIntakeError(f"{label} must be a lowercase 40-hex Git SHA")
    return value


def _validate_producer(value: object) -> dict[str, Any]:
    """Validate the closed shape of Palimpsest's producer declaration.

    A null workflow run is deliberately valid only as offline review metadata.
    The signed acceptance boundary applies the stronger push-run requirement.
    These self-declared fields are not independent GitHub attestation; the
    offline owner must verify the run and bundle hashes before signing.
    """

    producer = _exact_keys(value, _PRODUCER_KEYS, label="manifest.producer")
    if producer["schema_version"] != PRODUCER_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"manifest.producer must use {PRODUCER_SCHEMA}"
        )
    if producer["repository"] != _PRODUCER_REPOSITORY:
        raise PalimpsestChinaIntakeError(
            "manifest.producer repository is not release-reviewed"
        )
    commit_sha = _git_sha(producer["commit_sha"], label="manifest.producer.commit_sha")
    workflow_value = producer["workflow_run"]
    if workflow_value is None:
        return dict(producer)

    workflow = _exact_keys(
        workflow_value,
        _PRODUCER_WORKFLOW_KEYS,
        label="manifest.producer.workflow_run",
    )
    if workflow["provider"] != "github_actions":
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run provider must be github_actions"
        )
    if workflow["workflow_file"] != _PRODUCER_WORKFLOW_FILE:
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run workflow is not release-reviewed"
        )
    run_id = _count(
        workflow["run_id"],
        label="manifest.producer.workflow_run.run_id",
        positive=True,
    )
    _count(
        workflow["run_attempt"],
        label="manifest.producer.workflow_run.run_attempt",
        positive=True,
    )
    head_sha = _git_sha(
        workflow["head_sha"], label="manifest.producer.workflow_run.head_sha"
    )
    if head_sha != commit_sha:
        raise PalimpsestChinaIntakeError(
            "manifest.producer workflow head SHA does not match producer commit"
        )
    if workflow["event"] not in {"pull_request", "push"}:
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run event is not reviewed"
        )
    if workflow["conclusion"] != "success":
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run conclusion must be success"
        )
    expected_url = f"https://github.com/{_PRODUCER_REPOSITORY}/actions/runs/{run_id}"
    if workflow["url"] != expected_url:
        raise PalimpsestChinaIntakeError(
            "manifest.producer.workflow_run URL is not canonical"
        )
    return {**producer, "workflow_run": dict(workflow)}


def _normalize_github_commit_evidence(
    raw: bytes,
    *,
    expected_sha: str,
    accepted_at: datetime,
    label: str,
) -> dict[str, Any]:
    """Reparse one bounded raw GitHub commit response as a reviewed merge."""

    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > MAX_PRODUCER_COMMIT_EVIDENCE_BYTES
    ):
        raise PalimpsestChinaIntakeError(f"{label} is empty or too large")
    value = _strict_json(raw, label=label)
    if type(value) is not dict:
        raise PalimpsestChinaIntakeError(f"{label} must be an object")
    commit_sha = _git_sha(value.get("sha"), label=f"{label}.sha")
    if commit_sha != expected_sha:
        raise PalimpsestChinaIntakeError(
            f"{label} does not match the manifest producer or lineage commitment"
        )
    api_url = (
        f"https://api.github.com/repos/{_PRODUCER_REPOSITORY}/commits/{commit_sha}"
    )
    request_url = f"{api_url}?per_page=1"
    if value.get("url") != api_url:
        raise PalimpsestChinaIntakeError(f"{label} API URL is not canonical")

    author = value.get("author")
    committer = value.get("committer")
    if (
        not isinstance(author, dict)
        or author.get("login") != _PRODUCER_AUTHOR_LOGIN
        or not isinstance(committer, dict)
        or committer.get("login") != _PRODUCER_COMMITTER_LOGIN
    ):
        raise PalimpsestChinaIntakeError(
            f"{label} GitHub author or committer identity is not reviewed"
        )

    parents = value.get("parents")
    if not isinstance(parents, list) or not 2 <= len(parents) <= 64:
        raise PalimpsestChinaIntakeError(
            f"{label} must be a bounded merge with at least two parents"
        )
    parent_shas: list[str] = []
    for position, parent in enumerate(parents, 1):
        if not isinstance(parent, dict):
            raise PalimpsestChinaIntakeError(f"{label} parent {position} is malformed")
        parent_sha = _git_sha(parent.get("sha"), label=f"{label} parent {position}.sha")
        expected_parent_url = (
            f"https://api.github.com/repos/{_PRODUCER_REPOSITORY}/commits/{parent_sha}"
        )
        if parent.get("url") != expected_parent_url:
            raise PalimpsestChinaIntakeError(
                f"{label} parent {position} URL is not canonical"
            )
        parent_shas.append(parent_sha)
    if len(parent_shas) != len(set(parent_shas)):
        raise PalimpsestChinaIntakeError(f"{label} parent SHAs must be unique")

    commit = value.get("commit")
    verification = commit.get("verification") if isinstance(commit, dict) else None
    if not isinstance(verification, dict):
        raise PalimpsestChinaIntakeError(f"{label} verification evidence is missing")
    verified_at_text, verified_at = _canonical_timestamp(
        verification.get("verified_at"),
        label=f"{label}.verification.verified_at",
    )
    if (
        verification.get("verified") is not True
        or verification.get("reason") != "valid"
        or not isinstance(verification.get("signature"), str)
        or not verification["signature"].strip()
        or not isinstance(verification.get("payload"), str)
        or not verification["payload"].strip()
    ):
        raise PalimpsestChinaIntakeError(f"{label} GitHub verification is not valid")
    if verified_at > accepted_at:
        raise PalimpsestChinaIntakeError(
            f"{label} verification clock follows Seiche acceptance"
        )

    return {
        "sha": commit_sha,
        "request_url": request_url,
        "api_url": api_url,
        "author_login": _PRODUCER_AUTHOR_LOGIN,
        "committer_login": _PRODUCER_COMMITTER_LOGIN,
        "parent_shas": parent_shas,
        "verification": {
            "verified": True,
            "reason": "valid",
            "verified_at": verified_at_text,
        },
    }


def _validate_producer_commit_evidence(
    raw: bytes,
    *,
    producer: Mapping[str, Any],
    accepted_at: datetime,
) -> dict[str, Any]:
    """Reparse raw GitHub commit JSON into the signed acceptance receipt."""

    if producer.get("repository") != _PRODUCER_REPOSITORY:
        raise PalimpsestChinaIntakeError(
            "producer commit evidence repository is not reviewed"
        )
    normalized = _normalize_github_commit_evidence(
        raw,
        expected_sha=_git_sha(
            producer.get("commit_sha"), label="manifest.producer.commit_sha"
        ),
        accepted_at=accepted_at,
        label="producer commit evidence",
    )
    return {
        "path": "github-commit.json",
        "request_url": normalized["request_url"],
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "sha": normalized["sha"],
        "author_login": normalized["author_login"],
        "committer_login": normalized["committer_login"],
        "parent_shas": normalized["parent_shas"],
        "verification": normalized["verification"],
    }


def _validate_embedded_producer_commit_evidence(
    value: object,
) -> dict[str, Any]:
    evidence = _exact_keys(
        value,
        _PRODUCER_COMMIT_EVIDENCE_KEYS,
        label="acceptance.producer_commit_evidence",
    )
    if evidence["path"] != "github-commit.json":
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit evidence path changed"
        )
    sha = _git_sha(evidence["sha"], label="acceptance.producer_commit_evidence.sha")
    expected_url = (
        f"https://api.github.com/repos/{_PRODUCER_REPOSITORY}/commits/{sha}?per_page=1"
    )
    if evidence["request_url"] != expected_url:
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit request URL is not canonical"
        )
    _sha(
        evidence["sha256"],
        label="acceptance.producer_commit_evidence.sha256",
    )
    size = _count(
        evidence["bytes"],
        label="acceptance.producer_commit_evidence.bytes",
        positive=True,
    )
    if size > MAX_PRODUCER_COMMIT_EVIDENCE_BYTES:
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit evidence is too large"
        )
    if (
        evidence["author_login"] != _PRODUCER_AUTHOR_LOGIN
        or evidence["committer_login"] != _PRODUCER_COMMITTER_LOGIN
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit identity is not reviewed"
        )
    parent_shas = evidence["parent_shas"]
    if (
        not isinstance(parent_shas, list)
        or not 2 <= len(parent_shas) <= 64
        or len(parent_shas) != len(set(parent_shas))
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit parent SHAs are invalid"
        )
    for position, parent_sha in enumerate(parent_shas, 1):
        _git_sha(
            parent_sha,
            label=f"acceptance.producer_commit_evidence.parent_shas[{position}]",
        )
    verification = _exact_keys(
        evidence["verification"],
        _PRODUCER_COMMIT_VERIFICATION_KEYS,
        label="acceptance.producer_commit_evidence.verification",
    )
    if verification["verified"] is not True or verification["reason"] != "valid":
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit verification is not valid"
        )
    _canonical_timestamp(
        verification["verified_at"],
        label="acceptance.producer_commit_evidence.verification.verified_at",
    )
    return dict(evidence)


def _validate_producer_main_evidence(
    raw: bytes,
    *,
    producer: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Normalize one exact GitHub ``branches/main`` response for signing."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_PRODUCER_MAIN_EVIDENCE_BYTES:
        raise PalimpsestChinaIntakeError("producer main evidence is empty or too large")
    value = _strict_json(raw, label="producer main evidence")
    if type(value) is not dict or value.get("name") != "main":
        raise PalimpsestChinaIntakeError(
            "producer main evidence must describe the main branch"
        )
    commit = value.get("commit")
    if not isinstance(commit, dict):
        raise PalimpsestChinaIntakeError("producer main evidence commit is missing")
    commit_sha = _git_sha(commit.get("sha"), label="producer main evidence.commit.sha")
    if (
        producer.get("repository") != _PRODUCER_REPOSITORY
        or producer.get("commit_sha") != commit_sha
    ):
        raise PalimpsestChinaIntakeError(
            "producer main evidence does not match the manifest producer"
        )
    expected_commit_url = (
        f"https://api.github.com/repos/{_PRODUCER_REPOSITORY}/commits/{commit_sha}"
    )
    if commit.get("url") != expected_commit_url:
        raise PalimpsestChinaIntakeError(
            "producer main evidence commit URL is not canonical"
        )
    protected = value.get("protected")
    if type(protected) is not bool:
        raise PalimpsestChinaIntakeError(
            "producer main evidence protected state must be boolean"
        )
    observed_text = observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    request_url = f"https://api.github.com/repos/{_PRODUCER_REPOSITORY}/branches/main"
    return {
        "path": "github-main-branch.json",
        "request_url": request_url,
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "observed_at": observed_text,
        "name": "main",
        "commit": {"sha": commit_sha},
        "protected": protected,
    }


def _validate_embedded_producer_main_evidence(value: object) -> dict[str, Any]:
    evidence = _exact_keys(
        value,
        _PRODUCER_MAIN_EVIDENCE_KEYS,
        label="acceptance.producer_main_evidence",
    )
    if evidence["path"] != "github-main-branch.json":
        raise PalimpsestChinaIntakeError(
            "acceptance producer main evidence path changed"
        )
    expected_url = f"https://api.github.com/repos/{_PRODUCER_REPOSITORY}/branches/main"
    if evidence["request_url"] != expected_url:
        raise PalimpsestChinaIntakeError(
            "acceptance producer main request URL is not canonical"
        )
    _sha(evidence["sha256"], label="acceptance.producer_main_evidence.sha256")
    size = _count(
        evidence["bytes"],
        label="acceptance.producer_main_evidence.bytes",
        positive=True,
    )
    if size > MAX_PRODUCER_MAIN_EVIDENCE_BYTES:
        raise PalimpsestChinaIntakeError(
            "acceptance producer main evidence is too large"
        )
    _canonical_timestamp(
        evidence["observed_at"],
        label="acceptance.producer_main_evidence.observed_at",
    )
    if evidence["name"] != "main" or type(evidence["protected"]) is not bool:
        raise PalimpsestChinaIntakeError(
            "acceptance producer main branch state is invalid"
        )
    commit = _exact_keys(
        evidence["commit"],
        _PRODUCER_MAIN_COMMIT_KEYS,
        label="acceptance.producer_main_evidence.commit",
    )
    _git_sha(commit["sha"], label="acceptance.producer_main_evidence.commit.sha")
    return dict(evidence)


def _validate_embedded_handoff_receipt(value: object) -> dict[str, Any]:
    receipt = _exact_keys(
        value, _ACCEPTANCE_HANDOFF_KEYS, label="acceptance.handoff_receipt"
    )
    if (
        receipt["path"] != "handoff-receipt.json"
        or receipt["schema_version"] != HANDOFF_SCHEMA
    ):
        raise PalimpsestChinaIntakeError("acceptance handoff receipt contract changed")
    _sha(receipt["sha256"], label="acceptance.handoff_receipt.sha256")
    size = _count(
        receipt["bytes"], label="acceptance.handoff_receipt.bytes", positive=True
    )
    if size > MAX_HANDOFF_BYTES:
        raise PalimpsestChinaIntakeError("acceptance handoff receipt is too large")
    return dict(receipt)


def _validate_embedded_checksum_subject(value: object) -> dict[str, Any]:
    receipt = _exact_keys(
        value, _ACCEPTANCE_CHECKSUM_KEYS, label="acceptance.checksum_subject"
    )
    if receipt["path"] != "SHA256SUMS":
        raise PalimpsestChinaIntakeError("acceptance checksum subject path changed")
    _sha(receipt["sha256"], label="acceptance.checksum_subject.sha256")
    size = _count(
        receipt["bytes"], label="acceptance.checksum_subject.bytes", positive=True
    )
    records = _count(
        receipt["records"], label="acceptance.checksum_subject.records", positive=True
    )
    if size > MAX_CHECKSUMS_BYTES or records != len(_CHECKSUM_SUBJECT_NAMES):
        raise PalimpsestChinaIntakeError("acceptance checksum subject bounds changed")
    return dict(receipt)


def _validate_embedded_lineage_receipt(value: object) -> dict[str, Any]:
    receipt = _exact_keys(
        value, _LINEAGE_CHAIN_RECEIPT_KEYS, label="acceptance.governed_lineage"
    )
    if (
        receipt["schema_version"] != LINEAGE_CHAIN_SCHEMA
        or receipt["path"] != LINEAGE_CHAIN_PATH
        or receipt["governed_paths"]
        != [WDI_REGISTRY_PATH, WDI_LEDGER_PATH, WDI_AVAILABILITY_PATH]
    ):
        raise PalimpsestChinaIntakeError("acceptance governed lineage contract changed")
    _sha(receipt["sha256"], label="acceptance.governed_lineage.sha256")
    size = _count(
        receipt["bytes"], label="acceptance.governed_lineage.bytes", positive=True
    )
    records = _count(
        receipt["records"],
        label="acceptance.governed_lineage.records",
        positive=True,
    )
    if size > MAX_LINEAGE_CHAIN_BYTES or records > MAX_LINEAGE_RECORDS:
        raise PalimpsestChinaIntakeError("acceptance governed lineage bounds changed")
    _git_sha(
        receipt["root_commit_sha"],
        label="acceptance.governed_lineage.root_commit_sha",
    )
    _git_sha(
        receipt["tip_commit_sha"],
        label="acceptance.governed_lineage.tip_commit_sha",
    )
    _git_sha(
        receipt["evaluated_at_commit_sha"],
        label="acceptance.governed_lineage.evaluated_at_commit_sha",
    )
    evidence = _exact_keys(
        receipt["evidence"],
        _LINEAGE_EVIDENCE_RECEIPT_KEYS,
        label="acceptance.governed_lineage.evidence",
    )
    if (
        evidence["schema_version"] != LINEAGE_EVIDENCE_SCHEMA
        or evidence["path"] != LINEAGE_EVIDENCE_PATH
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance governed lineage evidence contract changed"
        )
    _sha(
        evidence["sha256"],
        label="acceptance.governed_lineage.evidence.sha256",
    )
    evidence_size = _count(
        evidence["bytes"],
        label="acceptance.governed_lineage.evidence.bytes",
        positive=True,
    )
    evidence_records = _count(
        evidence["records"],
        label="acceptance.governed_lineage.evidence.records",
        positive=True,
    )
    if evidence_size > MAX_LINEAGE_EVIDENCE_BYTES or evidence_records != records:
        raise PalimpsestChinaIntakeError(
            "acceptance governed lineage evidence bounds changed"
        )
    return dict(receipt)


def _validate_embedded_operator_confirmations(value: object) -> dict[str, Any]:
    confirmations = _exact_keys(
        value,
        _OPERATOR_CONFIRMATION_KEYS,
        label="acceptance.operator_confirmations",
    )
    for key in _OPERATOR_CONFIRMATION_INPUT_KEYS:
        if confirmations[key] is not True:
            raise PalimpsestChinaIntakeError(
                f"acceptance operator confirmation {key} is not true"
            )
    for key in (
        "github_attestation_subject_sha256",
        "checksum_subject_sha256",
        "producer_commit_evidence_sha256",
        "lineage_chain_sha256",
        "lineage_evidence_sha256",
        "producer_main_evidence_sha256",
        "manifest_sha256",
        "availability_receipt_sha256",
    ):
        _sha(confirmations[key], label=f"acceptance.operator_confirmations.{key}")
    _git_sha(
        confirmations["lineage_evaluated_at_commit_sha"],
        label="acceptance.operator_confirmations.lineage_evaluated_at_commit_sha",
    )
    _canonical_timestamp(
        confirmations["rights_expires_at"],
        label="acceptance.operator_confirmations.rights_expires_at",
    )
    return dict(confirmations)


def _timestamp(value: object, *, label: str) -> datetime:
    text = _required_string(value, label=label, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PalimpsestChinaIntakeError(f"{label} must be timezone-aware")
    if parsed.utcoffset().total_seconds() != 0:
        raise PalimpsestChinaIntakeError(f"{label} must use UTC")
    return parsed.astimezone(UTC)


def _canonical_timestamp(value: object, *, label: str) -> tuple[str, datetime]:
    parsed = _timestamp(value, label=label)
    normalized = parsed.isoformat().replace("+00:00", "Z")
    if value != normalized:
        raise PalimpsestChinaIntakeError(f"{label} must be canonical UTC")
    return normalized, parsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _date(value: object, *, label: str) -> date:
    text = _required_string(value, label=label, maximum=10)
    if len(text) != 10:
        raise PalimpsestChinaIntakeError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(f"{label} must be an ISO date") from exc


def _https(value: object, *, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _required_string(value, label=label)
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(f"{label} must be an HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise PalimpsestChinaIntakeError(f"{label} must be a credential-free HTTPS URL")
    return text


def _safe_relative_path(value: object, *, label: str, suffix: str | None = None) -> str:
    text = _required_string(value, label=label, maximum=512)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PalimpsestChinaIntakeError(f"{label} must be a safe relative path")
    if suffix is not None and not text.endswith(suffix):
        raise PalimpsestChinaIntakeError(f"{label} must end in {suffix}")
    return text


def _stat_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stable_read(
    path: str | Path,
    *,
    label: str,
    maximum: int,
    expected_identity: tuple[str, int, int, int, int, int] | None = None,
) -> bytes:
    selected = Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(selected, flags)
    except OSError as exc:
        raise PalimpsestChinaIntakeError(f"cannot safely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PalimpsestChinaIntakeError(
                f"{label} must be a single-link regular file"
            )
        before_fingerprint = _stat_fingerprint(before)
        if (
            expected_identity is not None
            and before_fingerprint != expected_identity[1:]
        ):
            raise PalimpsestChinaIntakeError(
                f"{label} identity changed before it was read"
            )
        if before.st_size <= 0 or before.st_size > maximum:
            raise PalimpsestChinaIntakeError(
                f"{label} is empty or exceeds {maximum} bytes"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or len(payload) > maximum
            or before_fingerprint != _stat_fingerprint(after)
        ):
            raise PalimpsestChinaIntakeError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class PalimpsestChinaObservation:
    """One validated WDI context row plus its reviewed channel mapping."""

    record: Mapping[str, Any]
    market_channels: tuple[str, ...]

    @property
    def series_id(self) -> str:
        return str(self.record["series_id"])

    @property
    def released_at(self) -> datetime:
        return _timestamp(self.record["released_at"], label="observation.released_at")

    @property
    def collected_at(self) -> datetime:
        return _timestamp(self.record["collected_at"], label="observation.collected_at")

    @property
    def period_end(self) -> date:
        return _date(self.record["period_end"], label="observation.period_end")

    def public_record(self, *, accepted_at: str) -> dict[str, Any]:
        """Return the exact upstream row with Seiche's clock kept separate."""

        return {
            **_copy_json(self.record),
            "market_channels": list(self.market_channels),
            "accepted_at": accepted_at,
            "freshness": {
                "native_cadence": "annual",
                "classification": "structural",
                "state": "annual_structural",
                "is_live_market_data": False,
                "clock": "collected_at",
            },
        }


@dataclass(frozen=True, slots=True)
class PalimpsestChinaEconomicContext:
    """Verified context projection for one exact Palimpsest export."""

    accepted_at: str
    manifest_schema_version: str
    manifest_sha256: str
    producer: Mapping[str, Any] | None
    producer_commit_evidence: Mapping[str, Any] | None
    producer_main_evidence: Mapping[str, Any] | None
    handoff_receipt: Mapping[str, Any] | None
    checksum_subject: Mapping[str, Any] | None
    governed_lineage: Mapping[str, Any] | None
    operator_confirmations: Mapping[str, Any] | None
    artifact_sha256: str
    artifact_bytes: int
    input_ledger_sha256: str
    availability_receipt_sha256: str | None
    availability_batch_raw_sha256: str | None
    current_numeric_identities_sha256: str | None
    current_projectable_series_sha256: str | None
    current_projectable_source_indicators_sha256: str | None
    withdrawn_numeric_identities_sha256: str | None
    policy_sha256: str
    series_registry_sha256: str
    acceptance_sha256: str | None
    acceptance_signer_key_id: str | None
    source_decision: Mapping[str, Any]
    observations: tuple[PalimpsestChinaObservation, ...]
    current_observations: tuple[PalimpsestChinaObservation, ...]
    _acceptance_authority: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def owner_attested(self) -> bool:
        """Whether the trusted signed-acceptance loader authorized serving."""

        return self._acceptance_authority is _OWNER_ATTESTED_AUTHORITY

    def to_dict(self) -> dict[str, Any]:
        current = list(self.current_observations)
        channel_families: dict[str, Any] = {}
        for channel in MARKET_CHANNELS:
            available = [row for row in current if channel in row.market_channels]
            featured_ids = featured_series(
                channel, (row.series_id for row in available)
            )
            by_series = {row.series_id: row for row in available}
            rows = [
                by_series[series_id].public_record(accepted_at=self.accepted_at)
                for series_id in featured_ids
            ]
            channel_families[channel] = {
                "family": channel,
                "context_only": True,
                "scoring_eligible": False,
                "gauge_eligible": False,
                "native_cadence": "annual",
                "interpretation": "structural",
                "series_count": len(available),
                "returned_observation_count": len(rows),
                "observations_truncated": len(rows) < len(available),
                "featured_series": list(featured_ids),
                "observations": rows,
            }

        latest_release = max(row.released_at for row in self.observations)
        latest_collection = max(row.collected_at for row in self.observations)
        latest_period = max(row.period_end for row in self.observations)
        decision = self.source_decision
        return {
            "schema": CONTEXT_SCHEMA,
            "status": "structural",
            "evidence_status": "observed",
            "available": True,
            "context_only": True,
            "scoring_eligible": False,
            "cn_cny_gauge_eligible": False,
            "market_observation_eligible": False,
            "source_id": "world_bank_wdi",
            "source_registry_ids": ["world_bank_wdi"],
            "observation_count": len(self.observations),
            "current_series_count": len(current),
            "clocks": {
                "latest_observation_period_end": latest_period.isoformat(),
                "latest_source_released_at": latest_release.isoformat().replace(
                    "+00:00", "Z"
                ),
                "latest_palimpsest_collected_at": latest_collection.isoformat().replace(
                    "+00:00", "Z"
                ),
                "seiche_accepted_at": self.accepted_at,
            },
            "freshness": {
                "native_cadence": "annual",
                "classification": "structural",
                "state": "annual_structural",
                "is_live_market_data": False,
                "advances_world_observation_clocks": False,
                "advances_cn_cny_freshness": False,
            },
            "rights": {
                "decision": "allowed",
                "decision_sha256": decision["decision_sha256"],
                "license": decision["license"],
                "license_url": decision["license_url"],
                "rights_evidence_url": decision["rights_evidence_url"],
                "attribution": decision["attribution"],
                "reviewed_at": decision["reviewed_at"],
                "expires_at": decision["expires_at"],
            },
            "provenance": {
                "producer": (
                    _copy_json(self.producer) if self.producer is not None else None
                ),
                "producer_commit_evidence": (
                    _copy_json(self.producer_commit_evidence)
                    if self.producer_commit_evidence is not None
                    else None
                ),
                "producer_main_evidence": (
                    _copy_json(self.producer_main_evidence)
                    if self.producer_main_evidence is not None
                    else None
                ),
                "handoff_receipt": (
                    _copy_json(self.handoff_receipt)
                    if self.handoff_receipt is not None
                    else None
                ),
                "checksum_subject": (
                    _copy_json(self.checksum_subject)
                    if self.checksum_subject is not None
                    else None
                ),
                "governed_lineage": (
                    _copy_json(self.governed_lineage)
                    if self.governed_lineage is not None
                    else None
                ),
                "operator_confirmations": (
                    _copy_json(self.operator_confirmations)
                    if self.operator_confirmations is not None
                    else None
                ),
                "manifest_sha256": self.manifest_sha256,
                "artifact_sha256": self.artifact_sha256,
                "artifact_bytes": self.artifact_bytes,
                "input_ledger_sha256": self.input_ledger_sha256,
                "availability_receipt_sha256": self.availability_receipt_sha256,
                "availability_batch_raw_sha256": self.availability_batch_raw_sha256,
                "current_numeric_identities_sha256": (
                    self.current_numeric_identities_sha256
                ),
                "current_projectable_series_sha256": (
                    self.current_projectable_series_sha256
                ),
                "current_projectable_source_indicators_sha256": (
                    self.current_projectable_source_indicators_sha256
                ),
                "withdrawn_numeric_identities_sha256": (
                    self.withdrawn_numeric_identities_sha256
                ),
                "policy_sha256": self.policy_sha256,
                "series_registry_sha256": self.series_registry_sha256,
                "acceptance_sha256": self.acceptance_sha256,
                "acceptance_signer_key_id": self.acceptance_signer_key_id,
                "owner_attestation": ("ed25519" if self.owner_attested else None),
            },
            "channel_families": channel_families,
            "boundaries": [
                "Annual WDI observations are structural context, not live China money-market data.",
                "Palimpsest release and collection clocks remain distinct from Seiche accepted_at.",
                "The export cannot enter a Seiche score, market pack, CN-CNY gauge, forecast, or trading signal.",
                "World Bank transport is not independent confirmation of every upstream statistical source.",
            ],
        }


def _validate_wdi_url(value: object, *, source_series_id: str) -> str:
    text = _https(value, label="observation.evidence_url")
    assert text is not None
    parsed = urlsplit(text)
    if parsed.hostname != "api.worldbank.org" or parsed.port not in {None, 443}:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi evidence_url must use the reviewed API host"
        )
    prefix = "/v2/country/CHN/indicator/"
    if not parsed.path.startswith(prefix):
        raise PalimpsestChinaIntakeError("world_bank_wdi evidence_url path changed")
    indicators = parsed.path[len(prefix) :].split(";")
    if source_series_id not in indicators:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi evidence_url does not bind the source series"
        )
    query = parse_qs(parsed.query, strict_parsing=True)
    if set(query) != {"source", "date", "format", "per_page", "footnote"}:
        raise PalimpsestChinaIntakeError("world_bank_wdi evidence_url query changed")
    if (
        query["source"] != ["2"]
        or query["format"] != ["json"]
        or query["per_page"] != ["20000"]
        or query["footnote"] != ["y"]
        or len(query["date"]) != 1
        or re.fullmatch(r"\d{4}:\d{4}", query["date"][0]) is None
    ):
        raise PalimpsestChinaIntakeError("world_bank_wdi evidence_url query changed")
    return text


def _validate_observation(
    value: object,
    *,
    channels: tuple[str, ...],
    accepted_at: datetime,
    position: int,
) -> PalimpsestChinaObservation:
    row = _exact_keys(value, _OBSERVATION_KEYS, label=f"observation {position}")
    series_id = row["series_id"]
    if type(series_id) is not str or _SERIES_ID_RE.fullmatch(series_id) is None:
        raise PalimpsestChinaIntakeError(
            f"observation {position} has a non-WDI series_id"
        )
    source_id = row["source_id"]
    if source_id not in ALLOWED_SOURCE_IDS:
        raise PalimpsestChinaIntakeError(
            f"observation {position} source {source_id!r} is not allowlisted"
        )
    if any(
        marker in str(source_id).lower() for marker in _FORBIDDEN_VALUE_SOURCE_MARKERS
    ):
        raise PalimpsestChinaIntakeError(
            f"observation {position} contains prohibited CFETS/ChinaMoney values"
        )

    if row["frequency"] != "A":
        raise PalimpsestChinaIntakeError("world_bank_wdi observations must be annual")
    if row["geography"] != "CN" or any(
        row[name] != "all" for name in ("sector", "firm_size", "ownership")
    ):
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi observations must be aggregate China rows"
        )
    if row["status"] != "estimate":
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi observations must retain estimate status"
        )
    for name in ("unit",):
        _required_string(row[name], label=f"observation.{name}", maximum=256)
    if isinstance(row["value"], bool) or not isinstance(row["value"], (int, float)):
        raise PalimpsestChinaIntakeError("observation.value must be numeric")
    if not math.isfinite(float(row["value"])):
        raise PalimpsestChinaIntakeError("observation.value must be finite")
    if isinstance(row["quality"], bool) or not isinstance(row["quality"], (int, float)):
        raise PalimpsestChinaIntakeError("observation.quality must be numeric")
    quality = float(row["quality"])
    if not math.isfinite(quality) or not 0 <= quality <= 1:
        raise PalimpsestChinaIntakeError("observation.quality must lie in [0, 1]")
    revision = _count(row["revision"], label="observation.revision")
    raw_sha256 = _sha(row["raw_sha256"], label="observation.raw_sha256")

    period_start = _date(row["period_start"], label="observation.period_start")
    period_end = _date(row["period_end"], label="observation.period_end")
    if period_start != date(period_start.year, 1, 1) or period_end != date(
        period_start.year, 12, 31
    ):
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi periods must be complete calendar years"
        )
    released_at = _timestamp(row["released_at"], label="observation.released_at")
    collected_at = _timestamp(row["collected_at"], label="observation.collected_at")
    if released_at > collected_at:
        raise PalimpsestChinaIntakeError(
            "observation released_at cannot follow collected_at"
        )
    if collected_at > accepted_at:
        raise PalimpsestChinaIntakeError(
            "observation collected_at cannot follow Seiche accepted_at"
        )

    metadata = _exact_keys(
        row["metadata"], _WDI_METADATA_KEYS, label=f"observation {position}.metadata"
    )
    source_series_id = metadata["source_series_id"]
    if (
        type(source_series_id) is not str
        or _SOURCE_SERIES_ID_RE.fullmatch(source_series_id) is None
    ):
        raise PalimpsestChinaIntakeError("invalid WDI source_series_id")
    if metadata != {
        "family": "wdi_officially_recognized_sources",
        "source_series_id": source_series_id,
        "source_document_version": metadata["source_document_version"],
        "parser_version": "world-bank-wdi-json.v1",
        "release_time_semantics": "dataset_lastupdated_upper_bound",
        "aggregation_window": "calendar_year",
    }:
        raise PalimpsestChinaIntakeError("world_bank_wdi metadata contract changed")
    _date(
        metadata["source_document_version"],
        label="observation.metadata.source_document_version",
    )
    _validate_wdi_url(row["evidence_url"], source_series_id=source_series_id)

    supplied_id = _sha(row["observation_id"], label="observation.observation_id")
    canonical_record = dict(row)
    canonical_record.pop("observation_id")
    # Palimpsest's EconomicObservation normalizes all real values to float and
    # integral revisions to int before hashing its canonical payload.
    canonical_record["value"] = float(row["value"])
    canonical_record["quality"] = quality
    canonical_record["revision"] = revision
    canonical_record["raw_sha256"] = raw_sha256
    computed_id = hashlib.sha256(
        json.dumps(
            canonical_record,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if supplied_id != computed_id:
        raise PalimpsestChinaIntakeError(
            f"observation {position} observation_id does not match record contents"
        )
    normalized = dict(canonical_record)
    normalized["observation_id"] = supplied_id
    return PalimpsestChinaObservation(
        record=_freeze_json(normalized), market_channels=channels
    )


def _source_policy_decision_digest(row: Mapping[str, Any]) -> str:
    """Recompute Palimpsest's digest over the configured policy decision."""

    effective = row["decision"]
    if effective not in {"allowed", "denied"}:
        raise PalimpsestChinaIntakeError(
            "only currently effective policy decisions may be accepted"
        )
    payload = {
        "source_id": row["source_id"],
        "decision": "allow" if effective == "allowed" else "deny",
        "values_allowed": row["values_allowed"],
        "seiche_export_allowed": row["seiche_export_allowed"],
        "license": row["license"],
        "license_url": row["license_url"],
        "rights_evidence_url": row["rights_evidence_url"],
        "attribution": row["attribution"],
        "reviewed_at": row["reviewed_at"],
        "expires_at": row["expires_at"],
        "reason": row["reason"],
    }
    return _sha256(_canonical_json_line(payload))


def _validate_source_decisions(
    value: object,
    *,
    artifact_records: int,
    input_ledger_records: int,
    generated_at: datetime,
) -> Mapping[str, Any]:
    if type(value) is not list or not value or len(value) > 256:
        raise PalimpsestChinaIntakeError("source_decisions must be a bounded list")
    seen: set[str] = set()
    source_order: list[str] = []
    allowed: Mapping[str, Any] | None = None
    input_total = 0
    exported_total = 0
    for position, candidate in enumerate(value, 1):
        row = _exact_keys(
            candidate, _SOURCE_DECISION_KEYS, label=f"source decision {position}"
        )
        source_id = _required_string(
            row["source_id"], label=f"source decision {position}.source_id", maximum=128
        )
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise PalimpsestChinaIntakeError(
                f"source decision {position}.source_id is invalid"
            )
        if source_id in seen:
            raise PalimpsestChinaIntakeError(f"duplicate source decision {source_id}")
        seen.add(source_id)
        source_order.append(source_id)
        decision = row["decision"]
        if decision not in {"allowed", "denied", "expired", "unknown"}:
            raise PalimpsestChinaIntakeError(f"invalid source decision for {source_id}")
        if (
            type(row["values_allowed"]) is not bool
            or type(row["seiche_export_allowed"]) is not bool
        ):
            raise PalimpsestChinaIntakeError(
                f"source decision booleans are invalid for {source_id}"
            )
        inputs = _count(row["input_records"], label=f"{source_id}.input_records")
        exported = _count(
            row["exported_records"], label=f"{source_id}.exported_records"
        )
        if exported > inputs:
            raise PalimpsestChinaIntakeError(
                f"source decision {source_id} exports more records than it received"
            )
        input_total += inputs
        exported_total += exported
        _required_string(row["reason"], label=f"source decision {source_id}.reason")

        reviewed_at: datetime | None = None
        expires_at: datetime | None = None
        if decision == "unknown":
            if any(
                row[key] is not None
                for key in (
                    "decision_sha256",
                    "license",
                    "license_url",
                    "rights_evidence_url",
                    "attribution",
                    "reviewed_at",
                    "expires_at",
                )
            ):
                raise PalimpsestChinaIntakeError(
                    f"unknown source decision {source_id} invents policy evidence"
                )
        else:
            supplied_decision_sha = _sha(
                row["decision_sha256"], label=f"{source_id}.decision_sha256"
            )
            reviewed_at = _timestamp(
                row["reviewed_at"], label=f"source decision {source_id}.reviewed_at"
            )
            expires_at = _timestamp(
                row["expires_at"], label=f"source decision {source_id}.expires_at"
            )
            if reviewed_at > generated_at:
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} was reviewed after export"
                )
            if decision in {"allowed", "denied"} and expires_at <= generated_at:
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} has an incorrect effective state"
                )
            if decision == "expired" and expires_at > generated_at:
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} has an incorrect effective state"
                )
            for key in ("license", "attribution"):
                if row[key] is not None:
                    _required_string(row[key], label=f"{source_id}.{key}")
            for key in ("license_url", "rights_evidence_url"):
                _https(row[key], label=f"{source_id}.{key}", nullable=True)
            if decision == "expired":
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} is expired and must be re-reviewed"
                )
            if supplied_decision_sha != _source_policy_decision_digest(row):
                raise PalimpsestChinaIntakeError(
                    f"source decision {source_id} digest does not match its fields"
                )

        is_code_allowed = source_id in ALLOWED_SOURCE_IDS
        if is_code_allowed:
            if (
                decision != "allowed"
                or row["values_allowed"] is not True
                or row["seiche_export_allowed"] is not True
                or expires_at is None
                or expires_at <= generated_at
                or row["decision_sha256"] is None
                or row["license"] != "CC-BY-4.0"
                or row["license_url"] != WDI_LICENSE_URL
                or row["rights_evidence_url"] != WDI_RIGHTS_EVIDENCE_URL
                or row["attribution"] != WDI_ATTRIBUTION
                or exported != artifact_records
            ):
                raise PalimpsestChinaIntakeError(
                    "world_bank_wdi is not covered by the reviewed allow decision"
                )
            allowed = dict(row)
        elif (
            decision == "allowed"
            or row["values_allowed"] is not False
            or row["seiche_export_allowed"] is not False
            or exported != 0
        ):
            marker = (
                "CFETS/ChinaMoney"
                if any(
                    token in source_id.lower()
                    for token in _FORBIDDEN_VALUE_SOURCE_MARKERS
                )
                else "unknown source"
            )
            raise PalimpsestChinaIntakeError(f"{marker} is not export-allowlisted")
    if source_order != sorted(source_order):
        raise PalimpsestChinaIntakeError("source decisions must be uniquely sorted")
    required_boundaries = {"world_bank_wdi", "cfets_benchmarks", "chinamoney"}
    if not required_boundaries.issubset(seen):
        raise PalimpsestChinaIntakeError(
            "source decisions omit WDI or the explicit CFETS/ChinaMoney boundary"
        )
    if allowed is None:
        raise PalimpsestChinaIntakeError("world_bank_wdi allow decision is missing")
    if input_total != input_ledger_records:
        raise PalimpsestChinaIntakeError(
            "source decision input_records do not match input ledger records"
        )
    if exported_total != artifact_records:
        raise PalimpsestChinaIntakeError(
            "source decision exported_records do not match artifact records"
        )
    return allowed


@dataclass(frozen=True, slots=True)
class _LedgerState:
    identities: frozenset[tuple[str, int]]
    source_to_series: Mapping[str, str]
    record_sha256_by_observation_id: Mapping[str, str]
    latest_observation_id_by_identity: Mapping[tuple[str, int], str]
    latest_collected_at: datetime
    sha256: str
    bytes: int
    records: int


@dataclass(frozen=True, slots=True)
class _AvailabilityState:
    receipt_sha256: str
    batch_raw_sha256: str
    current_identities: frozenset[tuple[str, int]]
    projectable_source_indicators: frozenset[str]
    projectable_series: frozenset[str]
    expected_artifact_identities: frozenset[tuple[str, int]]
    current_numeric_identities_sha256: str
    current_projectable_series_sha256: str
    current_projectable_source_indicators_sha256: str
    withdrawn_numeric_identities_sha256: str


@dataclass(frozen=True, slots=True)
class _LineageAuthority:
    handoff_sha256: str
    checksums_sha256: str
    chain_sha256: str
    evidence_sha256: str
    chain_receipt: Mapping[str, Any]


def _git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git SHA-1 ID


def _canonical_jsonl_rows(
    payload: bytes,
    *,
    label: str,
    maximum: int,
    expected_records: int,
) -> list[dict[str, Any]]:
    if (
        type(payload) is not bytes
        or not payload
        or len(payload) > maximum
        or not payload.endswith(b"\n")
        or b"\r" in payload
    ):
        raise PalimpsestChinaIntakeError(
            f"{label} is empty, oversized, or not LF-terminated"
        )
    lines = payload.splitlines(keepends=True)
    if len(lines) != expected_records or not 1 <= len(lines) <= MAX_LINEAGE_RECORDS:
        raise PalimpsestChinaIntakeError(f"{label} record count does not match")
    rows: list[dict[str, Any]] = []
    for position, line in enumerate(lines, 1):
        if line == b"\n" or not line.endswith(b"\n"):
            raise PalimpsestChinaIntakeError(
                f"{label} line {position} is blank or unterminated"
            )
        value = _strict_json(line[:-1], label=f"{label} line {position}")
        if type(value) is not dict or _canonical_json_line(value) != line:
            raise PalimpsestChinaIntakeError(
                f"{label} line {position} is not canonical JSON"
            )
        rows.append(value)
    return rows


def _lineage_registry_receipt(value: object, *, label: str) -> dict[str, Any]:
    receipt = _exact_keys(value, _LINEAGE_REGISTRY_RECEIPT_KEYS, label=label)
    if (
        receipt["path"] != WDI_REGISTRY_PATH
        or receipt["schema_version"] != SERIES_REGISTRY_SCHEMA
    ):
        raise PalimpsestChinaIntakeError(f"{label} registry contract changed")
    _sha(receipt["sha256"], label=f"{label}.sha256")
    _count(receipt["bytes"], label=f"{label}.bytes", positive=True)
    records = _count(
        receipt["series_records"], label=f"{label}.series_records", positive=True
    )
    if records > MAX_SERIES:
        raise PalimpsestChinaIntakeError(f"{label} exceeds the reviewed series bound")
    return dict(receipt)


def _lineage_ledger_receipt(value: object, *, label: str) -> dict[str, Any]:
    receipt = _exact_keys(value, _LINEAGE_LEDGER_KEYS, label=label)
    if receipt["path"] != WDI_LEDGER_PATH:
        raise PalimpsestChinaIntakeError(f"{label} path changed")
    _sha(receipt["sha256"], label=f"{label}.sha256")
    _count(receipt["bytes"], label=f"{label}.bytes", positive=True)
    records = _count(receipt["records"], label=f"{label}.records", positive=True)
    if records > MAX_RECORDS:
        raise PalimpsestChinaIntakeError(f"{label} exceeds the ledger row bound")
    return dict(receipt)


def _lineage_availability_receipt(value: object, *, label: str) -> dict[str, Any]:
    receipt = _exact_keys(value, _LINEAGE_AVAILABILITY_KEYS, label=label)
    if (
        receipt["path"] != WDI_AVAILABILITY_PATH
        or receipt["schema_version"] != AVAILABILITY_RECEIPT_SCHEMA
    ):
        raise PalimpsestChinaIntakeError(f"{label} contract changed")
    _sha(receipt["sha256"], label=f"{label}.sha256")
    _count(receipt["bytes"], label=f"{label}.bytes", positive=True)
    _canonical_timestamp(receipt["generated_at"], label=f"{label}.generated_at")
    return dict(receipt)


def _validate_lineage_evidence(
    evidence_bytes: bytes,
    *,
    receipt_value: object,
    accepted_at: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = _exact_keys(
        receipt_value,
        _LINEAGE_EVIDENCE_RECEIPT_KEYS,
        label="handoff.revision_lineage.chain.evidence",
    )
    if (
        receipt["schema_version"] != LINEAGE_EVIDENCE_SCHEMA
        or receipt["path"] != LINEAGE_EVIDENCE_PATH
    ):
        raise PalimpsestChinaIntakeError("lineage evidence receipt contract changed")
    expected_sha = _sha(receipt["sha256"], label="lineage evidence.sha256")
    expected_bytes = _count(
        receipt["bytes"], label="lineage evidence.bytes", positive=True
    )
    expected_records = _count(
        receipt["records"], label="lineage evidence.records", positive=True
    )
    if (
        expected_sha != _sha256(evidence_bytes)
        or expected_bytes != len(evidence_bytes)
        or expected_records > MAX_LINEAGE_RECORDS
    ):
        raise PalimpsestChinaIntakeError(
            "lineage evidence hash, bytes, or record commitment does not match"
        )
    rows = _canonical_jsonl_rows(
        evidence_bytes,
        label="lineage evidence",
        maximum=MAX_LINEAGE_EVIDENCE_BYTES,
        expected_records=expected_records,
    )
    normalized: list[dict[str, Any]] = []
    seen_commits: set[str] = set()
    for sequence, candidate in enumerate(rows):
        row = _exact_keys(
            candidate,
            _LINEAGE_EVIDENCE_RECORD_KEYS,
            label=f"lineage evidence row {sequence}",
        )
        if (
            row["schema_version"] != LINEAGE_EVIDENCE_RECORD_SCHEMA
            or _count(
                row["sequence"], label=f"lineage evidence row {sequence}.sequence"
            )
            != sequence
            or row["encoding"] != "base64"
        ):
            raise PalimpsestChinaIntakeError(
                f"lineage evidence row {sequence} contract changed"
            )
        commit_sha = _git_sha(
            row["commit_sha"], label=f"lineage evidence row {sequence}.commit_sha"
        )
        if commit_sha in seen_commits:
            raise PalimpsestChinaIntakeError(
                "lineage evidence repeats a governed commit"
            )
        seen_commits.add(commit_sha)
        raw_sha = _sha(
            row["raw_sha256"], label=f"lineage evidence row {sequence}.raw_sha256"
        )
        raw_bytes = _count(
            row["raw_bytes"],
            label=f"lineage evidence row {sequence}.raw_bytes",
            positive=True,
        )
        if raw_bytes > MAX_PRODUCER_COMMIT_EVIDENCE_BYTES:
            raise PalimpsestChinaIntakeError(
                "lineage evidence contains an oversized GitHub response"
            )
        encoded = row["payload_base64"]
        if (
            type(encoded) is not str
            or not encoded.isascii()
            or len(encoded) > 4 * ((raw_bytes + 2) // 3)
        ):
            raise PalimpsestChinaIntakeError(
                "lineage evidence base64 payload is malformed"
            )
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise PalimpsestChinaIntakeError(
                "lineage evidence base64 payload is malformed"
            ) from exc
        if (
            len(raw) != raw_bytes
            or _sha256(raw) != raw_sha
            or base64.b64encode(raw).decode("ascii") != encoded
        ):
            raise PalimpsestChinaIntakeError(
                "lineage evidence raw response commitment does not match"
            )
        commit = _normalize_github_commit_evidence(
            raw,
            expected_sha=commit_sha,
            accepted_at=accepted_at,
            label=f"lineage evidence commit {sequence}",
        )
        normalized.append(
            {
                "sequence": sequence,
                "commit": commit,
                "raw_sha256": raw_sha,
                "raw_bytes": raw_bytes,
                "raw": raw,
            }
        )
    return dict(receipt), normalized


def _validate_lineage_chain(
    chain_bytes: bytes,
    evidence_bytes: bytes,
    *,
    receipt_value: object,
    producer: Mapping[str, Any],
    manifest: Mapping[str, Any],
    input_ledger_bytes: bytes,
    availability_bytes: bytes,
    accepted_at: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = _exact_keys(
        receipt_value,
        _LINEAGE_CHAIN_RECEIPT_KEYS,
        label="handoff.revision_lineage.chain",
    )
    if (
        receipt["schema_version"] != LINEAGE_CHAIN_SCHEMA
        or receipt["path"] != LINEAGE_CHAIN_PATH
    ):
        raise PalimpsestChinaIntakeError("governed lineage chain contract changed")
    chain_sha = _sha(receipt["sha256"], label="lineage chain.sha256")
    chain_bytes_count = _count(
        receipt["bytes"], label="lineage chain.bytes", positive=True
    )
    records = _count(receipt["records"], label="lineage chain.records", positive=True)
    if (
        chain_sha != _sha256(chain_bytes)
        or chain_bytes_count != len(chain_bytes)
        or records > MAX_LINEAGE_RECORDS
    ):
        raise PalimpsestChinaIntakeError(
            "lineage chain hash, bytes, or record commitment does not match"
        )
    root_sha = _git_sha(
        receipt["root_commit_sha"], label="lineage chain.root_commit_sha"
    )
    tip_sha = _git_sha(receipt["tip_commit_sha"], label="lineage chain.tip_commit_sha")
    producer_sha = _git_sha(
        producer.get("commit_sha"), label="manifest.producer.commit_sha"
    )
    if receipt["evaluated_at_commit_sha"] != producer_sha or receipt[
        "governed_paths"
    ] != [WDI_REGISTRY_PATH, WDI_LEDGER_PATH, WDI_AVAILABILITY_PATH]:
        raise PalimpsestChinaIntakeError(
            "lineage evaluation commit or governed paths changed"
        )
    evidence_receipt, evidence = _validate_lineage_evidence(
        evidence_bytes,
        receipt_value=receipt["evidence"],
        accepted_at=accepted_at,
    )
    if evidence_receipt["records"] != records:
        raise PalimpsestChinaIntakeError(
            "lineage chain and evidence record counts differ"
        )
    rows = _canonical_jsonl_rows(
        chain_bytes,
        label="lineage chain",
        maximum=MAX_LINEAGE_CHAIN_BYTES,
        expected_records=records,
    )

    previous_sha: str | None = None
    previous_registry: dict[str, Any] | None = None
    previous_ledger: dict[str, Any] | None = None
    previous_availability: dict[str, Any] | None = None
    for sequence, candidate in enumerate(rows):
        row = _exact_keys(
            candidate,
            _LINEAGE_RECORD_KEYS,
            label=f"lineage chain row {sequence}",
        )
        if (
            row["schema_version"] != LINEAGE_RECORD_SCHEMA
            or _count(row["sequence"], label=f"lineage chain row {sequence}.sequence")
            != sequence
            or row["previous_change_sha"] != previous_sha
        ):
            raise PalimpsestChinaIntakeError(
                f"lineage chain row {sequence} sequence or predecessor changed"
            )
        commit = _exact_keys(
            row["commit"],
            _LINEAGE_COMMIT_KEYS,
            label=f"lineage chain row {sequence}.commit",
        )
        evidence_row = evidence[sequence]
        expected_commit = {
            **evidence_row["commit"],
            "raw_sha256": evidence_row["raw_sha256"],
            "raw_bytes": evidence_row["raw_bytes"],
        }
        if commit != expected_commit:
            raise PalimpsestChinaIntakeError(
                f"lineage chain row {sequence} does not match raw commit evidence"
            )
        commit_sha = str(commit["sha"])

        tree = _exact_keys(
            row["git_tree_entries"],
            frozenset({WDI_REGISTRY_PATH, WDI_LEDGER_PATH, WDI_AVAILABILITY_PATH}),
            label=f"lineage chain row {sequence}.git_tree_entries",
        )
        normalized_tree: dict[str, dict[str, Any]] = {}
        for path in (WDI_REGISTRY_PATH, WDI_LEDGER_PATH, WDI_AVAILABILITY_PATH):
            entry = _exact_keys(
                tree[path],
                _LINEAGE_TREE_ENTRY_KEYS,
                label=f"lineage chain row {sequence}.git_tree_entries.{path}",
            )
            if entry["mode"] != "100644" or entry["type"] != "blob":
                raise PalimpsestChinaIntakeError(
                    f"lineage chain row {sequence} contains a non-regular Git object"
                )
            _git_sha(
                entry["object_sha"],
                label=f"lineage chain row {sequence}.git_tree_entries.{path}.object_sha",
            )
            normalized_tree[path] = dict(entry)

        registry_transition = _exact_keys(
            row["registry_transition"],
            _LINEAGE_REGISTRY_TRANSITION_KEYS,
            label=f"lineage chain row {sequence}.registry_transition",
        )
        current_registry = _lineage_registry_receipt(
            registry_transition["current"],
            label=f"lineage chain row {sequence}.registry_transition.current",
        )
        added = registry_transition["added_source_indicators"]
        if (
            type(added) is not list
            or added != sorted(set(added))
            or any(
                type(indicator) is not str
                or _SOURCE_SERIES_ID_RE.fullmatch(indicator) is None
                for indicator in added
            )
        ):
            raise PalimpsestChinaIntakeError(
                f"lineage chain row {sequence} added indicators are invalid"
            )

        ledger = _lineage_ledger_receipt(
            row["ledger"], label=f"lineage chain row {sequence}.ledger"
        )
        availability = _lineage_availability_receipt(
            row["availability_receipt"],
            label=f"lineage chain row {sequence}.availability_receipt",
        )
        transition = _exact_keys(
            row["ledger_transition"],
            _LINEAGE_LEDGER_TRANSITION_KEYS,
            label=f"lineage chain row {sequence}.ledger_transition",
        )
        prefix_bytes = _count(
            transition["prefix_bytes"],
            label=f"lineage chain row {sequence}.ledger_transition.prefix_bytes",
        )
        appended = _count(
            transition["appended_records"],
            label=f"lineage chain row {sequence}.ledger_transition.appended_records",
        )
        receipt_appended = _count(
            transition["receipt_appended_observations"],
            label=(
                f"lineage chain row {sequence}.ledger_transition."
                "receipt_appended_observations"
            ),
        )
        if appended != receipt_appended:
            raise PalimpsestChinaIntakeError(
                f"lineage chain row {sequence} append receipts disagree"
            )

        if sequence == 0:
            if (
                registry_transition["state"] != "initial_registry"
                or registry_transition["previous"] is not None
                or len(added) != current_registry["series_records"]
                or transition["state"] != "initial_seed"
                or prefix_bytes != 0
                or appended != ledger["records"]
            ):
                raise PalimpsestChinaIntakeError(
                    "lineage genesis is not an exact initial registry and ledger seed"
                )
        else:
            assert previous_registry is not None
            assert previous_ledger is not None
            assert previous_availability is not None
            prior = _lineage_registry_receipt(
                registry_transition["previous"],
                label=f"lineage chain row {sequence}.registry_transition.previous",
            )
            registry_delta = (
                current_registry["series_records"] - previous_registry["series_records"]
            )
            ledger_delta = ledger["records"] - previous_ledger["records"]
            registry_state = registry_transition["state"]
            if (
                prior != previous_registry
                or registry_delta < 0
                or registry_delta != len(added)
                or (
                    registry_state == "unchanged"
                    and (added or current_registry != previous_registry)
                )
                or (
                    registry_state == "append_only_addition"
                    and (not added or registry_delta < 1)
                )
                or registry_state not in {"unchanged", "append_only_addition"}
            ):
                raise PalimpsestChinaIntakeError(
                    f"lineage chain row {sequence} registry transition is not append-only"
                )
            if (
                ledger_delta < 0
                or prefix_bytes != previous_ledger["bytes"]
                or appended != ledger_delta
                or (
                    transition["state"] == "unchanged"
                    and (appended != 0 or ledger != previous_ledger)
                )
                or (
                    transition["state"] == "reviewed_prefix_extension"
                    and (appended < 1 or ledger["bytes"] <= previous_ledger["bytes"])
                )
                or transition["state"] not in {"unchanged", "reviewed_prefix_extension"}
            ):
                raise PalimpsestChinaIntakeError(
                    f"lineage chain row {sequence} ledger transition is not append-only"
                )
            if _timestamp(
                availability["generated_at"],
                label=f"lineage chain row {sequence}.availability.generated_at",
            ) < _timestamp(
                previous_availability["generated_at"],
                label=f"lineage chain row {sequence - 1}.availability.generated_at",
            ):
                raise PalimpsestChinaIntakeError(
                    "lineage availability clock moved backward"
                )
            if (
                current_registry == previous_registry
                and ledger == previous_ledger
                and availability == previous_availability
            ):
                raise PalimpsestChinaIntakeError(
                    "lineage includes a row with no governed-path change"
                )

        if sequence == records - 1:
            if normalized_tree[WDI_LEDGER_PATH]["object_sha"] != _git_blob_oid(
                input_ledger_bytes
            ) or normalized_tree[WDI_AVAILABILITY_PATH]["object_sha"] != _git_blob_oid(
                availability_bytes
            ):
                raise PalimpsestChinaIntakeError(
                    "lineage tip Git blobs do not match the supplied governed bytes"
                )
        previous_sha = commit_sha
        previous_registry = current_registry
        previous_ledger = ledger
        previous_availability = availability

    assert previous_registry is not None
    assert previous_ledger is not None
    assert previous_availability is not None
    if root_sha != rows[0]["commit"]["sha"] or tip_sha != previous_sha:
        raise PalimpsestChinaIntakeError(
            "lineage root or tip commitment does not match its rows"
        )
    manifest_registry = manifest["series_registry"]
    manifest_ledger = manifest["input_ledger"]
    manifest_availability = manifest["availability_receipt"]
    if (
        {key: previous_registry[key] for key in ("sha256", "bytes", "schema_version")}
        != {
            key: manifest_registry[key] for key in ("sha256", "bytes", "schema_version")
        }
        or {key: previous_ledger[key] for key in ("sha256", "bytes", "records")}
        != {key: manifest_ledger[key] for key in ("sha256", "bytes", "records")}
        or {
            key: previous_availability[key]
            for key in ("sha256", "bytes", "schema_version", "generated_at")
        }
        != {
            key: manifest_availability[key]
            for key in ("sha256", "bytes", "schema_version", "generated_at")
        }
    ):
        raise PalimpsestChinaIntakeError(
            "lineage tip does not match the exact export governed receipts"
        )
    return dict(receipt), evidence


def _validate_checksum_subject(
    checksums_bytes: bytes,
    *,
    expected_hashes: Mapping[str, str],
) -> dict[str, str]:
    if (
        type(checksums_bytes) is not bytes
        or not checksums_bytes
        or len(checksums_bytes) > MAX_CHECKSUMS_BYTES
        or not checksums_bytes.endswith(b"\n")
        or b"\r" in checksums_bytes
    ):
        raise PalimpsestChinaIntakeError(
            "SHA256SUMS is empty, oversized, or not LF-terminated"
        )
    try:
        lines = checksums_bytes.decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaIntakeError("SHA256SUMS is not ASCII") from exc
    parsed: dict[str, str] = {}
    ordered_names: list[str] = []
    for position, line in enumerate(lines, 1):
        if len(line) < 67 or line[64:66] != " *":
            raise PalimpsestChinaIntakeError(f"SHA256SUMS line {position} is malformed")
        digest = line[:64]
        name = line[66:]
        if (
            _SHA256_RE.fullmatch(digest) is None
            or not name
            or PurePosixPath(name).name != name
            or name in parsed
        ):
            raise PalimpsestChinaIntakeError(
                f"SHA256SUMS line {position} has an unsafe or duplicate subject"
            )
        parsed[name] = digest
        ordered_names.append(name)
    if (
        frozenset(parsed) != _CHECKSUM_SUBJECT_NAMES
        or ordered_names != sorted(ordered_names)
        or parsed != dict(expected_hashes)
    ):
        raise PalimpsestChinaIntakeError(
            "SHA256SUMS exact subject set or digest commitments changed"
        )
    return parsed


def _validate_handoff_authority(
    *,
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    input_ledger_bytes: bytes,
    availability_bytes: bytes,
    producer_commit_evidence_bytes: bytes,
    handoff_bytes: bytes,
    checksums_bytes: bytes,
    lineage_chain_bytes: bytes,
    lineage_evidence_bytes: bytes,
    accepted_at: datetime,
) -> _LineageAuthority:
    if not handoff_bytes or len(handoff_bytes) > MAX_HANDOFF_BYTES:
        raise PalimpsestChinaIntakeError(
            "Palimpsest handoff receipt is empty or too large"
        )
    handoff = _strict_json(handoff_bytes, label="Palimpsest handoff receipt")
    if type(handoff) is not dict or _canonical_json_line(handoff) != handoff_bytes:
        raise PalimpsestChinaIntakeError(
            "Palimpsest handoff receipt must use exact canonical JSON bytes"
        )
    handoff = _exact_keys(handoff, _HANDOFF_KEYS, label="Palimpsest handoff receipt")
    if handoff["schema_version"] != HANDOFF_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"Palimpsest handoff receipt must use {HANDOFF_SCHEMA}"
        )
    manifest = _strict_json(manifest_bytes, label="manifest")
    assert type(manifest) is dict
    producer = _validate_producer(manifest["producer"])
    if handoff["producer"] != producer:
        raise PalimpsestChinaIntakeError(
            "Palimpsest handoff producer does not match the manifest"
        )

    normalized_commit = _normalize_github_commit_evidence(
        producer_commit_evidence_bytes,
        expected_sha=_git_sha(
            producer["commit_sha"], label="manifest.producer.commit_sha"
        ),
        accepted_at=accepted_at,
        label="handoff producer commit evidence",
    )
    handoff_commit = _exact_keys(
        handoff["producer_commit_evidence"],
        _HANDOFF_PRODUCER_COMMIT_KEYS,
        label="handoff.producer_commit_evidence",
    )
    expected_commit = {
        "path": "github-commit.json",
        **normalized_commit,
        "sha256": _sha256(producer_commit_evidence_bytes),
        "bytes": len(producer_commit_evidence_bytes),
    }
    if handoff_commit != expected_commit:
        raise PalimpsestChinaIntakeError(
            "handoff producer identity does not match the exact raw GitHub response"
        )

    lineage = _exact_keys(
        handoff["revision_lineage"],
        _HANDOFF_LINEAGE_KEYS,
        label="handoff.revision_lineage",
    )
    if (
        lineage["mode"] != HANDOFF_LINEAGE_MODE
        or lineage["cross_run_revision_authority"] is not True
        or _count(
            lineage["live_check_new_vintages_appended"],
            label="handoff.revision_lineage.live_check_new_vintages_appended",
        )
        != 0
    ):
        raise PalimpsestChinaIntakeError(
            "handoff does not confer exact cross-run revision authority"
        )
    chain_receipt, lineage_evidence = _validate_lineage_chain(
        lineage_chain_bytes,
        lineage_evidence_bytes,
        receipt_value=lineage["chain"],
        producer=producer,
        manifest=manifest,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
    )
    if (
        chain_receipt["tip_commit_sha"] == producer["commit_sha"]
        and lineage_evidence[-1]["raw"] != producer_commit_evidence_bytes
    ):
        raise PalimpsestChinaIntakeError(
            "producer commit evidence differs from the matching lineage tip evidence"
        )

    if (
        handoff["artifact"] != manifest["artifact"]
        or handoff["input_ledger"] != manifest["input_ledger"]
        or handoff["reviewed_availability_receipt"] != manifest["availability_receipt"]
    ):
        raise PalimpsestChinaIntakeError(
            "handoff export receipts do not match the exact manifest"
        )
    live = _exact_keys(
        handoff["live_verification"],
        _HANDOFF_LIVE_VERIFICATION_KEYS,
        label="handoff.live_verification",
    )
    live_raw = _exact_keys(
        handoff["live_raw_response"],
        _HANDOFF_LIVE_RAW_KEYS,
        label="handoff.live_raw_response",
    )
    reviewed_availability = _strict_json(
        availability_bytes, label="reviewed availability receipt"
    )
    assert type(reviewed_availability) is dict
    expected_current_availability_sha = _sha256(
        _canonical_json_line(reviewed_availability["availability"])
    )
    if (
        live["path"] != "china-econ-wdi-live-check.json"
        or _sha(live["sha256"], label="handoff.live_verification.sha256")
        != live["sha256"]
        or _count(live["bytes"], label="handoff.live_verification.bytes", positive=True)
        != live["bytes"]
        or _sha(
            live["batch_raw_sha256"],
            label="handoff.live_verification.batch_raw_sha256",
        )
        != live["batch_raw_sha256"]
        or live["current_availability_sha256"] != expected_current_availability_sha
        or live_raw
        != {
            "path": "world-bank-wdi-response.json",
            "sha256": live["batch_raw_sha256"],
        }
    ):
        raise PalimpsestChinaIntakeError(
            "handoff live current-response verification does not reconcile"
        )

    files = handoff["files"]
    if type(files) is not list or len(files) != len(_HANDOFF_CORE_NAMES):
        raise PalimpsestChinaIntakeError("handoff exact file receipt set changed")
    file_receipts: dict[str, dict[str, Any]] = {}
    ordered_names: list[str] = []
    for position, candidate in enumerate(files, 1):
        receipt = _exact_keys(
            candidate,
            _HANDOFF_FILE_KEYS,
            label=f"handoff.files[{position}]",
        )
        path = receipt["path"]
        if (
            type(path) is not str
            or PurePosixPath(path).name != path
            or path in file_receipts
        ):
            raise PalimpsestChinaIntakeError(
                "handoff file receipt path is unsafe or duplicated"
            )
        _sha(receipt["sha256"], label=f"handoff.files[{position}].sha256")
        _count(
            receipt["bytes"],
            label=f"handoff.files[{position}].bytes",
            positive=True,
        )
        file_receipts[path] = dict(receipt)
        ordered_names.append(path)
    if frozenset(file_receipts) != _HANDOFF_CORE_NAMES or ordered_names != sorted(
        ordered_names
    ):
        raise PalimpsestChinaIntakeError(
            "handoff file receipts are not the exact sorted subject set"
        )

    exact_supplied = {
        "palimpsest-china-economic-export-v3-manifest.json": manifest_bytes,
        "palimpsest-china-economic-export-v1.jsonl": artifact_bytes,
        "china-econ-wdi-observations.jsonl": input_ledger_bytes,
        "china-econ-wdi-latest.json": availability_bytes,
        "github-commit.json": producer_commit_evidence_bytes,
        LINEAGE_CHAIN_PATH: lineage_chain_bytes,
        LINEAGE_EVIDENCE_PATH: lineage_evidence_bytes,
    }
    if (
        PurePosixPath(manifest["artifact"]["path"]).name
        != "palimpsest-china-economic-export-v1.jsonl"
        or PurePosixPath(manifest["input_ledger"]["path"]).name
        != "china-econ-wdi-observations.jsonl"
        or PurePosixPath(manifest["availability_receipt"]["path"]).name
        != "china-econ-wdi-latest.json"
    ):
        raise PalimpsestChinaIntakeError(
            "manifest handoff filenames do not match the reviewed subject set"
        )
    for name, body in exact_supplied.items():
        if file_receipts[name] != {
            "path": name,
            "sha256": _sha256(body),
            "bytes": len(body),
        }:
            raise PalimpsestChinaIntakeError(
                f"handoff file receipt does not match exact supplied {name} bytes"
            )
    manifest_receipts = {
        "china_econ_source_policy.json": manifest["policy"],
        "china_econ_wdi_series.json": manifest["series_registry"],
    }
    for name, manifest_receipt in manifest_receipts.items():
        file_receipt = file_receipts[name]
        if file_receipt["sha256"] != manifest_receipt["sha256"] or (
            "bytes" in manifest_receipt
            and file_receipt["bytes"] != manifest_receipt["bytes"]
        ):
            raise PalimpsestChinaIntakeError(
                f"handoff {name} receipt does not match the manifest"
            )
    if (
        file_receipts["china-econ-wdi-live-check.json"]
        != {"path": live["path"], "sha256": live["sha256"], "bytes": live["bytes"]}
        or file_receipts["world-bank-wdi-response.json"]["sha256"] != live_raw["sha256"]
    ):
        raise PalimpsestChinaIntakeError("handoff live receipt files do not reconcile")

    handoff_sha = _sha256(handoff_bytes)
    checksum_hashes = {
        name: receipt["sha256"] for name, receipt in file_receipts.items()
    }
    checksum_hashes["handoff-receipt.json"] = handoff_sha
    _validate_checksum_subject(checksums_bytes, expected_hashes=checksum_hashes)
    return _LineageAuthority(
        handoff_sha256=handoff_sha,
        checksums_sha256=_sha256(checksums_bytes),
        chain_sha256=_sha256(lineage_chain_bytes),
        evidence_sha256=_sha256(lineage_evidence_bytes),
        chain_receipt=MappingProxyType(dict(chain_receipt)),
    )


def _identity_digest(identities: set[tuple[str, int]]) -> str:
    body = b"".join(
        _canonical_json_line({"indicator_id": indicator_id, "year": year})
        for indicator_id, year in sorted(identities)
    )
    return _sha256(body)


def _source_indicator_digest(indicators: set[str]) -> str:
    body = b"".join(
        _canonical_json_line({"indicator_id": indicator_id})
        for indicator_id in sorted(indicators)
    )
    return _sha256(body)


def _series_digest(series_ids: set[str]) -> str:
    body = b"".join(
        _canonical_json_line({"series_id": series_id})
        for series_id in sorted(series_ids)
    )
    return _sha256(body)


def _ledger_snapshot(value: object, *, label: str) -> dict[str, Any]:
    snapshot = _exact_keys(value, _LEDGER_SNAPSHOT_KEYS, label=label)
    digest = _sha(snapshot["sha256"], label=f"{label}.sha256")
    byte_size = _count(snapshot["bytes"], label=f"{label}.bytes")
    records = _count(snapshot["records"], label=f"{label}.records")
    if (records == 0) != (byte_size == 0):
        raise PalimpsestChinaIntakeError(
            f"{label} empty byte and record counts do not reconcile"
        )
    if records == 0 and digest != _sha256(b""):
        raise PalimpsestChinaIntakeError(
            f"{label} empty snapshot digest does not reconcile"
        )
    return {"sha256": digest, "bytes": byte_size, "records": records}


def _canonical_wdi_request_url(
    indicators: set[str], *, start_year: int, end_year: int
) -> str:
    if not indicators:
        raise PalimpsestChinaIntakeError(
            "availability receipt represents no reviewed WDI indicators"
        )
    joined = ";".join(sorted(indicators))
    return (
        "https://api.worldbank.org/v2/country/CHN/indicator/"
        f"{joined}?source=2&date={start_year}%3A{end_year}&format=json&"
        "per_page=20000&footnote=y"
    )


def _bounded_utf8_string(value: object, *, label: str, maximum_bytes: int) -> str:
    text = _required_string(value, label=label, maximum=maximum_bytes)
    if len(text.encode("utf-8")) > maximum_bytes:
        raise PalimpsestChinaIntakeError(f"{label} must be a bounded non-empty string")
    return text


def _validate_input_ledger(
    ledger_bytes: bytes,
    *,
    receipt: Mapping[str, Any],
    accepted_at: datetime,
) -> _LedgerState:
    if not ledger_bytes or len(ledger_bytes) > MAX_INPUT_LEDGER_BYTES:
        raise PalimpsestChinaIntakeError("input ledger is empty or too large")
    expected_sha = _sha(receipt["sha256"], label="input_ledger.sha256")
    expected_bytes = _count(receipt["bytes"], label="input_ledger.bytes", positive=True)
    expected_records = _count(
        receipt["records"], label="input_ledger.records", positive=True
    )
    if expected_sha != _sha256(ledger_bytes) or expected_bytes != len(ledger_bytes):
        raise PalimpsestChinaIntakeError(
            "input ledger hash/bytes commitment does not match"
        )
    if not ledger_bytes.endswith(b"\n") or b"\r" in ledger_bytes:
        raise PalimpsestChinaIntakeError(
            "input ledger must be canonical LF-terminated JSONL"
        )
    lines = ledger_bytes.splitlines(keepends=True)
    if len(lines) != expected_records or len(lines) > MAX_RECORDS:
        raise PalimpsestChinaIntakeError(
            "input ledger records commitment does not match"
        )

    identities: set[tuple[str, int]] = set()
    observation_ids: set[str] = set()
    source_to_series: dict[str, str] = {}
    series_to_source: dict[str, str] = {}
    record_sha256_by_observation_id: dict[str, str] = {}
    latest_by_identity: dict[
        tuple[str, int], tuple[tuple[int, datetime, datetime, str], str]
    ] = {}
    previous_global_collection: datetime | None = None
    previous_by_identity: dict[tuple[str, int], tuple[float, int, datetime]] = {}
    for position, line in enumerate(lines, 1):
        if line == b"\n" or not line.endswith(b"\n"):
            raise PalimpsestChinaIntakeError(
                f"input ledger line {position} is blank or unterminated"
            )
        value = _strict_json(line[:-1], label=f"input ledger line {position}")
        if _ledger_json_line(value) != line:
            raise PalimpsestChinaIntakeError(
                f"input ledger line {position} does not use the durable wire format"
            )
        observation = _validate_observation(
            value,
            channels=(),
            accepted_at=accepted_at,
            position=position,
        )
        row = observation.record
        observation_id = str(row["observation_id"])
        if observation_id in observation_ids:
            raise PalimpsestChinaIntakeError(
                "input ledger observation_id values must be unique"
            )
        observation_ids.add(observation_id)
        collected_at = observation.collected_at
        if (
            previous_global_collection is not None
            and collected_at < previous_global_collection
        ):
            raise PalimpsestChinaIntakeError(
                "input ledger collected_at order moved backward"
            )
        previous_global_collection = collected_at

        source_series_id = str(row["metadata"]["source_series_id"])
        series_id = observation.series_id
        existing_series = source_to_series.setdefault(source_series_id, series_id)
        existing_source = series_to_source.setdefault(series_id, source_series_id)
        if existing_series != series_id or existing_source != source_series_id:
            raise PalimpsestChinaIntakeError(
                "input ledger source/internal series mapping is not one-to-one"
            )
        identity = (source_series_id, observation.period_end.year)
        identities.add(identity)
        numeric_value = float(row["value"])
        revision = int(row["revision"])
        released_at = observation.released_at
        previous = previous_by_identity.get(identity)
        if previous is None:
            if revision != 0:
                raise PalimpsestChinaIntakeError(
                    "input ledger first identity revision must be zero"
                )
        else:
            previous_value, previous_revision, previous_release = previous
            if released_at < previous_release:
                raise PalimpsestChinaIntakeError(
                    "input ledger release clock moved backward within an identity"
                )
            if numeric_value != previous_value:
                if revision != previous_revision + 1:
                    raise PalimpsestChinaIntakeError(
                        "input ledger value change did not increment revision"
                    )
            elif revision != previous_revision:
                raise PalimpsestChinaIntakeError(
                    "input ledger same-value provenance changed revision"
                )
        previous_by_identity[identity] = (numeric_value, revision, released_at)
        record_sha256_by_observation_id[observation_id] = _sha256(
            _canonical_json_line(value)
        )
        selection = (revision, released_at, collected_at, observation_id)
        selected = latest_by_identity.get(identity)
        if selected is None or selection > selected[0]:
            latest_by_identity[identity] = (selection, observation_id)

    assert previous_global_collection is not None
    return _LedgerState(
        identities=frozenset(identities),
        source_to_series=MappingProxyType(dict(source_to_series)),
        record_sha256_by_observation_id=MappingProxyType(
            record_sha256_by_observation_id
        ),
        latest_observation_id_by_identity=MappingProxyType(
            {identity: selected[1] for identity, selected in latest_by_identity.items()}
        ),
        latest_collected_at=previous_global_collection,
        sha256=expected_sha,
        bytes=len(ledger_bytes),
        records=len(lines),
    )


def _validate_availability(
    availability_bytes: bytes,
    *,
    receipt_value: object,
    manifest_generated_at: datetime,
    ledger: _LedgerState,
) -> _AvailabilityState:
    if not availability_bytes or len(availability_bytes) > MAX_AVAILABILITY_BYTES:
        raise PalimpsestChinaIntakeError("availability receipt is empty or too large")
    receipt = _exact_keys(
        receipt_value,
        _AVAILABILITY_RECEIPT_KEYS,
        label="manifest.availability_receipt",
    )
    path = _safe_relative_path(
        receipt["path"], label="availability_receipt.path", suffix=".json"
    )
    if PurePosixPath(path).name != "china-econ-wdi-latest.json":
        raise PalimpsestChinaIntakeError(
            "availability receipt path is not release-reviewed"
        )
    expected_sha = _sha(receipt["sha256"], label="availability_receipt.sha256")
    expected_bytes = _count(
        receipt["bytes"], label="availability_receipt.bytes", positive=True
    )
    if expected_sha != _sha256(availability_bytes) or expected_bytes != len(
        availability_bytes
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt hash/bytes commitment does not match"
        )
    if receipt["schema_version"] != AVAILABILITY_RECEIPT_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"availability receipt must use {AVAILABILITY_RECEIPT_SCHEMA}"
        )
    receipt_generated_text, receipt_generated_at = _canonical_timestamp(
        receipt["generated_at"], label="availability_receipt.generated_at"
    )
    if receipt_generated_at > manifest_generated_at:
        raise PalimpsestChinaIntakeError(
            "availability receipt was generated after the export manifest"
        )
    if ledger.latest_collected_at > receipt_generated_at:
        raise PalimpsestChinaIntakeError(
            "input ledger collection clock follows the availability receipt"
        )
    batch_raw_sha256 = _sha(
        receipt["batch_raw_sha256"], label="availability_receipt.batch_raw_sha256"
    )
    if receipt["availability_schema_version"] != AVAILABILITY_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"availability receipt must bind {AVAILABILITY_SCHEMA}"
        )

    run = _strict_json(availability_bytes, label="availability receipt")
    if _canonical_json_line(run) != availability_bytes:
        raise PalimpsestChinaIntakeError(
            "availability receipt must use exact canonical JSON bytes"
        )
    run = _exact_keys(run, _RUN_RECEIPT_KEYS, label="availability receipt")
    if (
        run["schema_version"] != AVAILABILITY_RECEIPT_SCHEMA
        or run["generated_at"] != receipt_generated_text
        or run["batch_raw_sha256"] != batch_raw_sha256
        or run["source_id"] != "world_bank_wdi"
        or run["dataset"] != WDI_DATASET
        or run["context_only"] is not True
        or run["scoring_allowed"] is not False
        or run["license"] != "CC-BY-4.0"
        or run["license_url"] != WDI_LICENSE_URL
        or run["rights_evidence_url"] != WDI_RIGHTS_EVIDENCE_URL
        or run["redistribution_status"] != "allowed"
        or run["publication_state"] != WDI_PUBLICATION_STATE
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt source, rights, clocks, or safety fields changed"
        )
    dataset_last_updated = _date(
        run["dataset_last_updated"],
        label="availability receipt.dataset_last_updated",
    )
    if dataset_last_updated > receipt_generated_at.date():
        raise PalimpsestChinaIntakeError(
            "availability receipt dataset clock follows its generation clock"
        )
    limitations = run["limitations"]
    if type(limitations) is not list or not 1 <= len(limitations) <= 32:
        raise PalimpsestChinaIntakeError(
            "availability receipt limitations must be a bounded non-empty list"
        )
    for position, limitation in enumerate(limitations, 1):
        _bounded_utf8_string(
            limitation,
            label=f"availability receipt.limitations[{position}]",
            maximum_bytes=8192,
        )
    if WDI_UPSTREAM_ATTRIBUTION_REQUIREMENT not in limitations:
        raise PalimpsestChinaIntakeError(
            "availability receipt omits the upstream attribution limitation"
        )

    lineage = _exact_keys(
        run["revision_lineage"],
        _REVISION_LINEAGE_KEYS,
        label="availability receipt.revision_lineage",
    )
    if lineage != {
        "mode": WDI_LINEAGE_MODE,
        "durable_cross_run": True,
        "ledger_path": WDI_LEDGER_PATH,
    }:
        raise PalimpsestChinaIntakeError(
            "public availability receipt requires the reviewed durable lineage"
        )

    ledger_before = _ledger_snapshot(
        run["ledger_before"], label="availability receipt.ledger_before"
    )
    ledger_after = _ledger_snapshot(
        run["ledger_after"], label="availability receipt.ledger_after"
    )
    appended = _count(
        run["appended_observations"],
        label="availability receipt.appended_observations",
    )
    if (
        ledger_after
        != {"sha256": ledger.sha256, "bytes": ledger.bytes, "records": ledger.records}
        or ledger_after["records"] != ledger_before["records"] + appended
        or ledger_after["bytes"] < ledger_before["bytes"]
        or (appended == 0 and ledger_before != ledger_after)
        or (appended > 0 and ledger_after["bytes"] == ledger_before["bytes"])
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt ledger transition does not match the exact input ledger"
        )

    availability = _exact_keys(
        run["availability"], _AVAILABILITY_KEYS, label="availability"
    )
    if (
        availability["schema_version"] != AVAILABILITY_SCHEMA
        or availability["coverage_semantics"] != "exact_current_response"
        or availability["withdrawal_state"]
        != "residual_gate_no_append_only_withdrawal_ledger"
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt current-response semantics changed"
        )
    _bounded_utf8_string(
        availability["withdrawal_limitation"],
        label="availability.withdrawal_limitation",
        maximum_bytes=8192,
    )
    entries = availability["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_RECORDS:
        raise PalimpsestChinaIntakeError("availability entries must be a bounded list")
    if _count(availability["records"], label="availability.records") != len(entries):
        raise PalimpsestChinaIntakeError("availability records count does not match")

    current_identities: set[tuple[str, int]] = set()
    ordered_identities: list[tuple[str, int]] = []
    represented_indicators: set[str] = set()
    null_records = 0
    for position, candidate in enumerate(entries, 1):
        entry = _exact_keys(
            candidate,
            _AVAILABILITY_ENTRY_KEYS,
            label=f"availability entry {position}",
        )
        indicator_id = entry["indicator_id"]
        if (
            type(indicator_id) is not str
            or _SOURCE_SERIES_ID_RE.fullmatch(indicator_id) is None
        ):
            raise PalimpsestChinaIntakeError(
                f"availability entry {position} indicator_id is invalid"
            )
        year = entry["year"]
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not 1900 <= year <= receipt_generated_at.year + 1
        ):
            raise PalimpsestChinaIntakeError(
                f"availability entry {position} year is invalid"
            )
        if type(entry["available"]) is not bool:
            raise PalimpsestChinaIntakeError(
                f"availability entry {position} available must be boolean"
            )
        footnote = entry["footnote"]
        if footnote is not None:
            _bounded_utf8_string(
                footnote,
                label=f"availability entry {position}.footnote",
                maximum_bytes=4096,
            )
        identity = (indicator_id, year)
        if ordered_identities and identity <= ordered_identities[-1]:
            raise PalimpsestChinaIntakeError(
                "availability entries must be uniquely sorted"
            )
        ordered_identities.append(identity)
        represented_indicators.add(indicator_id)
        if entry["available"]:
            current_identities.add(identity)
        else:
            null_records += 1
    if (
        _count(availability["null_records"], label="availability.null_records")
        != null_records
    ):
        raise PalimpsestChinaIntakeError(
            "availability null_records count does not match"
        )
    if not current_identities.issubset(ledger.identities):
        raise PalimpsestChinaIntakeError(
            "current numeric availability contains an identity absent from the ledger"
        )

    response_coverage = _exact_keys(
        run["response_coverage"],
        _RESPONSE_COVERAGE_KEYS,
        label="availability receipt.response_coverage",
    )
    start_year = _count(
        response_coverage["requested_start_year"],
        label="availability receipt.response_coverage.requested_start_year",
        positive=True,
    )
    end_year = _count(
        response_coverage["requested_end_year"],
        label="availability receipt.response_coverage.requested_end_year",
        positive=True,
    )
    if not 1900 <= start_year <= end_year <= receipt_generated_at.year + 1 or any(
        not start_year <= year <= end_year for _indicator, year in ordered_identities
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt response request range does not reconcile"
        )
    populated_indicators = {indicator_id for indicator_id, _year in current_identities}
    expected_response_start = (
        f"{min(year for _indicator, year in current_identities):04d}-01-01"
        if current_identities
        else None
    )
    expected_response_end = (
        f"{max(year for _indicator, year in current_identities):04d}-12-31"
        if current_identities
        else None
    )
    if (
        response_coverage["coverage_semantics"] != "exact_current_response"
        or _count(
            response_coverage["configured_indicators"],
            label="availability receipt.response_coverage.configured_indicators",
        )
        != len(represented_indicators)
        or _count(
            response_coverage["represented_indicators"],
            label="availability receipt.response_coverage.represented_indicators",
        )
        != len(represented_indicators)
        or _count(
            response_coverage["populated_indicators"],
            label="availability receipt.response_coverage.populated_indicators",
        )
        != len(populated_indicators)
        or _count(
            response_coverage["null_only_indicators"],
            label="availability receipt.response_coverage.null_only_indicators",
        )
        != len(represented_indicators - populated_indicators)
        or _count(
            response_coverage["source_rows"],
            label="availability receipt.response_coverage.source_rows",
        )
        != len(entries)
        or _count(
            response_coverage["populated_observations"],
            label="availability receipt.response_coverage.populated_observations",
        )
        != len(current_identities)
        or _count(
            response_coverage["null_rows"],
            label="availability receipt.response_coverage.null_rows",
        )
        != null_records
        or response_coverage["period_start"] != expected_response_start
        or response_coverage["period_end"] != expected_response_end
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt response coverage does not reconcile"
        )

    ledger_coverage = _exact_keys(
        run["ledger_coverage"],
        _LEDGER_COVERAGE_KEYS,
        label="availability receipt.ledger_coverage",
    )
    expected_ledger_start = (
        f"{min(year for _indicator, year in ledger.identities):04d}-01-01"
        if ledger.identities
        else None
    )
    expected_ledger_end = (
        f"{max(year for _indicator, year in ledger.identities):04d}-12-31"
        if ledger.identities
        else None
    )
    if (
        ledger_coverage["coverage_semantics"]
        != "accumulated_append_only_history_not_current_response"
        or _count(
            ledger_coverage["records"],
            label="availability receipt.ledger_coverage.records",
        )
        != ledger.records
        or _count(
            ledger_coverage["series_count"],
            label="availability receipt.ledger_coverage.series_count",
        )
        != len(ledger.source_to_series)
        or ledger_coverage["period_start"] != expected_ledger_start
        or ledger_coverage["period_end"] != expected_ledger_end
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt ledger coverage does not reconcile"
        )

    provenance = _exact_keys(
        run["indicator_provenance"],
        _INDICATOR_PROVENANCE_KEYS,
        label="availability receipt.indicator_provenance",
    )
    provenance_entries = provenance["entries"]
    if (
        provenance["schema_version"] != INDICATOR_PROVENANCE_SCHEMA
        or provenance["upstream_attribution_state"] != WDI_UPSTREAM_ATTRIBUTION_STATE
        or provenance["upstream_attribution_requirement"]
        != WDI_UPSTREAM_ATTRIBUTION_REQUIREMENT
        or type(provenance_entries) is not list
        or _count(
            provenance["records"],
            label="availability receipt.indicator_provenance.records",
        )
        != len(provenance_entries)
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt indicator provenance is invalid"
        )
    provenance_indicators: list[str] = []
    for position, candidate in enumerate(provenance_entries, 1):
        entry = _exact_keys(
            candidate,
            _INDICATOR_PROVENANCE_ENTRY_KEYS,
            label=f"availability receipt.indicator_provenance.entries[{position}]",
        )
        indicator_id = entry["indicator_id"]
        if (
            type(indicator_id) is not str
            or _SOURCE_SERIES_ID_RE.fullmatch(indicator_id) is None
        ):
            raise PalimpsestChinaIntakeError(
                "availability receipt indicator provenance ID is invalid"
            )
        _bounded_utf8_string(
            entry["reviewed_name"],
            label=f"indicator provenance entry {position}.reviewed_name",
            maximum_bytes=512,
        )
        _bounded_utf8_string(
            entry["source_title"],
            label=f"indicator provenance entry {position}.source_title",
            maximum_bytes=512,
        )
        provenance_indicators.append(indicator_id)
    if provenance_indicators != sorted(represented_indicators):
        raise PalimpsestChinaIntakeError(
            "availability receipt indicator provenance is not exact and sorted"
        )

    collector = _exact_keys(
        run["collector_artifact"],
        _COLLECTOR_ARTIFACT_KEYS,
        label="availability receipt.collector_artifact",
    )
    source_receipt = _exact_keys(
        collector["source_receipt"],
        _COLLECTOR_SOURCE_RECEIPT_KEYS,
        label="availability receipt.collector_artifact.source_receipt",
    )
    freshness = _exact_keys(
        collector["freshness"],
        _COLLECTOR_FRESHNESS_KEYS,
        label="availability receipt.collector_artifact.freshness",
    )
    payload = dict(run)
    payload.pop("collector_artifact")
    payload_sha256 = _sha256(_canonical_json_line(payload))
    dataset_age_days = (receipt_generated_at.date() - dataset_last_updated).days
    expected_evidence_state = "fresh" if dataset_age_days <= 120 else "stale"
    expected_request_url = _canonical_wdi_request_url(
        represented_indicators,
        start_year=start_year,
        end_year=end_year,
    )
    if (
        collector["schema_version"] != COLLECTOR_ARTIFACT_SCHEMA
        or collector["collector_id"] != WDI_COLLECTOR_ID
        or collector["abstention"] is not None
        or collector["coverage"] != response_coverage
        or _sha(
            collector["payload_sha256"],
            label="availability receipt.collector_artifact.payload_sha256",
        )
        != payload_sha256
        or source_receipt["url"] != expected_request_url
        or _sha(
            source_receipt["raw_sha256"],
            label="availability receipt.collector_artifact.source_receipt.raw_sha256",
        )
        != batch_raw_sha256
        or source_receipt["dataset_last_updated"] != run["dataset_last_updated"]
        or source_receipt["license"] != "CC-BY-4.0"
        or freshness["evidence_state"] != expected_evidence_state
        or freshness["observed_at"] != receipt_generated_text
        or freshness["native_cadence"] != "annual"
        or _count(
            freshness["dataset_age_days"],
            label="availability receipt.collector_artifact.freshness.dataset_age_days",
        )
        != dataset_age_days
    ):
        raise PalimpsestChinaIntakeError(
            "availability receipt collector artifact does not reconcile"
        )

    withdrawn = set(ledger.identities) - current_identities
    ledger_sources = set(ledger.source_to_series)
    withdrawn_sources = {indicator_id for indicator_id, _year in withdrawn}
    projectable_sources = ledger_sources - withdrawn_sources
    projectable_series = {
        ledger.source_to_series[source] for source in projectable_sources
    }
    expected_artifact_identities = {
        identity
        for identity in current_identities
        if identity[0] in projectable_sources
    }

    commitments: tuple[tuple[str, str, int, str], ...] = (
        (
            "current_numeric_identities",
            _identity_digest(current_identities),
            len(current_identities),
            "current numeric identities",
        ),
        (
            "current_projectable_series",
            _series_digest(projectable_series),
            len(projectable_series),
            "current projectable series",
        ),
        (
            "current_projectable_source_indicators",
            _source_indicator_digest(projectable_sources),
            len(projectable_sources),
            "current projectable source indicators",
        ),
        (
            "withdrawn_numeric_identities",
            _identity_digest(withdrawn),
            len(withdrawn),
            "withdrawn numeric identities",
        ),
    )
    computed: dict[str, str] = {}
    for prefix, digest, records, label in commitments:
        supplied_digest = _sha(
            receipt[f"{prefix}_sha256"],
            label=f"availability_receipt.{prefix}_sha256",
        )
        supplied_records = _count(
            receipt[f"{prefix}_records"],
            label=f"availability_receipt.{prefix}_records",
        )
        if supplied_digest != digest or supplied_records != records:
            raise PalimpsestChinaIntakeError(f"{label} commitment does not match")
        computed[prefix] = digest

    return _AvailabilityState(
        receipt_sha256=expected_sha,
        batch_raw_sha256=batch_raw_sha256,
        current_identities=frozenset(current_identities),
        projectable_source_indicators=frozenset(projectable_sources),
        projectable_series=frozenset(projectable_series),
        expected_artifact_identities=frozenset(expected_artifact_identities),
        current_numeric_identities_sha256=computed["current_numeric_identities"],
        current_projectable_series_sha256=computed["current_projectable_series"],
        current_projectable_source_indicators_sha256=computed[
            "current_projectable_source_indicators"
        ],
        withdrawn_numeric_identities_sha256=computed["withdrawn_numeric_identities"],
    )


def verify_export(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    *,
    input_ledger_bytes: bytes | None = None,
    availability_bytes: bytes | None = None,
    accepted_at: datetime,
) -> PalimpsestChinaEconomicContext:
    """Verify exact offline bytes and return a bounded context projection."""

    if (
        type(accepted_at) is not datetime
        or accepted_at.tzinfo is None
        or accepted_at.utcoffset() is None
    ):
        raise PalimpsestChinaIntakeError(
            "accepted_at must be a timezone-aware datetime"
        )
    accepted_at = accepted_at.astimezone(UTC)
    if not manifest_bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise PalimpsestChinaIntakeError("manifest is empty or too large")
    if not artifact_bytes or len(artifact_bytes) > MAX_ARTIFACT_BYTES:
        raise PalimpsestChinaIntakeError("artifact is empty or too large")

    manifest = _strict_json(manifest_bytes, label="manifest")
    if _canonical_json_line(manifest) != manifest_bytes:
        raise PalimpsestChinaIntakeError("manifest must use exact canonical JSON bytes")
    if type(manifest) is not dict:
        raise PalimpsestChinaIntakeError("manifest must be an object")
    manifest_schema = manifest.get("schema_version")
    if manifest_schema == MANIFEST_SCHEMA:
        manifest = _exact_keys(manifest, _MANIFEST_KEYS, label="manifest")
        producer = _validate_producer(manifest["producer"])
        if input_ledger_bytes is None or availability_bytes is None:
            raise PalimpsestChinaIntakeError(
                "manifest v3 requires exact input ledger and availability bytes"
            )
    elif manifest_schema == REVIEW_MANIFEST_SCHEMA:
        manifest = _exact_keys(manifest, _MANIFEST_V2_KEYS, label="manifest")
        producer = _validate_producer(manifest["producer"])
        if input_ledger_bytes is not None or availability_bytes is not None:
            raise PalimpsestChinaIntakeError(
                "review manifest v2 cannot accept v3 supplemental inputs"
            )
    elif manifest_schema == LEGACY_MANIFEST_SCHEMA:
        manifest = _exact_keys(manifest, _MANIFEST_V1_KEYS, label="manifest")
        producer = None
        if input_ledger_bytes is not None or availability_bytes is not None:
            raise PalimpsestChinaIntakeError(
                "legacy manifest v1 cannot accept v3 supplemental inputs"
            )
    else:
        raise PalimpsestChinaIntakeError(
            "manifest must use "
            f"{LEGACY_MANIFEST_SCHEMA}, {REVIEW_MANIFEST_SCHEMA}, or {MANIFEST_SCHEMA}"
        )
    if manifest["context_only"] is not True or manifest["scoring_allowed"] is not False:
        raise PalimpsestChinaIntakeError(
            "manifest must remain context-only and unscored"
        )
    if manifest_schema == MANIFEST_SCHEMA:
        _generated_text, generated_at = _canonical_timestamp(
            manifest["generated_at"], label="manifest.generated_at"
        )
    else:
        generated_at = _timestamp(
            manifest["generated_at"], label="manifest.generated_at"
        )
    if generated_at > accepted_at:
        raise PalimpsestChinaIntakeError(
            "manifest.generated_at follows Seiche accepted_at"
        )

    artifact = _exact_keys(
        manifest["artifact"], _ARTIFACT_RECEIPT_KEYS, label="manifest.artifact"
    )
    if artifact["schema_version"] != EXPORT_SCHEMA:
        raise PalimpsestChinaIntakeError(f"artifact must use {EXPORT_SCHEMA}")
    _safe_relative_path(artifact["path"], label="artifact.path", suffix=".jsonl")
    if artifact["media_type"] != "application/x-ndjson":
        raise PalimpsestChinaIntakeError(
            "artifact media_type must be application/x-ndjson"
        )
    artifact_sha256 = _sha(artifact["sha256"], label="artifact.sha256")
    artifact_size = _count(artifact["bytes"], label="artifact.bytes", positive=True)
    artifact_records = _count(
        artifact["records"], label="artifact.records", positive=True
    )
    if artifact_size != len(artifact_bytes) or artifact_sha256 != _sha256(
        artifact_bytes
    ):
        raise PalimpsestChinaIntakeError(
            "artifact hash/bytes commitment does not match"
        )
    if artifact_records > MAX_RECORDS:
        raise PalimpsestChinaIntakeError(f"artifact exceeds {MAX_RECORDS} records")

    input_ledger = _exact_keys(
        manifest["input_ledger"], _INPUT_LEDGER_KEYS, label="manifest.input_ledger"
    )
    _safe_relative_path(
        input_ledger["path"], label="input_ledger.path", suffix=".jsonl"
    )
    input_ledger_sha256 = _sha(input_ledger["sha256"], label="input_ledger.sha256")
    _count(input_ledger["bytes"], label="input_ledger.bytes", positive=True)
    input_records = _count(
        input_ledger["records"], label="input_ledger.records", positive=True
    )
    if input_records < artifact_records:
        raise PalimpsestChinaIntakeError(
            "input ledger cannot contain fewer records than the export"
        )
    ledger_state = (
        _validate_input_ledger(
            input_ledger_bytes,
            receipt=input_ledger,
            accepted_at=accepted_at,
        )
        if input_ledger_bytes is not None
        else None
    )

    policy = _exact_keys(
        manifest["policy"], _POLICY_RECEIPT_KEYS, label="manifest.policy"
    )
    if policy["schema_version"] != POLICY_SCHEMA:
        raise PalimpsestChinaIntakeError(f"policy must use {POLICY_SCHEMA}")
    if policy["path"] != "china_econ_source_policy.json":
        raise PalimpsestChinaIntakeError("policy path is not release-reviewed")
    policy_sha256 = _sha(policy["sha256"], label="policy.sha256")
    evaluated_at = _timestamp(policy["evaluated_at"], label="policy.evaluated_at")
    if evaluated_at != generated_at:
        raise PalimpsestChinaIntakeError(
            "policy evaluation clock must equal manifest generation"
        )

    series_registry = _exact_keys(
        manifest["series_registry"],
        _SERIES_REGISTRY_RECEIPT_KEYS,
        label="manifest.series_registry",
    )
    if series_registry["schema_version"] != SERIES_REGISTRY_SCHEMA:
        raise PalimpsestChinaIntakeError(
            f"series registry must use {SERIES_REGISTRY_SCHEMA}"
        )
    if series_registry["path"] != "china_econ_wdi_series.json":
        raise PalimpsestChinaIntakeError("series registry path is not release-reviewed")
    series_registry_sha256 = _sha(
        series_registry["sha256"], label="series_registry.sha256"
    )
    series_registry_size = _count(
        series_registry["bytes"], label="series_registry.bytes", positive=True
    )
    if series_registry_size > MAX_SERIES_REGISTRY_BYTES:
        raise PalimpsestChinaIntakeError("series registry receipt is too large")

    availability_state = (
        _validate_availability(
            availability_bytes,
            receipt_value=manifest["availability_receipt"],
            manifest_generated_at=generated_at,
            ledger=ledger_state,
        )
        if availability_bytes is not None and ledger_state is not None
        else None
    )

    source_decision = _validate_source_decisions(
        manifest["source_decisions"],
        artifact_records=artifact_records,
        input_ledger_records=input_records,
        generated_at=generated_at,
    )
    decision_expiry = _timestamp(
        source_decision["expires_at"], label="world_bank_wdi.expires_at"
    )
    if decision_expiry <= accepted_at:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi rights decision is expired at Seiche acceptance"
        )

    mapping = _exact_keys(
        manifest["market_channel_mapping"],
        frozenset(MARKET_CHANNELS),
        label="manifest.market_channel_mapping",
    )
    declared_mapping: dict[str, list[str]] = {}
    for channel in MARKET_CHANNELS:
        values = mapping[channel]
        if (
            type(values) is not list
            or not values
            or len(values) > MAX_SERIES
            or any(
                type(item) is not str or _SERIES_ID_RE.fullmatch(item) is None
                for item in values
            )
            or values != sorted(set(values))
        ):
            raise PalimpsestChinaIntakeError(
                f"market_channel_mapping.{channel} must contain at most "
                f"{MAX_SERIES} sorted unique WDI series"
            )
        declared_mapping[channel] = values

    if not artifact_bytes.endswith(b"\n") or b"\r" in artifact_bytes:
        raise PalimpsestChinaIntakeError(
            "artifact must be canonical LF-terminated JSONL"
        )
    lines = artifact_bytes.splitlines(keepends=True)
    if len(lines) != artifact_records:
        raise PalimpsestChinaIntakeError("artifact records commitment does not match")

    observations: list[PalimpsestChinaObservation] = []
    seen_ids: set[str] = set()
    derived_mapping = {channel: set() for channel in MARKET_CHANNELS}
    series_contracts: dict[str, tuple[object, ...]] = {}
    artifact_identities: set[tuple[str, int]] = set()
    artifact_source_indicators: set[str] = set()
    artifact_series_ids: set[str] = set()
    artifact_source_to_series: dict[str, str] = {}
    artifact_series_to_source: dict[str, str] = {}
    seen_artifact_identities: set[tuple[str, int]] = set()
    for position, line in enumerate(lines, 1):
        if not line.endswith(b"\n") or line == b"\n":
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} is blank or unterminated"
            )
        wrapper = _strict_json(line[:-1], label=f"artifact line {position}")
        if _canonical_json_line(wrapper) != line:
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} is not canonical JSON"
            )
        wrapper = _exact_keys(wrapper, _WRAPPER_KEYS, label=f"artifact line {position}")
        if wrapper["schema_version"] != EXPORT_SCHEMA:
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} must use {EXPORT_SCHEMA}"
            )
        if (
            wrapper["context_only"] is not True
            or wrapper["scoring_allowed"] is not False
        ):
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} attempts scoring or non-context use"
            )
        channel_value = wrapper["market_channels"]
        if (
            type(channel_value) is not list
            or not channel_value
            or channel_value != sorted(set(channel_value))
            or any(channel not in MARKET_CHANNELS for channel in channel_value)
        ):
            raise PalimpsestChinaIntakeError(
                f"artifact line {position} has invalid market_channels"
            )
        channels = tuple(channel_value)
        observation = _validate_observation(
            wrapper["observation"],
            channels=channels,
            accepted_at=accepted_at,
            position=position,
        )
        observation_id = str(observation.record["observation_id"])
        if observation_id in seen_ids:
            raise PalimpsestChinaIntakeError(
                f"artifact duplicates observation_id {observation_id}"
            )
        seen_ids.add(observation_id)
        source_series_id = str(observation.record["metadata"]["source_series_id"])
        identity = (source_series_id, observation.period_end.year)
        if identity in seen_artifact_identities:
            raise PalimpsestChinaIntakeError(
                "artifact must contain exactly one latest row per identity"
            )
        seen_artifact_identities.add(identity)
        artifact_identities.add(identity)
        artifact_source_indicators.add(source_series_id)
        artifact_series_ids.add(observation.series_id)
        mapped_series = artifact_source_to_series.setdefault(
            source_series_id, observation.series_id
        )
        mapped_source = artifact_series_to_source.setdefault(
            observation.series_id, source_series_id
        )
        if mapped_series != observation.series_id or mapped_source != source_series_id:
            raise PalimpsestChinaIntakeError(
                "artifact source/internal series mapping is not one-to-one"
            )
        if ledger_state is not None:
            ledger_record_sha256 = ledger_state.record_sha256_by_observation_id.get(
                observation_id
            )
            if (
                ledger_record_sha256
                != _sha256(_canonical_json_line(wrapper["observation"]))
                or ledger_state.source_to_series.get(source_series_id)
                != observation.series_id
            ):
                raise PalimpsestChinaIntakeError(
                    "artifact observation is not an exact input-ledger row"
                )
            if (
                ledger_state.latest_observation_id_by_identity.get(identity)
                != observation_id
            ):
                raise PalimpsestChinaIntakeError(
                    "artifact observation is not the latest input-ledger row"
                )
        for channel in channels:
            derived_mapping[channel].add(observation.series_id)
        contract = (
            observation.record["unit"],
            observation.record["frequency"],
            observation.record["geography"],
            observation.record["sector"],
            observation.record["firm_size"],
            observation.record["ownership"],
            observation.record["metadata"]["source_series_id"],
            channels,
        )
        prior_contract = series_contracts.setdefault(observation.series_id, contract)
        if prior_contract != contract:
            raise PalimpsestChinaIntakeError(
                f"series contract drift for {observation.series_id}"
            )
        observations.append(observation)

    for channel in MARKET_CHANNELS:
        if declared_mapping[channel] != sorted(derived_mapping[channel]):
            raise PalimpsestChinaIntakeError(
                f"market_channel_mapping.{channel} does not match artifact rows"
            )
    if len(series_contracts) > MAX_SERIES:
        raise PalimpsestChinaIntakeError(f"artifact exceeds {MAX_SERIES} series")

    if availability_state is not None:
        if artifact_identities != set(availability_state.expected_artifact_identities):
            raise PalimpsestChinaIntakeError(
                "artifact identities do not match current projectable availability"
            )
        if artifact_source_indicators != set(
            availability_state.projectable_source_indicators
        ):
            raise PalimpsestChinaIntakeError(
                "artifact source indicators do not match current projectable availability"
            )
        if artifact_series_ids != set(availability_state.projectable_series):
            raise PalimpsestChinaIntakeError(
                "artifact series do not match current projectable availability"
            )

    latest_identity: dict[tuple[str, int], PalimpsestChinaObservation] = {}
    for row in observations:
        identity = (
            str(row.record["metadata"]["source_series_id"]),
            row.period_end.year,
        )
        prior = latest_identity.get(identity)
        if prior is None or (
            int(row.record["revision"]),
            row.released_at,
            row.collected_at,
            str(row.record["observation_id"]),
        ) > (
            int(prior.record["revision"]),
            prior.released_at,
            prior.collected_at,
            str(prior.record["observation_id"]),
        ):
            latest_identity[identity] = row

    latest: dict[str, PalimpsestChinaObservation] = {}
    for row in latest_identity.values():
        prior = latest.get(row.series_id)
        if prior is None or (
            row.period_end,
            int(row.record["revision"]),
            row.released_at,
            row.collected_at,
            str(row.record["observation_id"]),
        ) > (
            prior.period_end,
            int(prior.record["revision"]),
            prior.released_at,
            prior.collected_at,
            str(prior.record["observation_id"]),
        ):
            latest[row.series_id] = row

    accepted_text = accepted_at.isoformat().replace("+00:00", "Z")
    return PalimpsestChinaEconomicContext(
        accepted_at=accepted_text,
        manifest_schema_version=manifest_schema,
        manifest_sha256=_sha256(manifest_bytes),
        producer=_freeze_json(producer) if producer is not None else None,
        producer_commit_evidence=None,
        producer_main_evidence=None,
        handoff_receipt=None,
        checksum_subject=None,
        governed_lineage=None,
        operator_confirmations=None,
        artifact_sha256=artifact_sha256,
        artifact_bytes=artifact_size,
        input_ledger_sha256=input_ledger_sha256,
        availability_receipt_sha256=(
            availability_state.receipt_sha256
            if availability_state is not None
            else None
        ),
        availability_batch_raw_sha256=(
            availability_state.batch_raw_sha256
            if availability_state is not None
            else None
        ),
        current_numeric_identities_sha256=(
            availability_state.current_numeric_identities_sha256
            if availability_state is not None
            else None
        ),
        current_projectable_series_sha256=(
            availability_state.current_projectable_series_sha256
            if availability_state is not None
            else None
        ),
        current_projectable_source_indicators_sha256=(
            availability_state.current_projectable_source_indicators_sha256
            if availability_state is not None
            else None
        ),
        withdrawn_numeric_identities_sha256=(
            availability_state.withdrawn_numeric_identities_sha256
            if availability_state is not None
            else None
        ),
        policy_sha256=policy_sha256,
        series_registry_sha256=series_registry_sha256,
        acceptance_sha256=None,
        acceptance_signer_key_id=None,
        source_decision=_freeze_json(dict(source_decision)),
        observations=tuple(observations),
        current_observations=tuple(latest[key] for key in sorted(latest)),
    )


def _require_authoritative_producer(
    context: PalimpsestChinaEconomicContext,
) -> None:
    """Require a final-main producer declaration before owner acceptance."""

    producer = context.producer
    workflow = producer.get("workflow_run") if isinstance(producer, Mapping) else None
    if (
        context.manifest_schema_version != MANIFEST_SCHEMA
        or not isinstance(workflow, Mapping)
        or workflow.get("event") != "push"
        or workflow.get("conclusion") != "success"
        or workflow.get("head_sha") != producer.get("commit_sha")
    ):
        raise PalimpsestChinaIntakeError(
            "Palimpsest producer declaration must identify a successful "
            "exact-commit push run"
        )


def build_acceptance_claim(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    *,
    input_ledger_bytes: bytes | None = None,
    availability_bytes: bytes | None = None,
    producer_commit_evidence_bytes: bytes | None = None,
    producer_main_evidence_bytes: bytes | None = None,
    handoff_bytes: bytes | None = None,
    checksums_bytes: bytes | None = None,
    lineage_chain_bytes: bytes | None = None,
    lineage_evidence_bytes: bytes | None = None,
    operator_confirmations: Mapping[str, object] | None = None,
    accepted_at: datetime,
    signer_key_id: str,
) -> dict[str, Any]:
    """Build the domain-separated claim a Seiche operator signs offline."""

    if (
        type(accepted_at) is not datetime
        or accepted_at.tzinfo is None
        or accepted_at.utcoffset() is None
    ):
        raise PalimpsestChinaIntakeError(
            "accepted_at must be a timezone-aware datetime"
        )
    if accepted_at.astimezone(UTC) > _utc_now():
        raise PalimpsestChinaIntakeError("accepted_at cannot be in the future")
    context = verify_export(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
    )
    _require_authoritative_producer(context)
    if (
        input_ledger_bytes is None
        or availability_bytes is None
        or producer_commit_evidence_bytes is None
        or producer_main_evidence_bytes is None
        or handoff_bytes is None
        or checksums_bytes is None
        or lineage_chain_bytes is None
        or lineage_evidence_bytes is None
        or context.producer is None
    ):
        raise PalimpsestChinaIntakeError(
            "authoritative acceptance requires every exact handoff and producer evidence file"
        )
    producer_commit_evidence = _validate_producer_commit_evidence(
        producer_commit_evidence_bytes,
        producer=context.producer,
        accepted_at=accepted_at.astimezone(UTC),
    )
    producer_main_evidence = _validate_producer_main_evidence(
        producer_main_evidence_bytes,
        producer=context.producer,
        observed_at=accepted_at.astimezone(UTC),
    )
    authority = _validate_handoff_authority(
        manifest_bytes=manifest_bytes,
        artifact_bytes=artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=producer_commit_evidence_bytes,
        handoff_bytes=handoff_bytes,
        checksums_bytes=checksums_bytes,
        lineage_chain_bytes=lineage_chain_bytes,
        lineage_evidence_bytes=lineage_evidence_bytes,
        accepted_at=accepted_at.astimezone(UTC),
    )
    supplied_confirmations = _exact_keys(
        operator_confirmations,
        _OPERATOR_CONFIRMATION_INPUT_KEYS,
        label="operator confirmations",
    )
    for key in _OPERATOR_CONFIRMATION_INPUT_KEYS:
        if supplied_confirmations[key] is not True:
            raise PalimpsestChinaIntakeError(
                f"operator confirmation {key} must be explicit true"
            )
    assert context.availability_receipt_sha256 is not None
    signed_confirmations = {
        **supplied_confirmations,
        "github_attestation_subject_sha256": authority.checksums_sha256,
        "checksum_subject_sha256": authority.checksums_sha256,
        "producer_commit_evidence_sha256": producer_commit_evidence["sha256"],
        "lineage_chain_sha256": authority.chain_sha256,
        "lineage_evidence_sha256": authority.evidence_sha256,
        "lineage_evaluated_at_commit_sha": authority.chain_receipt[
            "evaluated_at_commit_sha"
        ],
        "producer_main_evidence_sha256": producer_main_evidence["sha256"],
        "manifest_sha256": context.manifest_sha256,
        "availability_receipt_sha256": context.availability_receipt_sha256,
        "rights_expires_at": context.source_decision["expires_at"],
    }
    _validate_embedded_operator_confirmations(signed_confirmations)
    signer = _sha(signer_key_id, label="acceptance.signer_key_id")
    return {
        "schema_version": ACCEPTANCE_SCHEMA,
        "algorithm": "ed25519",
        "domain": ACCEPTANCE_DOMAIN,
        "accepted_at": context.accepted_at,
        "manifest_sha256": context.manifest_sha256,
        "artifact_sha256": context.artifact_sha256,
        "producer_commit_evidence": producer_commit_evidence,
        "producer_main_evidence": producer_main_evidence,
        "handoff_receipt": {
            "path": "handoff-receipt.json",
            "schema_version": HANDOFF_SCHEMA,
            "sha256": authority.handoff_sha256,
            "bytes": len(handoff_bytes),
        },
        "checksum_subject": {
            "path": "SHA256SUMS",
            "sha256": authority.checksums_sha256,
            "bytes": len(checksums_bytes),
            "records": len(_CHECKSUM_SUBJECT_NAMES),
        },
        "governed_lineage": dict(authority.chain_receipt),
        "operator_confirmations": signed_confirmations,
        "signer_key_id": signer,
    }


def build_acceptance_claim_from_files(
    manifest_path: str | Path,
    artifact_path: str | Path,
    *,
    input_ledger_path: str | Path | None = None,
    availability_path: str | Path | None = None,
    producer_commit_evidence_path: str | Path | None = None,
    producer_main_evidence_path: str | Path | None = None,
    handoff_path: str | Path | None = None,
    checksums_path: str | Path | None = None,
    lineage_chain_path: str | Path | None = None,
    lineage_evidence_path: str | Path | None = None,
    operator_confirmations: Mapping[str, object] | None = None,
    accepted_at: datetime,
    signer_key_id: str,
) -> dict[str, Any]:
    """Safely read exact export bytes and build their offline signing claim."""

    manifest_bytes = _stable_read(
        manifest_path, label="Palimpsest China manifest", maximum=MAX_MANIFEST_BYTES
    )
    artifact_bytes = _stable_read(
        artifact_path, label="Palimpsest China artifact", maximum=MAX_ARTIFACT_BYTES
    )
    input_ledger_bytes = (
        _stable_read(
            input_ledger_path,
            label="Palimpsest China input ledger",
            maximum=MAX_INPUT_LEDGER_BYTES,
        )
        if input_ledger_path is not None
        else None
    )
    availability_bytes = (
        _stable_read(
            availability_path,
            label="Palimpsest China availability receipt",
            maximum=MAX_AVAILABILITY_BYTES,
        )
        if availability_path is not None
        else None
    )
    producer_commit_evidence_bytes = (
        _stable_read(
            producer_commit_evidence_path,
            label="Palimpsest producer commit evidence",
            maximum=MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
        )
        if producer_commit_evidence_path is not None
        else None
    )
    producer_main_evidence_bytes = (
        _stable_read(
            producer_main_evidence_path,
            label="Palimpsest producer main evidence",
            maximum=MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
        )
        if producer_main_evidence_path is not None
        else None
    )
    handoff_bytes = (
        _stable_read(
            handoff_path,
            label="Palimpsest China handoff receipt",
            maximum=MAX_HANDOFF_BYTES,
        )
        if handoff_path is not None
        else None
    )
    checksums_bytes = (
        _stable_read(
            checksums_path,
            label="Palimpsest China checksum subject",
            maximum=MAX_CHECKSUMS_BYTES,
        )
        if checksums_path is not None
        else None
    )
    lineage_chain_bytes = (
        _stable_read(
            lineage_chain_path,
            label="Palimpsest China governed lineage chain",
            maximum=MAX_LINEAGE_CHAIN_BYTES,
        )
        if lineage_chain_path is not None
        else None
    )
    lineage_evidence_bytes = (
        _stable_read(
            lineage_evidence_path,
            label="Palimpsest China governed lineage evidence",
            maximum=MAX_LINEAGE_EVIDENCE_BYTES,
        )
        if lineage_evidence_path is not None
        else None
    )
    return build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=producer_commit_evidence_bytes,
        producer_main_evidence_bytes=producer_main_evidence_bytes,
        handoff_bytes=handoff_bytes,
        checksums_bytes=checksums_bytes,
        lineage_chain_bytes=lineage_chain_bytes,
        lineage_evidence_bytes=lineage_evidence_bytes,
        operator_confirmations=operator_confirmations,
        accepted_at=accepted_at,
        signer_key_id=signer_key_id,
    )


def encode_acceptance_claim(claim: Mapping[str, Any]) -> bytes:
    """Return the exact canonical bytes covered by the detached signature."""

    row = _exact_keys(claim, _ACCEPTANCE_CLAIM_KEYS, label="acceptance claim")
    if (
        row["schema_version"] != ACCEPTANCE_SCHEMA
        or row["algorithm"] != "ed25519"
        or row["domain"] != ACCEPTANCE_DOMAIN
    ):
        raise PalimpsestChinaIntakeError("acceptance claim domain changed")
    _canonical_timestamp(row["accepted_at"], label="acceptance.accepted_at")
    _sha(row["manifest_sha256"], label="acceptance.manifest_sha256")
    _sha(row["artifact_sha256"], label="acceptance.artifact_sha256")
    commit_evidence = _validate_embedded_producer_commit_evidence(
        row["producer_commit_evidence"]
    )
    main_evidence = _validate_embedded_producer_main_evidence(
        row["producer_main_evidence"]
    )
    if main_evidence["observed_at"] != row["accepted_at"]:
        raise PalimpsestChinaIntakeError(
            "producer main observation clock must equal Seiche accepted_at"
        )
    _validate_embedded_handoff_receipt(row["handoff_receipt"])
    checksum = _validate_embedded_checksum_subject(row["checksum_subject"])
    lineage = _validate_embedded_lineage_receipt(row["governed_lineage"])
    confirmations = _validate_embedded_operator_confirmations(
        row["operator_confirmations"]
    )
    if (
        lineage["evaluated_at_commit_sha"] != commit_evidence["sha"]
        or main_evidence["commit"]["sha"] != commit_evidence["sha"]
        or confirmations["github_attestation_subject_sha256"] != checksum["sha256"]
        or confirmations["checksum_subject_sha256"] != checksum["sha256"]
        or confirmations["producer_commit_evidence_sha256"] != commit_evidence["sha256"]
        or confirmations["lineage_chain_sha256"] != lineage["sha256"]
        or confirmations["lineage_evidence_sha256"] != lineage["evidence"]["sha256"]
        or confirmations["lineage_evaluated_at_commit_sha"]
        != lineage["evaluated_at_commit_sha"]
        or confirmations["producer_main_evidence_sha256"] != main_evidence["sha256"]
        or confirmations["manifest_sha256"] != row["manifest_sha256"]
        or _timestamp(
            confirmations["rights_expires_at"],
            label="acceptance.operator_confirmations.rights_expires_at",
        )
        <= _timestamp(row["accepted_at"], label="acceptance.accepted_at")
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance evidence and operator confirmation commitments disagree"
        )
    _sha(row["signer_key_id"], label="acceptance.signer_key_id")
    return _canonical_json_line(dict(row))


def build_acceptance_receipt(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    *,
    input_ledger_bytes: bytes | None = None,
    availability_bytes: bytes | None = None,
    producer_commit_evidence_bytes: bytes | None = None,
    producer_main_evidence_bytes: bytes | None = None,
    handoff_bytes: bytes | None = None,
    checksums_bytes: bytes | None = None,
    lineage_chain_bytes: bytes | None = None,
    lineage_evidence_bytes: bytes | None = None,
    operator_confirmations: Mapping[str, object] | None = None,
    accepted_at: datetime,
    signer_key_id: str,
    signature: str,
    attest_dir: str | Path | None = None,
) -> bytes:
    """Assemble a trusted canonical receipt around an offline signature."""

    claim = build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=producer_commit_evidence_bytes,
        producer_main_evidence_bytes=producer_main_evidence_bytes,
        handoff_bytes=handoff_bytes,
        checksums_bytes=checksums_bytes,
        lineage_chain_bytes=lineage_chain_bytes,
        lineage_evidence_bytes=lineage_evidence_bytes,
        operator_confirmations=operator_confirmations,
        accepted_at=accepted_at,
        signer_key_id=signer_key_id,
    )
    if type(signature) is not str or _ED25519_SIGNATURE_RE.fullmatch(signature) is None:
        raise PalimpsestChinaIntakeError("acceptance signature is malformed")
    try:
        verify_trusted_palimpsest_china_signature(
            encode_acceptance_claim(claim),
            signature,
            signer_key_id,
            attest_dir=attest_dir,
        )
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(
            "acceptance signature is not trusted and valid"
        ) from exc
    return _canonical_json_line({**claim, "signature": signature})


def build_acceptance_receipt_from_files(
    manifest_path: str | Path,
    artifact_path: str | Path,
    *,
    input_ledger_path: str | Path | None = None,
    availability_path: str | Path | None = None,
    producer_commit_evidence_path: str | Path | None = None,
    producer_main_evidence_path: str | Path | None = None,
    handoff_path: str | Path | None = None,
    checksums_path: str | Path | None = None,
    lineage_chain_path: str | Path | None = None,
    lineage_evidence_path: str | Path | None = None,
    operator_confirmations: Mapping[str, object] | None = None,
    accepted_at: datetime,
    signer_key_id: str,
    signature: str,
    attest_dir: str | Path | None = None,
) -> bytes:
    """Safely re-read the signed inputs and assemble their trusted receipt."""

    manifest_bytes = _stable_read(
        manifest_path, label="Palimpsest China manifest", maximum=MAX_MANIFEST_BYTES
    )
    artifact_bytes = _stable_read(
        artifact_path, label="Palimpsest China artifact", maximum=MAX_ARTIFACT_BYTES
    )
    input_ledger_bytes = (
        _stable_read(
            input_ledger_path,
            label="Palimpsest China input ledger",
            maximum=MAX_INPUT_LEDGER_BYTES,
        )
        if input_ledger_path is not None
        else None
    )
    availability_bytes = (
        _stable_read(
            availability_path,
            label="Palimpsest China availability receipt",
            maximum=MAX_AVAILABILITY_BYTES,
        )
        if availability_path is not None
        else None
    )
    producer_commit_evidence_bytes = (
        _stable_read(
            producer_commit_evidence_path,
            label="Palimpsest producer commit evidence",
            maximum=MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
        )
        if producer_commit_evidence_path is not None
        else None
    )
    producer_main_evidence_bytes = (
        _stable_read(
            producer_main_evidence_path,
            label="Palimpsest producer main evidence",
            maximum=MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
        )
        if producer_main_evidence_path is not None
        else None
    )
    handoff_bytes = (
        _stable_read(
            handoff_path,
            label="Palimpsest China handoff receipt",
            maximum=MAX_HANDOFF_BYTES,
        )
        if handoff_path is not None
        else None
    )
    checksums_bytes = (
        _stable_read(
            checksums_path,
            label="Palimpsest China checksum subject",
            maximum=MAX_CHECKSUMS_BYTES,
        )
        if checksums_path is not None
        else None
    )
    lineage_chain_bytes = (
        _stable_read(
            lineage_chain_path,
            label="Palimpsest China governed lineage chain",
            maximum=MAX_LINEAGE_CHAIN_BYTES,
        )
        if lineage_chain_path is not None
        else None
    )
    lineage_evidence_bytes = (
        _stable_read(
            lineage_evidence_path,
            label="Palimpsest China governed lineage evidence",
            maximum=MAX_LINEAGE_EVIDENCE_BYTES,
        )
        if lineage_evidence_path is not None
        else None
    )
    return build_acceptance_receipt(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=producer_commit_evidence_bytes,
        producer_main_evidence_bytes=producer_main_evidence_bytes,
        handoff_bytes=handoff_bytes,
        checksums_bytes=checksums_bytes,
        lineage_chain_bytes=lineage_chain_bytes,
        lineage_evidence_bytes=lineage_evidence_bytes,
        operator_confirmations=operator_confirmations,
        accepted_at=accepted_at,
        signer_key_id=signer_key_id,
        signature=signature,
        attest_dir=attest_dir,
    )


def _file_identity(
    path: str | Path, *, label: str
) -> tuple[str, int, int, int, int, int]:
    selected = os.path.abspath(os.fspath(path))
    try:
        metadata = os.stat(selected, follow_symlinks=False)
    except OSError as exc:
        raise PalimpsestChinaIntakeError(f"cannot inspect {label}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise PalimpsestChinaIntakeError(f"{label} must be a single-link regular file")
    return (selected, *_stat_fingerprint(metadata))


@lru_cache(maxsize=8)
def _load_accepted_export_cached(
    manifest_identity: tuple[str, int, int, int, int, int],
    artifact_identity: tuple[str, int, int, int, int, int],
    input_ledger_identity: tuple[str, int, int, int, int, int] | None,
    availability_identity: tuple[str, int, int, int, int, int] | None,
    producer_commit_evidence_identity: tuple[str, int, int, int, int, int] | None,
    producer_main_evidence_identity: tuple[str, int, int, int, int, int] | None,
    handoff_identity: tuple[str, int, int, int, int, int] | None,
    checksums_identity: tuple[str, int, int, int, int, int] | None,
    lineage_chain_identity: tuple[str, int, int, int, int, int] | None,
    lineage_evidence_identity: tuple[str, int, int, int, int, int] | None,
    acceptance_identity: tuple[str, int, int, int, int, int],
    attest_dir: str | None,
    trust_identity: tuple[str, int, int, int, int, int] | None,
) -> PalimpsestChinaEconomicContext:
    """Verify one immutable input set once; identities form the cache key."""

    del trust_identity  # cache-key commitment; signature verification reads it below
    manifest_path, artifact_path, acceptance_path = (
        manifest_identity[0],
        artifact_identity[0],
        acceptance_identity[0],
    )
    manifest_bytes = _stable_read(
        manifest_path,
        label="Palimpsest China manifest",
        maximum=MAX_MANIFEST_BYTES,
        expected_identity=manifest_identity,
    )
    artifact_bytes = _stable_read(
        artifact_path,
        label="Palimpsest China artifact",
        maximum=MAX_ARTIFACT_BYTES,
        expected_identity=artifact_identity,
    )
    input_ledger_bytes = (
        _stable_read(
            input_ledger_identity[0],
            label="Palimpsest China input ledger",
            maximum=MAX_INPUT_LEDGER_BYTES,
            expected_identity=input_ledger_identity,
        )
        if input_ledger_identity is not None
        else None
    )
    availability_bytes = (
        _stable_read(
            availability_identity[0],
            label="Palimpsest China availability receipt",
            maximum=MAX_AVAILABILITY_BYTES,
            expected_identity=availability_identity,
        )
        if availability_identity is not None
        else None
    )
    producer_commit_evidence_bytes = (
        _stable_read(
            producer_commit_evidence_identity[0],
            label="Palimpsest producer commit evidence",
            maximum=MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
            expected_identity=producer_commit_evidence_identity,
        )
        if producer_commit_evidence_identity is not None
        else None
    )
    producer_main_evidence_bytes = (
        _stable_read(
            producer_main_evidence_identity[0],
            label="Palimpsest producer main evidence",
            maximum=MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
            expected_identity=producer_main_evidence_identity,
        )
        if producer_main_evidence_identity is not None
        else None
    )
    handoff_bytes = (
        _stable_read(
            handoff_identity[0],
            label="Palimpsest China handoff receipt",
            maximum=MAX_HANDOFF_BYTES,
            expected_identity=handoff_identity,
        )
        if handoff_identity is not None
        else None
    )
    checksums_bytes = (
        _stable_read(
            checksums_identity[0],
            label="Palimpsest China checksum subject",
            maximum=MAX_CHECKSUMS_BYTES,
            expected_identity=checksums_identity,
        )
        if checksums_identity is not None
        else None
    )
    lineage_chain_bytes = (
        _stable_read(
            lineage_chain_identity[0],
            label="Palimpsest China governed lineage chain",
            maximum=MAX_LINEAGE_CHAIN_BYTES,
            expected_identity=lineage_chain_identity,
        )
        if lineage_chain_identity is not None
        else None
    )
    lineage_evidence_bytes = (
        _stable_read(
            lineage_evidence_identity[0],
            label="Palimpsest China governed lineage evidence",
            maximum=MAX_LINEAGE_EVIDENCE_BYTES,
            expected_identity=lineage_evidence_identity,
        )
        if lineage_evidence_identity is not None
        else None
    )
    acceptance_bytes = _stable_read(
        acceptance_path,
        label="Palimpsest China acceptance receipt",
        maximum=MAX_ACCEPTANCE_BYTES,
        expected_identity=acceptance_identity,
    )
    receipt = _strict_json(acceptance_bytes, label="acceptance receipt")
    if _canonical_json_line(receipt) != acceptance_bytes:
        raise PalimpsestChinaIntakeError(
            "acceptance receipt must use exact canonical JSON bytes"
        )
    receipt = _exact_keys(receipt, _ACCEPTANCE_KEYS, label="acceptance receipt")
    signature = receipt["signature"]
    if type(signature) is not str or _ED25519_SIGNATURE_RE.fullmatch(signature) is None:
        raise PalimpsestChinaIntakeError("acceptance signature is malformed")
    claim = {key: receipt[key] for key in _ACCEPTANCE_CLAIM_KEYS}
    claim_bytes = encode_acceptance_claim(claim)
    if claim["manifest_sha256"] != _sha256(manifest_bytes):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt manifest hash does not match"
        )
    if claim["artifact_sha256"] != _sha256(artifact_bytes):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt artifact hash does not match"
        )
    embedded_evidence = _validate_embedded_producer_commit_evidence(
        claim["producer_commit_evidence"]
    )
    if (
        producer_commit_evidence_bytes is None
        or embedded_evidence["sha256"] != _sha256(producer_commit_evidence_bytes)
        or embedded_evidence["bytes"] != len(producer_commit_evidence_bytes)
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt producer commit evidence hash/bytes do not match"
        )
    embedded_main_evidence = _validate_embedded_producer_main_evidence(
        claim["producer_main_evidence"]
    )
    if (
        producer_main_evidence_bytes is None
        or embedded_main_evidence["sha256"] != _sha256(producer_main_evidence_bytes)
        or embedded_main_evidence["bytes"] != len(producer_main_evidence_bytes)
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt producer main evidence hash/bytes do not match"
        )
    embedded_handoff = _validate_embedded_handoff_receipt(claim["handoff_receipt"])
    if (
        handoff_bytes is None
        or embedded_handoff["sha256"] != _sha256(handoff_bytes)
        or embedded_handoff["bytes"] != len(handoff_bytes)
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt handoff hash/bytes do not match"
        )
    embedded_checksums = _validate_embedded_checksum_subject(claim["checksum_subject"])
    if (
        checksums_bytes is None
        or embedded_checksums["sha256"] != _sha256(checksums_bytes)
        or embedded_checksums["bytes"] != len(checksums_bytes)
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt checksum subject hash/bytes do not match"
        )
    embedded_lineage = _validate_embedded_lineage_receipt(claim["governed_lineage"])
    if (
        lineage_chain_bytes is None
        or lineage_evidence_bytes is None
        or embedded_lineage["sha256"] != _sha256(lineage_chain_bytes)
        or embedded_lineage["bytes"] != len(lineage_chain_bytes)
        or embedded_lineage["evidence"]["sha256"] != _sha256(lineage_evidence_bytes)
        or embedded_lineage["evidence"]["bytes"] != len(lineage_evidence_bytes)
    ):
        raise PalimpsestChinaIntakeError(
            "acceptance receipt governed lineage hash/bytes do not match"
        )
    try:
        verify_trusted_palimpsest_china_signature(
            claim_bytes,
            signature,
            claim["signer_key_id"],
            attest_dir=attest_dir,
        )
    except ValueError as exc:
        raise PalimpsestChinaIntakeError(
            "acceptance signature is not trusted and valid"
        ) from exc
    accepted_at_text, accepted_at = _canonical_timestamp(
        claim["accepted_at"], label="acceptance.accepted_at"
    )
    context = verify_export(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
    )
    _require_authoritative_producer(context)
    assert context.producer is not None
    normalized_evidence = _validate_producer_commit_evidence(
        producer_commit_evidence_bytes,
        producer=context.producer,
        accepted_at=accepted_at,
    )
    normalized_main_evidence = _validate_producer_main_evidence(
        producer_main_evidence_bytes,
        producer=context.producer,
        observed_at=accepted_at,
    )
    if normalized_evidence != embedded_evidence:
        raise PalimpsestChinaIntakeError(
            "acceptance producer commit evidence does not match raw GitHub bytes"
        )
    if normalized_main_evidence != embedded_main_evidence:
        raise PalimpsestChinaIntakeError(
            "acceptance producer main evidence does not match raw GitHub bytes"
        )
    if (
        input_ledger_bytes is None
        or availability_bytes is None
        or handoff_bytes is None
        or checksums_bytes is None
        or lineage_chain_bytes is None
        or lineage_evidence_bytes is None
    ):
        raise PalimpsestChinaIntakeError(
            "accepted export is missing an authoritative handoff input"
        )
    authority = _validate_handoff_authority(
        manifest_bytes=manifest_bytes,
        artifact_bytes=artifact_bytes,
        input_ledger_bytes=input_ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=producer_commit_evidence_bytes,
        handoff_bytes=handoff_bytes,
        checksums_bytes=checksums_bytes,
        lineage_chain_bytes=lineage_chain_bytes,
        lineage_evidence_bytes=lineage_evidence_bytes,
        accepted_at=accepted_at,
    )
    confirmations = _validate_embedded_operator_confirmations(
        claim["operator_confirmations"]
    )
    expected_confirmations = {key: True for key in _OPERATOR_CONFIRMATION_INPUT_KEYS}
    assert context.availability_receipt_sha256 is not None
    expected_confirmations.update(
        {
            "github_attestation_subject_sha256": authority.checksums_sha256,
            "checksum_subject_sha256": authority.checksums_sha256,
            "producer_commit_evidence_sha256": normalized_evidence["sha256"],
            "lineage_chain_sha256": authority.chain_sha256,
            "lineage_evidence_sha256": authority.evidence_sha256,
            "lineage_evaluated_at_commit_sha": authority.chain_receipt[
                "evaluated_at_commit_sha"
            ],
            "producer_main_evidence_sha256": normalized_main_evidence["sha256"],
            "manifest_sha256": context.manifest_sha256,
            "availability_receipt_sha256": context.availability_receipt_sha256,
            "rights_expires_at": context.source_decision["expires_at"],
        }
    )
    if (
        embedded_lineage != dict(authority.chain_receipt)
        or embedded_handoff["sha256"] != authority.handoff_sha256
        or embedded_checksums["sha256"] != authority.checksums_sha256
        or confirmations != expected_confirmations
    ):
        raise PalimpsestChinaIntakeError(
            "signed acceptance does not match the revalidated handoff authority"
        )
    if context.accepted_at != accepted_at_text:
        raise PalimpsestChinaIntakeError("acceptance clock normalization changed")
    context = replace(
        context,
        acceptance_sha256=_sha256(acceptance_bytes),
        acceptance_signer_key_id=claim["signer_key_id"],
        producer_commit_evidence=_freeze_json(normalized_evidence),
        producer_main_evidence=_freeze_json(normalized_main_evidence),
        handoff_receipt=_freeze_json(embedded_handoff),
        checksum_subject=_freeze_json(embedded_checksums),
        governed_lineage=_freeze_json(dict(authority.chain_receipt)),
        operator_confirmations=_freeze_json(confirmations),
    )
    object.__setattr__(
        context,
        "_acceptance_authority",
        _OWNER_ATTESTED_AUTHORITY,
    )
    return context


def clear_accepted_export_cache() -> None:
    """Clear the bounded process cache after an operator-controlled rotation."""

    _load_accepted_export_cached.cache_clear()


def load_accepted_export(
    manifest_path: str | Path,
    artifact_path: str | Path,
    acceptance_path: str | Path,
    *,
    input_ledger_path: str | Path | None = None,
    availability_path: str | Path | None = None,
    producer_commit_evidence_path: str | Path | None = None,
    producer_main_evidence_path: str | Path | None = None,
    handoff_path: str | Path | None = None,
    checksums_path: str | Path | None = None,
    lineage_chain_path: str | Path | None = None,
    lineage_evidence_path: str | Path | None = None,
    attest_dir: str | Path | None = None,
    now: datetime | None = None,
) -> PalimpsestChinaEconomicContext:
    """Load a signed local export and re-evaluate rights on every invocation."""

    selected_attest_dir = (
        os.path.abspath(os.fspath(attest_dir)) if attest_dir is not None else None
    )
    trust_identity = (
        _file_identity(
            Path(selected_attest_dir) / "trusted_operator_keys",
            label="Palimpsest China operator trust policy",
        )
        if selected_attest_dir is not None
        else None
    )
    context = _load_accepted_export_cached(
        _file_identity(manifest_path, label="Palimpsest China manifest"),
        _file_identity(artifact_path, label="Palimpsest China artifact"),
        (
            _file_identity(input_ledger_path, label="Palimpsest China input ledger")
            if input_ledger_path is not None
            else None
        ),
        (
            _file_identity(
                availability_path,
                label="Palimpsest China availability receipt",
            )
            if availability_path is not None
            else None
        ),
        (
            _file_identity(
                producer_commit_evidence_path,
                label="Palimpsest producer commit evidence",
            )
            if producer_commit_evidence_path is not None
            else None
        ),
        (
            _file_identity(
                producer_main_evidence_path,
                label="Palimpsest producer main evidence",
            )
            if producer_main_evidence_path is not None
            else None
        ),
        (
            _file_identity(handoff_path, label="Palimpsest China handoff receipt")
            if handoff_path is not None
            else None
        ),
        (
            _file_identity(checksums_path, label="Palimpsest China checksum subject")
            if checksums_path is not None
            else None
        ),
        (
            _file_identity(
                lineage_chain_path,
                label="Palimpsest China governed lineage chain",
            )
            if lineage_chain_path is not None
            else None
        ),
        (
            _file_identity(
                lineage_evidence_path,
                label="Palimpsest China governed lineage evidence",
            )
            if lineage_evidence_path is not None
            else None
        ),
        _file_identity(acceptance_path, label="Palimpsest China acceptance receipt"),
        selected_attest_dir,
        trust_identity,
    )
    evaluation = now or _utc_now()
    if (
        type(evaluation) is not datetime
        or evaluation.tzinfo is None
        or evaluation.utcoffset() is None
    ):
        raise PalimpsestChinaIntakeError("now must be a timezone-aware datetime")
    evaluation = evaluation.astimezone(UTC)
    accepted_at = _timestamp(context.accepted_at, label="acceptance.accepted_at")
    if accepted_at > evaluation:
        raise PalimpsestChinaIntakeError("Seiche acceptance clock is in the future")
    expiry = _timestamp(
        context.source_decision["expires_at"], label="world_bank_wdi.expires_at"
    )
    if expiry <= evaluation:
        raise PalimpsestChinaIntakeError(
            "world_bank_wdi rights decision has expired at serve time"
        )
    return context


__all__ = [
    "ACCEPTANCE_DOMAIN",
    "ACCEPTANCE_SCHEMA",
    "ALLOWED_SOURCE_IDS",
    "AVAILABILITY_RECEIPT_SCHEMA",
    "AVAILABILITY_SCHEMA",
    "CONTEXT_SCHEMA",
    "EXPORT_SCHEMA",
    "HANDOFF_SCHEMA",
    "LEGACY_MANIFEST_SCHEMA",
    "MANIFEST_SCHEMA",
    "MARKET_CHANNELS",
    "MAX_ACCEPTANCE_BYTES",
    "MAX_ARTIFACT_BYTES",
    "MAX_AVAILABILITY_BYTES",
    "MAX_CHECKSUMS_BYTES",
    "MAX_HANDOFF_BYTES",
    "MAX_INPUT_LEDGER_BYTES",
    "MAX_LINEAGE_CHAIN_BYTES",
    "MAX_LINEAGE_EVIDENCE_BYTES",
    "MAX_MANIFEST_BYTES",
    "MAX_PRODUCER_COMMIT_EVIDENCE_BYTES",
    "MAX_PRODUCER_MAIN_EVIDENCE_BYTES",
    "POLICY_SCHEMA",
    "PRODUCER_SCHEMA",
    "REVIEW_MANIFEST_SCHEMA",
    "SERIES_REGISTRY_SCHEMA",
    "PalimpsestChinaEconomicContext",
    "PalimpsestChinaIntakeError",
    "PalimpsestChinaObservation",
    "build_acceptance_claim",
    "build_acceptance_claim_from_files",
    "build_acceptance_receipt",
    "build_acceptance_receipt_from_files",
    "clear_accepted_export_cache",
    "encode_acceptance_claim",
    "load_accepted_export",
    "verify_export",
]
