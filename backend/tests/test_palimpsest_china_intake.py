"""Offline Palimpsest China-export integrity and context boundaries."""

from __future__ import annotations

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

from seiche import api, context_views
from seiche import mcp_server as mcp
from seiche import palimpsest_china_intake as intake
from seiche import palimpsest_china_acceptance_cli as acceptance_cli
from seiche.markets.world import project_world_markets
from seiche.palimpsest_china_intake import (
    ACCEPTANCE_SCHEMA,
    EXPORT_SCHEMA,
    LEGACY_MANIFEST_SCHEMA,
    MANIFEST_SCHEMA,
    POLICY_SCHEMA,
    PRODUCER_SCHEMA,
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
EVIDENCE_URL = (
    "https://api.worldbank.org/v2/country/CHN/indicator/"
    "AG.PRD.CREL.MT;FM.LBL.BMNY.ZG?"
    "source=2&date=2024%3A2024&format=json&per_page=20000&footnote=y"
)


@pytest.fixture(autouse=True)
def fixed_acceptance_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        intake,
        "_utc_now",
        lambda: datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
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
) -> dict:
    row = {
        "series_id": series_id,
        "value": float(value),
        "unit": unit,
        "frequency": "A",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "released_at": "2026-07-13T23:59:59+00:00",
        "collected_at": "2026-08-24T12:00:00+00:00",
        "source_id": "world_bank_wdi",
        "evidence_url": EVIDENCE_URL,
        "revision": 0,
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
            "source_document_version": "2026-07-13",
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


def _bundle() -> tuple[dict, bytes, bytes]:
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
        {
            "schema_version": EXPORT_SCHEMA,
            "context_only": True,
            "scoring_allowed": False,
            "market_channels": ["capital_market"],
            "observation": cereal,
        },
        {
            "schema_version": EXPORT_SCHEMA,
            "context_only": True,
            "scoring_allowed": False,
            "market_channels": ["capital_market", "money_market"],
            "observation": money,
        },
    ]
    artifact_bytes = b"".join(_canonical(row) for row in wrappers)
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
            "sha256": "b" * 64,
            "bytes": 10_000,
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
    return manifest, _canonical(manifest), artifact_bytes


def _replace_artifact(manifest: dict, wrappers: list[dict]) -> tuple[bytes, bytes]:
    artifact_bytes = b"".join(_canonical(row) for row in wrappers)
    manifest["artifact"].update(
        sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        bytes=len(artifact_bytes),
        records=len(wrappers),
    )
    return _canonical(manifest), artifact_bytes


