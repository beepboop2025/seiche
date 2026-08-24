"""Offline Palimpsest China-export integrity and context boundaries."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from copy import deepcopy
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from seiche import api, context_views, nbs_trust
from seiche import mcp_server as mcp
from seiche import palimpsest_china_intake as intake
from seiche import palimpsest_china_acceptance_cli as acceptance_cli
from seiche.markets.world import project_world_markets
from seiche.palimpsest_china_intake import (
    ACCEPTANCE_SCHEMA,
    AVAILABILITY_RECEIPT_SCHEMA,
    AVAILABILITY_SCHEMA,
    EXPORT_SCHEMA,
    LEGACY_MANIFEST_SCHEMA,
    MANIFEST_SCHEMA,
    POLICY_SCHEMA,
    PRODUCER_SCHEMA,
    REVIEW_MANIFEST_SCHEMA,
    SERIES_REGISTRY_SCHEMA,
    PalimpsestChinaIntakeError,
    build_acceptance_claim,
    build_acceptance_receipt,
    clear_accepted_export_cache,
    encode_acceptance_claim,
    load_accepted_export,
    verify_export,
)

ACCEPTED_AT = datetime(2026, 8, 24, 12, 2, tzinfo=UTC)
PALIMPSEST_COMMIT_SHA = "e" * 40
PALIMPSEST_RUN_ID = 12_345_678_901


@pytest.fixture(autouse=True)
def fixed_acceptance_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        intake,
        "_utc_now",
        lambda: datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        acceptance_cli,
        "_utc_now",
        lambda: datetime(2026, 8, 24, 12, 2, 30, tzinfo=UTC),
    )
    clear_accepted_export_cache()
    yield
    clear_accepted_export_cache()


@pytest.fixture
def signer(tmp_path: Path) -> tuple[Ed25519PrivateKey, str, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw().hex()
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / "trusted_operator_keys").write_text(public_key + "\n")
    return private_key, public_key, trust


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _ledger(value: object) -> bytes:
    """Match Palimpsest's append-only EconomicLedger JSONL serializer."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _observation_id(row: dict) -> str:
    body = dict(row)
    body.pop("observation_id", None)
    body["value"] = float(body["value"])
    body["quality"] = float(body["quality"])
    body["revision"] = int(body["revision"])
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _observation(
    *,
    series_id: str,
    source_series_id: str,
    value: float,
    unit: str,
    year: int = 2024,
    revision: int = 0,
    released_at: str = "2026-07-13T23:59:59+00:00",
    collected_at: str = "2026-08-24T12:00:00+00:00",
    source_document_version: str = "2026-07-13",
) -> dict:
    row = {
        "series_id": series_id,
        "value": float(value),
        "unit": unit,
        "frequency": "A",
        "period_start": f"{year:04d}-01-01",
        "period_end": f"{year:04d}-12-31",
        "released_at": released_at,
        "collected_at": collected_at,
        "source_id": "world_bank_wdi",
        "evidence_url": (
            "https://api.worldbank.org/v2/country/CHN/indicator/"
            f"{source_series_id}?source=2&date={year}%3A{year}&format=json&"
            "per_page=20000&footnote=y"
        ),
        "revision": revision,
        "status": "estimate",
        "geography": "CN",
        "sector": "all",
        "firm_size": "all",
        "ownership": "all",
        "quality": 0.8,
        "raw_sha256": "a" * 64,
        "metadata": {
            "family": "wdi_officially_recognized_sources",
            "source_series_id": source_series_id,
            "source_document_version": source_document_version,
            "parser_version": "world-bank-wdi-json.v1",
            "release_time_semantics": "dataset_lastupdated_upper_bound",
            "aggregation_window": "calendar_year",
        },
    }
    row["observation_id"] = _observation_id(row)
    return row


def _decision(
    source_id: str,
    *,
    allowed: bool,
    input_records: int,
    exported_records: int,
) -> dict:
    row = {
        "source_id": source_id,
        "decision": "allowed" if allowed else "denied",
        "decision_sha256": "",
        "values_allowed": allowed,
        "seiche_export_allowed": allowed,
        "license": "CC-BY-4.0" if allowed else None,
        "license_url": (
            "https://creativecommons.org/licenses/by/4.0/" if allowed else None
        ),
        "rights_evidence_url": (
            "https://datacatalog.worldbank.org/search/dataset/0037712/"
            "world-development-indicators"
            if allowed
            else None
        ),
        "attribution": (
            "World Bank, World Development Indicators"
            if allowed
            else "China Foreign Exchange Trade System"
        ),
        "reviewed_at": "2026-08-24T00:00:00Z",
        "expires_at": "2027-08-24T00:00:00Z",
        "reason": "Reviewed source decision retained with the export.",
        "input_records": input_records,
        "exported_records": exported_records,
    }
    row["decision_sha256"] = _decision_digest(row)
    return row


def _decision_digest(row: dict) -> str:
    policy_payload = {
        key: value
        for key, value in row.items()
        if key not in {"decision_sha256", "input_records", "exported_records"}
    }
    policy_payload["decision"] = "allow" if row["decision"] == "allowed" else "deny"
    return hashlib.sha256(_canonical(policy_payload)).hexdigest()


def _producer(*, event: str = "push", workflow_run: bool = True) -> dict:
    run = (
        {
            "provider": "github_actions",
            "workflow_file": ".github/workflows/tests.yml",
            "run_id": PALIMPSEST_RUN_ID,
            "run_attempt": 1,
            "head_sha": PALIMPSEST_COMMIT_SHA,
            "event": event,
            "conclusion": "success",
            "url": (
                "https://github.com/beepboop2025/palimpsest/actions/runs/"
                f"{PALIMPSEST_RUN_ID}"
            ),
        }
        if workflow_run
        else None
    )
    return {
        "schema_version": PRODUCER_SCHEMA,
        "repository": "beepboop2025/palimpsest",
        "commit_sha": PALIMPSEST_COMMIT_SHA,
        "workflow_run": run,
    }


def _producer_commit_evidence(
    *,
    commit_sha: str = PALIMPSEST_COMMIT_SHA,
) -> bytes:
    api_url = (
        f"https://api.github.com/repos/beepboop2025/palimpsest/commits/{commit_sha}"
    )
    parents = ("a" * 40, "b" * 40)
    value = {
        "sha": commit_sha,
        "url": api_url,
        "author": {"login": "beepboop2025"},
        "committer": {"login": "web-flow"},
        "parents": [
            {
                "sha": parent,
                "url": (
                    "https://api.github.com/repos/beepboop2025/palimpsest/commits/"
                    f"{parent}"
                ),
            }
            for parent in parents
        ],
        "commit": {
            "verification": {
                "verified": True,
                "reason": "valid",
                "signature": "test verified GitHub signature",
                "payload": "test signed Git commit payload",
                "verified_at": "2026-08-24T11:59:00Z",
            }
        },
    }
    # The handoff preserves the GitHub API response bytes; they are not required
    # to use Palimpsest's canonical JSON representation.
    return (json.dumps(value, indent=2) + "\n").encode()


def _normalized_producer_commit_evidence(
    *,
    commit_sha: str = PALIMPSEST_COMMIT_SHA,
) -> dict:
    raw = _producer_commit_evidence(commit_sha=commit_sha)
    return {
        "path": "github-commit.json",
        "request_url": (
            "https://api.github.com/repos/beepboop2025/palimpsest/commits/"
            f"{commit_sha}?per_page=1"
        ),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "sha": commit_sha,
        "author_login": "beepboop2025",
        "committer_login": "web-flow",
        "parent_shas": ["a" * 40, "b" * 40],
        "verification": {
            "verified": True,
            "reason": "valid",
            "verified_at": "2026-08-24T11:59:00Z",
        },
    }


def _producer_main_evidence(*, commit_sha: str = PALIMPSEST_COMMIT_SHA) -> bytes:
    value = {
        "name": "main",
        "commit": {
            "sha": commit_sha,
            "url": (
                "https://api.github.com/repos/beepboop2025/palimpsest/commits/"
                f"{commit_sha}"
            ),
        },
        "protected": False,
    }
    return (json.dumps(value, indent=2) + "\n").encode()


def _normalized_producer_main_evidence() -> dict:
    raw = _producer_main_evidence()
    return {
        "path": "github-main-branch.json",
        "request_url": (
            "https://api.github.com/repos/beepboop2025/palimpsest/branches/main"
        ),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "observed_at": "2026-08-24T12:02:00Z",
        "name": "main",
        "commit": {"sha": PALIMPSEST_COMMIT_SHA},
        "protected": False,
    }


def _operator_confirmations() -> dict[str, bool]:
    return {
        "github_attestation_verified": True,
        "exact_checksum_subject_set_verified": True,
        "producer_raw_identity_verified": True,
        "detached_first_parent_lineage_rebuild_verified": True,
        "current_main_branch_evidence_verified": True,
        "rights_and_freshness_reviewed": True,
    }


def _git_blob_oid(payload: bytes) -> str:
    return hashlib.sha1(  # noqa: S324 - fixture matches Git's object ID
        f"blob {len(payload)}\0".encode("ascii") + payload
    ).hexdigest()


