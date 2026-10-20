"""A source refusal never becomes broad release or source-access permission."""
from __future__ import annotations

import copy
import importlib.util
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "source_degradation_test", ROOT / "ops/release/source_degradation.py",
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
NOW = datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc)
UUID = "12345678-1234-1234-1234-123456789012"


def context(purpose="recovery"):
    operation = {
        "recovery": {"repository": "beepboop2025/seiche",
                     "workflow_path": ".github/workflows/railway-stateful-recovery.yml",
                     "workflow_ref": "refs/heads/main", "operation": "export-recovery"},
        "native_recovery": {"controller_deployment_id": UUID,
                            "controller_manifest_sha256": "4" * 64,
                            "execution_day": "2026-10-20"},
        "catalog_metadata": {"publication_request_id": "5" * 64, "catalog_sha256": "6" * 64,
                             "health_pair_sha256": "7" * 64},
    }.get(purpose, {"request_id": "5" * 64, "parent_activation_sha256": "6" * 64})
    return {"purpose": purpose, "intent_id": "1" * 64, "controller_source_sha": "2" * 40,
            "runtime_source_sha": "3" * 40, "target": {key: UUID for key in
            ("project_id", "environment_id", "service_id")}, "deployment_id": UUID,
            "expected_version": "0.16.5", "expected_authority": "production", "operation": operation}


def fault(source="cbuae_donia"):
    return {"source": source, "status": "FAILED", "category": "HTTP_ERROR", "detail": gate.HTTP_DETAIL}


def admission(ctx=None):
    return {"schema": gate.SCHEMA, "context": ctx or context(),
            "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
            "expires_at": (NOW + timedelta(hours=3)).isoformat(),
            "faults": [fault()], "faults_sha256": gate.digest(gate.canonical([fault()])),
            "limits": dict(gate.LIMITS)}


def observation(ctx=None):
    ctx = ctx or context()
    return {"body": {"generated_at": NOW.isoformat(), "version": "0.16.5 estuary",
                     "faults": [fault()], "provenance": [{"source": "cbuae_donia",
                     "observed_at": "2026-10-19T22:35:00Z", "retrieved_at": "2026-10-19T22:35:00Z"}]},
            "identity": {"source_sha": ctx["runtime_source_sha"], "deployment_id": ctx["deployment_id"],
                         "authority": ctx["expected_authority"]}}


def assess(value=None, *, ctx=None, origin=None, public=None):
    ctx = ctx or context()
    return gate.assess(gate.canonical(value or admission(ctx)), b"signature", expected_context=ctx,
                       origin=origin or observation(ctx), public=public or observation(ctx), now=NOW)


@pytest.fixture
def verified(monkeypatch):
    monkeypatch.setattr(gate, "verify_owner_signature", lambda body, signature: None)


def test_signed_scope_preserves_faults_source_clocks_and_never_claims_runtime_readiness(verified):
    origin = observation()
    before = copy.deepcopy(origin)
    result = assess(origin=origin)
    assert origin == before
    assert result["source_health_admitted"] is True
    assert result["evidence_status"] == "degraded"
    assert result["admitted_source_faults"] == [fault()]
    assert "runtime_ready" not in result
    assert "observed_at" not in result
    assert result["context"] == context()


def test_no_admission_is_strict_and_recovery_needs_no_expired_admission():
    with pytest.raises(gate.AdmissionError, match="requires owner admission"):
        gate.assess(None, None, expected_context=context(), origin=observation(), public=observation(), now=NOW)
    healthy = observation()
    healthy["body"]["faults"] = []
    result = gate.assess(None, None, expected_context=context(), origin=healthy, public=healthy, now=NOW)
    assert result["evidence_status"] == "healthy"
    assert result["source_health_admitted"] is False
    with pytest.raises(gate.AdmissionError, match="requires an authenticated"):
        gate.claim_document(result, execution={})


@pytest.mark.parametrize("purpose", sorted(gate.PURPOSES))
def test_each_purpose_is_explicit_and_separate(verified, purpose):
    ctx = context(purpose)
    result = assess(ctx=ctx)
    assert result["context"]["purpose"] == purpose
    other = copy.deepcopy(ctx)
    other["intent_id"] = "f" * 64
    with pytest.raises(gate.AdmissionError, match="expected operation"):
        assess(admission(ctx), ctx=other)