def _signed_receipt(
    manifest_bytes: bytes,
    artifact_bytes: bytes,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> bytes:
    private_key, public_key, trust = signer
    claim = build_acceptance_claim(
        manifest_bytes,
        artifact_bytes,
        accepted_at=ACCEPTED_AT,
        signer_key_id=public_key,
    )
    signature = private_key.sign(encode_acceptance_claim(claim)).hex()
    return build_acceptance_receipt(
        manifest_bytes,
        artifact_bytes,
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
    claim = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "algorithm": "ed25519",
        "domain": intake.ACCEPTANCE_DOMAIN,
        "accepted_at": "2026-08-24T12:02:00Z",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "signer_key_id": public_key,
    }
    signature = private_key.sign(encode_acceptance_claim(claim)).hex()
    return _canonical({**claim, "signature": signature})


def _write_signed_bundle(
    directory: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> tuple[Path, Path, Path]:
    _manifest, manifest_bytes, artifact_bytes = _bundle()
    manifest_path = directory / "manifest.json"
    artifact_path = directory / "artifact.jsonl"
    acceptance_path = directory / "acceptance.json"
    manifest_path.write_bytes(manifest_bytes)
    artifact_path.write_bytes(artifact_bytes)
    acceptance_path.write_bytes(_signed_receipt(manifest_bytes, artifact_bytes, signer))
    return manifest_path, artifact_path, acceptance_path


def _expanded_bundle() -> tuple[bytes, bytes]:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    additions = (
        "cn.wdi.equity_market_cap_share",
        "cn.wdi.equity_turnover_ratio",
        "cn.wdi.fdi_net_inflows_share",
        "cn.wdi.electric_power_consumption_per_capita",
        "cn.wdi.container_port_traffic",
    )
    for position, series_id in enumerate(additions, 1):
        wrappers.append(
            {
                "schema_version": EXPORT_SCHEMA,
                "context_only": True,
                "scoring_allowed": False,
                "market_channels": ["capital_market"],
                "observation": _observation(
                    series_id=series_id,
                    source_series_id="AG.PRD.CREL.MT",
                    value=float(position),
                    unit="index",
                ),
            }
        )
    manifest_bytes, artifact_bytes = _replace_artifact(manifest, wrappers)
    manifest = json.loads(manifest_bytes)
    manifest["input_ledger"]["records"] = len(wrappers)
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
    return _canonical(manifest), artifact_bytes


def test_verified_export_preserves_four_clocks_and_bounded_channel_families() -> None:
    _manifest, manifest_bytes, artifact_bytes = _bundle()

    context = verify_export(
        manifest_bytes,
        artifact_bytes,
        accepted_at=ACCEPTED_AT,
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


def test_review_manifest_may_omit_run_but_cannot_be_signed_or_loaded(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
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
    manifest_path.write_bytes(manifest_bytes)
    artifact_path.write_bytes(artifact_bytes)
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
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )


def test_legacy_manifest_remains_offline_review_only(
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    manifest["schema_version"] = LEGACY_MANIFEST_SCHEMA
    manifest.pop("producer")
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
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    mutation(manifest["producer"])

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        verify_export(_canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT)


def test_successful_pull_request_run_is_reviewable_but_not_authoritative(
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
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


def test_default_projection_returns_only_featured_current_observations() -> None:
    manifest_bytes, artifact_bytes = _expanded_bundle()
    public = verify_export(
        manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT
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
    _manifest, manifest_bytes, artifact_bytes = _bundle()
    candidate = verify_export(manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT)

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

    context = load_accepted_export(
        *_write_signed_bundle(tmp_path, signer),
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


def test_rest_and_mcp_expose_the_same_context_without_building_the_world_board(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = load_accepted_export(
        *_write_signed_bundle(tmp_path, signer),
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(context_views, "public_china_macro_context", lambda: None)
    monkeypatch.setattr(context_views, "public_china_economic_context", lambda: context)

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
    assert rest["generated_at"] is None


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
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    wrappers[0]["observation"]["source_id"] = source_id
    wrappers[0]["observation"]["observation_id"] = _observation_id(
        wrappers[0]["observation"]
    )
    manifest_bytes, artifact_bytes = _replace_artifact(manifest, wrappers)

    with pytest.raises(PalimpsestChinaIntakeError, match="not allowlisted"):
        verify_export(manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT)


def test_nonallowlisted_source_decision_cannot_enable_values_or_export() -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
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
        verify_export(_canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT)


def test_unknown_source_decision_is_null_default_deny_with_zero_exports() -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
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
        "input_records": 1,
        "exported_records": 0,
    }
    manifest["source_decisions"].insert(2, unknown)
    manifest["input_ledger"]["records"] += 1

    context = verify_export(
        _canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT
    )

    assert context.to_dict()["observation_count"] == 2
    assert all(
        row.record["source_id"] == "world_bank_wdi" for row in context.observations
    )


def test_allow_decision_expiring_at_generation_time_is_rejected() -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["expires_at"] = manifest["generated_at"]

    with pytest.raises(PalimpsestChinaIntakeError, match="effective state"):
        verify_export(_canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT)


def test_rights_must_remain_effective_at_acceptance() -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
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
        verify_export(_canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT)


def test_source_decision_digest_is_recomputed() -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    wdi = next(
        row
        for row in manifest["source_decisions"]
        if row["source_id"] == "world_bank_wdi"
    )
    wdi["decision_sha256"] = "0" * 64

    with pytest.raises(PalimpsestChinaIntakeError, match="digest does not match"):
        verify_export(_canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT)


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
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    mutation(manifest)

    with pytest.raises(PalimpsestChinaIntakeError, match=message):
        verify_export(_canonical(manifest), artifact_bytes, accepted_at=ACCEPTED_AT)


def test_observation_id_is_recomputed_after_artifact_commitments_pass() -> None:
    manifest, _manifest_bytes, artifact_bytes = _bundle()
    wrappers = [json.loads(line) for line in artifact_bytes.splitlines()]
    wrappers[0]["observation"]["value"] = 1.0
    manifest_bytes, artifact_bytes = _replace_artifact(manifest, wrappers)

    with pytest.raises(PalimpsestChinaIntakeError, match="observation_id"):
        verify_export(manifest_bytes, artifact_bytes, accepted_at=ACCEPTED_AT)


def test_seiche_acceptance_clock_cannot_precede_palimpest_collection() -> None:
    _manifest, manifest_bytes, artifact_bytes = _bundle()

    with pytest.raises(PalimpsestChinaIntakeError, match="accepted_at"):
        verify_export(
            manifest_bytes,
            artifact_bytes,
            accepted_at=datetime(2026, 8, 24, 11, 59, tzinfo=UTC),
        )


def test_file_reader_rejects_symlinks_and_identity_swaps(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest_path, artifact_path, acceptance_path = _write_signed_bundle(
        tmp_path, signer
    )
    manifest_link = tmp_path / "manifest-link.json"
    manifest_link.symlink_to(manifest_path)
    with pytest.raises(PalimpsestChinaIntakeError, match="single-link regular file"):
        load_accepted_export(
            manifest_link,
            artifact_path,
            acceptance_path,
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
    _manifest, manifest_bytes, artifact_bytes = _bundle()
    _private_key, public_key, trust = signer
    receipt_bytes = _signed_receipt(manifest_bytes, artifact_bytes, signer)
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
    manifest_path.write_bytes(manifest_bytes)
    artifact_path.write_bytes(artifact_bytes)
    acceptance_path.write_bytes(receipt_bytes)

    loaded = load_accepted_export(
        manifest_path,
        artifact_path,
        acceptance_path,
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
            attest_dir=trust,
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )


def test_untrusted_or_tampered_acceptance_signature_fails_closed(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    manifest_path, artifact_path, acceptance_path = _write_signed_bundle(
        tmp_path, signer
    )
    other_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    other_trust = tmp_path / "other-trust"
    other_trust.mkdir()
    (other_trust / "trusted_operator_keys").write_text(other_key + "\n")

    with pytest.raises(PalimpsestChinaIntakeError, match="not trusted and valid"):
        load_accepted_export(
            manifest_path,
            artifact_path,
            acceptance_path,
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
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
        )


def test_cached_verification_still_rechecks_future_clock_and_rights_expiry(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_signed_bundle(tmp_path, signer)
    loaded = load_accepted_export(
        *paths,
        attest_dir=signer[2],
        now=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
    )

    def forbidden_verify(*_args, **_kwargs):
        raise AssertionError("immutable accepted export was re-verified")

    monkeypatch.setattr(intake, "verify_export", forbidden_verify)
    assert (
        load_accepted_export(
            *paths,
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 13, 1, tzinfo=UTC),
        )
        is loaded
    )
    with pytest.raises(
        PalimpsestChinaIntakeError, match="acceptance clock is in the future"
    ):
        load_accepted_export(
            *paths,
            attest_dir=signer[2],
            now=datetime(2026, 8, 24, 12, 1, tzinfo=UTC),
        )
    with pytest.raises(PalimpsestChinaIntakeError, match="expired at serve time"):
        load_accepted_export(
            *paths,
            attest_dir=signer[2],
            now=datetime(2027, 8, 24, 0, 0, tzinfo=UTC),
        )


def test_acceptance_cli_emits_exact_claim_and_verified_receipt(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    private_key, public_key, trust = signer
    manifest_path, artifact_path, _acceptance_path = _write_signed_bundle(
        tmp_path, signer
    )
    common = [
        str(manifest_path),
        str(artifact_path),
        "--accepted-at",
        "2026-08-24T12:02:00Z",
        "--signer-key-id",
        public_key,
        "--confirm-github-run-attestation-verified",
        "--confirm-exact-input-hashes-verified",
    ]

    assert acceptance_cli.main(["claim", *common]) == 0
    claim_bytes = capfd.readouterr().out.encode()
    signature = private_key.sign(claim_bytes).hex()
    assert (
        acceptance_cli.main(
            [
                "receipt",
                *common,
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


def test_acceptance_cli_requires_independent_run_and_input_hash_confirmations(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    _private_key, public_key, _trust = signer
    manifest_path, artifact_path, _acceptance_path = _write_signed_bundle(
        tmp_path, signer
    )
    command = [
        "claim",
        str(manifest_path),
        str(artifact_path),
        "--accepted-at",
        "2026-08-24T12:02:00Z",
        "--signer-key-id",
        public_key,
    ]

    with pytest.raises(SystemExit) as missing_both:
        acceptance_cli.main(command)
    assert missing_both.value.code == 2
    with pytest.raises(SystemExit) as missing_hashes:
        acceptance_cli.main([*command, "--confirm-github-run-attestation-verified"])
    assert missing_hashes.value.code == 2


def test_configured_loader_is_offline_and_partial_configuration_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH",
        "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH",
        "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    assert context_views.public_china_economic_context() is None

    monkeypatch.setenv("SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH", "/local/manifest")
    with pytest.raises(PalimpsestChinaIntakeError, match="incomplete"):
        context_views.public_china_economic_context()