def _handoff_files(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    ledger_bytes: bytes,
    availability_bytes: bytes,
) -> tuple[bytes, bytes, bytes, bytes]:
    manifest = json.loads(manifest_bytes)
    commit_raw = _producer_commit_evidence()
    normalized_commit = _normalized_producer_commit_evidence()
    api_url = normalized_commit["request_url"].removesuffix("?per_page=1")
    lineage_commit = {
        "sha": PALIMPSEST_COMMIT_SHA,
        "request_url": normalized_commit["request_url"],
        "api_url": api_url,
        "author_login": "beepboop2025",
        "committer_login": "web-flow",
        "parent_shas": normalized_commit["parent_shas"],
        "verification": normalized_commit["verification"],
        "raw_sha256": hashlib.sha256(commit_raw).hexdigest(),
        "raw_bytes": len(commit_raw),
    }
    evidence_row = {
        "schema_version": "palimpsest.china-economic-lineage-evidence-record.v1",
        "sequence": 0,
        "commit_sha": PALIMPSEST_COMMIT_SHA,
        "raw_sha256": hashlib.sha256(commit_raw).hexdigest(),
        "raw_bytes": len(commit_raw),
        "encoding": "base64",
        "payload_base64": base64.b64encode(commit_raw).decode("ascii"),
    }
    evidence_bytes = _canonical(evidence_row)
    availability = json.loads(availability_bytes)
    registry_receipt = {
        "path": "config/china_econ_wdi_series.json",
        "schema_version": SERIES_REGISTRY_SCHEMA,
        "sha256": manifest["series_registry"]["sha256"],
        "bytes": manifest["series_registry"]["bytes"],
        "series_records": 2,
    }
    chain_row = {
        "schema_version": "palimpsest.china-economic-lineage-record.v1",
        "sequence": 0,
        "commit": lineage_commit,
        "previous_change_sha": None,
        "git_tree_entries": {
            "config/china_econ_wdi_series.json": {
                "mode": "100644",
                "type": "blob",
                "object_sha": "f" * 40,
            },
            "readings/china-econ-wdi-observations.jsonl": {
                "mode": "100644",
                "type": "blob",
                "object_sha": _git_blob_oid(ledger_bytes),
            },
            "readings/china-econ-wdi-latest.json": {
                "mode": "100644",
                "type": "blob",
                "object_sha": _git_blob_oid(availability_bytes),
            },
        },
        "registry_transition": {
            "state": "initial_registry",
            "previous": None,
            "current": registry_receipt,
            "added_source_indicators": ["AG.PRD.CREL.MT", "FM.LBL.BMNY.ZG"],
        },
        "ledger": {
            "path": "readings/china-econ-wdi-observations.jsonl",
            **_ledger_snapshot(ledger_bytes),
        },
        "availability_receipt": {
            "path": "readings/china-econ-wdi-latest.json",
            "schema_version": AVAILABILITY_RECEIPT_SCHEMA,
            "sha256": hashlib.sha256(availability_bytes).hexdigest(),
            "bytes": len(availability_bytes),
            "generated_at": availability["generated_at"],
        },
        "ledger_transition": {
            "state": "initial_seed",
            "prefix_bytes": 0,
            "appended_records": len(ledger_bytes.splitlines()),
            "receipt_appended_observations": len(ledger_bytes.splitlines()),
        },
    }
    chain_bytes = _canonical(chain_row)
    chain_receipt = {
        "schema_version": "palimpsest.china-economic-lineage-chain.v1",
        "path": "china-econ-wdi-lineage-chain.jsonl",
        "sha256": hashlib.sha256(chain_bytes).hexdigest(),
        "bytes": len(chain_bytes),
        "records": 1,
        "root_commit_sha": PALIMPSEST_COMMIT_SHA,
        "tip_commit_sha": PALIMPSEST_COMMIT_SHA,
        "evaluated_at_commit_sha": PALIMPSEST_COMMIT_SHA,
        "governed_paths": [
            "config/china_econ_wdi_series.json",
            "readings/china-econ-wdi-observations.jsonl",
            "readings/china-econ-wdi-latest.json",
        ],
        "evidence": {
            "schema_version": "palimpsest.china-economic-lineage-evidence.v1",
            "path": "github-commit-lineage-evidence.jsonl",
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "bytes": len(evidence_bytes),
            "records": 1,
        },
    }
    live_sha = "1" * 64
    live_bytes = 10_000
    live_batch_sha = "8" * 64
    core = {
        "china-econ-wdi-latest.json": {
            "sha256": hashlib.sha256(availability_bytes).hexdigest(),
            "bytes": len(availability_bytes),
        },
        "china-econ-wdi-live-check.json": {
            "sha256": live_sha,
            "bytes": live_bytes,
        },
        "china-econ-wdi-lineage-chain.jsonl": {
            "sha256": hashlib.sha256(chain_bytes).hexdigest(),
            "bytes": len(chain_bytes),
        },
        "china-econ-wdi-observations.jsonl": {
            "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
            "bytes": len(ledger_bytes),
        },
        "china_econ_source_policy.json": {
            "sha256": manifest["policy"]["sha256"],
            "bytes": 1_000,
        },
        "china_econ_wdi_series.json": {
            "sha256": manifest["series_registry"]["sha256"],
            "bytes": manifest["series_registry"]["bytes"],
        },
        "github-commit.json": {
            "sha256": hashlib.sha256(commit_raw).hexdigest(),
            "bytes": len(commit_raw),
        },
        "github-commit-lineage-evidence.jsonl": {
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "bytes": len(evidence_bytes),
        },
        "palimpsest-china-economic-export-v1.jsonl": {
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "bytes": len(artifact_bytes),
        },
        "palimpsest-china-economic-export-v3-manifest.json": {
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "bytes": len(manifest_bytes),
        },
        "world-bank-wdi-response.json": {
            "sha256": live_batch_sha,
            "bytes": 20_000,
        },
    }
    handoff = {
        "schema_version": "palimpsest.china-economic-handoff-receipt.v3",
        "producer": manifest["producer"],
        "producer_commit_evidence": {
            "path": "github-commit.json",
            "sha": PALIMPSEST_COMMIT_SHA,
            "request_url": normalized_commit["request_url"],
            "api_url": api_url,
            "author_login": "beepboop2025",
            "committer_login": "web-flow",
            "parent_shas": normalized_commit["parent_shas"],
            "verification": normalized_commit["verification"],
            "sha256": hashlib.sha256(commit_raw).hexdigest(),
            "bytes": len(commit_raw),
        },
        "revision_lineage": {
            "mode": "git_tracked_reviewed_merge_chain",
            "chain": chain_receipt,
            "cross_run_revision_authority": True,
            "live_check_new_vintages_appended": 0,
        },
        "artifact": manifest["artifact"],
        "input_ledger": manifest["input_ledger"],
        "reviewed_availability_receipt": manifest["availability_receipt"],
        "live_verification": {
            "path": "china-econ-wdi-live-check.json",
            "sha256": live_sha,
            "bytes": live_bytes,
            "batch_raw_sha256": live_batch_sha,
            "current_availability_sha256": hashlib.sha256(
                _canonical(availability["availability"])
            ).hexdigest(),
        },
        "live_raw_response": {
            "path": "world-bank-wdi-response.json",
            "sha256": live_batch_sha,
        },
        "files": [{"path": name, **receipt} for name, receipt in sorted(core.items())],
    }
    handoff_bytes = _canonical(handoff)
    checksum_hashes = {
        **{name: receipt["sha256"] for name, receipt in core.items()},
        "handoff-receipt.json": hashlib.sha256(handoff_bytes).hexdigest(),
    }
    checksums_bytes = "".join(
        f"{digest} *{name}\n" for name, digest in sorted(checksum_hashes.items())
    ).encode("ascii")
    return handoff_bytes, checksums_bytes, chain_bytes, evidence_bytes


def _reseal_authority_documents(
    handoff: dict,
    chain_rows: list[dict],
    evidence_rows: list[dict],
) -> tuple[bytes, bytes, bytes, bytes]:
    """Recompute only outer byte receipts after an intentional test mutation."""

    chain_bytes = b"".join(_canonical(row) for row in chain_rows)
    evidence_bytes = b"".join(_canonical(row) for row in evidence_rows)
    chain_receipt = handoff["revision_lineage"]["chain"]
    chain_receipt.update(
        sha256=hashlib.sha256(chain_bytes).hexdigest(),
        bytes=len(chain_bytes),
        records=len(chain_rows),
    )
    chain_receipt["evidence"].update(
        sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        bytes=len(evidence_bytes),
        records=len(evidence_rows),
    )
    file_receipts = {row["path"]: row for row in handoff["files"]}
    file_receipts["china-econ-wdi-lineage-chain.jsonl"].update(
        sha256=hashlib.sha256(chain_bytes).hexdigest(),
        bytes=len(chain_bytes),
    )
    file_receipts["github-commit-lineage-evidence.jsonl"].update(
        sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        bytes=len(evidence_bytes),
    )
    handoff_bytes = _canonical(handoff)
    checksum_hashes = {row["path"]: row["sha256"] for row in handoff["files"]}
    checksum_hashes["handoff-receipt.json"] = hashlib.sha256(handoff_bytes).hexdigest()
    checksums_bytes = "".join(
        f"{digest} *{name}\n" for name, digest in sorted(checksum_hashes.items())
    ).encode("ascii")
    return handoff_bytes, checksums_bytes, chain_bytes, evidence_bytes


def _authority_inputs(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    ledger_bytes: bytes,
    availability_bytes: bytes,
) -> dict[str, object]:
    handoff, checksums, chain, lineage_evidence = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    return {
        "handoff_bytes": handoff,
        "checksums_bytes": checksums,
        "lineage_chain_bytes": chain,
        "lineage_evidence_bytes": lineage_evidence,
        "operator_confirmations": _operator_confirmations(),
    }


def _identity_digest(identities: set[tuple[str, int]]) -> str:
    return hashlib.sha256(
        b"".join(
            _canonical({"indicator_id": indicator_id, "year": year})
            for indicator_id, year in sorted(identities)
        )
    ).hexdigest()


def _indicator_digest(indicators: set[str]) -> str:
    return hashlib.sha256(
        b"".join(
            _canonical({"indicator_id": indicator_id})
            for indicator_id in sorted(indicators)
        )
    ).hexdigest()


def _series_digest(series_ids: set[str]) -> str:
    return hashlib.sha256(
        b"".join(
            _canonical({"series_id": series_id}) for series_id in sorted(series_ids)
        )
    ).hexdigest()


def _ledger_snapshot(raw: bytes) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "records": len(raw.splitlines()),
    }


def _availability_document(
    entries: list[dict],
    *,
    ledger_bytes: bytes,
    ledger_before_bytes: bytes = b"",
) -> bytes:
    ledger_rows = [json.loads(line) for line in ledger_bytes.splitlines()]
    indicators = {row["indicator_id"] for row in entries}
    current = {
        (row["indicator_id"], row["year"]) for row in entries if row["available"]
    }
    populated_indicators = {indicator_id for indicator_id, _year in current}
    requested_start_year = 1960
    requested_end_year = 2026
    response_coverage = {
        "coverage_semantics": "exact_current_response",
        "requested_start_year": requested_start_year,
        "requested_end_year": requested_end_year,
        "configured_indicators": len(indicators),
        "represented_indicators": len(indicators),
        "populated_indicators": len(populated_indicators),
        "null_only_indicators": len(indicators - populated_indicators),
        "source_rows": len(entries),
        "populated_observations": len(current),
        "null_rows": len(entries) - len(current),
        "period_start": (
            f"{min(year for _indicator, year in current):04d}-01-01"
            if current
            else None
        ),
        "period_end": (
            f"{max(year for _indicator, year in current):04d}-12-31"
            if current
            else None
        ),
    }
    ledger_years = {int(row["period_end"][:4]) for row in ledger_rows}
    ledger_series = {row["series_id"] for row in ledger_rows}
    after = _ledger_snapshot(ledger_bytes)
    before = _ledger_snapshot(ledger_before_bytes)
    payload = {
        "appended_observations": after["records"] - before["records"],
        "availability": {
            "coverage_semantics": "exact_current_response",
            "entries": entries,
            "null_records": len([row for row in entries if row["available"] is False]),
            "records": len(entries),
            "schema_version": AVAILABILITY_SCHEMA,
            "withdrawal_limitation": (
                "An unavailable indicator/year in this exact response is not "
                "appended as a numeric observation. Any older value retained in "
                "the accumulated ledger must not be treated as present in "
                "current-response coverage."
            ),
            "withdrawal_state": ("residual_gate_no_append_only_withdrawal_ledger"),
        },
        "batch_raw_sha256": "9" * 64,
        "context_only": True,
        "dataset": "World Development Indicators",
        "dataset_last_updated": "2026-07-13",
        "generated_at": "2026-08-24T12:00:30Z",
        "indicator_provenance": {
            "schema_version": ("palimpsest-china-econ-wdi-indicator-provenance.v1"),
            "records": len(indicators),
            "entries": [
                {
                    "indicator_id": indicator_id,
                    "reviewed_name": f"Reviewed {indicator_id}",
                    "source_title": f"Source title {indicator_id}",
                }
                for indicator_id in sorted(indicators)
            ],
            "upstream_attribution_state": "residual_gate",
            "upstream_attribution_requirement": (
                intake.WDI_UPSTREAM_ATTRIBUTION_REQUIREMENT
            ),
        },
        "ledger_after": after,
        "ledger_before": before,
        "ledger_coverage": {
            "coverage_semantics": (
                "accumulated_append_only_history_not_current_response"
            ),
            "records": len(ledger_rows),
            "series_count": len(ledger_series),
            "period_start": (
                f"{min(ledger_years):04d}-01-01" if ledger_years else None
            ),
            "period_end": (f"{max(ledger_years):04d}-12-31" if ledger_years else None),
        },
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "limitations": [
            "WDI is annual structural context, not live market data.",
            intake.WDI_UPSTREAM_ATTRIBUTION_REQUIREMENT,
        ],
        "publication_state": "public_context_only",
        "redistribution_status": "allowed",
        "response_coverage": response_coverage,
        "revision_lineage": {
            "durable_cross_run": True,
            "ledger_path": "readings/china-econ-wdi-observations.jsonl",
            "mode": "git_tracked_append_only",
        },
        "rights_evidence_url": (
            "https://datacatalog.worldbank.org/search/dataset/0037712/"
            "world-development-indicators"
        ),
        "schema_version": AVAILABILITY_RECEIPT_SCHEMA,
        "scoring_allowed": False,
        "source_id": "world_bank_wdi",
    }
    collector_payload_sha256 = hashlib.sha256(_canonical(payload)).hexdigest()
    joined_indicators = ";".join(sorted(indicators))
    payload["collector_artifact"] = {
        "schema_version": "palimpsest-collector-artifact/v1",
        "collector_id": "world-bank-wdi-china",
        "source_receipt": {
            "url": (
                "https://api.worldbank.org/v2/country/CHN/indicator/"
                f"{joined_indicators}?source=2&date={requested_start_year}%3A"
                f"{requested_end_year}&format=json&per_page=20000&footnote=y"
            ),
            "raw_sha256": "9" * 64,
            "dataset_last_updated": "2026-07-13",
            "license": "CC-BY-4.0",
        },
        "freshness": {
            "evidence_state": "fresh",
            "observed_at": "2026-08-24T12:00:30Z",
            "native_cadence": "annual",
            "dataset_age_days": 42,
        },
        "coverage": response_coverage,
        "abstention": None,
        "payload_sha256": collector_payload_sha256,
    }
    return _canonical(payload)


