"""Owner-scoped source-health admission; never source or execution authority.

The caller still verifies runtime/storage/provider identity and its existing
operation authorization. It constructs expected_context from those trusted
inputs. This module performs no network calls, retries, signing, or claim I/O.
Before an authority action, the caller must atomically persist claim_document
and reject an existing different claim. A read-only monitor does not consume it.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "seiche.source-degradation.v1"
CLAIM_SCHEMA = "seiche.source-degradation-claim.v1"
SIGNATURE_NAMESPACE = "seiche-source-degradation-v1"
OWNER_PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"
MAX_BYTES = 16_384
MAX_LIFETIME = timedelta(hours=4)
MAX_SNAPSHOT_AGE = timedelta(seconds=900)
PURPOSES = frozenset({
    "recovery", "native_recovery", "application_candidate",
    "application_activation", "application_handoff", "catalog_metadata",
})
# Fixed collector identities from assemble._gather_sources. Per-series and
# arbitrary diagnostics are deliberately outside this first admission contract.
KNOWN_SOURCES = frozenset({
    "fred", "fred_cp_rates", "fred_custody", "eia_petroleum", "ofr",
    "ofr_gcf", "ofr_pd_financing", "llama_hacks", "windfetch", "ecb",
    "ecb_fx", "cbuae_fx", "cbuae_donia", "boj", "bis", "crypto",
    "nyfed_rates", "nyfed_srf", "nyfed_pd", "nyfed_fxs", "nyfed_rde",
    "tga", "auctions", "upcoming", "mspd", "tff", "commodity_cot",
    "eia_inventory", "palimpsest", "fedtext", "gdelt",
})
LIMITS = {
    "source_permission": False,
    "execution_authority": False,
    "raw_data_publication": False,
}
HTTP_DETAIL = "official source returned an HTTP error"
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


class AdmissionError(ValueError):
    """A bounded, non-sensitive admission failure."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdmissionError(message)


def _matches(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise AdmissionError("document is not canonical JSON") from None


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _require(key not in value, "document contains duplicate keys")
        value[key] = item
    return value


def document(body: bytes) -> dict[str, Any]:
    """Parse canonical bounded JSON only. This does not authenticate it."""
    _require(isinstance(body, bytes) and 0 < len(body) <= MAX_BYTES,
             "document is missing or oversized")
    try:
        value = json.loads(body, object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, RecursionError):
        raise AdmissionError("document is not canonical JSON") from None
    _require(isinstance(value, dict) and canonical(value) == body,
             "document is not canonical JSON")
    return value


def _time(value: Any) -> datetime:
    _require(isinstance(value, str) and len(value) <= 40,
             "timestamp is malformed")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AdmissionError("timestamp is malformed") from None
    _require(result.tzinfo is not None and result.utcoffset() == timedelta(0),
             "timestamp must use UTC")
    return result


def _now(now: datetime) -> None:
    _require(isinstance(now, datetime) and now.tzinfo is not None,
             "assessment clock must be timezone aware")


def validate_context(context: dict[str, Any]) -> None:
    _require(isinstance(context, dict) and set(context) == {
        "purpose", "intent_id", "controller_source_sha", "runtime_source_sha",
        "target", "deployment_id", "expected_version", "expected_authority",
        "operation",
    }, "operation context has an invalid shape")
    purpose = context["purpose"]
    _require(isinstance(purpose, str) and purpose in PURPOSES,
             "operation purpose is not allowed")
    _require(_matches(context["intent_id"], _DIGEST), "intent identity is malformed")
    _require(all(_matches(context[name], _SHA) for name in
                 ("controller_source_sha", "runtime_source_sha")),
             "operation source identity is malformed")
    _require(_matches(context["deployment_id"], _UUID),
             "application deployment identity is malformed")
    target = context["target"]
    _require(isinstance(target, dict) and set(target) ==
             {"project_id", "environment_id", "service_id"} and
             all(_matches(value, _UUID) for value in target.values()),
             "application target is malformed")
    _require(_matches(context["expected_version"], _VERSION),
             "expected application version is malformed")
    _require(context["expected_authority"] in ("production", "candidate"),
             "application authority is not allowed")
    operation = context["operation"]
    _require(isinstance(operation, dict), "operation binding is malformed")
    if purpose == "recovery":
        _require(set(operation) == {"repository", "workflow_path", "workflow_ref", "operation"},
                 "recovery operation binding is malformed")
        _require(operation["repository"] == "beepboop2025/seiche" and
                 operation["workflow_path"] == ".github/workflows/railway-stateful-recovery.yml" and
                 operation["operation"] in ("monitor", "export-recovery"),
                 "recovery operation is not allowed")
        ref = operation["workflow_ref"]
        _require(isinstance(ref, str) and 0 < len(ref) <= 160 and
                 (re.fullmatch(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*", ref) is not None
                  or _matches(ref, _SHA)) and ".." not in ref,
                 "recovery workflow ref is malformed")
    elif purpose == "native_recovery":
        _require(set(operation) ==
                 {"controller_deployment_id", "controller_manifest_sha256", "execution_day"} and
                 _matches(operation["controller_deployment_id"], _UUID) and
                 _matches(operation["controller_manifest_sha256"], _DIGEST),
                 "native recovery operation binding is malformed")
        day = operation["execution_day"]
        _require(isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is not None,
                 "native execution day is malformed")
        _time(day + "T00:00:00Z")
    elif purpose.startswith("application_"):
        _require(set(operation) == {"request_id", "parent_activation_sha256"} and
                 all(_matches(item, _DIGEST) for item in operation.values()),
                 "application operation binding is malformed")
    else:
        _require(set(operation) == {"publication_request_id", "catalog_sha256", "health_pair_sha256"} and
                 all(_matches(item, _DIGEST) for item in operation.values()),
                 "catalog operation binding is malformed")
    _require(len(canonical(context)) <= 4096, "operation context is oversized")


def normalized_faults(faults: Any, *, allow_empty: bool = False) -> list[dict[str, str]]:
    _require(isinstance(faults, list) and
             (0 if allow_empty else 1) <= len(faults) <= 8,
             "source fault set is missing or oversized")
    sources: set[str] = set()
    clean = []
    for fault in faults:
        _require(isinstance(fault, dict) and set(fault) ==
                 {"source", "status", "category", "detail"},
                 "source fault record has an invalid shape")
        source = fault["source"]
        _require(isinstance(source, str) and source in KNOWN_SOURCES and source not in sources,
                 "source fault identity is unknown or duplicated")
        _require(fault["status"] == "FAILED" and fault["category"] == "HTTP_ERROR" and
                 fault["detail"] == HTTP_DETAIL,
                 "fault is outside the admitted source failure class")
        sources.add(source)
        clean.append(dict(fault))
    return sorted(clean, key=lambda item: item["source"])


def verify_owner_signature(body: bytes, signature: bytes) -> None:
    _require(isinstance(signature, bytes) and 0 < len(signature) <= 8192,
             "owner signature is missing or oversized")
    # No key or namespace from the document is trusted. Temp artifacts contain
    # only public data; operators choose their existing private TMPDIR boundary.
    try:
        with tempfile.TemporaryDirectory(prefix="source-health-signature-") as name:
            root = Path(name)
            (root / "signers").write_text("owner " + OWNER_PUBLIC + "\n", encoding="ascii")
            (root / "signature").write_bytes(signature)
            result = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(root / "signers"),
                 "-I", "owner", "-n", SIGNATURE_NAMESPACE, "-s", str(root / "signature")],
                input=body, capture_output=True, timeout=15, check=False,
                env={"PATH": "/usr/bin:/bin", "HOME": name},
            )
            _require(result.returncode == 0, "owner admission signature is invalid")
    except (OSError, subprocess.TimeoutExpired):
        raise AdmissionError("owner admission signature verification failed") from None


