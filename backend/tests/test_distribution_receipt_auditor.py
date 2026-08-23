"""Offline contracts for the read-only public distribution receipt auditor."""

from __future__ import annotations

from copy import deepcopy
from email.message import Message
import importlib.util
from io import BytesIO
from pathlib import Path
import re
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "release" / "audit_distribution_receipts.py"
SPEC = importlib.util.spec_from_file_location("distribution_receipt_auditor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDITOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDITOR
SPEC.loader.exec_module(AUDITOR)


def _contract() -> dict:
    return AUDITOR.load_local_contract(ROOT)


def _artifact(filename: str, package_type: str, marker: str) -> dict:
    path_hash = marker * 64
    sha256 = ("c" if marker == "a" else "d") * 64
    return {
        "filename": filename,
        "packagetype": package_type,
        "yanked": False,
        "digests": {"sha256": sha256, "blake2b_256": path_hash},
        "size": 1234,
        "url": (
            "https://files.pythonhosted.org/packages/"
            f"{path_hash[:2]}/{path_hash[2:4]}/{path_hash[4:]}/{filename}"
        ),
    }


def _pypi_payload(contract: dict) -> dict:
    version = contract["version"]
    artifacts = [
        _artifact(f"seiche-{version}-py3-none-any.whl", "bdist_wheel", "a"),
        _artifact(f"seiche-{version}.tar.gz", "sdist", "b"),
    ]
    return {
        "info": {"name": "seiche", "version": version},
        "urls": deepcopy(artifacts),
        "releases": {version: deepcopy(artifacts)},
    }


def _registry_payload(contract: dict) -> dict:
    server = deepcopy(contract["server"])
    for package in server["packages"]:
        for variable in package.get("environmentVariables", []):
            variable.pop("isRequired", None)
    return {
        "server": server,
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": "active",
                "isLatest": True,
                "statusChangedAt": "2026-08-23T11:59:59.125+00:00",
                "publishedAt": "2026-08-23T12:00:00Z",
                "updatedAt": "2026-08-23T17:30:00+05:30",
            }
        },
    }


def test_exact_latest_receipts_pass_with_bounded_fetch_contract() -> None:
    contract = _contract()
    calls: list[tuple[str, float]] = []

    def fetch(url: str, timeout: float) -> dict:
        calls.append((url, timeout))
        if url == AUDITOR.PYPI_URL:
            return _pypi_payload(contract)
        if url == AUDITOR.MCP_REGISTRY_URL:
            return _registry_payload(contract)
        raise AssertionError(f"unexpected URL {url}")

    report = AUDITOR.audit_distribution_receipts(
        repo_root=ROOT, timeout=3.0, fetch_json=fetch
    )

    assert report["status"] == "pass"
    assert report["errors"] == []
    assert [receipt["surface"] for receipt in report["receipts"]] == [
        "pypi",
        "official_mcp_registry",
    ]
    registry_receipt = report["receipts"][1]
    pypi_artifacts = report["receipts"][0]["artifacts"]
    assert all(
        artifact["sha256"] != artifact["blake2b_256"] for artifact in pypi_artifacts
    )
    assert registry_receipt["status_changed_at"].endswith("+00:00")
    assert registry_receipt["published_at"].endswith("Z")
    assert registry_receipt["updated_at"].endswith("+05:30")
    assert calls == [(AUDITOR.PYPI_URL, 3.0), (AUDITOR.MCP_REGISTRY_URL, 3.0)]


def test_both_mandatory_surfaces_report_version_drift() -> None:
    contract = _contract()
    pypi = _pypi_payload(contract)
    pypi["info"]["version"] = "0.10.1"
    registry = _registry_payload(contract)
    registry["server"]["version"] = "0.10.1"

    def fetch(url: str, _timeout: float) -> dict:
        return pypi if url == AUDITOR.PYPI_URL else registry

    report = AUDITOR.audit_distribution_receipts(
        repo_root=ROOT, timeout=1.0, fetch_json=fetch
    )

    assert report["status"] == "fail"
    assert report["receipts"] == []
    assert [error["surface"] for error in report["errors"]] == [
        "pypi",
        "official_mcp_registry",
    ]
    assert all("repository requires" in error["error"] for error in report["errors"])