def _reseal_collector(run: dict) -> None:
    payload = deepcopy(run)
    payload.pop("collector_artifact")
    run["collector_artifact"]["payload_sha256"] = hashlib.sha256(
        _canonical(payload)
    ).hexdigest()


def _replace_availability(manifest: dict, run: dict) -> bytes:
    availability_bytes = _canonical(run)
    manifest["availability_receipt"].update(
        sha256=hashlib.sha256(availability_bytes).hexdigest(),
        bytes=len(availability_bytes),
    )
    return availability_bytes


def _wrapper(row: dict, *channels: str) -> dict:
    return {
        "schema_version": EXPORT_SCHEMA,
        "context_only": True,
        "scoring_allowed": False,
        "market_channels": sorted(channels),
        "observation": row,
    }


def _bundle() -> tuple[dict, bytes, bytes, bytes, bytes]:
    cereal = _observation(
        series_id="cn.wdi.cereal_production",
        source_series_id="AG.PRD.CREL.MT",
        value=652_290_000,
        unit="metric tons",
    )
    money = _observation(
        series_id="cn.wdi.broad_money_growth",
        source_series_id="FM.LBL.BMNY.ZG",
        value=8.1,
        unit="annual percent",
    )
    wrappers = [
        _wrapper(cereal, "capital_market"),
        _wrapper(money, "capital_market", "money_market"),
    ]
    artifact_bytes = b"".join(_canonical(row) for row in wrappers)
    ledger_bytes = b"".join(_ledger(row) for row in (cereal, money))
    current_identities = {
        ("AG.PRD.CREL.MT", 2024),
        ("FM.LBL.BMNY.ZG", 2024),
    }
    projectable_indicators = {identity[0] for identity in current_identities}
    projectable_series = {
        "cn.wdi.broad_money_growth",
        "cn.wdi.cereal_production",
    }
    availability_bytes = _availability_document(
        [
            {
                "available": True,
                "footnote": None,
                "indicator_id": indicator_id,
                "year": year,
            }
            for indicator_id, year in sorted(current_identities)
        ],
        ledger_bytes=ledger_bytes,
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at": "2026-08-24T12:01:00Z",
        "context_only": True,
        "scoring_allowed": False,
        "producer": _producer(),
        "artifact": {
            "path": "data/review/palimpsest-china-economic-export-v1.jsonl",
            "media_type": "application/x-ndjson",
            "schema_version": EXPORT_SCHEMA,
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "bytes": len(artifact_bytes),
            "records": len(wrappers),
        },
        "input_ledger": {
            "path": "data/review/china-econ-wdi-observations.jsonl",
            "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
            "bytes": len(ledger_bytes),
            "records": len(wrappers),
        },
        "policy": {
            "path": "china_econ_source_policy.json",
            "sha256": "c" * 64,
            "schema_version": POLICY_SCHEMA,
            "evaluated_at": "2026-08-24T12:01:00Z",
        },
        "series_registry": {
            "path": "china_econ_wdi_series.json",
            "sha256": "d" * 64,
            "bytes": 20_000,
            "schema_version": SERIES_REGISTRY_SCHEMA,
        },
        "availability_receipt": {
            "path": "data/review/china-econ-wdi-latest.json",
            "sha256": hashlib.sha256(availability_bytes).hexdigest(),
            "bytes": len(availability_bytes),
            "schema_version": AVAILABILITY_RECEIPT_SCHEMA,
            "generated_at": "2026-08-24T12:00:30Z",
            "batch_raw_sha256": "9" * 64,
            "availability_schema_version": AVAILABILITY_SCHEMA,
            "current_numeric_identities_sha256": _identity_digest(current_identities),
            "current_numeric_identities_records": len(current_identities),
            "current_projectable_series_sha256": _series_digest(projectable_series),
            "current_projectable_series_records": len(projectable_series),
            "current_projectable_source_indicators_sha256": _indicator_digest(
                projectable_indicators
            ),
            "current_projectable_source_indicators_records": len(
                projectable_indicators
            ),
            "withdrawn_numeric_identities_sha256": _identity_digest(set()),
            "withdrawn_numeric_identities_records": 0,
        },
        "source_decisions": [
            _decision(
                "cfets_benchmarks",
                allowed=False,
                input_records=0,
                exported_records=0,
            ),
            _decision(
                "chinamoney",
                allowed=False,
                input_records=0,
                exported_records=0,
            ),
            _decision(
                "world_bank_wdi",
                allowed=True,
                input_records=len(wrappers),
                exported_records=len(wrappers),
            ),
        ],
        "market_channel_mapping": {
            "capital_market": [
                "cn.wdi.broad_money_growth",
                "cn.wdi.cereal_production",
            ],
            "money_market": ["cn.wdi.broad_money_growth"],
        },
    }
    return (
        manifest,
        _canonical(manifest),
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )


def _replace_artifact(manifest: dict, wrappers: list[dict]) -> tuple[bytes, bytes]:
    artifact_bytes = b"".join(_canonical(row) for row in wrappers)
    manifest["artifact"].update(
        sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        bytes=len(artifact_bytes),
        records=len(wrappers),
    )
    return _canonical(manifest), artifact_bytes


def _rebuild_v3(
    manifest: dict,
    *,
    wrappers: list[dict],
    ledger_rows: list[dict],
    availability_entries: list[dict],
) -> tuple[bytes, bytes, bytes, bytes]:
    """Recompute fixture commitments from exact v3 handoff inputs."""

    artifact_bytes = b"".join(_canonical(row) for row in wrappers)
    ledger_bytes = b"".join(_ledger(row) for row in ledger_rows)
    availability_bytes = _availability_document(
        availability_entries,
        ledger_bytes=ledger_bytes,
    )
    ledger_identities = {
        (row["metadata"]["source_series_id"], int(row["period_end"][:4]))
        for row in ledger_rows
    }
    current_identities = {
        (row["indicator_id"], row["year"])
        for row in availability_entries
        if row["available"]
    }
    source_to_series = {
        row["metadata"]["source_series_id"]: row["series_id"] for row in ledger_rows
    }
    withdrawn = ledger_identities - current_identities
    withdrawn_sources = {indicator_id for indicator_id, _year in withdrawn}
    projectable_indicators = set(source_to_series) - withdrawn_sources
    projectable_series = {
        source_to_series[indicator_id] for indicator_id in projectable_indicators
    }

    manifest["artifact"].update(
        sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        bytes=len(artifact_bytes),
        records=len(wrappers),
    )
    manifest["input_ledger"].update(
        sha256=hashlib.sha256(ledger_bytes).hexdigest(),
        bytes=len(ledger_bytes),
        records=len(ledger_rows),
    )
    manifest["availability_receipt"].update(
        sha256=hashlib.sha256(availability_bytes).hexdigest(),
        bytes=len(availability_bytes),
        current_numeric_identities_sha256=_identity_digest(current_identities),
        current_numeric_identities_records=len(current_identities),
        current_projectable_series_sha256=_series_digest(projectable_series),
        current_projectable_series_records=len(projectable_series),
        current_projectable_source_indicators_sha256=_indicator_digest(
            projectable_indicators
        ),
        current_projectable_source_indicators_records=len(projectable_indicators),
        withdrawn_numeric_identities_sha256=_identity_digest(withdrawn),
        withdrawn_numeric_identities_records=len(withdrawn),
    )
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["input_records"] = len(ledger_rows)
    wdi["exported_records"] = len(wrappers)
    manifest["market_channel_mapping"] = {
        channel: sorted(
            {
                wrapper["observation"]["series_id"]
                for wrapper in wrappers
                if channel in wrapper["market_channels"]
            }
        )
        for channel in ("capital_market", "money_market")
    }
    return _canonical(manifest), artifact_bytes, ledger_bytes, availability_bytes


def _verify(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    ledger_bytes: bytes,
    availability_bytes: bytes,
    *,
    accepted_at: datetime = ACCEPTED_AT,
):
    return verify_export(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=ledger_bytes,
        availability_bytes=availability_bytes,
        accepted_at=accepted_at,
    )


def _signed_receipt(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    ledger_bytes: bytes,
    availability_bytes: bytes,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> bytes:
    private_key, public_key, trust = signer
    handoff, checksums, chain, lineage_evidence = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    claim = build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=_producer_commit_evidence(),
        producer_main_evidence_bytes=_producer_main_evidence(),
        handoff_bytes=handoff,
        checksums_bytes=checksums,
        lineage_chain_bytes=chain,
        lineage_evidence_bytes=lineage_evidence,
        operator_confirmations=_operator_confirmations(),
        accepted_at=ACCEPTED_AT,
        signer_key_id=public_key,
    )
    signature = private_key.sign(encode_acceptance_claim(claim)).hex()
    return build_acceptance_receipt(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=_producer_commit_evidence(),
        producer_main_evidence_bytes=_producer_main_evidence(),
        handoff_bytes=handoff,
        checksums_bytes=checksums,
        lineage_chain_bytes=chain,
        lineage_evidence_bytes=lineage_evidence,
        operator_confirmations=_operator_confirmations(),
        accepted_at=ACCEPTED_AT,
        signer_key_id=public_key,
        signature=signature,
        attest_dir=trust,
    )


