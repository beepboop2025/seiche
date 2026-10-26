"""Cold candidate evidence never becomes a production release capability."""
from copy import deepcopy
from datetime import UTC, datetime
import os

import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble
from seiche import stateful_application as application
from seiche import stateful_cold_snapshot as cold
from seiche import stateful_cutover as cutover
from test_application_parent_readiness import parent_candidate  # noqa: F401
from test_stateful_application import signed, transition  # noqa: F401


@pytest.fixture
def cold_candidate(parent_candidate, signed, monkeypatch):  # noqa: F811
    platform, environment, request, old_path, envelope, calls = parent_candidate
    candidate = application.read_document(old_path)
    request["operation"] = "application_cold_recovery"
    request.pop("request_id")
    request["request_id"] = application.digest(request)
    candidate["request"].update(
        id=request["request_id"], sha256=application.digest(request)
    )
    fence = deepcopy(candidate["source_fence"]["payload"])
    fence["request_id"] = request["request_id"]
    candidate["source_fence"] = signed("source_stopped", fence)
    application.REQUEST_PATH.write_bytes(application.canonical(request))
    new_path = platform / "cutover-receipts" / (
        request["request_id"] + ".application-candidate.json"
    )
    new_path.write_bytes(application.canonical(candidate))
    for name in ("SEICHE_RAILWAY_APPLICATION_REQUEST_ID",
                 "SEICHE_RAILWAY_CUTOVER_REQUEST_ID"):
        environment[name] = request["request_id"]
        monkeypatch.setenv(name, request["request_id"])
    application.validate_runtime(os.environ, production=False)
    payload = deepcopy(envelope["payload"])
    payload["generated_at"] = datetime.now(UTC).isoformat()
    payload["version"] = "candidate fresh computation"
    result = {
        "payload": payload,
        "evidence": {"artifact_sha256": "7" * 64},
        "cache_source": "application_cold_candidate",
    }
    monkeypatch.setattr(cold, "load_candidate_snapshot", lambda _: deepcopy(result))
    return platform, environment, request, envelope, calls, result