@pytest.mark.parametrize(
    "mutation",
    ["inactive", "missing-status", "not-latest", "missing-latest", "card-drift"],
)
def test_registry_receipt_fails_closed_on_official_metadata_or_card_drift(
    mutation: str,
) -> None:
    contract = _contract()
    payload = _registry_payload(contract)
    official = payload["_meta"]["io.modelcontextprotocol.registry/official"]
    if mutation == "inactive":
        official["status"] = "deleted"
    elif mutation == "missing-status":
        official.pop("status")
    elif mutation == "not-latest":
        official["isLatest"] = False
    elif mutation == "missing-latest":
        official.pop("isLatest")
    else:
        payload["server"]["websiteUrl"] = "https://example.invalid"

    with pytest.raises(AUDITOR.AuditError):
        AUDITOR.audit_mcp_registry(
            contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
        )


def test_registry_normalizes_false_defaults_only_at_schema_input_paths() -> None:
    contract = deepcopy(_contract())
    package = contract["server"]["packages"][0]
    environment = package["environmentVariables"][0]
    environment["isSecret"] = False
    package["packageArguments"] = [
        {
            "type": "named",
            "name": "--probe",
            "isRequired": False,
            "isSecret": False,
            "isRepeated": False,
            "variables": {
                "nested": {"isRequired": False, "isSecret": False},
            },
        }
    ]
    remote = contract["server"]["remotes"][0]
    remote["headers"] = [
        {"name": "X-Probe", "isRequired": False, "isSecret": False},
    ]
    remote["variables"] = {
        "region": {"isRequired": False, "isSecret": False},
    }

    payload = _registry_payload(contract)
    registry_package = payload["server"]["packages"][0]
    registry_package["environmentVariables"][0].pop("isSecret")
    registry_argument = registry_package["packageArguments"][0]
    for field in ("isRequired", "isSecret", "isRepeated"):
        registry_argument.pop(field)
    for field in ("isRequired", "isSecret"):
        registry_argument["variables"]["nested"].pop(field)
        payload["server"]["remotes"][0]["headers"][0].pop(field)
        payload["server"]["remotes"][0]["variables"]["region"].pop(field)

    receipt = AUDITOR.audit_mcp_registry(
        contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
    )

    assert receipt["status"] == "pass"


@pytest.mark.parametrize(
    "location",
    ["top-level", "repository", "environment-isRepeated", "remote-object"],
)
def test_registry_preserves_false_fields_outside_schema_default_paths(
    location: str,
) -> None:
    contract = _contract()
    payload = _registry_payload(contract)
    server = payload["server"]
    if location == "top-level":
        server["isRequired"] = False
    elif location == "repository":
        server["repository"]["isSecret"] = False
    elif location == "environment-isRepeated":
        server["packages"][0]["environmentVariables"][0]["isRepeated"] = False
    else:
        server["remotes"][0]["isRequired"] = False

    with pytest.raises(AUDITOR.AuditError, match="server card differs"):
        AUDITOR.audit_mcp_registry(
            contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("statusChangedAt", None),
        ("publishedAt", None),
        ("statusChangedAt", "2026-08-23T12:00:00"),
        ("publishedAt", "2026-08-23 12:00:00Z"),
        ("publishedAt", "2026-13-23T12:00:00Z"),
        ("updatedAt", "2026-08-23T12:00:00+0000"),
        ("updatedAt", 123),
    ],
)
def test_registry_receipt_rejects_missing_or_malformed_clocks(
    field: str, value: object
) -> None:
    contract = _contract()
    payload = _registry_payload(contract)
    official = payload["_meta"]["io.modelcontextprotocol.registry/official"]
    if value is None:
        official.pop(field)
    else:
        official[field] = value

    with pytest.raises(AUDITOR.AuditError, match=field):
        AUDITOR.audit_mcp_registry(
            contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
        )