def verify_admission(
    body: bytes, signature: bytes, *, expected_context: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    """Verify owner scope; does not assess live health or consume the operation."""
    _now(now)
    validate_context(expected_context)
    value = document(body)
    _require(set(value) == {"schema", "context", "issued_at", "expires_at",
                            "faults", "faults_sha256", "limits"} and value["schema"] == SCHEMA,
             "admission schema is not supported")
    verify_owner_signature(body, signature)
    validate_context(value["context"])
    _require(canonical(value["context"]) == canonical(expected_context),
             "admission differs from the expected operation")
    _require(canonical(value["limits"]) == canonical(LIMITS),
             "admission cannot grant source or execution authority")
    issued, expires = _time(value["issued_at"]), _time(value["expires_at"])
    _require(issued <= now < expires and timedelta(0) < expires - issued <= MAX_LIFETIME,
             "admission is future dated, expired, or overlong")
    if expected_context["purpose"] == "native_recovery":
        _require(expected_context["operation"]["execution_day"] ==
                 now.astimezone(timezone.utc).date().isoformat(),
                 "native admission is for a different execution day")
    faults = normalized_faults(value["faults"])
    _require(value["faults"] == faults and value["faults_sha256"] == digest(canonical(faults)),
             "admitted fault set is not canonical or its digest differs")
    return value


def _observe(observation: dict[str, Any], context: dict[str, Any], now: datetime) -> list[dict[str, str]]:
    _require(isinstance(observation, dict) and set(observation) == {"body", "identity"},
             "health observation is malformed")
    expected_identity = {
        "source_sha": context["runtime_source_sha"],
        "deployment_id": context["deployment_id"],
        "authority": context["expected_authority"],
    }
    _require(observation["identity"] == expected_identity,
             "health observation identity differs")
    body = observation["body"]
    _require(isinstance(body, dict), "health body is malformed")
    generated = _time(body.get("generated_at"))
    _require(timedelta(0) <= now - generated <= MAX_SNAPSHOT_AGE,
             "health snapshot is stale or future dated")
    version = body.get("version")
    _require(isinstance(version, str) and 0 < len(version) <= 128 and bool(version.split()) and
             version.split(maxsplit=1)[0] == context["expected_version"],
             "health version differs")
    _require(isinstance(body.get("provenance"), list) and bool(body["provenance"]),
             "health provenance is missing")
    return normalized_faults(body.get("faults"), allow_empty=True)


def assess(
    body: bytes | None, signature: bytes | None, *,
    expected_context: dict[str, Any], origin: dict[str, Any],
    public: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    """Assess source evidence only, preserving health bodies and their clocks."""
    _now(now)
    validate_context(expected_context)
    origin_faults = _observe(origin, expected_context, now)
    public_faults = _observe(public, expected_context, now)
    _require(origin_faults == public_faults, "public and origin source faults differ")
    _require(origin["body"]["generated_at"] == public["body"]["generated_at"] and
             canonical(origin["body"]["provenance"]) == canonical(public["body"]["provenance"]),
             "public and origin evidence snapshots differ")
    return _assessment(body, signature, expected_context, origin_faults, now)


def assess_internal(
    body: bytes | None, signature: bytes | None, *,
    expected_context: dict[str, Any], observation: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    """Assess the internal handoff candidate while public still serves parent.

    Only the existing application_handoff operation may use this form. Its
    caller still authenticates the internal transport, candidate activation
    token, signed payload hashes/counts and parent before the existing DB write.
    """
    _now(now)
    validate_context(expected_context)
    _require(expected_context["purpose"] == "application_handoff",
             "internal assessment is only allowed for application handoff")
    faults = _observe(observation, expected_context, now)
    return _assessment(body, signature, expected_context, faults, now)


def _assessment(body, signature, expected_context, origin_faults, now):
    if body is None and signature is None:
        _require(not origin_faults, "source degradation requires owner admission")
        return {
            "source_health_admitted": False, "evidence_status": "healthy",
            "admitted_source_faults": [], "admission_sha256": None,
            "faults_sha256": digest(canonical([])), "context": dict(expected_context),
        }
    _require(body is not None and signature is not None, "admission body and signature are required together")
    value = verify_admission(body, signature, expected_context=expected_context, now=now)
    _require(value["faults"] == origin_faults, "observed faults differ from owner admission")
    return {
        "source_health_admitted": True, "evidence_status": "degraded",
        "admitted_source_faults": origin_faults, "admission_sha256": digest(body),
        "faults_sha256": value["faults_sha256"], "context": dict(expected_context),
        "issued_at": value["issued_at"], "expires_at": value["expires_at"],
    }


def claim_document(assessment: dict[str, Any], *, execution: dict[str, Any]) -> dict[str, Any]:
    """Deterministic claim bytes; caller must enforce atomic durable creation."""
    _require(isinstance(assessment, dict) and assessment.get("source_health_admitted") is True and
             assessment.get("evidence_status") == "degraded" and
             _matches(assessment.get("admission_sha256"), _DIGEST),
             "a claim requires an authenticated degraded assessment")
    context = assessment["context"]
    validate_context(context)
    _time(assessment["expires_at"])
    _require(isinstance(execution, dict), "claim execution is malformed")
    purpose, operation = context["purpose"], context["operation"]
    if purpose == "recovery":
        _require(set(execution) == {"kind", "repository", "run_id", "run_attempt"} and
                 execution["kind"] == "github_actions" and execution["repository"] == operation["repository"] and
                 all(isinstance(execution[key], str) and
                     re.fullmatch(r"[1-9][0-9]{0,19}", execution[key]) is not None
                     for key in ("run_id", "run_attempt")),
                 "claim must bind the actual workflow execution")
    elif purpose == "native_recovery":
        _require(execution == {"kind": "native_recovery",
                               "controller_deployment_id": operation["controller_deployment_id"],
                               "execution_day": operation["execution_day"]},
                 "claim must bind the actual native execution")
    elif purpose.startswith("application_"):
        _require(execution == {"kind": "local_application", "purpose": purpose,
                               "request_id": operation["request_id"],
                               "operator_intent_id": context["intent_id"]},
                 "claim must bind the actual application operation")
    else:
        _require(execution == {"kind": "catalog_publication",
                               "publication_request_id": operation["publication_request_id"]},
                 "claim must bind the actual catalog publication")
    return {
        "schema": CLAIM_SCHEMA, "admission_sha256": assessment["admission_sha256"],
        "context": context, "execution": dict(execution), "expires_at": assessment["expires_at"],
    }


def verify_claim(claim: dict[str, Any], assessment: dict[str, Any], *, execution: dict[str, Any]) -> None:
    """Compare a durable claim to a fresh assessment; never creates a claim."""
    _require(canonical(claim) == canonical(claim_document(assessment, execution=execution)),
             "admission was claimed by a different operation")