def _raw_signed_receipt(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> bytes:
    """Sign the closed claim directly to exercise load-time defenses."""

    private_key, public_key, _trust = signer
    _manifest, good_manifest, good_artifact, ledger, availability = _bundle()
    handoff, checksums, chain, lineage_evidence = _handoff_files(
        good_manifest, good_artifact, ledger, availability
    )
    claim = build_acceptance_claim(
        good_manifest,
        good_artifact,
        input_ledger_bytes=ledger,
        availability_bytes=availability,
        producer_commit_evidence_bytes=_producer_commit_evidence(),
        producer_main_evidence_bytes=_producer_main_evidence(),
        handoff_bytes=handoff,
        checksums_bytes=checksums,
        lineage_chain_bytes=chain,
        lineage_evidence_bytes=lineage_evidence,
        operator_confirmations=_operator_confirmations(),
        accepted_at=ACCEPTED_AT,
        signer_key_id=public_key,
    )
    claim["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    claim["artifact_sha256"] = hashlib.sha256(artifact_bytes).hexdigest()
    claim["operator_confirmations"]["manifest_sha256"] = claim["manifest_sha256"]
    signature = private_key.sign(encode_acceptance_claim(claim)).hex()
    return _canonical({**claim, "signature": signature})


def _write_signed_bundle(
    directory: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    manifest_path = directory / "manifest.json"
    artifact_path = directory / "artifact.jsonl"
    acceptance_path = directory / "acceptance.json"
    ledger_path = directory / "ledger.jsonl"
    availability_path = directory / "availability.json"
    producer_commit_evidence_path = directory / "github-commit.json"
    producer_main_evidence_path = directory / "github-main-branch.json"
    handoff_path = directory / "handoff-receipt.json"
    checksums_path = directory / "SHA256SUMS"
    lineage_chain_path = directory / "china-econ-wdi-lineage-chain.jsonl"
    lineage_evidence_path = directory / "github-commit-lineage-evidence.jsonl"
    manifest_path.write_bytes(manifest_bytes)
    artifact_path.write_bytes(artifact_bytes)
    ledger_path.write_bytes(ledger_bytes)
    availability_path.write_bytes(availability_bytes)
    producer_commit_evidence_path.write_bytes(_producer_commit_evidence())
    producer_main_evidence_path.write_bytes(_producer_main_evidence())
    handoff, checksums, chain, lineage_evidence = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    handoff_path.write_bytes(handoff)
    checksums_path.write_bytes(checksums)
    lineage_chain_path.write_bytes(chain)
    lineage_evidence_path.write_bytes(lineage_evidence)
    acceptance_path.write_bytes(
        _signed_receipt(
            manifest_bytes,
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
            signer,
        )
    )
    return (
        manifest_path,
        artifact_path,
        acceptance_path,
        ledger_path,
        availability_path,
        producer_commit_evidence_path,
        producer_main_evidence_path,
    )


def _load_written_bundle(
    paths: tuple[Path, Path, Path, Path, Path, Path, Path],
    *,
    attest_dir: Path,
    now: datetime,
):
    (
        manifest,
        artifact,
        acceptance,
        ledger,
        availability,
        producer_evidence,
        producer_main_evidence,
    ) = paths
    return load_accepted_export(
        manifest,
        artifact,
        acceptance,
        input_ledger_path=ledger,
        availability_path=availability,
        producer_commit_evidence_path=producer_evidence,
        producer_main_evidence_path=producer_main_evidence,
        handoff_path=manifest.parent / "handoff-receipt.json",
        checksums_path=manifest.parent / "SHA256SUMS",
        lineage_chain_path=manifest.parent / "china-econ-wdi-lineage-chain.jsonl",
        lineage_evidence_path=(
            manifest.parent / "github-commit-lineage-evidence.jsonl"
        ),
        attest_dir=attest_dir,
        now=now,
    )


def _installed_authority_paths(manifest_path: Path) -> dict[str, Path]:
    return {
        "handoff_path": manifest_path.parent / "handoff-receipt.json",
        "checksums_path": manifest_path.parent / "SHA256SUMS",
        "lineage_chain_path": (
            manifest_path.parent / "china-econ-wdi-lineage-chain.jsonl"
        ),
        "lineage_evidence_path": (
            manifest_path.parent / "github-commit-lineage-evidence.jsonl"
        ),
    }


def _expanded_bundle() -> tuple[bytes, bytes, bytes, bytes]:
    manifest, _manifest_bytes, artifact_bytes, _ledger_bytes, _availability_bytes = (
        _bundle()
    )
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    additions = (
        "cn.wdi.equity_market_cap_share",
        "cn.wdi.equity_turnover_ratio",
        "cn.wdi.fdi_net_inflows_share",
        "cn.wdi.electric_power_consumption_per_capita",
        "cn.wdi.container_port_traffic",
    )
    for position, series_id in enumerate(additions, 1):
        source_series_id = f"TEST.WDI.SERIES.{position}"
        wrappers.append(
            {
                "schema_version": EXPORT_SCHEMA,
                "context_only": True,
                "scoring_allowed": False,
                "market_channels": ["capital_market"],
                "observation": _observation(
                    series_id=series_id,
                    source_series_id=source_series_id,
                    value=float(position),
                    unit="index",
                ),
            }
        )
    artifact_bytes = b"".join(_canonical(row) for row in wrappers)
    ledger_rows = [row["observation"] for row in wrappers]
    ledger_bytes = b"".join(_ledger(row) for row in ledger_rows)
    identities = {
        (row["metadata"]["source_series_id"], int(row["period_end"][:4]))
        for row in ledger_rows
    }
    indicators = {identity[0] for identity in identities}
    series_ids = {row["series_id"] for row in ledger_rows}
    availability_bytes = _availability_document(
        [
            {
                "available": True,
                "footnote": None,
                "indicator_id": indicator_id,
                "year": year,
            }
            for indicator_id, year in sorted(identities)
        ],
        ledger_bytes=ledger_bytes,
    )
    manifest["artifact"].update(
        sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        bytes=len(artifact_bytes),
        records=len(wrappers),
    )
    manifest["input_ledger"].update(
        sha256=hashlib.sha256(ledger_bytes).hexdigest(),
        bytes=len(ledger_bytes),
        records=len(ledger_rows),
    )
    manifest["availability_receipt"].update(
        sha256=hashlib.sha256(availability_bytes).hexdigest(),
        bytes=len(availability_bytes),
        current_numeric_identities_sha256=_identity_digest(identities),
        current_numeric_identities_records=len(identities),
        current_projectable_series_sha256=_series_digest(series_ids),
        current_projectable_series_records=len(series_ids),
        current_projectable_source_indicators_sha256=_indicator_digest(indicators),
        current_projectable_source_indicators_records=len(indicators),
    )
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["input_records"] = len(wrappers)
    wdi["exported_records"] = len(wrappers)
    manifest["market_channel_mapping"]["capital_market"] = sorted(
        {row["observation"]["series_id"] for row in wrappers}
    )
    return _canonical(manifest), artifact_bytes, ledger_bytes, availability_bytes


def _series_boundary_bundle(series_count: int) -> tuple[bytes, bytes, bytes, bytes]:
    manifest, _manifest_bytes, _artifact, _ledger_bytes, _availability = _bundle()
    ledger_rows: list[dict] = []
    wrappers: list[dict] = []
    availability_entries: list[dict] = []
    for position in range(series_count):
        source_series_id = f"TEST.WDI.SERIES.{position:04d}"
        row = _observation(
            series_id=f"cn.wdi.boundary_{position:04d}",
            source_series_id=source_series_id,
            value=float(position + 1),
            unit="index",
        )
        ledger_rows.append(row)
        wrappers.append(_wrapper(row, "capital_market", "money_market"))
        availability_entries.append(
            {
                "available": True,
                "footnote": None,
                "indicator_id": source_series_id,
                "year": 2024,
            }
        )
    return _rebuild_v3(
        manifest,
        wrappers=wrappers,
        ledger_rows=ledger_rows,
        availability_entries=availability_entries,
    )


def test_verified_export_preserves_four_clocks_and_bounded_channel_families() -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )

    context = _verify(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    public = context.to_dict()

    assert public["status"] == "structural"
    assert public["freshness"] == {
        "native_cadence": "annual",
        "classification": "structural",
        "state": "annual_structural",
        "is_live_market_data": False,
        "advances_world_observation_clocks": False,
        "advances_cn_cny_freshness": False,
    }
    assert public["clocks"] == {
        "latest_observation_period_end": "2024-12-31",
        "latest_source_released_at": "2026-07-13T23:59:59Z",
        "latest_palimpsest_collected_at": "2026-08-24T12:00:00Z",
        "seiche_accepted_at": "2026-08-24T12:02:00Z",
    }
    assert public["scoring_eligible"] is False
    assert public["cn_cny_gauge_eligible"] is False
    assert public["market_observation_eligible"] is False
    assert set(public["channel_families"]) == {"capital_market", "money_market"}
    assert public["channel_families"]["capital_market"]["series_count"] == 2
    assert public["channel_families"]["money_market"]["series_count"] == 1
    money = public["channel_families"]["money_market"]["observations"][0]
    assert money["released_at"] == "2026-07-13T23:59:59+00:00"
    assert money["collected_at"] == "2026-08-24T12:00:00+00:00"
    assert money["accepted_at"] == "2026-08-24T12:02:00Z"
    assert public["provenance"]["producer"] == _producer()


def test_thick_corpus_accepts_512_series_and_rejects_513() -> None:
    accepted = _verify(*_series_boundary_bundle(512))
    assert len({row.series_id for row in accepted.observations}) == 512
    public = accepted.to_dict()
    assert public["current_series_count"] == 512
    assert (
        public["channel_families"]["capital_market"]["returned_observation_count"] <= 6
    )
    assert (
        public["channel_families"]["capital_market"]["observations_truncated"] is True
    )

    with pytest.raises(PalimpsestChinaIntakeError, match="at most 512"):
        _verify(*_series_boundary_bundle(513))


def test_review_manifest_may_omit_run_but_cannot_be_signed_or_loaded(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    manifest["schema_version"] = REVIEW_MANIFEST_SCHEMA
    manifest.pop("availability_receipt")
    manifest["producer"] = _producer(workflow_run=False)
    manifest_bytes = _canonical(manifest)

    review = verify_export(manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT)
    assert review.owner_attested is False
    assert review.to_dict()["provenance"]["producer"]["workflow_run"] is None
    with pytest.raises(
        PalimpsestChinaIntakeError, match="successful exact-commit push"
    ):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )

    manifest_path = tmp_path / "manifest.json"
    artifact_path = tmp_path / "artifact.jsonl"
    acceptance_path = tmp_path / "acceptance.json"
    producer_evidence_path = tmp_path / "github-commit.json"
    producer_main_evidence_path = tmp_path / "github-main-branch.json"
    handoff_path = tmp_path / "handoff-receipt.json"
    checksums_path = tmp_path / "SHA256SUMS"
    lineage_chain_path = tmp_path / "china-econ-wdi-lineage-chain.jsonl"
    lineage_evidence_path = tmp_path / "github-commit-lineage-evidence.jsonl"
    manifest_path.write_bytes(manifest_bytes)
    artifact_path.write_bytes(artifact_bytes)
    producer_evidence_path.write_bytes(_producer_commit_evidence())
    producer_main_evidence_path.write_bytes(_producer_main_evidence())
    _good, good_manifest, good_artifact, good_ledger, good_availability = _bundle()
    handoff, checksums, chain, lineage_evidence = _handoff_files(
        good_manifest,
        good_artifact,
        good_ledger,
        good_availability,
    )
    handoff_path.write_bytes(handoff)
    checksums_path.write_bytes(checksums)
    lineage_chain_path.write_bytes(chain)
    lineage_evidence_path.write_bytes(lineage_evidence)
    acceptance_path.write_bytes(
        _raw_signed_receipt(manifest_bytes, artifact_bytes, signer)
    )
    with pytest.raises(
        PalimpsestChinaIntakeError, match="successful exact-commit push"
    ):
        load_accepted_export(
            manifest_path,
            artifact_path,
            acceptance_path,
            producer_commit_evidence_path=producer_evidence_path,
            producer_main_evidence_path=producer_main_evidence_path,
            handoff_path=handoff_path,
            checksums_path=checksums_path,
            lineage_chain_path=lineage_chain_path,
            lineage_evidence_path=lineage_evidence_path,
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )


def test_live_nbs_notary_key_cannot_authorize_palimpsest_acceptance() -> None:
    assert nbs_trust.PRODUCTION_TRUSTED_PALIMPSEST_CHINA_OPERATOR_KEYS == frozenset()
    assert nbs_trust.PRODUCTION_TRUSTED_OPERATOR_KEYS.isdisjoint(
        nbs_trust.PRODUCTION_TRUSTED_PALIMPSEST_CHINA_OPERATOR_KEYS
    )
    live_nbs_key = next(iter(nbs_trust.PRODUCTION_TRUSTED_OPERATOR_KEYS))

    with pytest.raises(ValueError, match="not trusted"):
        nbs_trust.verify_trusted_palimpsest_china_signature(
            b"palimpsest-owner-acceptance",
            "0" * 128,
            live_nbs_key,
        )


def test_legacy_manifest_remains_offline_review_only(
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    manifest["schema_version"] = LEGACY_MANIFEST_SCHEMA
    manifest.pop("producer")
    manifest.pop("availability_receipt")
    manifest_bytes = _canonical(manifest)

    context = verify_export(manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT)
    assert context.manifest_schema_version == LEGACY_MANIFEST_SCHEMA
    assert context.producer is None
    with pytest.raises(
        PalimpsestChinaIntakeError, match="successful exact-commit push"
    ):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda producer: producer.update(repository="someone/fork"),
            "repository is not release-reviewed",
        ),
        (
            lambda producer: producer.update(commit_sha="not-a-git-sha"),
            "lowercase 40-hex Git SHA",
        ),
        (
            lambda producer: producer["workflow_run"].update(head_sha="f" * 40),
            "does not match producer commit",
        ),
        (
            lambda producer: producer["workflow_run"].update(
                url="https://github.com/someone/fork/actions/runs/12345678901"
            ),
            "URL is not canonical",
        ),
        (
            lambda producer: producer["workflow_run"].update(event="workflow_dispatch"),
            "event is not reviewed",
        ),
        (
            lambda producer: producer["workflow_run"].update(conclusion="failure"),
            "conclusion must be success",
        ),
    ],
)
def test_producer_receipt_malformed_mismatched_or_cross_repo_fails_closed(
    mutation,
    message: str,
) -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    mutation(manifest["producer"])

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["author"].update(login="someone-else"),
            "author or committer identity",
        ),
        (
            lambda value: value["committer"].update(login="someone-else"),
            "author or committer identity",
        ),
        (
            lambda value: value.update(parents=value["parents"][:1]),
            "bounded merge",
        ),
        (
            lambda value: value["parents"].__setitem__(
                1, deepcopy(value["parents"][0])
            ),
            "parent SHAs must be unique",
        ),
        (
            lambda value: value["commit"]["verification"].update(verified=False),
            "verification is not valid",
        ),
        (
            lambda value: value["commit"]["verification"].update(reason="unsigned"),
            "verification is not valid",
        ),
        (
            lambda value: value["commit"]["verification"].update(
                verified_at="2026-08-24T12:03:00Z"
            ),
            "verification clock follows",
        ),
        (
            lambda value: value.update(sha="f" * 40),
            "does not match the manifest producer",
        ),
        (
            lambda value: value.update(url="https://api.github.com/wrong"),
            "API URL is not canonical",
        ),
        (
            lambda value: value["parents"][0].update(
                url="https://api.github.com/wrong"
            ),
            "parent 1 URL is not canonical",
        ),
    ],
)
def test_owner_acceptance_reparses_exact_github_commit_evidence(
    mutation,
    message: str,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    value = json.loads(_producer_commit_evidence())
    mutation(value)
    raw = (json.dumps(value, indent=2) + "\n").encode()

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            input_ledger_bytes=ledger_bytes,
            availability_bytes=availability_bytes,
            producer_commit_evidence_bytes=raw,
            producer_main_evidence_bytes=_producer_main_evidence(),
            **_authority_inputs(
                manifest_bytes,
                artifact_bytes,
                ledger_bytes,
                availability_bytes,
            ),
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value.update(name="release"),
            "describe the main branch",
        ),
        (
            lambda value: value["commit"].update(sha="f" * 40),
            "does not match the manifest producer",
        ),
        (
            lambda value: value["commit"].update(url="https://api.github.com/wrong"),
            "commit URL is not canonical",
        ),
        (
            lambda value: value.update(protected="false"),
            "protected state must be boolean",
        ),
    ],
)
def test_owner_acceptance_reparses_current_main_evidence(
    mutation,
    message: str,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    value = json.loads(_producer_main_evidence())
    mutation(value)
    raw = (json.dumps(value, indent=2) + "\n").encode()

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            input_ledger_bytes=ledger_bytes,
            availability_bytes=availability_bytes,
            producer_commit_evidence_bytes=_producer_commit_evidence(),
            producer_main_evidence_bytes=raw,
            **_authority_inputs(
                manifest_bytes,
                artifact_bytes,
                ledger_bytes,
                availability_bytes,
            ),
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


def test_governed_lineage_tip_may_predate_evaluated_producer_commit(
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    handoff_bytes, _checksums, chain_bytes, evidence_bytes = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    handoff = json.loads(handoff_bytes)
    chain_rows = [json.loads(line) for line in chain_bytes.splitlines()]
    evidence_rows = [json.loads(line) for line in evidence_bytes.splitlines()]
    governed_tip = "d" * 40
    governed_raw = _producer_commit_evidence(commit_sha=governed_tip)
    normalized = _normalized_producer_commit_evidence(commit_sha=governed_tip)
    evidence_rows[0].update(
        commit_sha=governed_tip,
        raw_sha256=hashlib.sha256(governed_raw).hexdigest(),
        raw_bytes=len(governed_raw),
        payload_base64=base64.b64encode(governed_raw).decode("ascii"),
    )
    chain_rows[0]["commit"] = {
        "sha": governed_tip,
        "request_url": normalized["request_url"],
        "api_url": normalized["request_url"].removesuffix("?per_page=1"),
        "author_login": normalized["author_login"],
        "committer_login": normalized["committer_login"],
        "parent_shas": normalized["parent_shas"],
        "verification": normalized["verification"],
        "raw_sha256": hashlib.sha256(governed_raw).hexdigest(),
        "raw_bytes": len(governed_raw),
    }
    chain_receipt = handoff["revision_lineage"]["chain"]
    chain_receipt["root_commit_sha"] = governed_tip
    chain_receipt["tip_commit_sha"] = governed_tip
    handoff_bytes, checksums, chain_bytes, evidence_bytes = _reseal_authority_documents(
        handoff, chain_rows, evidence_rows
    )

    claim = build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        input_ledger_bytes=ledger_bytes,
        availability_bytes=availability_bytes,
        producer_commit_evidence_bytes=_producer_commit_evidence(),
        producer_main_evidence_bytes=_producer_main_evidence(),
        handoff_bytes=handoff_bytes,
        checksums_bytes=checksums,
        lineage_chain_bytes=chain_bytes,
        lineage_evidence_bytes=evidence_bytes,
        operator_confirmations=_operator_confirmations(),
        accepted_at=ACCEPTED_AT,
        signer_key_id=signer[1],
    )

    assert claim["governed_lineage"]["tip_commit_sha"] == governed_tip
    assert claim["governed_lineage"]["evaluated_at_commit_sha"] == PALIMPSEST_COMMIT_SHA


@pytest.mark.parametrize(
    ("target", "value", "message"),
    [
        ("cross_run", False, "cross-run revision authority"),
        ("live_appended", 1, "cross-run revision authority"),
        ("evaluated_at", "f" * 40, "lineage evaluation commit"),
        ("ledger_blob", "f" * 40, "Git blobs do not match"),
        ("tree_mode", "120000", "non-regular Git object"),
    ],
)
def test_authoritative_handoff_and_governed_lineage_fail_closed(
    target: str,
    value: object,
    message: str,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    handoff_bytes, _checksums, chain_bytes, evidence_bytes = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    handoff = json.loads(handoff_bytes)
    chain_rows = [json.loads(line) for line in chain_bytes.splitlines()]
    evidence_rows = [json.loads(line) for line in evidence_bytes.splitlines()]
    if target == "cross_run":
        handoff["revision_lineage"]["cross_run_revision_authority"] = value
    elif target == "live_appended":
        handoff["revision_lineage"]["live_check_new_vintages_appended"] = value
    elif target == "evaluated_at":
        handoff["revision_lineage"]["chain"]["evaluated_at_commit_sha"] = value
    elif target == "ledger_blob":
        chain_rows[0]["git_tree_entries"]["readings/china-econ-wdi-observations.jsonl"][
            "object_sha"
        ] = value
    else:
        chain_rows[0]["git_tree_entries"]["readings/china-econ-wdi-latest.json"][
            "mode"
        ] = value
    handoff_bytes, checksums, chain_bytes, evidence_bytes = _reseal_authority_documents(
        handoff, chain_rows, evidence_rows
    )

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            input_ledger_bytes=ledger_bytes,
            availability_bytes=availability_bytes,
            producer_commit_evidence_bytes=_producer_commit_evidence(),
            producer_main_evidence_bytes=_producer_main_evidence(),
            handoff_bytes=handoff_bytes,
            checksums_bytes=checksums,
            lineage_chain_bytes=chain_bytes,
            lineage_evidence_bytes=evidence_bytes,
            operator_confirmations=_operator_confirmations(),
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


def test_governed_lineage_reparses_every_raw_commit_evidence(
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    handoff_bytes, _checksums, chain_bytes, evidence_bytes = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    handoff = json.loads(handoff_bytes)
    chain_rows = [json.loads(line) for line in chain_bytes.splitlines()]
    evidence_rows = [json.loads(line) for line in evidence_bytes.splitlines()]
    raw = json.loads(base64.b64decode(evidence_rows[0]["payload_base64"]))
    raw["committer"]["login"] = "github-actions[bot]"
    raw_bytes = (json.dumps(raw, indent=2) + "\n").encode()
    evidence_rows[0].update(
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        raw_bytes=len(raw_bytes),
        payload_base64=base64.b64encode(raw_bytes).decode("ascii"),
    )
    handoff_bytes, checksums, chain_bytes, evidence_bytes = _reseal_authority_documents(
        handoff, chain_rows, evidence_rows
    )

    with pytest.raises(
        PalimpsestChinaIntakeError, match="author or committer identity"
    ):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            input_ledger_bytes=ledger_bytes,
            availability_bytes=availability_bytes,
            producer_commit_evidence_bytes=_producer_commit_evidence(),
            producer_main_evidence_bytes=_producer_main_evidence(),
            handoff_bytes=handoff_bytes,
            checksums_bytes=checksums,
            lineage_chain_bytes=chain_bytes,
            lineage_evidence_bytes=evidence_bytes,
            operator_confirmations=_operator_confirmations(),
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "reordered"])
def test_attested_checksum_subject_set_is_exact_sorted_and_closed(
    mutation: str,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    authority = _authority_inputs(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    lines = authority["checksums_bytes"].splitlines(keepends=True)
    if mutation == "missing":
        lines = lines[:-1]
    elif mutation == "extra":
        lines.append(("0" * 64 + " *unexpected.json\n").encode("ascii"))
    else:
        lines = list(reversed(lines))
    authority["checksums_bytes"] = b"".join(lines)

    with pytest.raises(PalimpsestChinaIntakeError, match="exact subject set"):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            input_ledger_bytes=ledger_bytes,
            availability_bytes=availability_bytes,
            producer_commit_evidence_bytes=_producer_commit_evidence(),
            producer_main_evidence_bytes=_producer_main_evidence(),
            **authority,
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


@pytest.mark.parametrize("confirmation", sorted(_operator_confirmations()))
def test_owner_acceptance_requires_each_explicit_operator_confirmation(
    confirmation: str,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    confirmations = _operator_confirmations()
    confirmations[confirmation] = False

    with pytest.raises(PalimpsestChinaIntakeError, match=confirmation):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            input_ledger_bytes=ledger_bytes,
            availability_bytes=availability_bytes,
            producer_commit_evidence_bytes=_producer_commit_evidence(),
            producer_main_evidence_bytes=_producer_main_evidence(),
            **{
                **_authority_inputs(
                    manifest_bytes,
                    artifact_bytes,
                    ledger_bytes,
                    availability_bytes,
                ),
                "operator_confirmations": confirmations,
            },
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


def test_successful_pull_request_run_is_reviewable_but_not_authoritative(
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    manifest["schema_version"] = REVIEW_MANIFEST_SCHEMA
    manifest.pop("availability_receipt")
    manifest["producer"] = _producer(event="pull_request")
    manifest_bytes = _canonical(manifest)

    review = verify_export(manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT)
    assert review.producer is not None
    with pytest.raises(
        PalimpsestChinaIntakeError, match="successful exact-commit push"
    ):
        build_acceptance_claim(
            manifest_bytes,
            artifact_bytes,
            accepted_at=ACCEPTED_AT,
            signer_key_id=signer[1],
        )


@pytest.mark.parametrize("missing", ["input_ledger", "availability"])
def test_authoritative_v3_requires_both_exact_supplemental_inputs(
    missing: str,
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    kwargs = {
        "input_ledger_bytes": ledger_bytes,
        "availability_bytes": availability_bytes,
    }
    kwargs[f"{missing}_bytes"] = None

    with pytest.raises(
        PalimpsestChinaIntakeError,
        match="requires exact input ledger and availability bytes",
    ):
        verify_export(
            manifest_bytes,
            artifact_bytes,
            accepted_at=ACCEPTED_AT,
            **kwargs,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "0" * 64, "hash/bytes commitment"),
        (
            "current_numeric_identities_sha256",
            "0" * 64,
            "current numeric identities commitment",
        ),
        (
            "current_projectable_series_sha256",
            "0" * 64,
            "current projectable series commitment",
        ),
        (
            "current_projectable_source_indicators_sha256",
            "0" * 64,
            "current projectable source indicators commitment",
        ),
        (
            "withdrawn_numeric_identities_sha256",
            "0" * 64,
            "withdrawn numeric identities commitment",
        ),
    ],
)
def test_availability_hash_and_derived_set_commitments_fail_closed(
    field: str,
    value: object,
    message: str,
) -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    manifest["availability_receipt"][field] = value

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


def test_availability_clock_batch_and_entry_order_fail_closed() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    run = json.loads(availability_bytes)
    run["generated_at"] = "2026-08-24T12:02:00Z"
    later_bytes = _canonical(run)
    manifest["availability_receipt"].update(
        generated_at=run["generated_at"],
        sha256=hashlib.sha256(later_bytes).hexdigest(),
        bytes=len(later_bytes),
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="generated after"):
        _verify(_canonical(manifest), artifact_bytes, ledger_bytes, later_bytes)

    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    manifest["availability_receipt"]["batch_raw_sha256"] = "8" * 64
    with pytest.raises(PalimpsestChinaIntakeError, match="fields changed"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )

    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    run = json.loads(availability_bytes)
    run["availability"]["entries"].reverse()
    reordered_bytes = _canonical(run)
    manifest["availability_receipt"].update(
        sha256=hashlib.sha256(reordered_bytes).hexdigest(),
        bytes=len(reordered_bytes),
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="uniquely sorted"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            reordered_bytes,
        )


def test_availability_ledger_counts_bind_the_exact_input_ledger() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    run = json.loads(availability_bytes)
    run["ledger_before"] = deepcopy(run["ledger_after"])
    mismatched_bytes = _canonical(run)
    manifest["availability_receipt"].update(
        sha256=hashlib.sha256(mismatched_bytes).hexdigest(),
        bytes=len(mismatched_bytes),
    )

    with pytest.raises(PalimpsestChinaIntakeError, match="ledger transition"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            mismatched_bytes,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda run: run.update(publication_state="review_only"),
            "source, rights, clocks, or safety fields",
        ),
        (
            lambda run: run.update(redistribution_status="review_only"),
            "source, rights, clocks, or safety fields",
        ),
        (
            lambda run: run["revision_lineage"].update(mode="local_review_append_only"),
            "reviewed durable lineage",
        ),
        (
            lambda run: run["revision_lineage"].update(durable_cross_run=False),
            "reviewed durable lineage",
        ),
        (
            lambda run: run["revision_lineage"].update(ledger_path="local.jsonl"),
            "reviewed durable lineage",
        ),
    ],
)
def test_authoritative_v3_requires_public_durable_lineage(
    mutation,
    message: str,
) -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    run = json.loads(availability_bytes)
    mutation(run)
    mutated = _replace_availability(manifest, run)

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        _verify(_canonical(manifest), artifact_bytes, ledger_bytes, mutated)


@pytest.mark.parametrize(
    ("mutation", "message", "reseal"),
    [
        (
            lambda run: run["response_coverage"].update(populated_observations=999),
            "response coverage",
            True,
        ),
        (
            lambda run: run["ledger_coverage"].update(records=999),
            "ledger coverage",
            True,
        ),
        (
            lambda run: run["indicator_provenance"].update(
                upstream_attribution_state="complete"
            ),
            "indicator provenance",
            True,
        ),
        (
            lambda run: run["collector_artifact"].update(payload_sha256="0" * 64),
            "collector artifact",
            False,
        ),
        (
            lambda run: run["collector_artifact"]["source_receipt"].update(
                url="https://api.worldbank.org/v2/country/CHN/indicator/wrong"
            ),
            "collector artifact",
            False,
        ),
        (
            lambda run: run["collector_artifact"]["freshness"].update(
                dataset_age_days=41
            ),
            "collector artifact",
            False,
        ),
    ],
)
def test_authoritative_v3_reconciles_coverage_provenance_and_collector_seal(
    mutation,
    message: str,
    reseal: bool,
) -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    run = json.loads(availability_bytes)
    mutation(run)
    if reseal:
        _reseal_collector(run)
    mutated = _replace_availability(manifest, run)

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        _verify(_canonical(manifest), artifact_bytes, ledger_bytes, mutated)


def test_input_ledger_requires_publisher_wire_but_uses_semantic_artifact_digest() -> (
    None
):
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    assert b'"collected_at": "' in ledger_bytes
    assert _verify(
        _canonical(manifest), artifact_bytes, ledger_bytes, availability_bytes
    ).observations

    compact_ledger = b"".join(
        _canonical(json.loads(line)) for line in ledger_bytes.splitlines()
    )
    manifest["input_ledger"].update(
        sha256=hashlib.sha256(compact_ledger).hexdigest(),
        bytes=len(compact_ledger),
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="durable wire format"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            compact_ledger,
            availability_bytes,
        )


def test_ledger_collection_clock_cannot_follow_availability_receipt() -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    money = wrappers[1]["observation"]
    late_cereal = _observation(
        series_id="cn.wdi.cereal_production",
        source_series_id="AG.PRD.CREL.MT",
        value=652_290_000,
        unit="metric tons",
        collected_at="2026-08-24T12:00:45+00:00",
    )
    entries = [
        {
            "available": True,
            "footnote": None,
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2024,
        },
        {
            "available": True,
            "footnote": None,
            "indicator_id": "FM.LBL.BMNY.ZG",
            "year": 2024,
        },
    ]
    handoff = _rebuild_v3(
        manifest,
        wrappers=[
            _wrapper(late_cereal, "capital_market"),
            _wrapper(money, "capital_market", "money_market"),
        ],
        ledger_rows=[money, late_cereal],
        availability_entries=entries,
    )

    with pytest.raises(PalimpsestChinaIntakeError, match="collection clock"):
        _verify(*handoff)


def test_withdrawn_numeric_identity_omits_entire_series_without_old_fallback() -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    original_wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    cereal_current = original_wrappers[0]["observation"]
    money = original_wrappers[1]["observation"]
    cereal_old = _observation(
        series_id="cn.wdi.cereal_production",
        source_series_id="AG.PRD.CREL.MT",
        value=640_000_000,
        unit="metric tons",
        year=2023,
    )
    entries = [
        {
            "available": True,
            "footnote": None,
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2023,
        },
        {
            "available": False,
            "footnote": "withdrawn by current source response",
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2024,
        },
        {
            "available": True,
            "footnote": None,
            "indicator_id": "FM.LBL.BMNY.ZG",
            "year": 2024,
        },
    ]
    handoff = _rebuild_v3(
        manifest,
        wrappers=[_wrapper(money, "capital_market", "money_market")],
        ledger_rows=[cereal_old, cereal_current, money],
        availability_entries=entries,
    )

    context = _verify(*handoff)
    assert {row.series_id for row in context.observations} == {
        "cn.wdi.broad_money_growth"
    }
    assert {
        row["series_id"]
        for family in context.to_dict()["channel_families"].values()
        for row in family["observations"]
    } == {"cn.wdi.broad_money_growth"}

    bad_handoff = _rebuild_v3(
        manifest,
        wrappers=[
            _wrapper(cereal_old, "capital_market"),
            _wrapper(money, "capital_market", "money_market"),
        ],
        ledger_rows=[cereal_old, cereal_current, money],
        availability_entries=entries,
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="artifact identities"):
        _verify(*bad_handoff)


def test_never_numeric_null_year_does_not_withdraw_a_projectable_series() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, _availability = _bundle()
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    ledger_rows = [json.loads(line) for line in ledger_bytes.splitlines()]
    entries = [
        {
            "available": True,
            "footnote": None,
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2024,
        },
        {
            "available": False,
            "footnote": "no numeric value has ever entered the ledger",
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2025,
        },
        {
            "available": True,
            "footnote": None,
            "indicator_id": "FM.LBL.BMNY.ZG",
            "year": 2024,
        },
    ]

    context = _verify(
        *_rebuild_v3(
            manifest,
            wrappers=wrappers,
            ledger_rows=ledger_rows,
            availability_entries=entries,
        )
    )
    assert {row.series_id for row in context.observations} == {
        "cn.wdi.broad_money_growth",
        "cn.wdi.cereal_production",
    }


def test_current_numeric_availability_must_have_an_exact_ledger_identity() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    ledger_rows = [json.loads(line) for line in ledger_bytes.splitlines()]
    entries = json.loads(availability_bytes)["availability"]["entries"]
    entries.append(
        {
            "available": True,
            "footnote": None,
            "indicator_id": "NY.GDP.MKTP.CD",
            "year": 2024,
        }
    )
    entries.sort(key=lambda row: (row["indicator_id"], row["year"]))
    handoff = _rebuild_v3(
        manifest,
        wrappers=wrappers,
        ledger_rows=ledger_rows,
        availability_entries=entries,
    )

    with pytest.raises(PalimpsestChinaIntakeError, match="absent from the ledger"):
        _verify(*handoff)


def test_artifact_must_select_exact_latest_ledger_vintage() -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    original_wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    cereal_old = original_wrappers[0]["observation"]
    money = original_wrappers[1]["observation"]
    cereal_latest = _observation(
        series_id="cn.wdi.cereal_production",
        source_series_id="AG.PRD.CREL.MT",
        value=653_000_000,
        unit="metric tons",
        revision=1,
        released_at="2026-07-14T23:59:59+00:00",
        collected_at="2026-08-24T12:00:10+00:00",
        source_document_version="2026-07-14",
    )
    entries = [
        {
            "available": True,
            "footnote": None,
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2024,
        },
        {
            "available": True,
            "footnote": None,
            "indicator_id": "FM.LBL.BMNY.ZG",
            "year": 2024,
        },
    ]
    ledger = [cereal_old, money, cereal_latest]
    context = _verify(
        *_rebuild_v3(
            manifest,
            wrappers=[
                _wrapper(cereal_latest, "capital_market"),
                _wrapper(money, "capital_market", "money_market"),
            ],
            ledger_rows=ledger,
            availability_entries=entries,
        )
    )
    cereal = next(
        row
        for row in context.observations
        if row.series_id == "cn.wdi.cereal_production"
    )
    assert cereal.record["value"] == 653_000_000.0
    assert cereal.record["revision"] == 1

    stale = _rebuild_v3(
        manifest,
        wrappers=[
            _wrapper(cereal_old, "capital_market"),
            _wrapper(money, "capital_market", "money_market"),
        ],
        ledger_rows=ledger,
        availability_entries=entries,
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="not the latest"):
        _verify(*stale)


def test_artifact_cannot_emit_multiple_vintages_for_one_identity() -> None:
    manifest, _manifest_bytes, artifact_bytes, _ledger, _availability = _bundle()
    original_wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    cereal_old = original_wrappers[0]["observation"]
    money = original_wrappers[1]["observation"]
    cereal_latest = _observation(
        series_id="cn.wdi.cereal_production",
        source_series_id="AG.PRD.CREL.MT",
        value=653_000_000,
        unit="metric tons",
        revision=1,
        released_at="2026-07-14T23:59:59+00:00",
        collected_at="2026-08-24T12:00:10+00:00",
        source_document_version="2026-07-14",
    )
    entries = [
        {
            "available": True,
            "footnote": None,
            "indicator_id": "AG.PRD.CREL.MT",
            "year": 2024,
        },
        {
            "available": True,
            "footnote": None,
            "indicator_id": "FM.LBL.BMNY.ZG",
            "year": 2024,
        },
    ]
    handoff = _rebuild_v3(
        manifest,
        wrappers=[
            _wrapper(cereal_latest, "capital_market"),
            _wrapper(cereal_old, "capital_market"),
            _wrapper(money, "capital_market", "money_market"),
        ],
        ledger_rows=[cereal_old, money, cereal_latest],
        availability_entries=entries,
    )

    with pytest.raises(PalimpsestChinaIntakeError, match="exactly one latest row"):
        _verify(*handoff)


def test_default_projection_returns_only_featured_current_observations() -> None:
    manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _expanded_bundle()
    )
    public = _verify(
        manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes
    ).to_dict()
    capital = public["channel_families"]["capital_market"]

    assert capital["series_count"] == 7
    assert capital["returned_observation_count"] == 6
    assert capital["observations_truncated"] is True
    assert [row["series_id"] for row in capital["observations"]] == capital[
        "featured_series"
    ]


def test_only_owner_attested_context_is_additive_and_never_changes_gauge(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    candidate = _verify(
        manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes
    )

    baseline = project_world_markets({}, selector="china_macro")["china_macro"]
    unsigned = project_world_markets(
        {},
        selector="china_macro",
        china_economic_context=candidate,
    )["china_macro"]
    assert "economic_context" not in unsigned
    assert candidate.owner_attested is False

    forged_metadata = replace(
        candidate,
        acceptance_sha256="a" * 64,
        acceptance_signer_key_id=signer[1],
    )
    assert forged_metadata.owner_attested is False
    assert (
        "economic_context"
        not in project_world_markets(
            {},
            selector="china_macro",
            china_economic_context=forged_metadata,
        )["china_macro"]
    )

    context = _load_written_bundle(
        _write_signed_bundle(tmp_path, signer),
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    assert context.owner_attested is True
    china = project_world_markets(
        {},
        selector="china_macro",
        china_economic_context=context,
    )["china_macro"]

    assert {
        key: value for key, value in china.items() if key != "economic_context"
    } == baseline
    economic = china["economic_context"]
    assert economic["source_id"] == "world_bank_wdi"
    assert economic["context_only"] is True
    assert economic["scoring_eligible"] is False
    assert economic["cn_cny_gauge_eligible"] is False
    assert economic["market_observation_eligible"] is False
    assert (
        "economic_context"
        not in project_world_markets(
            {},
            selector="china_macro",
            china_economic_context=economic,
        )["china_macro"]
    )
    all_context = project_world_markets(
        {}, selector="all", china_economic_context=context
    )
    wdi_source = next(
        row for row in all_context["sources"] if row["id"] == "world_bank_wdi"
    )
    assert wdi_source["used_in_snapshot"] is True
    assert "china_macro.economic_context" in wdi_source["projection_paths"]


def test_rest_and_mcp_label_legacy_v1_context_provisional_without_building_board(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _load_written_bundle(
        _write_signed_bundle(tmp_path, signer),
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(context_views, "public_china_macro_context", lambda: None)
    monkeypatch.setattr(context_views, "public_china_economic_context", lambda: context)
    legacy_environment = {
        "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH": "/legacy/manifest.json",
        "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH": "/legacy/artifact.jsonl",
        "SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH": "/legacy/input-ledger.jsonl",
        "SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH": "/legacy/availability.json",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH": (
            "/legacy/github-commit.json"
        ),
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH": (
            "/legacy/github-main-branch.json"
        ),
        "SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH": "/legacy/handoff-receipt.json",
        "SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH": "/legacy/SHA256SUMS",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH": (
            "/legacy/china-econ-wdi-lineage-chain.jsonl"
        ),
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH": (
            "/legacy/github-commit-lineage-evidence.jsonl"
        ),
        "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH": "/legacy/acceptance.json",
    }
    for name, value in legacy_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(
        "SEICHE_PALIMPSEST_CHINA_PUBLICATION_STATUS",
        raising=False,
    )

    def forbidden_board(*_args, **_kwargs):
        raise AssertionError("China context must not build or restore the world board")

    monkeypatch.setattr(api.assemble, "cached_snapshot", forbidden_board)
    monkeypatch.setattr(api.assemble, "restore_cached_snapshot", forbidden_board)
    rest_response = TestClient(api.app).get("/api/v2/world-markets?section=china_macro")
    assert rest_response.status_code == 200
    rest = rest_response.json()

    monkeypatch.setattr(mcp, "_get_completed_snapshot", forbidden_board)
    rpc = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": "palimpsest-china",
            "method": "tools/call",
            "params": {
                "name": "world_markets_context",
                "arguments": {"section": "china_macro"},
            },
        },
        public=True,
    )
    mcp_payload = json.loads(rpc["result"]["content"][0]["text"])

    assert (
        mcp_payload["china_macro"]["economic_context"]
        == (rest["china_macro"]["economic_context"])
    )
    assert rest["china_macro"]["economic_context"]["scoring_eligible"] is False
    assert (
        rest["china_macro"]["economic_context"]["publication_status"] == "provisional"
    )
    assert rest["generated_at"] is None

    monkeypatch.setattr(api, "_completed_world_markets_snapshot", lambda: None)
    cold_rest_response = TestClient(api.app).get("/api/v2/world-markets?section=all")
    assert cold_rest_response.status_code == 503
    cold_rest = cold_rest_response.json()
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: None)
    cold_rpc = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": "palimpsest-china-cold",
            "method": "tools/call",
            "params": {
                "name": "world_markets_context",
                "arguments": {"section": "all"},
            },
        },
        public=True,
    )
    cold_mcp = json.loads(cold_rpc["result"]["content"][0]["text"])
    assert (
        cold_mcp["china_macro"]["economic_context"]
        == cold_rest["china_macro"]["economic_context"]
    )
    assert (
        cold_rest["china_macro"]["economic_context"]["publication_status"]
        == "provisional"
    )


