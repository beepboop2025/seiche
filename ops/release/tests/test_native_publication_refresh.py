"""Post-build observations cannot replace authority, recovery, or request identity."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "native_refresh_publisher", ROOT / "ops/railway-automation/full-publisher/publish.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def package():
    return {"schema": "seiche.native-migration-catalog-source-health.v1",
            "intent_id": "a" * 64, "publication_request_id": "b" * 64,
            "body_b64": None, "signature_b64": None,
            "health_pair": {k: {"body": {"faults": [], "generation": 1}}
                            for k in ("origin", "public")},
            "runtime_evidence": {
                "schema": "seiche.native-migration-publication-evidence.v1",
                **{k: {"identity": k} for k in (
                    "grant", "acceptance", "transfer", "final_restore", "release",
                    "boot", "restart", "backup", "backup_readback",
                    "source_equivalence", "source_acceptance")},
                "monitor": {"observed_at": "2026-11-07T09:00:00+00:00"}}}


def raw(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


@pytest.fixture
def files():
    # A private directory under the checked-out workspace avoids a writable /tmp
    # ancestor; Linux production runs as root under a similarly protected /srv.
    with tempfile.TemporaryDirectory(prefix=".native-refresh-test-", dir=ROOT) as temporary:
        parent = Path(temporary)
        yield parent / "current.json", parent / "accepted.json"
        parent.chmod(0o700)


def invoke(files, monkeypatch, original, refreshed, *, failure=None):
    current, destination = files
    current.write_bytes(raw(refreshed)); current.chmod(0o600)
    calls = []
    def gate(trusted, source, signer, selected, *, catalog):
        calls.append((trusted, source, signer, selected, catalog))
        assert selected.read_bytes() == raw(refreshed)
        if failure:
            raise RuntimeError(failure)
    monkeypatch.setattr(publisher, "hetzner_gate", gate)
    accepted = publisher.refresh_native_observation(
        str(current), raw(original), destination,
        trusted=Path("/trusted"), source="c" * 40, signer="owner", catalog=Path("/sealed/catalog.json"))
    return accepted, calls


def test_fresh_observation_is_fully_validated_then_pinned(files, monkeypatch):
    original = package(); refreshed = deepcopy(original)
    refreshed["runtime_evidence"]["monitor"]["observed_at"] = "2026-11-07T09:05:00+00:00"
    for value in refreshed["health_pair"].values():
        value["body"]["generation"] = 2
    accepted, calls = invoke(files, monkeypatch, original, refreshed)
    assert accepted == raw(refreshed)
    assert calls == [(Path("/trusted"), "c" * 40, "owner", files[1], Path("/sealed/catalog.json"))]
    assert files[1].stat().st_mode & 0o777 == 0o600
    # An observer's later replacement cannot mutate the pinned package/claim.
    files[0].write_bytes(raw(package()))
    assert files[1].read_bytes() == accepted


@pytest.mark.parametrize("key", ["intent_id", "publication_request_id"])
def test_request_cannot_change(files, monkeypatch, key):
    original = package(); refreshed = deepcopy(original); refreshed[key] = "d" * 64
    with pytest.raises(RuntimeError, match="immutable publication bindings"):
        invoke(files, monkeypatch, original, refreshed)
    assert not files[1].exists()


@pytest.mark.parametrize("key", ["grant", "acceptance", "transfer", "final_restore", "release",
                                     "boot", "restart", "backup", "backup_readback",
                                     "source_equivalence", "source_acceptance"])
def test_immutable_evidence_cannot_change(files, monkeypatch, key):
    original = package(); refreshed = deepcopy(original)
    refreshed["runtime_evidence"][key] = {"identity": "different"}
    with pytest.raises(RuntimeError, match="immutable publication bindings"):
        invoke(files, monkeypatch, original, refreshed)
    assert not files[1].exists()


def test_old_mode_cannot_enter_refresh(files, monkeypatch):
    original = package(); original["schema"] = "seiche.hetzner-catalog-source-health.v1"
    with pytest.raises(RuntimeError, match="explicit native publication"):
        invoke(files, monkeypatch, original, original)


def test_faults_cannot_enter_refresh(files, monkeypatch):
    original = package(); refreshed = deepcopy(original)
    refreshed["health_pair"]["public"]["body"]["faults"] = ["broken"]
    with pytest.raises(RuntimeError, match="requires healthy Hetzner evidence"):
        invoke(files, monkeypatch, original, refreshed)


def test_full_validator_failure_cannot_become_acceptance(files, monkeypatch):
    with pytest.raises(RuntimeError, match="stale actual observation"):
        invoke(files, monkeypatch, package(), package(), failure="stale actual observation")


def test_writable_observer_ancestry_is_rejected(files, monkeypatch):
    files[0].parent.chmod(0o777)
    with pytest.raises(RuntimeError, match="ancestry is not owner controlled"):
        invoke(files, monkeypatch, package(), package())
    assert not files[1].exists()


def test_existing_pinned_file_cannot_be_overwritten(files, monkeypatch):
    files[1].write_bytes(b"prior claim"); files[1].chmod(0o600)
    with pytest.raises(FileExistsError):
        invoke(files, monkeypatch, package(), package())
    assert files[1].read_bytes() == b"prior claim"