def test_registry_updated_clock_is_optional() -> None:
    contract = _contract()
    payload = _registry_payload(contract)
    payload["_meta"]["io.modelcontextprotocol.registry/official"].pop("updatedAt")

    receipt = AUDITOR.audit_mcp_registry(
        contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
    )

    assert "updated_at" not in receipt


@pytest.mark.parametrize("mutation", ["foreign", "yanked", "digest", "url"])
def test_pypi_receipt_rejects_untrusted_artifact_inventory(mutation: str) -> None:
    contract = _contract()
    payload = _pypi_payload(contract)
    targets = [payload["urls"][0], payload["releases"][contract["version"]][0]]
    for target in targets:
        if mutation == "foreign":
            target["filename"] = "foreign.whl"
        elif mutation == "yanked":
            target["yanked"] = True
        elif mutation == "digest":
            target["digests"]["sha256"] = "invalid"
        else:
            target["url"] = "https://example.invalid/artifact.whl"

    with pytest.raises(AUDITOR.AuditError):
        AUDITOR.audit_pypi(
            contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
        )


@pytest.mark.parametrize("mutation", ["missing", "invalid", "path-mismatch"])
def test_pypi_receipt_requires_path_bound_blake2b_256(mutation: str) -> None:
    contract = _contract()
    payload = _pypi_payload(contract)
    artifact = payload["urls"][0]
    if mutation == "missing":
        artifact["digests"].pop("blake2b_256")
    elif mutation == "invalid":
        artifact["digests"]["blake2b_256"] = "invalid"
    else:
        artifact["digests"]["blake2b_256"] = "f" * 64

    with pytest.raises(AUDITOR.AuditError, match="BLAKE2b-256"):
        AUDITOR.audit_pypi(
            contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid-ipv6",
        "invalid-port",
        "explicit-port",
        "userinfo",
        "query",
        "empty-query",
        "fragment",
        "wrong-host",
        "trailing-dot-host",
        "wrong-path",
        "encoded-path",
        "traversal-path",
    ],
)
def test_pypi_receipt_rejects_malformed_artifact_urls(mutation: str) -> None:
    contract = _contract()
    payload = _pypi_payload(contract)
    artifact = payload["urls"][0]
    filename = artifact["filename"]
    canonical = artifact["url"]
    path = canonical.removeprefix("https://files.pythonhosted.org")
    malformed = {
        "invalid-ipv6": f"https://[::1{path}",
        "invalid-port": f"https://files.pythonhosted.org:notaport{path}",
        "explicit-port": f"https://files.pythonhosted.org:443{path}",
        "userinfo": f"https://auditor@files.pythonhosted.org{path}",
        "query": f"{canonical}?download=1",
        "empty-query": f"{canonical}?",
        "fragment": f"{canonical}#receipt",
        "wrong-host": f"https://files.pythonhosted.org.invalid{path}",
        "trailing-dot-host": f"https://files.pythonhosted.org.{path}",
        "wrong-path": canonical.replace("/packages/", "/project/"),
        "encoded-path": canonical.replace(filename, f"%73{filename[1:]}"),
        "traversal-path": canonical.replace(f"/{filename}", f"/../{filename}"),
    }
    artifact["url"] = malformed[mutation]

    with pytest.raises(AUDITOR.AuditError, match="artifact URL"):
        AUDITOR.audit_pypi(
            contract, timeout=1.0, fetch_json=lambda _url, _timeout: payload
        )


class _Response:
    def __init__(
        self,
        body: bytes = b"{}",
        *,
        status: int = 200,
        url: str = AUDITOR.PYPI_URL,
        content_type: str | None = "application/json",
        content_encoding: str | None = None,
        read_error: BaseException | None = None,
    ):
        self.body = body
        self.status = status
        self.url = url
        self.read_error = read_error
        self.headers = Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        if content_encoding is not None:
            self.headers["Content-Encoding"] = content_encoding

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, limit: int) -> bytes:
        assert limit == AUDITOR.MAX_RESPONSE_BYTES + 1
        if self.read_error is not None:
            raise self.read_error
        return self.body[:limit]


