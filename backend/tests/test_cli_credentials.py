"""Operator CLI secrets use an explicit mode-0600 handoff, never stdout."""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest
from seiche import cli


def test_provision_writes_one_time_credentials_without_logging_them(
    tmp_path, monkeypatch, capsys
):
    from seiche import provisioning

    secret_password = "correct-horse-private-value"
    secret_token = "private-bearer-token"
    monkeypatch.setattr(
        provisioning,
        "provision",
        lambda *_args, **_kwargs: {
            "username": "desk_01",
            "tier": "pro",
            "password": secret_password,
            "token": secret_token,
            "token_expires_utc": "2026-09-21T00:00:00+00:00",
        },
    )
    destination = tmp_path / "buyer.json"
    args = SimpleNamespace(
        tier="pro",
        email="buyer@example.com",
        username="desk_01",
        ref="invoice_1",
        credentials_file=str(destination),
    )

    assert cli.cmd_provision(args) == 0
    captured = capsys.readouterr()
    assert secret_password not in captured.out + captured.err
    assert secret_token not in captured.out + captured.err
    assert json.loads(destination.read_text()) == {
        "password": secret_password,
        "tier": "pro",
        "token": secret_token,
        "token_expires_utc": "2026-09-21T00:00:00+00:00",
        "username": "desk_01",
    }
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_existing_credentials_path_blocks_before_provisioning(
    tmp_path, monkeypatch, capsys
):
    from seiche import provisioning

    destination = tmp_path / "existing.json"
    destination.write_text("operator-owned")
    called = False

    def must_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provisioning ran before the handoff was reserved")

    monkeypatch.setattr(provisioning, "provision", must_not_run)
    args = SimpleNamespace(
        tier="pro",
        email="",
        username="desk_01",
        ref="invoice_2",
        credentials_file=str(destination),
    )

    assert cli.cmd_provision(args) == 1
    assert called is False
    assert destination.read_text() == "operator-owned"
    assert "operator-owned" not in capsys.readouterr().err


def test_generated_user_password_requires_and_uses_secure_handoff(
    tmp_path, monkeypatch, capsys
):
    from seiche import accounts

    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    missing = SimpleNamespace(
        action="add",
        username="desk_02",
        tier="pro",
        password="",
        credentials_file="",
    )
    assert cli.cmd_user(missing) == 2
    assert accounts.list_users() == []
    capsys.readouterr()

    destination = tmp_path / "desk_02.json"
    secure = SimpleNamespace(**{**vars(missing), "credentials_file": str(destination)})
    assert cli.cmd_user(secure) == 0
    handoff = json.loads(destination.read_text())
    captured = capsys.readouterr()
    assert handoff["password"] not in captured.out + captured.err
    assert accounts.verify_user("desk_02", handoff["password"])["tier"] == "pro"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_idempotent_provision_removes_unused_reserved_file(
    tmp_path, monkeypatch, capsys
):
    from seiche import provisioning

    monkeypatch.setattr(
        provisioning,
        "provision",
        lambda *_args, **_kwargs: {
            "already": True,
            "username": "desk_03",
            "tier": "pro",
            "password": None,
            "token": None,
        },
    )
    destination = tmp_path / "unused.json"
    args = SimpleNamespace(
        tier="pro",
        email="",
        username="desk_03",
        ref="invoice_replay",
        credentials_file=str(destination),
    )
    assert cli.cmd_provision(args) == 0
    assert not destination.exists()
    assert "already provisioned" in capsys.readouterr().out


def test_unexpected_provisioning_failure_removes_reserved_file(tmp_path, monkeypatch):
    from seiche import provisioning

    def fail_after_reservation(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(provisioning, "provision", fail_after_reservation)
    destination = tmp_path / "unused-after-failure.json"
    args = SimpleNamespace(
        tier="pro",
        email="",
        username="desk_04",
        ref="invoice_failed",
        credentials_file=str(destination),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        cli.cmd_provision(args)
    assert not destination.exists()
