"""Private, opaque signed-delivery relay contracts."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from seiche import api, world_model_delivery


TOKEN = "a" * 64
WRONG_TOKEN = "b" * 64
ROUTE = world_model_delivery.DELIVERY_ROUTE


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api.app)


def _configured_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes = b'{"signed":"envelope"}\n',
    *,
    max_bytes: int = world_model_delivery.DEFAULT_MAX_BYTES,
) -> Path:
    export = tmp_path / "export"
    export.mkdir()
    delivery = export / world_model_delivery.DELIVERY_FILENAME
    delivery.write_bytes(payload)
    delivery.chmod(0o440)
    monkeypatch.setenv(world_model_delivery.DELIVERY_PATH_ENV, str(delivery))
    monkeypatch.setenv(world_model_delivery.DELIVERY_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(world_model_delivery.DELIVERY_MAX_BYTES_ENV, str(max_bytes))
    return delivery


def _authorization(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_relay_is_default_disabled_and_no_store(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(world_model_delivery.DELIVERY_PATH_ENV, raising=False)
    monkeypatch.delenv(world_model_delivery.DELIVERY_TOKEN_ENV, raising=False)

    response = client.get(ROUTE)

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, no-transform"


def test_relay_requires_exact_bearer_without_disclosing_secrets(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = _configured_file(tmp_path, monkeypatch)

    missing = client.get(ROUTE)
    malformed = client.get(ROUTE, headers={"Authorization": f"Basic {TOKEN}"})
    wrong = client.get(ROUTE, headers=_authorization(WRONG_TOKEN))

    for response in (missing, malformed, wrong):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store, no-transform"
        assert TOKEN not in response.text
        assert WRONG_TOKEN not in response.text
        assert str(delivery) not in response.text


def test_bearer_comparison_calls_compare_digest_for_missing_and_wrong_schemes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = tmp_path / world_model_delivery.DELIVERY_FILENAME
    config = world_model_delivery.DeliveryConfig(delivery, TOKEN, 100)
    calls: list[tuple[bytes, bytes]] = []

    def compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(world_model_delivery.hmac, "compare_digest", compare)

    assert world_model_delivery.bearer_authorized(config, None) is False
    assert world_model_delivery.bearer_authorized(config, f"Basic {TOKEN}") is False
    assert (
        world_model_delivery.bearer_authorized(config, f"Bearer {WRONG_TOKEN}") is False
    )
    assert calls == [
        (b"", TOKEN.encode()),
        (b"", TOKEN.encode()),
        (WRONG_TOKEN.encode(), TOKEN.encode()),
    ]


def test_relay_streams_exact_unchanged_bytes(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = (
        '{\n  "schema" : "liquilens.world-model-delivery.v1",\n'
        '  "opaque" : "雪", "spacing" : [ 1,  2 ]\n}\n'
    ).encode()
    _configured_file(tmp_path, monkeypatch, payload)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["pragma"] == "no-cache"


def test_missing_delivery_fails_closed_after_auth(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = tmp_path / "export" / world_model_delivery.DELIVERY_FILENAME
    monkeypatch.setenv(world_model_delivery.DELIVERY_PATH_ENV, str(delivery))
    monkeypatch.setenv(world_model_delivery.DELIVERY_TOKEN_ENV, TOKEN)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert str(delivery) not in response.text


def test_symlink_delivery_is_never_followed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "export"
    export.mkdir()
    target = tmp_path / "real-envelope.json"
    target.write_bytes(b'{"must":"not leak"}\n')
    delivery = export / world_model_delivery.DELIVERY_FILENAME
    delivery.symlink_to(target)
    monkeypatch.setenv(world_model_delivery.DELIVERY_PATH_ENV, str(delivery))
    monkeypatch.setenv(world_model_delivery.DELIVERY_TOKEN_ENV, TOKEN)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 503
    assert target.read_bytes() not in response.content


def test_symlink_parent_is_never_traversed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_export = tmp_path / "real-export"
    real_export.mkdir()
    (real_export / world_model_delivery.DELIVERY_FILENAME).write_bytes(b"hidden")
    linked_export = tmp_path / "export"
    linked_export.symlink_to(real_export, target_is_directory=True)
    monkeypatch.setenv(
        world_model_delivery.DELIVERY_PATH_ENV,
        str(linked_export / world_model_delivery.DELIVERY_FILENAME),
    )
    monkeypatch.setenv(world_model_delivery.DELIVERY_TOKEN_ENV, TOKEN)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 503
    assert response.content != b"hidden"


def test_oversize_delivery_is_never_streamed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_file(tmp_path, monkeypatch, b"1234", max_bytes=3)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 503
    assert response.content != b"1234"


def test_delivery_with_broader_file_mode_is_rejected(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = _configured_file(tmp_path, monkeypatch)
    delivery.chmod(0o444)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 503


@pytest.mark.parametrize("configured_max", ["0", "not-a-number", "5242881"])
def test_invalid_size_configuration_keeps_relay_disabled(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_max: str,
) -> None:
    _configured_file(tmp_path, monkeypatch)
    monkeypatch.setenv(world_model_delivery.DELIVERY_MAX_BYTES_ENV, configured_max)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 404


@pytest.mark.parametrize("configured_token", ["a" * 63, "A" * 64, "a" * 65])
def test_invalid_token_configuration_keeps_relay_disabled(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_token: str,
) -> None:
    _configured_file(tmp_path, monkeypatch)
    monkeypatch.setenv(world_model_delivery.DELIVERY_TOKEN_ENV, configured_token)

    response = client.get(ROUTE, headers=_authorization())

    assert response.status_code == 404


def test_private_route_is_unique_and_hidden_from_both_openapi_documents() -> None:
    routes = [
        route
        for route in api.app.routes
        if getattr(route, "path", None) == ROUTE
        and "GET" in (getattr(route, "methods", None) or set())
    ]

    assert len(routes) == 1
    assert routes[0].include_in_schema is False
    assert ROUTE not in api.app.openapi()["paths"]
    assert ROUTE not in api._public_openapi_document()["paths"]
    assert ROUTE not in api.api_index()["rest"].values()
