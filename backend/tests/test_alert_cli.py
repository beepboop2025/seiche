from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from seiche import cli


NOW = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
VALID_SNAPSHOT = {
    "generated_at": "2026-08-09T16:58:51+00:00",
    "engines": {
        "composite": {
            "ok": True,
            "value": 45.0,
            "regime": "EROSION",
            "coverage_pct": 100.0,
            "decomposition": [{"component": "tails", "status": "live"}],
        }
    },
    "deep": {},
    "headline": {},
}


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8787/api/overview",
        "http://127.1.2.3/api/overview",
        "http://localhost:8787/api/overview",
        "http://[::1]:8787/api/overview",
    ],
)
def test_alert_api_url_accepts_only_exact_loopback_endpoint(url: str) -> None:
    assert cli._localhost_alert_api_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8787/api/overview",
        "http://api.seiche.info/api/overview",
        "http://192.0.2.10/api/overview",
        "http://localhost.evil.invalid/api/overview",
        "http://user:pass@127.0.0.1:8787/api/overview",
        "http://127.0.0.1:8787/api/gauge",
        "http://127.0.0.1:8787/api/overview/",
        "http://127.0.0.1:8787/api/overview?force=true",
        "http://127.0.0.1:8787/api/overview#fragment",
        " http://127.0.0.1:8787/api/overview",
        "http://127.0.0.1:8787/api/over\tview",
        "http://127.0.0.1:8787/api/over\nview",
        "http://127.0.0.1:99999/api/overview",
    ],
)
def test_alert_api_url_rejects_nonlocal_or_ambiguous_targets(url: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli._localhost_alert_api_url(url)


def test_load_alert_snapshot_reads_one_bounded_json_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=VALID_SNAPSHOT)

    snapshot = cli._load_alert_snapshot(
        "http://127.0.0.1:8787/api/overview",
        max_age_seconds=3600,
        now=NOW,
        transport=httpx.MockTransport(handler),
    )

    assert snapshot == VALID_SNAPSHOT
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].headers["accept"] == "application/json"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(302, headers={"location": "https://example.com"}), "HTTP 302"),
        (httpx.Response(200, text="{}"), "not application/json"),
        (
            httpx.Response(
                200,
                content=b"not-json",
                headers={"content-type": "application/json"},
            ),
            "not valid JSON",
        ),
        (
            httpx.Response(
                200,
                content=b"{}",
                headers={"content-type": "application/json", "content-length": "invalid"},
            ),
            "Content-Length is invalid",
        ),
        (
            httpx.Response(
                200,
                content=b"{}",
                headers={"content-type": "application/json", "content-length": "-1"},
            ),
            "Content-Length is invalid",
        ),
    ],
)
def test_load_alert_snapshot_fails_closed_on_bad_http_contract(
    response: httpx.Response,
    message: str,
) -> None:
    transport = httpx.MockTransport(lambda _request: response)

    with pytest.raises(cli.AlertSnapshotError, match=message):
        cli._load_alert_snapshot(
            "http://127.0.0.1:8787/api/overview",
            max_age_seconds=3600,
            now=NOW,
            transport=transport,
        )


def test_load_alert_snapshot_translates_timeout_without_retrying() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("upstream detail", request=request)

    with pytest.raises(cli.AlertSnapshotError, match=r"request failed \(ReadTimeout\)"):
        cli._load_alert_snapshot(
            "http://127.0.0.1:8787/api/overview",
            max_age_seconds=3600,
            now=NOW,
            transport=httpx.MockTransport(handler),
        )
    assert calls == 1


def test_load_alert_snapshot_translates_invalid_url() -> None:
    with pytest.raises(cli.AlertSnapshotError, match=r"request failed \(InvalidURL\)"):
        cli._load_alert_snapshot(
            "http://127.0.0.1:8787/api/over\x00view",
            max_age_seconds=3600,
            now=NOW,
        )