@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"), ("limits", {**gate.LIMITS, "source_permission": True}),
    ("limits", {**gate.LIMITS, "execution_authority": True}),
    ("limits", {**gate.LIMITS, "raw_data_publication": True}),
    ("limits", {**gate.LIMITS, "raw_data_publication": 0}),
    ("issued_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("expires_at", NOW.isoformat()), ("expires_at", (NOW + timedelta(hours=5)).isoformat()),
    ("issued_at", "2026-10-20T12:59:00"), ("expires_at", "malformed"),
    ("faults_sha256", "0" * 64),
])
def test_closed_admission_and_lifetime_rejections(verified, field, value):
    data = admission()
    data[field] = value
    with pytest.raises(gate.AdmissionError):
        assess(data)


@pytest.mark.parametrize("field,value", [
    ("runtime_source_sha", "f" * 40), ("controller_source_sha", "f" * 40),
    ("deployment_id", "abcdefab-1234-1234-1234-123456789012"),
    ("expected_version", "0.16.6"), ("expected_authority", "candidate"),
    ("target", {key: "abcdefab-1234-1234-1234-123456789012" for key in
                ("project_id", "environment_id", "service_id")}),
])
def test_signed_target_source_version_and_authority_cannot_drift(verified, field, value):
    data = admission()
    data["context"][field] = value
    with pytest.raises(gate.AdmissionError, match="expected operation"):
        assess(data)


@pytest.mark.parametrize("field,value", [
    ("source", "unknown"), ("source", "*"), ("source", ["cbuae_donia"]),
    ("category", "WORKER_HEALTH"), ("category", "PERSISTENCE_ERROR"),
    ("category", "VALIDATION_ERROR"), ("category", "INTERNAL_ERROR"), ("category", "TIMEOUT"),
    ("status", "OVERDUE"), ("status", "HEALTHY"),
    ("detail", "https://secret:credential@example.invalid/body?key=SECRET_CANARY"),
])
def test_only_registered_sanitized_failed_http_source_class_can_be_admitted(verified, field, value):
    item = observation()
    item["body"]["faults"][0][field] = value
    with pytest.raises(gate.AdmissionError) as error:
        assess(origin=item)
    assert "SECRET_CANARY" not in str(error.value)
    assert "example.invalid" not in str(error.value)


@pytest.mark.parametrize("mutation", ["extra", "missing", "duplicate", "unknown_key", "empty_signed"])
def test_exact_complete_fault_set_is_required(verified, mutation):
    data, origin = admission(), observation()
    if mutation == "extra":
        origin["body"]["faults"].append(fault("cbuae_fx"))
    elif mutation == "missing":
        origin["body"]["faults"] = []
    elif mutation == "duplicate":
        origin["body"]["faults"].append(fault())
    elif mutation == "unknown_key":
        origin["body"]["faults"][0]["secret"] = "CANARY"
    else:
        data["faults"] = []
        data["faults_sha256"] = gate.digest(gate.canonical([]))
    with pytest.raises(gate.AdmissionError):
        assess(data, origin=origin)


@pytest.mark.parametrize("mutation", ["stale", "future", "no_provenance", "wrong_source",
                                    "wrong_deployment", "wrong_authority", "version",
                                    "pair_clock", "pair_provenance"])