def test_cold_candidate_health_has_no_release_or_writer_authority(cold_candidate):
    platform, environment, request, parent, calls, result = cold_candidate
    before = {str(p): p.read_bytes() for p in platform.rglob("*") if p.is_file()}
    parent_before = deepcopy(parent)
    assert assemble.restore_cached_snapshot(read_only=True) == "application_cold_candidate"
    assert calls == []
    assert assemble.cached_application_cold_ready()
    assert not assemble.cached_application_parent_ready()
    assert not assemble.cached_snapshot_was_rebuilt()
    assert assemble.cached_snapshot_release_receipt() is None
    assert assemble.cached_snapshot_release_handoff() is None
    with TestClient(
        api.app, headers={cutover.EDGE_HEADER: environment["SEICHE_RAILWAY_EDGE_TOKEN"]}
    ) as client:
        ready = client.get("/healthz")
        assert ready.status_code == 200, ready.text
        assert ready.json()["generated_at"] == result["payload"]["generated_at"]
        health = client.get("/api/health")
        assert health.headers["X-Seiche-Railway-Authority"] == "candidate"
        assert health.headers["X-Seiche-Release-SHA"] == request["commit"]
        assert client.get("/api/health?require_rebuilt=true").status_code == 503
        assert client.get("/api/internal/v1/release-health").status_code == 503
    assert parent == parent_before
    assert {str(p): p.read_bytes() for p in platform.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("change", ["payload", "evidence", "producer", "receipt", "handoff"])
def test_cold_readiness_rejects_changed_memory(cold_candidate, change):
    assemble.restore_cached_snapshot(read_only=True)
    if change == "payload":
        assemble._cache["payload"]["generated_at"] = "2099-01-01T00:00:00Z"
    elif change == "evidence":
        assemble._cache["cold_evidence"] = {}
    elif change == "producer":
        assemble._cache["producer_sha"] = "f" * 40
    elif change == "receipt":
        assemble._cache["release_receipt"] = {}
    else:
        assemble._cache["release_handoff_id"] = "f" * 64
    assert not assemble.cached_application_cold_ready()
    assert api._railway_stateful_health_response(api.Response()).status_code == 503


def test_expired_or_missing_cold_proof_cannot_use_parent(cold_candidate, monkeypatch):
    _, _, request, _, calls, _ = cold_candidate
    def unavailable(_):
        raise application.ApplicationContractError("cold snapshot expired")
    monkeypatch.setattr(cold, "load_candidate_snapshot", unavailable)
    with pytest.raises(application.ApplicationContractError, match="expired"):
        assemble.restore_cached_snapshot(read_only=True)
    assert calls == []
    assert not assemble.cached_application_cold_ready()
    with pytest.raises(application.ApplicationContractError, match="isolated fresh"):
        application.candidate_parent_handoff(
            os.environ, process_release_sha=request["commit"]
        )


def test_cold_operation_is_explicit_and_digest_bound(transition):
    request = deepcopy(transition[2])
    request["operation"] = "application_cold_recovery"
    with pytest.raises(application.ApplicationContractError, match="digest"):
        application.validate_request(request)
    request.pop("request_id")
    request["request_id"] = application.digest(request)
    assert application.validate_request(request)["operation"] == "application_cold_recovery"
    request["operation"] = "implicit_recovery"
    request.pop("request_id")
    request["request_id"] = application.digest(request)
    with pytest.raises(application.ApplicationContractError, match="policy"):
        application.validate_request(request)

def test_expired_cold_cache_stops_public_reads(cold_candidate, monkeypatch):
    import asyncio
    assemble.restore_cached_snapshot(read_only=True)
    def expired(_):
        raise application.ApplicationContractError("expired proof")
    monkeypatch.setattr(cold, "load_candidate_snapshot", expired)
    assert assemble.cached_snapshot() is None
    assert api._health_response(
        api.Response(), require_rebuilt=False, include_release_candidate=False
    ).status_code == 503
    with pytest.raises(RuntimeError, match="not hydrated"):
        asyncio.run(assemble.snapshot(force=True))


def test_cold_grant_rechecks_freshness_after_final_audit(cold_candidate, monkeypatch):
    from seiche import stateful_application_runtime as runtime
    platform, environment, request, _, _, _ = cold_candidate
    observed = []
    monkeypatch.setattr(application, "validate_grant", lambda *a, **k: {
        "edge_token_sha256": environment["SEICHE_RAILWAY_EDGE_TOKEN_SHA256"]
    })
    monkeypatch.setattr(runtime, "audit_current_state", lambda *a: observed.append("audit"))
    def expired(_):
        observed.append("freshness")
        raise application.ApplicationContractError("expired after audit")
    monkeypatch.setattr(cold, "load_candidate_snapshot", expired)
    def forbidden(*a, **k):
        raise AssertionError("expired cold evidence must not accept authority")
    monkeypatch.setattr(runtime, "accept_grant", forbidden)
    with pytest.raises(application.ApplicationContractError, match="expired after audit"):
        runtime._activate(environment, request, {}, {}, {}, platform / "activation.json", "")
    assert observed == ["audit", "freshness"]


def test_already_accepted_cold_grant_does_not_depend_on_temporary_cache(
    cold_candidate, monkeypatch
):
    from seiche import stateful_application_runtime as runtime
    platform, environment, request, _, _, _ = cold_candidate
    accepted = platform / "authority" / "application-grants" / (request["request_id"] + ".json")
    accepted.parent.mkdir(parents=True, exist_ok=True)
    accepted.write_text("{}")
    monkeypatch.setattr(application, "validate_grant", lambda *a, **k: {
        "edge_token_sha256": environment["SEICHE_RAILWAY_EDGE_TOKEN_SHA256"]
    })
    monkeypatch.setattr(runtime, "audit_current_state", lambda *a: None)
    def forbidden(_):
        raise AssertionError("accepted authority must not require expired cold cache")
    monkeypatch.setattr(cold, "load_candidate_snapshot", forbidden)
    class ReachedDurableAcceptance(Exception):
        pass
    def reached(*a, **k):
        raise ReachedDurableAcceptance
    monkeypatch.setattr(runtime, "accept_grant", reached)
    with pytest.raises(ReachedDurableAcceptance):
        runtime._activate(environment, request, {}, {}, {}, platform / "activation.json", "")