class _Opener:
    def __init__(
        self,
        response: _Response | None = None,
        *,
        error: BaseException | None = None,
    ):
        self.response = response
        self.error = error
        self.calls: list[tuple[object, float]] = []

    def open(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def test_network_reader_is_anonymous_get_only_and_size_bounded(monkeypatch) -> None:
    opener = _Opener(_Response(b"x" * (AUDITOR.MAX_RESPONSE_BYTES + 1)))
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", opener)
    with pytest.raises(AUDITOR.AuditError, match="exceeds"):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 2.5)

    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.get_method() == "GET"
    assert request.full_url == AUDITOR.PYPI_URL
    assert timeout == 2.5
    assert headers == {
        "accept": "application/json",
        "accept-encoding": "identity",
        "cache-control": "no-cache",
        "user-agent": AUDITOR.USER_AGENT,
    }
    assert not {"authorization", "cookie", "proxy-authorization"} & set(headers)


def test_network_opener_is_no_proxy_redirect_rejecting_and_https_only() -> None:
    handlers = AUDITOR._HTTPS_OPENER.handlers
    proxy_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, AUDITOR.urllib.request.ProxyHandler)
    ]

    # An empty ProxyHandler contributes no open method, so the manually built
    # director has no proxy-capable handler and never consults proxy env vars.
    assert proxy_handlers == []
    assert any(isinstance(handler, AUDITOR._RejectRedirect) for handler in handlers)
    assert any(
        isinstance(handler, AUDITOR.urllib.request.HTTPSHandler) for handler in handlers
    )
    assert not any(
        type(handler) is AUDITOR.urllib.request.HTTPHandler for handler in handlers
    )


def test_network_reader_refuses_every_non_exact_endpoint(monkeypatch) -> None:
    opener = _Opener(_Response())
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", opener)

    with pytest.raises(AUDITOR.AuditError, match="non-allowlisted"):
        AUDITOR._fetch_json(f"{AUDITOR.PYPI_URL}?mirror=1", 1.0)

    assert opener.calls == []


def test_network_reader_rejects_redirect_response(monkeypatch) -> None:
    headers = Message()
    headers["Location"] = "https://example.invalid/receipt"
    redirect = AUDITOR.urllib.error.HTTPError(
        AUDITOR.PYPI_URL,
        302,
        "Found",
        headers,
        BytesIO(b"redirecting"),
    )
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(error=redirect))

    with pytest.raises(AUDITOR.AuditError, match="redirect.*refused"):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)


def test_http_error_detail_cannot_escape_actions_markdown_fence(monkeypatch) -> None:
    hostile = AUDITOR.urllib.error.HTTPError(
        AUDITOR.PYPI_URL,
        500,
        "Internal Server Error",
        Message(),
        BytesIO(b"upstream failed ```\n# forged Actions heading\n```"),
    )
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(error=hostile))

    with pytest.raises(AUDITOR.AuditError) as captured:
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)

    message = str(captured.value)
    assert "```" not in message
    assert "forged Actions heading" not in message
    assert "response body omitted" in message


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response(status=204), "HTTP status"),
        (_Response(url="https://pypi.org/elsewhere"), "final URL"),
        (_Response(content_type="text/html"), "media type"),
        (_Response(content_type=None), "response headers"),
        (_Response(content_encoding="gzip"), "unsupported encoding"),
    ],
)
def test_network_reader_rejects_status_url_media_and_encoding(
    monkeypatch, response: _Response, message: str
) -> None:
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(response))

    with pytest.raises(AUDITOR.AuditError, match=message):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)


def test_network_reader_accepts_json_parameters_and_identity_encoding(
    monkeypatch,
) -> None:
    response = _Response(
        b'{"ok":true}',
        content_type="Application/JSON; charset=utf-8",
        content_encoding="Identity",
    )
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(response))

    assert AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0) == {"ok": True}


def test_network_reader_rejects_malformed_header_container(monkeypatch) -> None:
    response = _Response()
    response.headers = object()
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(response))

    with pytest.raises(AUDITOR.AuditError, match="malformed response headers"):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)