def test_both_current_observations_must_match_exact_operation_and_snapshot(verified, mutation):
    origin = observation()
    if mutation == "stale":
        origin["body"]["generated_at"] = (NOW - timedelta(seconds=901)).isoformat()
    elif mutation == "future":
        origin["body"]["generated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "no_provenance":
        origin["body"]["provenance"] = []
    elif mutation == "wrong_source":
        origin["identity"]["source_sha"] = "f" * 40
    elif mutation == "wrong_deployment":
        origin["identity"]["deployment_id"] = "f"
    elif mutation == "wrong_authority":
        origin["identity"]["authority"] = "candidate"
    elif mutation == "version":
        origin["body"]["version"] = "0.16.6"
    elif mutation == "pair_clock":
        origin["body"]["generated_at"] = (NOW - timedelta(seconds=1)).isoformat()
    else:
        origin["body"]["provenance"][0]["observed_at"] = NOW.isoformat()
    with pytest.raises(gate.AdmissionError):
        assess(origin=origin)


@pytest.mark.parametrize("body", [
    b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n',
    b'{ "x":1}\n', b'{"x":1}', b"[]\n", b"x" * 16_385, b"\xff",
])
def test_noncanonical_duplicate_nonfinite_or_oversized_documents_reject(body):
    with pytest.raises(gate.AdmissionError):
        gate.document(body)


def test_claim_binds_actual_run_attempt_and_original_intent(verified):
    result = assess()
    execution = {"kind": "github_actions", "repository": "beepboop2025/seiche",
                 "run_id": "12345", "run_attempt": "1"}
    claim = gate.claim_document(result, execution=execution)
    gate.verify_claim(claim, result, execution=execution)
    for changed in ({**execution, "run_id": "12346"}, {**execution, "run_attempt": "2"}):
        with pytest.raises(gate.AdmissionError, match="different operation"):
            gate.verify_claim(claim, result, execution=changed)
    other = copy.deepcopy(result)
    other["context"]["intent_id"] = "f" * 64
    with pytest.raises(gate.AdmissionError, match="different operation"):
        gate.verify_claim(claim, other, execution=execution)


def test_native_day_and_local_request_are_bound(verified):
    ctx = context("native_recovery")
    result = assess(ctx=ctx)
    gate.claim_document(result, execution={"kind": "native_recovery",
                        "controller_deployment_id": UUID, "execution_day": "2026-10-20"})
    ctx["operation"]["execution_day"] = "2026-10-19"
    with pytest.raises(gate.AdmissionError, match="execution day"):
        assess(ctx=ctx)
    ctx = context("application_handoff")
    result = assess(ctx=ctx)
    execution = {"kind": "local_application", "purpose": ctx["purpose"],
                 "request_id": ctx["operation"]["request_id"], "operator_intent_id": ctx["intent_id"]}
    gate.claim_document(result, execution=execution)
    with pytest.raises(gate.AdmissionError, match="actual application"):
        gate.claim_document(result, execution={**execution, "request_id": "f" * 64})


def test_internal_handoff_preserves_new_candidate_without_faking_public_snapshot(verified):
    ctx = context("application_handoff")
    internal = observation(ctx)
    before = copy.deepcopy(internal)
    result = gate.assess_internal(gate.canonical(admission(ctx)), b"signature",
                                 expected_context=ctx, observation=internal, now=NOW)
    assert result["evidence_status"] == "degraded"
    assert internal == before
    internal["body"]["faults"] = []
    result = gate.assess_internal(None, None, expected_context=ctx, observation=internal, now=NOW)
    assert result["evidence_status"] == "healthy"


@pytest.mark.parametrize("purpose", sorted(gate.PURPOSES - {"application_handoff"}))
def test_internal_assessment_cannot_replace_public_origin_gate(verified, purpose):
    ctx = context(purpose)
    with pytest.raises(gate.AdmissionError, match="only allowed for application handoff"):
        gate.assess_internal(gate.canonical(admission(ctx)), b"signature",
                             expected_context=ctx, observation=observation(ctx), now=NOW)


@pytest.mark.parametrize("mutation", ["identity", "stale", "fault", "version", "provenance", "signature"])
def test_internal_handoff_retains_signature_and_observation_gates(verified, monkeypatch, mutation):
    ctx = context("application_handoff")
    internal = observation(ctx)
    if mutation == "identity":
        internal["identity"]["source_sha"] = "f" * 40
    elif mutation == "stale":
        internal["body"]["generated_at"] = (NOW - timedelta(seconds=901)).isoformat()
    elif mutation == "fault":
        internal["body"]["faults"].append(fault("cbuae_fx"))
    elif mutation == "version":
        internal["body"]["version"] = "0.16.6"
    elif mutation == "provenance":
        internal["body"]["provenance"] = []
    else:
        def reject(*args):
            raise gate.AdmissionError("owner admission signature is invalid")
        monkeypatch.setattr(gate, "verify_owner_signature", reject)
    with pytest.raises(gate.AdmissionError):
        gate.assess_internal(gate.canonical(admission(ctx)), b"signature",
                             expected_context=ctx, observation=internal, now=NOW)


def test_real_ssh_signature_requires_pinned_owner_namespace_and_unmodified_bytes(tmp_path, monkeypatch):
    key = tmp_path / "test-only-owner"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    test_public = key.with_suffix(".pub").read_text().strip()
    body = tmp_path / "admission.json"
    body.write_bytes(gate.canonical(admission()))
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", gate.SIGNATURE_NAMESPACE,
                    str(body)], capture_output=True, check=True)
    signature = Path(str(body) + ".sig").read_bytes()
    with pytest.raises(gate.AdmissionError, match="invalid"):
        gate.verify_owner_signature(body.read_bytes(), signature)
    monkeypatch.setattr(gate, "OWNER_PUBLIC", test_public)
    gate.verify_owner_signature(body.read_bytes(), signature)
    with pytest.raises(gate.AdmissionError, match="invalid"):
        gate.verify_owner_signature(body.read_bytes() + b" ", signature)
    monkeypatch.setattr(gate, "SIGNATURE_NAMESPACE", "another-purpose")
    with pytest.raises(gate.AdmissionError, match="invalid"):
        gate.verify_owner_signature(body.read_bytes(), signature)
