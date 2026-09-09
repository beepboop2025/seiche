"""Real HTTP checks for the standalone relay and its import boundary."""

from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from seiche import world_model_delivery as delivery
from seiche.world_model_relay import RelayHandler, RelayServer

TOKEN = "a" * 64


@pytest.fixture()
def export(tmp_path, monkeypatch):
    # Resolve macOS /var aliases: the production validator rejects symlinks.
    path = tmp_path.resolve() / delivery.DELIVERY_FILENAME
    path.write_bytes(b'{ "opaque": [1, 2], "signature": "unchanged" }\n')
    path.chmod(0o440)
    monkeypatch.setenv(delivery.DELIVERY_PATH_ENV, str(path))
    monkeypatch.setenv(delivery.DELIVERY_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(delivery.DELIVERY_MAX_BYTES_ENV, "1024")
    return path


@contextmanager
def server():
    with RelayServer(("127.0.0.1", 0), RelayHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


def request(port, *, path=delivery.DELIVERY_ROUTE, method="GET", token=TOKEN, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    configured = {} if token is None else {"Authorization": f"Bearer {token}"}
    configured.update(headers or {})
    try:
        connection.request(method, path, headers=configured)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_exact_authenticated_bytes_and_no_cache(export):
    with server() as port:
        status, headers, body = request(port)
    assert status == 200
    assert body == export.read_bytes()
    assert headers["Content-Length"] == str(len(body))
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store, no-transform"


@pytest.mark.parametrize("token", [None, "b" * 64])
def test_unauthorized_never_discloses_export(export, token):
    with server() as port:
        status, headers, body = request(port, token=token)
    assert status == 401
    assert headers["WWW-Authenticate"] == "Bearer"
    assert TOKEN.encode() not in body
    assert str(export).encode() not in body


def test_disabled_configuration(export, monkeypatch):
    monkeypatch.delenv(delivery.DELIVERY_TOKEN_ENV)
    with server() as port:
        assert request(port)[0] == 404


def test_invalid_configuration_fails_closed(export, monkeypatch):
    monkeypatch.setenv(delivery.DELIVERY_PATH_ENV, "relative/us-usd-funding-core-v2.json")
    with server() as port:
        assert request(port)[0] == 404


def test_missing_or_oversized_export_fails_closed(export, monkeypatch):
    with server() as port:
        monkeypatch.setenv(delivery.DELIVERY_MAX_BYTES_ENV, "1")
        assert request(port)[0] == 503
        export.unlink()
        assert request(port)[0] == 503


@pytest.mark.parametrize("mode", [0o644, 0o600, 0o444])
def test_invalid_file_mode_fails_closed(export, mode):
    export.chmod(mode)
    with server() as port:
        assert request(port)[0] == 503


def test_symlink_export_fails_closed(export):
    target = export.with_name("other.json")
    export.rename(target)
    export.symlink_to(target)
    with server() as port:
        assert request(port)[0] == 503


def test_symlink_directory_is_rejected(export, monkeypatch):
    linked = export.parent / "linked"
    linked.symlink_to(export.parent, target_is_directory=True)
    monkeypatch.setenv(delivery.DELIVERY_PATH_ENV, str(linked / export.name))
    with server() as port:
        assert request(port)[0] == 503


@pytest.mark.parametrize("path", ["/api/health", "/", delivery.DELIVERY_ROUTE + "?token=x"])
def test_other_paths_do_not_expose_api(export, path):
    with server() as port:
        assert request(port, path=path)[0] == 404


@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"])
def test_non_get_never_serves_bytes(export, method):
    with server() as port:
        status, _, body = request(port, method=method)
    assert status == 404
    assert body != export.read_bytes()


@pytest.mark.parametrize("headers", [{"Content-Length": "1"}, {"Transfer-Encoding": "chunked"}])
def test_request_bodies_are_rejected(export, headers):
    with server() as port:
        assert request(port, headers=headers)[0] == 400


def test_oversized_headers_are_rejected(export):
    with server() as port:
        assert request(port, headers={"X-Unused": "x" * 17000})[0] == 431


def test_import_has_no_api_database_or_writer_dependencies():
    backend = str(Path(__file__).resolve().parents[1])
    code = (
        "import sys,json; sys.path.insert(0," + repr(backend) + ");"
        "import seiche.world_model_relay; print(json.dumps(sorted(sys.modules)))"
    )
    modules = json.loads(subprocess.check_output([sys.executable, "-I", "-c", code]))
    forbidden = ("seiche.api", "seiche.markets", "sqlalchemy", "psycopg", "fastapi")
    assert not [name for name in modules if name.startswith(forbidden)]
