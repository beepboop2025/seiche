"""Recovery-only evidence cannot be mistaken for healthy release acceptance."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow():
    return yaml.safe_load((ROOT / ".github/workflows/railway-stateful-recovery.yml").read_text())


def _admit():
    steps = _workflow()["jobs"]["monitor"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "proof")
    start = script.index("def recovery_fault_admission(")
    end = script.index("\nnow = datetime.now(UTC)", start)
    namespace = {"json": json, "re": re}
    exec(compile(ast.parse(script[start:end]), "<signed workflow admission>", "exec"), namespace)
    return namespace["recovery_fault_admission"]


FAULTS = [{"category": "WORKER_HEALTH", "source": "official-market-collector",
           "status": "OVERDUE", "heartbeat_at": "2026-10-05T17:16:24+00:00",
           "expected_by": "2026-10-05T17:18:24+00:00"}]


def _options():
    raw = (json.dumps(FAULTS, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return {"expected_digest": hashlib.sha256(raw).hexdigest(), "repair_source": "a" * 40,
            "workflow_source": "a" * 40, "runtime_source": "b" * 40,
            "operation": "export-recovery", "event": "workflow_dispatch"}


def test_normal_monitor_still_requires_no_faults():
    options = {**_options(), "expected_digest": "", "repair_source": ""}
    assert _admit()([], **options) is False
    for faults in (FAULTS, None, {}, "", [{}]):
        with pytest.raises(ValueError):
            _admit()(faults, **options)


@pytest.mark.parametrize("operation", ["export-recovery", "monitor"])
def test_recovery_only_requires_exact_observed_fault(operation):
    assert _admit()(FAULTS, **{**_options(), "operation": operation}) is True
    with pytest.raises(ValueError, match="changed"):
        _admit()([{**FAULTS[0], "expected_by": "2026-10-05T17:18:25+00:00"}], **_options())


@pytest.mark.parametrize("change", [
    {"expected_digest": ""}, {"expected_digest": "f" * 64}, {"repair_source": ""},
    {"repair_source": "c" * 40}, {"runtime_source": "a" * 40},
    {"operation": "attest-native-recovery"}, {"operation": "resume-offsite"},
    {"event": "schedule"}, {"event": "pull_request"},
])
def test_recovery_only_rejects_wrong_source_operation_or_fault_binding(change):
    with pytest.raises(ValueError):
        _admit()(FAULTS, **{**_options(), **change})


@pytest.mark.parametrize("faults", [
    [], None, {}, [None], FAULTS * 2,
    [{**FAULTS[0], "category": "SOURCE_REVIEW_HOLD"}],
    [{**FAULTS[0], "source": "legacy-source-worker"}],
    [{**FAULTS[0], "status": "UNAVAILABLE"}],
])
def test_recovery_only_rejects_other_faults_even_with_matching_digest(faults):
    digest = hashlib.sha256((json.dumps(faults, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
    with pytest.raises(ValueError):
        _admit()(faults, **{**_options(), "expected_digest": digest})


def test_degraded_evidence_is_distinct_from_strict_monitor_acceptance():
    steps = _workflow()["jobs"]["monitor"]["steps"]
    proof = next(step["run"] for step in steps if step.get("name") == "Record the accepted monitor identity")
    assert 'proof["status"] = "recovery_only_degraded"' in proof
    assert 'proof["degraded_health_sha256"]' in proof
    upload = next(step for step in steps if step.get("name") == "Retain the accepted monitor identity")
    assert "recovery-only-degraded.json" in upload["with"]["path"]


@pytest.mark.parametrize("degraded", [False, True])
@pytest.mark.parametrize("bootstrap", [False, True])
def test_recorded_proof_preserves_recovery_only_status(tmp_path, degraded, bootstrap):
    steps = _workflow()["jobs"]["monitor"]["steps"]
    script = next(step["run"] for step in steps
                  if step.get("name") == "Record the accepted monitor identity")
    program = script.split("<<'PYMONITOR'\n", 1)[1].split("\nPYMONITOR", 1)[0]
    root = tmp_path / "railway-recovery-monitor"
    root.mkdir()
    pair = {"paired_request_id": "c" * 64, "recovery_receipt_sha256": "d" * 64,
            "offsite_receipt_sha256": "e" * 64}
    (root / "monitor-pair-identity.json").write_text(json.dumps(pair))
    if degraded:
        raw = (json.dumps({"repair_source": "a" * 40, "observed_faults": FAULTS},
                          sort_keys=True, separators=(",", ":")) + "\n").encode()
        (root / "recovery-only-degraded.json").write_bytes(raw)
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path), "BOOTSTRAP": str(bootstrap).lower(),
           "GITHUB_REPOSITORY": "beepboop2025/seiche", "GITHUB_SHA": "a" * 40,
           "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
           "MONITORED_RELEASE": "b" * 40, "MONITORED_DEPLOYMENT": "fixture"}
    subprocess.run([sys.executable, "-I", "-S", "-c", program], env=env, cwd=ROOT,
                   capture_output=True, check=True, timeout=30)
    proof = json.loads((root / "monitor-proof.json").read_bytes())
    assert proof["bootstrap"] is bootstrap
    assert proof["status"] == ("recovery_only_degraded" if degraded else "pass")
    assert {key: proof[key] for key in pair} == (
        dict.fromkeys(pair) if bootstrap else pair)
    if degraded:
        assert proof["degraded_health_sha256"] == hashlib.sha256(raw).hexdigest()
        assert proof["repair_source_sha"] == "a" * 40
    else:
        assert "degraded_health_sha256" not in proof
        assert "repair_source_sha" not in proof