def test_served_palimpsest_context_rejects_nonprovisional_status(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _load_written_bundle(
        _write_signed_bundle(tmp_path, signer),
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    monkeypatch.setenv("SEICHE_PALIMPSEST_CHINA_PUBLICATION_STATUS", "durable")

    with pytest.raises(
        intake.PalimpsestChinaIntakeError,
        match="publication status must remain provisional",
    ):
        context_views.world_markets(
            {},
            selector="china_macro",
            china_economic_context=context,
        )


def test_non_china_selectors_do_not_read_the_offline_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_loader():
        raise AssertionError("non-China selector read the Palimpsest export")

    monkeypatch.setattr(
        context_views, "public_china_economic_context", forbidden_loader
    )
    monkeypatch.setattr(api, "_completed_world_markets_snapshot", lambda: {})

    payload = api.world_markets_v2(
        type("ResponseStub", (), {"headers": {}})(), section="capital_markets"
    )

    assert payload["selection"] == "capital_markets"


@pytest.mark.parametrize(
    "source_id", ["cfets_benchmarks", "chinamoney", "mystery_feed"]
)
def test_value_rows_from_cfets_chinamoney_or_unknown_sources_are_hard_rejected(
    source_id: str,
) -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    wrappers[0]["observation"]["source_id"] = source_id
    wrappers[0]["observation"]["observation_id"] = _observation_id(
        wrappers[0]["observation"]
    )
    manifest_bytes, artifact_bytes = _replace_artifact(manifest, wrappers)

    with pytest.raises(PalimpsestChinaIntakeError, match="not allowlisted"):
        _verify(manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes)


def test_nonallowlisted_source_decision_cannot_enable_values_or_export() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    cfets = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "cfets_benchmarks"
    )
    cfets.update(
        decision="allowed",
        values_allowed=True,
        seiche_export_allowed=True,
    )
    cfets["decision_sha256"] = _decision_digest(cfets)

    with pytest.raises(PalimpsestChinaIntakeError, match="CFETS/ChinaMoney"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


def test_unknown_source_decision_is_null_default_deny_with_zero_exports() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    unknown = {
        "source_id": "mystery_feed",
        "decision": "unknown",
        "decision_sha256": None,
        "values_allowed": False,
        "seiche_export_allowed": False,
        "license": None,
        "license_url": None,
        "rights_evidence_url": None,
        "attribution": None,
        "reviewed_at": None,
        "expires_at": None,
        "reason": "No reviewed source-policy decision; default deny applies.",
        "input_records": 0,
        "exported_records": 0,
    }
    manifest["source_decisions"].insert(2, unknown)

    context = _verify(
        _canonical(manifest), artifact_bytes, ledger_bytes, availability_bytes
    )

    assert context.to_dict()["observation_count"] == 2
    assert all(
        row.record["source_id"] == "world_bank_wdi" for row in context.observations
    )


def test_allow_decision_expiring_at_generation_time_is_rejected() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["expires_at"] = manifest["generated_at"]

    with pytest.raises(PalimpsestChinaIntakeError, match="effective state"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


def test_rights_must_remain_effective_at_acceptance() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["expires_at"] = "2026-08-24T12:01:30Z"
    wdi["decision_sha256"] = _decision_digest(wdi)

    with pytest.raises(
        PalimpsestChinaIntakeError, match="expired at Seiche acceptance"
    ):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


def test_source_decision_digest_is_recomputed() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["decision_sha256"] = "0" * 64

    with pytest.raises(PalimpsestChinaIntakeError, match="digest does not match"):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest["artifact"].update(sha256="0" * 64), "hash/bytes"),
        (lambda manifest: manifest["artifact"].update(bytes=1), "hash/bytes"),
        (lambda manifest: manifest["artifact"].update(records=99), "records"),
        (
            lambda manifest: manifest["market_channel_mapping"].update(
                money_market=["cn.wdi.cereal_production"]
            ),
            "does not match",
        ),
    ],
)
def test_manifest_artifact_commitments_and_channel_mapping_fail_closed(
    mutation,
    message: str,
) -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    mutation(manifest)

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        _verify(
            _canonical(manifest),
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
        )


