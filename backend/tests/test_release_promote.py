"""Tests for the fixed, non-interactive snapshot activation entry point."""

from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from seiche import assemble, release_promote


SHA = "a" * 40
TOKEN = "b" * 64


def _request(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "expected_sha": SHA,
        "activation_token": TOKEN,
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":")).encode()


def test_main_activates_only_the_exact_environment_bound_request(
    monkeypatch,
    capsys,
):
    calls = []
    monkeypatch.setattr(release_promote, "_request_bytes", _request)
    monkeypatch.setenv("SEICHE_RELEASE_SHA", SHA)
    monkeypatch.setattr(
        assemble,
        "activate_pending_snapshot",
        lambda expected_sha, activation_token: calls.append(
            (expected_sha, activation_token)
        )
        or True,
    )

    assert release_promote.main() == 0
    assert calls == [(SHA, TOKEN)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("raw", "environment_sha"),
    [
        (_request(expected_sha="A" * 40), SHA),
        (_request(activation_token="B" * 64), SHA),
        (_request(unexpected="value"), SHA),
        (b'{"expected_sha":"' + SHA.encode() + b'"}', SHA),
        (_request(expected_sha=1), SHA),
        (b"[]", SHA),
        (b'"not-an-object"', SHA),
        (_request(), "c" * 40),
        (_request(), "A" * 40),
        (
            b'{"expected_sha":"'
            + SHA.encode()
            + b'","expected_sha":"'
            + SHA.encode()
            + b'","activation_token":"'
            + TOKEN.encode()
            + b'"}',
            SHA,
        ),
    ],
)
def test_main_rejects_non_exact_requests_without_output_or_activation(
    monkeypatch,
    capsys,
    raw,
    environment_sha,
):
    calls = []
    monkeypatch.setattr(release_promote, "_request_bytes", lambda: raw)
    monkeypatch.setenv("SEICHE_RELEASE_SHA", environment_sha)
    monkeypatch.setattr(
        assemble,
        "activate_pending_snapshot",
        lambda *arguments: calls.append(arguments) or True,
    )

    assert release_promote.main() == 1
    assert calls == []
    assert capsys.readouterr() == ("", "")


def test_main_propagates_activation_failure_only_as_an_exit_status(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(release_promote, "_request_bytes", _request)
    monkeypatch.setenv("SEICHE_RELEASE_SHA", SHA)
    monkeypatch.setattr(assemble, "activate_pending_snapshot", lambda *_: False)

    assert release_promote.main() == 1
    assert capsys.readouterr() == ("", "")


def test_main_rejects_a_request_without_a_process_release_identity(
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(release_promote, "_request_bytes", _request)
    monkeypatch.delenv("SEICHE_RELEASE_SHA", raising=False)
    monkeypatch.setattr(assemble, "activate_pending_snapshot", lambda *_: True)

    assert release_promote.main() == 1
    assert "rejected the snapshot promotion request" in caplog.text


def _point_request_at(monkeypatch, directory, request) -> None:
    monkeypatch.setattr(release_promote, "REQUEST_DIRECTORY", directory)
    monkeypatch.setattr(release_promote, "REQUEST_PATH", request)


def _metadata(mode: int, *, uid: int = 0) -> SimpleNamespace:
    return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=os.getegid())


def test_request_bytes_rejects_an_unsafe_directory(monkeypatch, tmp_path):
    request = tmp_path / "promotion-request.json"
    _point_request_at(monkeypatch, tmp_path, request)
    monkeypatch.setattr(
        release_promote.os,
        "stat",
        lambda *_args, **_kwargs: _metadata(stat.S_IFDIR | 0o777),
    )

    with pytest.raises(ValueError, match="unsafe promotion request directory"):
        release_promote._request_bytes()


@pytest.mark.parametrize(
    ("mode", "uid"),
    [
        (stat.S_IFREG | 0o600, 0),
        (stat.S_IFREG | 0o640, 1),
    ],
)
def test_request_bytes_rejects_unsafe_file_metadata(
    monkeypatch, tmp_path, mode, uid):
    request = tmp_path / "promotion-request.json"
    request.write_bytes(_request())
    _point_request_at(monkeypatch, tmp_path, request)
    monkeypatch.setattr(
        release_promote.os,
        "stat",
        lambda *_args, **_kwargs: _metadata(stat.S_IFDIR | 0o750),
    )
    monkeypatch.setattr(
        release_promote.os,
        "fstat",
        lambda *_args, **_kwargs: _metadata(mode, uid=uid),
    )

    with pytest.raises(ValueError, match="unsafe promotion request file"):
        release_promote._request_bytes()


def test_request_bytes_rejects_a_symlink(monkeypatch, tmp_path):
    target = tmp_path / "target.json"
    target.write_bytes(_request())
    request = tmp_path / "promotion-request.json"
    request.symlink_to(target)
    _point_request_at(monkeypatch, tmp_path, request)
    monkeypatch.setattr(
        release_promote.os,
        "stat",
        lambda *_args, **_kwargs: _metadata(stat.S_IFDIR | 0o750),
    )

    with pytest.raises(OSError):
        release_promote._request_bytes()


def test_request_bytes_rejects_an_oversized_request(monkeypatch, tmp_path):
    request = tmp_path / "promotion-request.json"
    request.write_bytes(b"x" * (release_promote.MAX_REQUEST_BYTES + 1))
    _point_request_at(monkeypatch, tmp_path, request)
    monkeypatch.setattr(
        release_promote.os,
        "stat",
        lambda *_args, **_kwargs: _metadata(stat.S_IFDIR | 0o750),
    )
    monkeypatch.setattr(
        release_promote.os,
        "fstat",
        lambda *_args, **_kwargs: _metadata(stat.S_IFREG | 0o640),
    )

    with pytest.raises(ValueError, match="promotion request is too large"):
        release_promote._request_bytes()


def test_request_path_is_fixed_outside_the_mutable_checkout():
    assert release_promote.REQUEST_DIRECTORY.as_posix() == "/run/seiche-release"
    assert (
        release_promote.REQUEST_PATH.as_posix()
        == "/run/seiche-release/promotion-request.json"
    )
