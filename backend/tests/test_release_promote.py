"""Tests for the fixed, non-interactive snapshot activation entry point."""

from __future__ import annotations

import json

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


def test_request_path_is_fixed_outside_the_mutable_checkout():
    assert release_promote.REQUEST_DIRECTORY.as_posix() == "/run/seiche-release"
    assert (
        release_promote.REQUEST_PATH.as_posix()
        == "/run/seiche-release/promotion-request.json"
    )
