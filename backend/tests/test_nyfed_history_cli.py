from __future__ import annotations

import json
import sys

import pytest

from seiche import cli, repository, store
import test_nyfed_history as fixture_module

archive = fixture_module.archive


def _argv(root, pin, state):
    return [
        "seiche",
        "nyfed-history-import",
        "--archive-root",
        str(root),
        "--inventory-sha256",
        pin,
        "--state-path",
        str(state),
        "--dry-run",
    ]


def test_cli_dry_run_parses_and_validates_without_repository_or_state(
    archive, tmp_path, monkeypatch, capsys
):
    root, pin = archive
    state = tmp_path / "durable/state.json"

    def forbidden():
        raise AssertionError("dry run must not initialize any repository")

    monkeypatch.setattr(repository, "get_repository", forbidden)
    monkeypatch.setattr(sys, "argv", _argv(root, pin, state))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "PASS" and receipt["dry_run"] is True
    assert (
        receipt["accepted_observations"] == 34
        and receipt["canonical_jsonl_used"] is False
    )
    assert not state.parent.exists() and not store.DB_PATH.exists()


def test_cli_pin_mismatch_fails_before_repository_or_state(
    archive, tmp_path, monkeypatch
):
    root, _ = archive
    state = tmp_path / "durable/state.json"

    def forbidden():
        raise AssertionError("invalid dry run must not initialize any repository")

    monkeypatch.setattr(repository, "get_repository", forbidden)
    monkeypatch.setattr(sys, "argv", _argv(root, "0" * 64, state))
    with pytest.raises(ValueError, match="inventory SHA256 mismatch"):
        cli.main()
    assert not state.parent.exists() and not store.DB_PATH.exists()


def test_cli_requires_pin_and_exposes_no_knowledge_clock_override(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seiche",
            "nyfed-history-import",
            "--archive-root",
            str(tmp_path),
            "--state-path",
            str(tmp_path / "state.json"),
            "--dry-run",
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
    monkeypatch.setattr(
        sys,
        "argv",
        _argv(tmp_path, "0" * 64, tmp_path / "state.json")
        + ["--knowledge-time", "2001-01-01T00:00:00Z"],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