def test_load_alert_snapshot_enforces_decoded_size_limit(monkeypatch) -> None:
    class ChunkedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"generated_at"'
            yield b':"far too large"}'

    monkeypatch.setattr(cli, "ALERT_API_MAX_BYTES", 16)
    response = httpx.Response(
        200,
        stream=ChunkedBody(),
        headers={"content-type": "application/json"},
    )

    with pytest.raises(cli.AlertSnapshotError, match="exceeds the size limit"):
        cli._load_alert_snapshot(
            "http://127.0.0.1:8787/api/overview",
            max_age_seconds=3600,
            now=NOW,
            transport=httpx.MockTransport(lambda _request: response),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "not a JSON object"),
        ({"engines": {"composite": {}}}, "no generated_at"),
        ({"generated_at": "not-a-date", "engines": {"composite": {}}}, "not ISO-8601"),
        (
            {"generated_at": "2026-08-09T16:58:51", "engines": {"composite": {}}},
            "has no UTC offset",
        ),
        (
            {"generated_at": "2026-08-09T15:00:00Z", "engines": {"composite": {}}},
            "snapshot is stale",
        ),
        (
            {"generated_at": "2026-08-09T17:06:00Z", "engines": {"composite": {}}},
            "too far in the future",
        ),
        (
            {
                "generated_at": "9999-12-31T23:59:59-23:59",
                "engines": {"composite": {}},
            },
            "outside the supported range",
        ),
        ({"generated_at": "2026-08-09T16:58:51Z", "engines": {}}, "no populated engines"),
        (
            {"generated_at": "2026-08-09T16:58:51Z", "engines": {"weather": {}}},
            "no composite engine",
        ),
        (
            {
                "generated_at": "2026-08-09T16:58:51Z",
                "engines": {"composite": {"ok": False}},
            },
            "not usable",
        ),
    ],
)
def test_validate_alert_snapshot_rejects_untrustworthy_payloads(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(cli.AlertSnapshotError, match=message):
        cli._validate_alert_snapshot(payload, max_age_seconds=3600, now=NOW)


def test_validate_alert_snapshot_allows_small_clock_skew() -> None:
    snapshot = {**VALID_SNAPSHOT, "generated_at": (NOW + timedelta(minutes=4)).isoformat()}
    assert cli._validate_alert_snapshot(snapshot, max_age_seconds=3600, now=NOW) is snapshot


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"ok": False}, "not usable"),
        ({"regime": "UNKNOWN"}, "no known regime"),
        ({"value": float("nan")}, "invalid value"),
        ({"value": True}, "invalid value"),
        ({"value": 10**1000}, "invalid value"),
        ({"coverage_pct": 101.0}, "invalid coverage_pct"),
        ({"decomposition": []}, "invalid decomposition"),
        ({"decomposition": ["not-an-object"]}, "invalid decomposition"),
        ({"decomposition": [{}]}, "invalid decomposition"),
        ({"decomposition": [{"status": "DEAD"}]}, "invalid decomposition"),
        (
            {"decomposition": [{"component": "tails", "status": "unknown"}]},
            "invalid decomposition",
        ),
    ],
)
def test_validate_alert_snapshot_rejects_unusable_composite(
    patch: dict,
    message: str,
) -> None:
    snapshot = {
        **VALID_SNAPSHOT,
        "engines": {"composite": {**VALID_SNAPSHOT["engines"]["composite"], **patch}},
    }
    with pytest.raises(cli.AlertSnapshotError, match=message):
        cli._validate_alert_snapshot(snapshot, max_age_seconds=3600, now=NOW)


@pytest.mark.parametrize("field", ["deep", "headline"])
def test_validate_alert_snapshot_requires_alert_input_objects(field: str) -> None:
    snapshot = {**VALID_SNAPSHOT, field: None}
    with pytest.raises(cli.AlertSnapshotError, match=field):
        cli._validate_alert_snapshot(snapshot, max_age_seconds=3600, now=NOW)


def test_cmd_alert_api_mode_preserves_alert_exit_code_without_building(
    monkeypatch,
    capsys,
) -> None:
    from seiche import alerts, assemble

    monkeypatch.setattr(cli, "_load_alert_snapshot", lambda *_args, **_kwargs: VALID_SNAPSHOT)
    monkeypatch.setattr(
        assemble,
        "snapshot",
        lambda *_args, **_kwargs: pytest.fail("API alert mode rebuilt the snapshot"),
    )
    monkeypatch.setattr(
        alerts,
        "evaluate",
        lambda snapshot: [
            {"rule": "regime", "message": "EROSION"}
            if snapshot is VALID_SNAPSHOT
            else pytest.fail("wrong snapshot")
        ],
    )
    args = SimpleNamespace(
        api_url="http://127.0.0.1:8787/api/overview",
        max_snapshot_age_seconds=3600,
        force=False,
    )

    assert cli.cmd_alert(args) == 2
    assert "ALERT" in capsys.readouterr().out


def test_cmd_alert_api_failure_is_loud_and_skips_evaluation(
    monkeypatch,
    capsys,
) -> None:
    from seiche import alerts

    def fail(*_args, **_kwargs):
        raise cli.AlertSnapshotError("API returned HTTP 503")

    monkeypatch.setattr(cli, "_load_alert_snapshot", fail)
    monkeypatch.setattr(
        alerts,
        "evaluate",
        lambda _snapshot: pytest.fail("invalid snapshot reached alert evaluation"),
    )
    args = SimpleNamespace(
        api_url="http://127.0.0.1:8787/api/overview",
        max_snapshot_age_seconds=3600,
        force=False,
    )

    assert cli.cmd_alert(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "alert snapshot unavailable" in captured.err
    assert "HTTP 503" in captured.err


def test_cmd_alert_local_mode_preserves_force_behavior(monkeypatch, capsys) -> None:
    from seiche import alerts, assemble

    calls: list[bool] = []

    async def fake_snapshot(*, force: bool):
        calls.append(force)
        return VALID_SNAPSHOT

    monkeypatch.setattr(assemble, "snapshot", fake_snapshot)
    monkeypatch.setattr(alerts, "evaluate", lambda _snapshot: [])
    args = SimpleNamespace(api_url=None, max_snapshot_age_seconds=3600, force=True)

    assert cli.cmd_alert(args) == 0
    assert calls == [True]
    assert "no new alerts" in capsys.readouterr().out


def test_alert_cli_rejects_force_with_api_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "seiche",
            "alert",
            "--force",
            "--api-url",
            "http://127.0.0.1:8787/api/overview",
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