@pytest.mark.parametrize("header", ["Content-Type", "Content-Encoding"])
def test_network_reader_rejects_duplicate_semantic_headers(
    monkeypatch, header: str
) -> None:
    response = _Response(content_encoding="identity")
    response.headers[header] = response.headers[header]
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(response))

    with pytest.raises(AUDITOR.AuditError, match="ambiguous response headers"):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("socket timed out"),
        AUDITOR.http.client.RemoteDisconnected("peer disconnected"),
    ],
)
def test_network_reader_converts_open_failures_to_audit_error(
    monkeypatch, error: BaseException
) -> None:
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(error=error))

    with pytest.raises(AUDITOR.AuditError, match="could not read"):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("read timed out"),
        AUDITOR.http.client.IncompleteRead(b'{"partial":', 10),
    ],
)
def test_network_reader_converts_body_failures_to_audit_error(
    monkeypatch, error: BaseException
) -> None:
    response = _Response(read_error=error)
    monkeypatch.setattr(AUDITOR, "_HTTPS_OPENER", _Opener(response))

    with pytest.raises(AUDITOR.AuditError, match="could not read"):
        AUDITOR._fetch_json(AUDITOR.PYPI_URL, 1.0)


def test_network_failures_are_returned_as_structured_surface_reports(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        AUDITOR,
        "_HTTPS_OPENER",
        _Opener(error=TimeoutError("socket timed out")),
    )

    report = AUDITOR.audit_distribution_receipts(repo_root=ROOT, timeout=1.0)

    assert report["schema"] == "seiche.distribution-receipts.audit.v1"
    assert report["status"] == "fail"
    assert report["receipts"] == []
    assert report["errors"] == [
        {
            "surface": "pypi",
            "error": f"could not read {AUDITOR.PYPI_URL}: socket timed out",
        },
        {
            "surface": "official_mcp_registry",
            "error": f"could not read {AUDITOR.MCP_REGISTRY_URL}: socket timed out",
        },
    ]


def test_strict_json_rejects_duplicate_keys() -> None:
    with pytest.raises(AUDITOR.AuditError, match="duplicate JSON key"):
        AUDITOR._strict_json(b'{"version":"0.11.0","version":"0.10.1"}', source="test")


def test_strict_json_converts_excessive_depth_and_large_integer_failures() -> None:
    nesting = AUDITOR.MAX_JSON_NESTING_DEPTH + 1
    deeply_nested = b'{"value":' + (b"[" * nesting) + b"0" + (b"]" * nesting) + b"}"
    large_integer = b'{"value":' + (b"9" * (AUDITOR.MAX_JSON_INTEGER_DIGITS + 1)) + b"}"

    with pytest.raises(AUDITOR.AuditError, match="nesting depth"):
        AUDITOR._strict_json(deeply_nested, source="test")
    with pytest.raises(AUDITOR.AuditError, match="integer longer"):
        AUDITOR._strict_json(large_integer, source="test")


def test_strict_json_ignores_string_delimiters_and_rejects_non_json_constants() -> None:
    assert AUDITOR._strict_json(b'{"text":"[[[{{{\\""}', source="test") == {
        "text": '[[[{{{"'
    }
    with pytest.raises(AUDITOR.AuditError, match="non-standard JSON constant"):
        AUDITOR._strict_json(b'{"value":NaN}', source="test")


def test_scheduled_workflow_is_manual_read_only_and_action_pinned() -> None:
    workflow = (ROOT / ".github/workflows/audit-distribution-receipts.yml").read_text(
        encoding="utf-8"
    )
    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "timeout-minutes: 5" in workflow
    assert "persist-credentials: false" in workflow
    assert "ops/release/audit_distribution_receipts.py" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    references = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
    assert references
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in references)
    for forbidden in (
        "secrets.",
        "id-token: write",
        "contents: write",
        "packages: write",
        "distribution/submissions.csv",
        "mcp-publisher",
        "gh release",
        "twine",
    ):
        assert forbidden not in workflow
