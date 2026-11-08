"""Source binding reads immutable inventories and authenticates exact scope."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "native_publication_source_tested", Path(__file__).resolve().parents[1] / "native_publication_source.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
NOW = datetime(2026, 11, 7, 8, tzinfo=timezone.utc)
IMAGE = "sha256:" + "1" * 64
GRANT = "2" * 64


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


def commit(root):
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    for name, body in {
        "backend/seiche/__init__.py": "raise RuntimeError('candidate code must never execute')\n",
        "backend/settings.json": '{"not_only_python":true}\n',
        "backend/tests/test_retained.py": "raise RuntimeError('test source must never execute')\n",
        "ops/release/controller.py": "# controller v1\n",
        ".github/workflows/publish.yml": "name: fixture\n",
        ".github/release-signers.pub": "fixture public metadata\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    runtime = commit(root)
    (root / "ops/release/controller.py").write_text("# controller v2\n")
    publication = commit(root)
    key = tmp_path / "test-signing-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    monkeypatch.setattr(m, "OWNER_PUBLIC", key.with_suffix(".pub").read_text().strip())
    return root, runtime, publication, key


def args(repo):
    root, runtime, publication, _ = repo
    return dict(root=root, publication_source=publication, runtime_source=runtime,
                runtime_image_digest=IMAGE, runtime_grant_sha256=GRANT)


def prepared(repo):
    return m.prepare(**args(repo), issued_at=NOW, expires_at=NOW + timedelta(hours=4))


def signed(repo, payload, namespace=None):
    root, _, _, key = repo
    path = key.parent / "payload"
    path.write_bytes(m.canonical(payload))
    signature = path.with_suffix(".sig")
    signature.unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n",
                    namespace or m.SIGNATURE_NAMESPACE, str(path)],
                   check=True, capture_output=True)
    return {"schema": m.APPROVAL_SCHEMA, "payload": payload, "signature": signature.read_text()}


def accepted(envelope):
    p = envelope["payload"]
    return {"schema": m.ACCEPTANCE_SCHEMA, "approval_sha256": m.digest(envelope),
            **{key: deepcopy(p[key]) for key in (
                "publication", "runtime", "runtime_image_digest", "runtime_grant_sha256")},
            "accepted_at": (NOW + timedelta(minutes=5)).isoformat()}


def test_complete_inventory_and_no_candidate_execution(repo):
    payload, manifest = prepared(repo)
    assert {x["path"] for x in manifest["publication_backend"]} == {
        "backend/seiche/__init__.py", "backend/settings.json", "backend/tests/test_retained.py"}
    assert {x["path"] for x in manifest["publication_controller"]} == {
        "ops/release/controller.py", ".github/workflows/publish.yml", ".github/release-signers.pub"}
    assert manifest["runtime_backend"] == manifest["publication_backend"]
    assert manifest["differences"] == []
    assert all(set(x) == {"path", "mode", "type", "oid"} for x in manifest["runtime_backend"])
    assert m.verify(signed(repo, payload), **args(repo), now=NOW) == payload


def test_signature_namespace_and_payload_tampering_fail(repo):
    payload, _ = prepared(repo)
    with pytest.raises(m.SourceBindingError, match="signature"):
        m.verify(signed(repo, payload, "wrong-namespace"), **args(repo), now=NOW)
    envelope = signed(repo, payload)
    envelope["payload"]["limits"]["writer_authority"] = True
    with pytest.raises(m.SourceBindingError, match="signature"):
        m.verify(envelope, **args(repo), now=NOW)


def test_owner_signed_omitted_inventory_still_fails(repo):
    payload, _ = prepared(repo)
    payload["manifest"]["publication_backend"].pop()
    payload["manifest_sha256"] = m.digest(payload["manifest"])
    with pytest.raises(m.SourceBindingError, match="inventory"):
        m.verify(signed(repo, payload), **args(repo), now=NOW)


@pytest.mark.parametrize("field,value", [
    ("runtime_image_digest", "sha256:" + "3" * 64),
    ("runtime_grant_sha256", "4" * 64),
])
def test_runtime_identity_change_requires_new_approval(repo, field, value):
    payload, _ = prepared(repo)
    context = args(repo)
    context[field] = value
    with pytest.raises(m.SourceBindingError, match="identity"):
        m.verify(signed(repo, payload), **context, now=NOW)


@pytest.mark.parametrize("offset", [-1, 4 * 3600])
def test_fresh_admission_rejects_future_or_expired_window(repo, offset):
    payload, _ = prepared(repo)
    with pytest.raises(m.SourceBindingError, match="future dated or expired"):
        m.verify(signed(repo, payload), **args(repo), now=NOW + timedelta(seconds=offset))


def test_overlong_window_rejected(repo):
    with pytest.raises(m.SourceBindingError, match="four hours"):
        m.prepare(**args(repo), issued_at=NOW, expires_at=NOW + timedelta(hours=4, seconds=1))


@pytest.mark.parametrize("path,body", [
    ("backend/settings.json", "changed non-Python runtime configuration\n"),
    ("backend/tests/test_retained.py", "changed unreviewed test\n"),
    ("backend/tests/new_fixture.json", "new unreviewed fixture\n"),
    ("backend/seiche/new_module.py", "raise RuntimeError('must not execute')\n"),
])
def test_any_unreviewed_backend_difference_is_rejected(repo, path, body):
    root, runtime, _, key = repo
    (root / path).write_text(body)
    publication = commit(root)
    with pytest.raises(m.SourceBindingError, match="reviewed runtime"):
        prepared((root, runtime, publication, key))


def test_mode_change_is_a_backend_difference(repo):
    root, runtime, _, key = repo
    (root / "backend/settings.json").chmod(0o755)
    publication = commit(root)
    with pytest.raises(m.SourceBindingError):
        prepared((root, runtime, publication, key))


def test_symlink_cannot_escape_full_inventory(repo):
    root, runtime, _, key = repo
    (root / "backend/redirect.py").symlink_to("../ops/release/controller.py")
    publication = commit(root)
    with pytest.raises(m.SourceBindingError, match="symlink"):
        prepared((root, runtime, publication, key))


def test_dirty_controller_or_untracked_file_is_rejected(repo):
    root = repo[0]
    (root / "ops/release/controller.py").write_text("# changed after signed tree\n")
    with pytest.raises(m.SourceBindingError, match="not clean"):
        prepared(repo)
    git(root, "checkout", "--", "ops/release/controller.py")
    (root / "untracked.py").write_text("raise RuntimeError('untracked')\n")
    with pytest.raises(m.SourceBindingError, match="not clean"):
        prepared(repo)


def _sealed(root, name=".railway-publication-ab12_cd"):
    for rel, body in (("index.html", "<html></html>\n"), ("assets/app.js", "console.log(1)\n")):
        path = root / name / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def test_full_sealed_candidate_directory_is_the_only_untracked_exception(repo):
    root = repo[0]
    _sealed(root)
    payload, manifest = prepared(repo)
    assert payload["publication"]["commit"] == repo[2] and manifest["publication_controller"]


def test_sealed_candidate_does_not_excuse_any_other_change(repo):
    root = repo[0]
    _sealed(root)
    (root / "ops/release/controller.py").write_text("# changed after signed tree\n")
    with pytest.raises(m.SourceBindingError, match="not clean"):
        prepared(repo)
    git(root, "checkout", "--", "ops/release/controller.py")
    for stray in ("untracked.py", "backend/seiche/extra.py", "ops/release/extra.txt"):
        (root / stray).write_text("stray\n")
        with pytest.raises(m.SourceBindingError, match="not clean"):
            prepared(repo)
        (root / stray).unlink()
    prepared(repo)


@pytest.mark.parametrize("name", ["railway-publication-ab12", ".railway-publication-", ".railway-publication",
                                  ".railway-publication-ab.cd", "nested/.railway-publication-ab12"])
def test_lookalike_candidate_paths_are_rejected(repo, name):
    root = repo[0]
    _sealed(root, name)
    with pytest.raises(m.SourceBindingError, match="not clean"):
        prepared(repo)


def test_candidate_must_be_a_directory_and_only_one(repo):
    root = repo[0]
    (root / ".railway-publication-ab12").write_text("a file, not the sealed tree\n")
    with pytest.raises(m.SourceBindingError, match="not clean"):
        prepared(repo)
    (root / ".railway-publication-ab12").unlink()
    _sealed(root, ".railway-publication-one")
    _sealed(root, ".railway-publication-two")
    with pytest.raises(m.SourceBindingError, match="not clean"):
        prepared(repo)


def test_durable_acceptance_allows_same_pair_after_window(repo):
    payload, _ = prepared(repo)
    envelope = signed(repo, payload)
    acceptance = accepted(envelope)
    assert m.validate_acceptance(envelope, acceptance, **args(repo),
                                 now=NOW + timedelta(days=2)) == payload
    with pytest.raises(m.SourceBindingError, match="expired"):
        m.verify(envelope, **args(repo), now=NOW + timedelta(days=2))


@pytest.mark.parametrize("mutation", ["approval", "grant", "pair", "late", "future"])
def test_acceptance_cannot_change_binding_or_backfill_invalid_time(repo, mutation):
    payload, _ = prepared(repo)
    envelope = signed(repo, payload)
    acceptance = accepted(envelope)
    if mutation == "approval":
        acceptance["approval_sha256"] = "f" * 64
    elif mutation == "grant":
        acceptance["runtime_grant_sha256"] = "f" * 64
    elif mutation == "pair":
        acceptance["publication"]["commit"] = "f" * 40
    elif mutation == "late":
        acceptance["accepted_at"] = (NOW + timedelta(hours=4)).isoformat()
    else:
        acceptance["accepted_at"] = (NOW + timedelta(minutes=20)).isoformat()
    with pytest.raises(m.SourceBindingError):
        m.validate_acceptance(envelope, acceptance, **args(repo), now=NOW + timedelta(minutes=10))


def test_exact_difference_policy_cannot_be_widened_by_one_byte(repo, monkeypatch):
    root, runtime, _, key = repo
    paths = sorted(m.REVIEWABLE_DIFFERENCES)
    for path in paths:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("explicit fixture pair\n")
    publication = commit(root)
    entries = m._inventory(root, publication, ("backend",))
    policy = {path: (None, next(x["oid"] for x in entries if x["path"] == path)) for path in paths}
    monkeypatch.setattr(m, "RUNTIME_DIFFERENCE_SOURCE", runtime)
    monkeypatch.setattr(m, "RUNTIME_DIFFERENCE_TREE", git(root, "rev-parse", runtime + "^{tree}"))
    monkeypatch.setattr(m, "REVIEWABLE_DIFFERENCES", policy)
    qualified = (root, runtime, publication, key)
    payload, manifest = prepared(qualified)
    assert len(manifest["differences"]) == 5
    assert m.verify(signed(qualified, payload), **args(qualified), now=NOW) == payload
    (root / paths[0]).write_text("one byte outside the explicit pair\n")
    publication = commit(root)
    with pytest.raises(m.SourceBindingError, match="blob or mode"):
        prepared((root, runtime, publication, key))