def test_observation_id_is_recomputed_after_artifact_commitments_pass() -> None:
    manifest, _manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    wrappers[0]["observation"]["value"] = 1.0
    manifest_bytes, artifact_bytes = _replace_artifact(manifest, wrappers)

    with pytest.raises(PalimpsestChinaIntakeError, match="observation_id"):
        _verify(manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes)


def test_seiche_acceptance_clock_cannot_precede_palimpest_collection() -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )

    with pytest.raises(PalimpsestChinaIntakeError, match="accepted_at"):
        _verify(
            manifest_bytes,
            artifact_bytes,
            ledger_bytes,
            availability_bytes,
            accepted_at=datetime(2026, 8, 24, 11, 59, tzinfo=UTC),
        )


def test_file_reader_rejects_symlinks_and_identity_swaps(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    paths = _write_signed_bundle(tmp_path, signer)
    (
        manifest_path,
        artifact_path,
        acceptance_path,
        ledger_path,
        availability_path,
        producer_evidence_path,
        producer_main_evidence_path,
    ) = paths
    manifest_link = tmp_path / "manifest-link.json"
    manifest_link.symlink_to(manifest_path)
    with pytest.raises(PalimpsestChinaIntakeError, match="single-link regular file"):
        load_accepted_export(
            manifest_link,
            artifact_path,
            acceptance_path,
            input_ledger_path=ledger_path,
            availability_path=availability_path,
            producer_commit_evidence_path=producer_evidence_path,
            producer_main_evidence_path=producer_main_evidence_path,
            **_installed_authority_paths(manifest_path),
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )

    original_paths = [
        manifest_path,
        artifact_path,
        acceptance_path,
        ledger_path,
        availability_path,
        producer_evidence_path,
        producer_main_evidence_path,
        manifest_path.parent / "handoff-receipt.json",
        manifest_path.parent / "SHA256SUMS",
        manifest_path.parent / "china-econ-wdi-lineage-chain.jsonl",
        manifest_path.parent / "github-commit-lineage-evidence.jsonl",
    ]
    for position, target in enumerate(original_paths):
        link = tmp_path / f"bundle-link-{position}"
        link.symlink_to(target)
        selected = list(original_paths)
        selected[position] = link
        with pytest.raises(
            PalimpsestChinaIntakeError, match="single-link regular file"
        ):
            load_accepted_export(
                selected[0],
                selected[1],
                selected[2],
                input_ledger_path=selected[3],
                availability_path=selected[4],
                producer_commit_evidence_path=selected[5],
                producer_main_evidence_path=selected[6],
                handoff_path=selected[7],
                checksums_path=selected[8],
                lineage_chain_path=selected[9],
                lineage_evidence_path=selected[10],
                attest_dir=signer[2],
                now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
            )

    original_identity = intake._file_identity(
        manifest_path, label="Palimpsest China manifest"
    )
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(manifest_path.read_bytes())
    replacement.replace(manifest_path)
    with pytest.raises(PalimpsestChinaIntakeError, match="identity changed"):
        intake._stable_read(
            manifest_path,
            label="Palimpsest China manifest",
            maximum=intake.MAX_MANIFEST_BYTES,
            expected_identity=original_identity,
        )


def test_acceptance_receipt_binds_exact_local_files(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _manifest, manifest_bytes, artifact_bytes, ledger_bytes, availability_bytes = (
        _bundle()
    )
    _private_key, public_key, trust = signer
    receipt_bytes = _signed_receipt(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
        signer,
    )
    receipt = json.loads(receipt_bytes)
    assert receipt["schema_version"] == ACCEPTANCE_SCHEMA
    assert receipt["algorithm"] == "ed25519"
    assert receipt["accepted_at"] == "2026-08-24T12:02:00Z"
    assert receipt["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert receipt["artifact_sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert receipt["signer_key_id"] == public_key

    manifest_path = tmp_path / "manifest.json"
    artifact_path = tmp_path / "artifact.jsonl"
    acceptance_path = tmp_path / "acceptance.json"
    ledger_path = tmp_path / "ledger.jsonl"
    availability_path = tmp_path / "availability.json"
    producer_evidence_path = tmp_path / "github-commit.json"
    producer_main_evidence_path = tmp_path / "github-main-branch.json"
    handoff_path = tmp_path / "handoff-receipt.json"
    checksums_path = tmp_path / "SHA256SUMS"
    lineage_chain_path = tmp_path / "china-econ-wdi-lineage-chain.jsonl"
    lineage_evidence_path = tmp_path / "github-commit-lineage-evidence.jsonl"
    manifest_path.write_bytes(manifest_bytes)
    artifact_path.write_bytes(artifact_bytes)
    ledger_path.write_bytes(ledger_bytes)
    availability_path.write_bytes(availability_bytes)
    producer_evidence_path.write_bytes(_producer_commit_evidence())
    producer_main_evidence_path.write_bytes(_producer_main_evidence())
    handoff, checksums, chain, lineage_evidence = _handoff_files(
        manifest_bytes,
        artifact_bytes,
        ledger_bytes,
        availability_bytes,
    )
    handoff_path.write_bytes(handoff)
    checksums_path.write_bytes(checksums)
    lineage_chain_path.write_bytes(chain)
    lineage_evidence_path.write_bytes(lineage_evidence)
    acceptance_path.write_bytes(receipt_bytes)

    loaded = load_accepted_export(
        manifest_path,
        artifact_path,
        acceptance_path,
        input_ledger_path=ledger_path,
        availability_path=availability_path,
        producer_commit_evidence_path=producer_evidence_path,
        producer_main_evidence_path=producer_main_evidence_path,
        handoff_path=handoff_path,
        checksums_path=checksums_path,
        lineage_chain_path=lineage_chain_path,
        lineage_evidence_path=lineage_evidence_path,
        attest_dir=trust,
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    assert loaded.owner_attested is True
    assert loaded.to_dict()["clocks"]["seiche_accepted_at"] == ("2026-08-24T12:02:00Z")
    assert loaded.to_dict()["provenance"]["owner_attestation"] == "ed25519"

    tampered = deepcopy(receipt)
    tampered["artifact_sha256"] = "f" * 64
    acceptance_path.write_bytes(_canonical(tampered))
    metadata = acceptance_path.stat()
    os.utime(
        acceptance_path,
        ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1),
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="artifact hash"):
        load_accepted_export(
            manifest_path,
            artifact_path,
            acceptance_path,
            input_ledger_path=ledger_path,
            availability_path=availability_path,
            producer_commit_evidence_path=producer_evidence_path,
            producer_main_evidence_path=producer_main_evidence_path,
            handoff_path=handoff_path,
            checksums_path=checksums_path,
            lineage_chain_path=lineage_chain_path,
            lineage_evidence_path=lineage_evidence_path,
            attest_dir=trust,
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )


def test_untrusted_or_tampered_acceptance_signature_fails_closed(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    paths = _write_signed_bundle(tmp_path, signer)
    (
        manifest_path,
        artifact_path,
        acceptance_path,
        ledger_path,
        availability_path,
        producer_evidence_path,
        producer_main_evidence_path,
    ) = paths
    other_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    other_trust = tmp_path / "other-trust"
    other_trust.mkdir()
    (other_trust / "trusted_operator_keys").write_text(other_key + "\n")

    with pytest.raises(PalimpsestChinaIntakeError, match="not trusted and valid"):
        load_accepted_export(
            manifest_path,
            artifact_path,
            acceptance_path,
            input_ledger_path=ledger_path,
            availability_path=availability_path,
            producer_commit_evidence_path=producer_evidence_path,
            producer_main_evidence_path=producer_main_evidence_path,
            **_installed_authority_paths(manifest_path),
            attest_dir=other_trust,
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )

    receipt = json.loads(acceptance_path.read_bytes())
    receipt["signature"] = "0" * 128
    acceptance_path.write_bytes(_canonical(receipt))
    clear_accepted_export_cache()
    with pytest.raises(PalimpsestChinaIntakeError, match="not trusted and valid"):
        load_accepted_export(
            manifest_path,
            artifact_path,
            acceptance_path,
            input_ledger_path=ledger_path,
            availability_path=availability_path,
            producer_commit_evidence_path=producer_evidence_path,
            producer_main_evidence_path=producer_main_evidence_path,
            **_installed_authority_paths(manifest_path),
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )


def test_cached_verification_still_rechecks_future_clock_and_rights_expiry(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_signed_bundle(tmp_path, signer)
    loaded = _load_written_bundle(
        paths,
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )

    def forbidden_verify(*_args, **_kwargs):
        raise AssertionError("immutable accepted export was re-verified")

    monkeypatch.setattr(intake, "verify_export", forbidden_verify)
    assert (
        _load_written_bundle(
            paths,
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 1, tzinfo=UTC),
        )
        is loaded
    )
    with pytest.raises(
        PalimpsestChinaIntakeError, match="acceptance clock is in the future"
    ):
        _load_written_bundle(
            paths,
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 12, 1, tzinfo=UTC),
        )
    with pytest.raises(PalimpsestChinaIntakeError, match="expired at serve time"):
        _load_written_bundle(
            paths,
            attest_dir=signer[2],
            now=datetime(2027, 8, 24, 0, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("manifest.json", "manifest hash"),
        ("artifact.jsonl", "artifact hash"),
        ("acceptance.json", "canonical JSON bytes"),
        ("ledger.jsonl", "input ledger hash/bytes"),
        ("availability.json", "availability receipt hash/bytes"),
        ("github-commit.json", "producer commit evidence hash/bytes"),
        ("github-main-branch.json", "producer main evidence hash/bytes"),
        ("handoff-receipt.json", "handoff hash/bytes"),
        ("SHA256SUMS", "checksum subject hash/bytes"),
        ("china-econ-wdi-lineage-chain.jsonl", "governed lineage hash/bytes"),
        ("github-commit-lineage-evidence.jsonl", "governed lineage hash/bytes"),
    ],
)
def test_cache_identity_includes_every_authoritative_supplemental_file(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    filename: str,
    message: str,
) -> None:
    paths = _write_signed_bundle(tmp_path, signer)
    _load_written_bundle(
        paths,
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    selected = tmp_path / filename
    selected.write_bytes(selected.read_bytes() + b" ")

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        _load_written_bundle(
            paths,
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 1, tzinfo=UTC),
        )


def test_acceptance_cli_emits_exact_claim_and_verified_receipt(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, public_key, trust = signer
    (
        manifest_path,
        artifact_path,
        _acceptance_path,
        ledger_path,
        availability_path,
        producer_evidence_path,
        _installed_main_evidence_path,
    ) = _write_signed_bundle(tmp_path, signer)
    producer_main_evidence_path = tmp_path / "claim-main-branch.json"
    captured_at = datetime(2026, 8, 24, 12, 2, 45, tzinfo=UTC)
    monkeypatch.setattr(
        acceptance_cli,
        "_fetch_producer_main_evidence",
        lambda: (_producer_main_evidence(), captured_at),
    )
    common = [
        str(manifest_path),
        str(artifact_path),
        "--input-ledger",
        str(ledger_path),
        "--availability-receipt",
        str(availability_path),
        "--producer-commit-evidence",
        str(producer_evidence_path),
        "--producer-main-evidence",
        str(producer_main_evidence_path),
        "--handoff-receipt",
        str(tmp_path / "handoff-receipt.json"),
        "--checksum-subject",
        str(tmp_path / "SHA256SUMS"),
        "--lineage-chain",
        str(tmp_path / "china-econ-wdi-lineage-chain.jsonl"),
        "--lineage-evidence",
        str(tmp_path / "github-commit-lineage-evidence.jsonl"),
        "--signer-key-id",
        public_key,
        "--confirm-github-run-attestation-verified",
        "--confirm-exact-input-hashes-verified",
        "--confirm-producer-raw-identity-verified",
        "--confirm-detached-first-parent-lineage-rebuild-verified",
        "--confirm-current-main-branch-evidence-verified",
        "--confirm-rights-freshness-reviewed",
    ]

    assert acceptance_cli.main(["claim", *common]) == 0
    claim_bytes = capfd.readouterr().out.encode()
    claim = json.loads(claim_bytes)
    assert producer_main_evidence_path.read_bytes() == _producer_main_evidence()
    assert claim["accepted_at"] == "2026-08-24T12:02:45Z"
    assert claim["producer_main_evidence"]["observed_at"] == claim["accepted_at"]
    signature = private_key.sign(claim_bytes).hex()

    def forbidden_fetch() -> tuple[bytes, datetime]:
        raise AssertionError("receipt assembly must not access GitHub")

    monkeypatch.setattr(
        acceptance_cli,
        "_fetch_producer_main_evidence",
        forbidden_fetch,
    )
    assert (
        acceptance_cli.main(
            [
                "receipt",
                *common,
                "--accepted-at",
                claim["accepted_at"],
                "--signature",
                signature,
                "--attest-dir",
                str(trust),
            ]
        )
        == 0
    )
    receipt = json.loads(capfd.readouterr().out)
    assert receipt["signature"] == signature
    assert receipt["signer_key_id"] == public_key
    assert receipt["producer_main_evidence"]["commit"]["sha"] == (PALIMPSEST_COMMIT_SHA)
    assert receipt["producer_main_evidence"]["observed_at"] == (receipt["accepted_at"])


@pytest.mark.parametrize(
    "missing_flag",
    [
        "--confirm-github-run-attestation-verified",
        "--confirm-exact-input-hashes-verified",
        "--confirm-producer-raw-identity-verified",
        "--confirm-detached-first-parent-lineage-rebuild-verified",
        "--confirm-current-main-branch-evidence-verified",
        "--confirm-rights-freshness-reviewed",
    ],
)
def test_acceptance_cli_requires_every_independent_operator_confirmation(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    missing_flag: str,
) -> None:
    _private_key, public_key, _trust = signer
    (
        manifest_path,
        artifact_path,
        _acceptance_path,
        ledger_path,
        availability_path,
        producer_evidence_path,
        producer_main_evidence_path,
    ) = _write_signed_bundle(tmp_path, signer)
    command = [
        "claim",
        str(manifest_path),
        str(artifact_path),
        "--input-ledger",
        str(ledger_path),
        "--availability-receipt",
        str(availability_path),
        "--producer-commit-evidence",
        str(producer_evidence_path),
        "--producer-main-evidence",
        str(producer_main_evidence_path),
        "--handoff-receipt",
        str(tmp_path / "handoff-receipt.json"),
        "--checksum-subject",
        str(tmp_path / "SHA256SUMS"),
        "--lineage-chain",
        str(tmp_path / "china-econ-wdi-lineage-chain.jsonl"),
        "--lineage-evidence",
        str(tmp_path / "github-commit-lineage-evidence.jsonl"),
        "--signer-key-id",
        public_key,
    ]
    flags = [
        "--confirm-github-run-attestation-verified",
        "--confirm-exact-input-hashes-verified",
        "--confirm-producer-raw-identity-verified",
        "--confirm-detached-first-parent-lineage-rebuild-verified",
        "--confirm-current-main-branch-evidence-verified",
        "--confirm-rights-freshness-reviewed",
    ]

    with pytest.raises(SystemExit) as missing:
        acceptance_cli.main(
            [*command, *(flag for flag in flags if flag != missing_flag)]
        )
    assert missing.value.code == 2


def test_configured_loader_is_offline_and_partial_configuration_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH",
        "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH",
        "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH",
        "SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH",
        "SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH",
        "SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH",
        "SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    assert context_views.public_china_economic_context() is None

    monkeypatch.setenv("SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH", "/local/manifest")
    with pytest.raises(PalimpsestChinaIntakeError, match="incomplete"):
        context_views.public_china_economic_context()


def test_configured_loader_passes_all_eleven_immutable_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = {
        "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH": "/bundle/manifest.json",
        "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH": "/bundle/artifact.jsonl",
        "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH": "/bundle/acceptance.json",
        "SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH": "/bundle/ledger.jsonl",
        "SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH": "/bundle/availability.json",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH": (
            "/bundle/github-commit.json"
        ),
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH": (
            "/bundle/github-main-branch.json"
        ),
        "SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH": "/bundle/handoff-receipt.json",
        "SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH": "/bundle/SHA256SUMS",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH": (
            "/bundle/china-econ-wdi-lineage-chain.jsonl"
        ),
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH": (
            "/bundle/github-commit-lineage-evidence.jsonl"
        ),
    }
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_load(manifest, artifact, acceptance, **kwargs):
        captured.update(
            manifest=manifest,
            artifact=artifact,
            acceptance=acceptance,
            **kwargs,
        )
        return sentinel

    monkeypatch.setattr(context_views, "load_accepted_export", fake_load)

    assert context_views.public_china_economic_context() is sentinel
    assert captured == {
        "manifest": configured["SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH"],
        "artifact": configured["SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH"],
        "acceptance": configured["SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH"],
        "input_ledger_path": configured["SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH"],
        "availability_path": configured["SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH"],
        "producer_commit_evidence_path": configured[
            "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH"
        ],
        "producer_main_evidence_path": configured[
            "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH"
        ],
        "handoff_path": configured["SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH"],
        "checksums_path": configured["SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH"],
        "lineage_chain_path": configured["SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH"],
        "lineage_evidence_path": configured[
            "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH"
        ],
    }
