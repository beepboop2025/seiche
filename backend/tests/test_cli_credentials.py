"""Operator CLI secrets use an explicit mode-0600 handoff, never stdout."""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

from seiche import cli


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
    # Legacy "pro" from an old script is stored as the free access level.
    assert accounts.verify_user("desk_02", handoff["password"])["tier"] == "free"
    assert handoff["tier"] == "free"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
