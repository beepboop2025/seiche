from __future__ import annotations

import json
from types import SimpleNamespace

from seiche import cli, nbs_intake


def test_claim_emits_exact_signing_bytes_without_newline(monkeypatch, capsys) -> None:
    claim = {
        "schema": nbs_intake.NBS_SIGNATURE_SCHEMA,
        "algorithm": "ed25519",
        "domain": nbs_intake.NBS_SIGNATURE_DOMAIN,
        "export_id": "nbs-2026-07-r1",
        "signer_key_id": "a" * 64,
        "signed_at": "2026-08-22T10:05:00Z",
        "manifest_sha256": "b" * 64,
        "public_projection_sha256": "d" * 64,
    }
    monkeypatch.setattr(
        nbs_intake,
        "build_signature_claim_from_manifest_file",
        lambda *_args, **_kwargs: claim,
    )
    args = SimpleNamespace(
        nbs_action="claim",
        manifest="manifest.json",
        signed_at="2026-08-22T10:05:00Z",
        signer_key_id="a" * 64,
    )

    assert cli.cmd_nbs_intake(args) == 0

    assert capsys.readouterr().out.encode() == nbs_intake.encode_signature_claim(claim)


def test_status_is_public_only_and_not_onboarded_is_pending(
    monkeypatch, capsys
) -> None:
    calls: list[tuple[str, str | None]] = []

    def load(public_dir: str, *, attest_dir: str | None = None):
        calls.append((public_dir, attest_dir))
        raise nbs_intake.NBSNotOnboardedError("not onboarded")

    monkeypatch.setattr(nbs_intake, "load_public_context_strict_from_public_dir", load)
    args = SimpleNamespace(
        nbs_action="status",
        public_dir="/srv/seiche/nbs/public",
        attest_dir="/etc/seiche/attest",
    )

    assert cli.cmd_nbs_intake(args) == 2

    assert calls == [("/srv/seiche/nbs/public", "/etc/seiche/attest")]
    assert json.loads(capsys.readouterr().out)["available"] is False


def test_status_fails_closed_on_public_store_corruption(monkeypatch, capsys) -> None:
    def load(*_args, **_kwargs):
        raise nbs_intake.NBSIntegrityError("public head is malformed")

    monkeypatch.setattr(nbs_intake, "load_public_context_strict_from_public_dir", load)
    args = SimpleNamespace(
        nbs_action="status",
        public_dir="/srv/seiche/nbs/public",
        attest_dir="/etc/seiche/attest",
    )

    assert cli.cmd_nbs_intake(args) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["status"] == "rejected"
    assert error["error"]["type"] == "NBSIntegrityError"


def test_ingest_prints_only_the_public_projection(monkeypatch, capsys) -> None:
    public = {
        "schema": nbs_intake.NBS_PUBLIC_SCHEMA,
        "available": True,
        "values_published": False,
        "revision_id": "nbs-2026-07-r1",
    }
    calls: list[tuple[object, ...]] = []

    def ingest(manifest, signature, raw, *, root, attest_dir=None):
        calls.append((manifest, signature, raw, root, attest_dir))
        return SimpleNamespace(to_dict=lambda: public)

    monkeypatch.setattr(nbs_intake, "ingest_signed_export", ingest)
    args = SimpleNamespace(
        nbs_action="ingest",
        manifest="manifest.json",
        signature="signature.json",
        raw="owner-export.csv",
        root="/var/lib/seiche-nbs",
        attest_dir="/etc/seiche/attest",
    )

    assert cli.cmd_nbs_intake(args) == 0

    assert calls == [
        (
            "manifest.json",
            "signature.json",
            "owner-export.csv",
            "/var/lib/seiche-nbs",
            "/etc/seiche/attest",
        )
    ]
    assert json.loads(capsys.readouterr().out) == public


def test_integrity_rejection_is_structured_and_nonzero(monkeypatch, capsys) -> None:
    def reject(*_args, **_kwargs):
        raise nbs_intake.NBSIntegrityError("manifest is not canonical")

    monkeypatch.setattr(
        nbs_intake,
        "build_signature_claim_from_manifest_file",
        reject,
    )
    args = SimpleNamespace(
        nbs_action="claim",
        manifest="manifest.json",
        signed_at="2026-08-22T10:05:00Z",
        signer_key_id="a" * 64,
    )

    assert cli.cmd_nbs_intake(args) == 1

    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "rejected"
    assert error["error"] == {
        "type": "NBSIntegrityError",
        "message": "manifest is not canonical",
    }
