"""Contracts for the compute-only Railway snapshot prebuild boundary."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from seiche import (
    assemble,
    config,
    remote_snapshot_build,
    remote_snapshot_import,
    repository,
)


def _release_receipt(payload: dict) -> dict:
    return {
        "generated_at": payload["generated_at"],
        "producer": "seiche.markets.us_usd.materialize.seal_legacy_snapshot",
        "products": {
            "overview": {
                "snapshot_id": "a" * 64,
                "forward_record_id": "b" * 64,
                "snapshot_row_sha256": "c" * 64,
            },
            "gauge": {
                "snapshot_id": "d" * 64,
                "forward_record_id": "e" * 64,
                "snapshot_row_sha256": "f" * 64,
            },
        },
    }


def _artifact(payload: dict, release_sha: str) -> dict[str, object]:
    generated = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    payload_bytes = remote_snapshot_import.canonical_value(payload)
    provenance = payload["provenance"]
    faults = payload["faults"]
    return {
        "schema": remote_snapshot_import.SCHEMA,
        "repository": remote_snapshot_import.REPOSITORY,
        "workflow": remote_snapshot_import.WORKFLOW,
        "source_ref": remote_snapshot_import.SOURCE_REF,
        "commit": release_sha,
        "tree": "1" * 40,
        "source_archive_sha256": "2" * 64,
        "request_id": "3" * 64,
        "runner_provider": "railway",
        "runner_image": "pinned-image",
        "railway_deployment_id": "11111111-1111-4111-8111-111111111111",
        "railway_project_id": "22222222-2222-4222-8222-222222222222",
        "railway_environment_id": "33333333-3333-4333-8333-333333333333",
        "railway_service_id": "44444444-4444-4444-8444-444444444444",
        "railway_replica_region": "europe-west4-drams3a",
        "started_at": (generated - timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "completed_at": (generated + timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "conclusion": "success",
        "install_command": "pinned-install",
        "build_command": "pinned-build",
        "python_version": "3.12.11",
        "dependency_snapshot_sha256": "4" * 64,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload_size_bytes": len(payload_bytes),
        "generated_at": payload["generated_at"],
        "provenance_sha256": hashlib.sha256(
            remote_snapshot_import.canonical_value(provenance)
        ).hexdigest(),
        "provenance_count": len(provenance),
        "faults_sha256": hashlib.sha256(
            remote_snapshot_import.canonical_value(faults)
        ).hexdigest(),
        "fault_count": len(faults),
        "payload": payload,
    }


def test_runtime_data_dir_override_requires_an_absolute_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEICHE_RUNTIME_DATA_DIR", str(tmp_path))
    assert config._runtime_data_dir() == tmp_path

    monkeypatch.setenv("SEICHE_RUNTIME_DATA_DIR", "relative/runtime")
    with pytest.raises(ValueError, match="must be an absolute path"):
        config._runtime_data_dir()


@pytest.mark.asyncio
async def test_prebuild_runs_full_compute_without_local_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def gather_sources():
        return {}, []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("prebuild must not mutate local release state")

    async def forbidden_async(*_args, **_kwargs):
        raise AssertionError("prebuild must not publish local release state")

    monkeypatch.setattr(assemble, "_gather_sources", gather_sources)
    monkeypatch.setattr(assemble, "_rights_eligible_sources", lambda source: source)
    monkeypatch.setattr(
        assemble,
        "_derived",
        lambda _source: {"spread_bp": assemble.pd.Series(dtype=float)},
    )
    monkeypatch.setattr(assemble, "_run_engines", lambda *_args: {"composite": {}})
    monkeypatch.setattr(
        assemble,
        "_deep_layer",
        lambda *_args: {"historical_evidence": {"basis": "fixture"}},
    )
    monkeypatch.setattr(assemble.eng_modelcourt, "convene", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(assemble, "_headline", lambda *_args: {})
    monkeypatch.setattr(assemble, "_calendar", lambda *_args: {})
    monkeypatch.setattr(assemble, "_provenance", lambda *_args: [])
    monkeypatch.setattr(assemble, "_assert_snapshot_rights", lambda _payload: None)
    monkeypatch.setattr(assemble.editorial, "build_editorial", lambda **_kwargs: {})
    monkeypatch.setattr(assemble.editorial, "build_data_quality", lambda **_kwargs: {})
    monkeypatch.setattr(assemble, "_record_pit", forbidden)
    monkeypatch.setattr(assemble, "_seal_release_evidence", forbidden)
    monkeypatch.setattr(assemble, "_publish_rebuilt_snapshot", forbidden_async)

    cache_before = dict(assemble._cache)
    generation_before = assemble._build_generation
    payload = await assemble.prebuild_snapshot_payload()

    assert payload["historical_evidence"] == {"basis": "fixture"}
    assert payload["navigator"] == {
        "ok": False,
        "reason": "no spread data-day to commit against",
    }
    assert assemble._cache == cache_before
    assert assemble._build_generation == generation_before


def test_remote_builder_requires_a_servable_rights_clean_payload(
    monkeypatch: pytest.MonkeyPatch,
    fake_snap: dict,
) -> None:
    async def payload() -> dict:
        return fake_snap

    monkeypatch.setattr(assemble, "prebuild_snapshot_payload", payload)
    built = asyncio.run(remote_snapshot_build.build_payload())
    assert built is fake_snap
    assert remote_snapshot_build.canonical_payload(built).endswith(b"\n")

    async def invalid() -> dict:
        return {"generated_at": "2026-08-23T00:00:00Z"}

    monkeypatch.setattr(assemble, "prebuild_snapshot_payload", invalid)
    with pytest.raises(ValueError, match="not safely servable"):
        asyncio.run(remote_snapshot_build.build_payload())


def test_host_import_reseals_and_stages_exact_prebuilt_payload(
    monkeypatch: pytest.MonkeyPatch,
    fake_snap: dict,
) -> None:
    release_sha = "9" * 40
    artifact = _artifact(fake_snap, release_sha)
    generated = datetime.fromisoformat(
        fake_snap["generated_at"].replace("Z", "+00:00")
    ).astimezone(UTC)
    receipt = _release_receipt(fake_snap)
    calls = []
    monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: release_sha)
    monkeypatch.setattr(
        assemble, "_seal_release_evidence", lambda payload: receipt if payload is fake_snap else None
    )
    monkeypatch.setattr(
        assemble,
        "_persist_pending_snapshot",
        lambda payload, value: calls.append((payload, value)) or "8" * 64,
    )
    monkeypatch.setattr(
        assemble,
        "verify_pending_snapshot",
        lambda sha, token: (sha, token) == (release_sha, "8" * 64),
    )

    assert remote_snapshot_import.stage_artifact(artifact, now=generated) == "8" * 64
    assert calls == [(fake_snap, receipt)]

    tampered = json.loads(json.dumps(artifact))
    tampered["payload"]["version"] = "tampered"
    with pytest.raises(ValueError, match="payload digest"):
        remote_snapshot_import.stage_artifact(tampered, now=generated)


def test_import_requires_exact_canonical_artifact_bytes(fake_snap: dict) -> None:
    artifact = _artifact(fake_snap, "9" * 40)
    canonical = remote_snapshot_import.canonical_value(artifact) + b"\n"
    assert remote_snapshot_import.load_artifact(io.BytesIO(canonical)) == artifact
    noncanonical = json.dumps(artifact, ensure_ascii=False).encode("utf-8") + b"\n"
    with pytest.raises(ValueError, match="not canonical"):
        remote_snapshot_import.load_artifact(io.BytesIO(noncanonical))


def test_candidate_hydrates_only_root_selected_exact_pending_handoff(
    monkeypatch: pytest.MonkeyPatch,
    fake_snap: dict,
) -> None:
    release_sha = "7" * 40
    envelope = assemble._build_handoff(
        fake_snap,
        _release_receipt(fake_snap),
        release_sha,
    )
    token = envelope["handoff_id"]

    class FakeRepository:
        @staticmethod
        def load_release_handoff(handoff_id):
            return envelope if handoff_id == token else None

        @staticmethod
        def load_active_release_handoff():
            raise AssertionError("selected prebuild must precede the active fallback")

    monkeypatch.setattr(repository, "get_repository", FakeRepository)
    monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: release_sha)
    monkeypatch.setattr(
        assemble,
        "_cache",
        {
            "at": 0.0,
            "payload": None,
            "source": None,
            "release_receipt": None,
            "release_handoff_id": None,
            "producer_sha": None,
        },
    )
    monkeypatch.setenv("SEICHE_PREBUILT_HANDOFF_ID", token)
    monkeypatch.setenv(
        "SEICHE_PREBUILT_PAYLOAD_SHA256", assemble._snapshot_digest(fake_snap)
    )

    assert assemble.restore_cached_snapshot() == "prebuilt"
    assert assemble.cached_snapshot() == fake_snap
    assert assemble.cached_snapshot_was_rebuilt() is True
    assert assemble.cached_snapshot_release_handoff() == {
        "producer_sha": release_sha,
        "activation_token": token,
    }
