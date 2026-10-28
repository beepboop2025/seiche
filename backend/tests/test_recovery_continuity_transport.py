"""Recovery transport boundaries; no production requests or recovery operations."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
from types import SimpleNamespace
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "deploy/railway-ci/recovery-controller/continuity_transport.py"
spec = importlib.util.spec_from_file_location("recovery_continuity_transport", SOURCE)
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)

DEPLOYMENT = "41475d07-661f-408f-b4b4-a0d69e33be82"
REPLICA = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def scope(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        transport.pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(home))
    )
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "RAILWAY_ORIGIN": transport.ORIGIN,
        "RAILWAY_EDGE_TOKEN": "private-edge-test-token",
        "GITHUB_REPOSITORY": "beepboop2025/seiche",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "9" * 40,
        "RELEASE_SHA": "1" * 40,
        "DEPLOYMENT_ID": DEPLOYMENT,
        "GITHUB_RUN_ID": "40750350251",
        "GITHUB_RUN_ATTEMPT": "1",
        "RAILWAY_PROJECT_ID": "project",
        "RAILWAY_ENVIRONMENT_ID": "production",
        "RAILWAY_SERVICE_ID": "stateful",
    }
    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "activation.sha256").write_text("a" * 64 + "\n")
    (proof / "submission-replica-id.txt").write_text(REPLICA + "\n")
    (proof / "source-health-claim.json").write_text('{"signed":"existing-claim"}\n')
    (proof / "runtime.json").write_text(
        json.dumps(
            {
                "data": {
                    "deployment": {
                        "id": DEPLOYMENT,
                        "projectId": "project",
                        "environmentId": "production",
                        "serviceId": "stateful",
                        "status": "SUCCESS",
                        "instances": [{"id": REPLICA, "status": "RUNNING"}],
                    }
                }
            }
        )
    )
    (tmp_path / "request.json").write_text(
        json.dumps(
            {
                "schema": "seiche.railway-recovery-export-request.v2",
                "repository": "beepboop2025/seiche",
                "workflow": "beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml",
                "commit": env["RELEASE_SHA"],
                "deployment_id": DEPLOYMENT,
                "activation_receipt_sha256": "a" * 64,
                "confirmation": "EXPORT_WITHOUT_AUTHORITY_CHANGE",
                "request_id": "r" * 64,
            }
        )
    )
    return tmp_path, env


def result(context, *, status="200", peer="1.1.1.1", exit_code=0):
    return {
        "status": status,
        "exit_code": exit_code,
        "remote_ip": peer,
        "remote_port": 443,
        "tls_verify_result": 0,
        "http_connect": "000",
        "identity": transport.identity(context),
    }


def successful_probe(context, calls):
    def probe(url, token, peer, env):
        calls.append((url, token, peer, dict(env)))
        return result(context, peer=peer or "1.1.1.1")

    return probe


@pytest.mark.parametrize(
    "name",
    [
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ],
)
def test_proxy_ambiguity_stops_before_any_request(scope, name):
    root, env = scope
    env[name] = "http://name:secret@proxy.invalid:8080"
    with pytest.raises(transport.TransportError, match="proxy environment"):
        transport.prepare(
            root, env, probe=lambda *_: pytest.fail("network request attempted")
        )
    assert not (root / "proof" / transport.PIN_NAME).exists()


@pytest.mark.parametrize(
    "variable,filename",
    [
        ("HOME", ".curlrc"),
        ("CURL_HOME", ".curlrc"),
        ("XDG_CONFIG_HOME", "curlrc"),
    ],
)
def test_default_config_is_rejected_without_reading_or_using_it(
    scope, variable, filename
):
    root, env = scope
    directory = root / "config"
    directory.mkdir()
    env[variable] = str(directory)
    (directory / filename).write_text("insecure\nproxy=http://secret@proxy.invalid\n")
    with pytest.raises(transport.TransportError, match="default curl configuration"):
        transport.prepare(
            root, env, probe=lambda *_: pytest.fail("network request attempted")
        )


@pytest.mark.parametrize(
    "name",
    [
        "CURL_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_SSL_BACKEND",
        "SSLKEYLOGFILE",
    ],
)
def test_ambient_trust_and_tls_key_logging_are_rejected(scope, name):
    _, env = scope
    env[name] = "/private/override"
    with pytest.raises(transport.TransportError, match="overrides"):
        transport.direct_environment(env)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "2001:db8::1",
        "2606:4700::1111%eth0",
        "example.com",
        "1.1.1.1\n",
        None,
    ],
)
def test_non_global_or_non_numeric_peer_is_rejected(address):
    with pytest.raises(transport.TransportError, match="numeric global"):
        transport.global_peer(address)


@pytest.mark.parametrize(
    "changed",
    [
        {"status": "503"},
        {"status": "200", "exit_code": 28},
        {"tls_verify_result": 60},
        {"http_connect": "200"},
        {"remote_port": 8443},
        {"remote_ip": "127.0.0.1"},
    ],
)
def test_failed_discovery_is_never_retried_or_persisted(scope, changed):
    root, env = scope
    context = transport.context_for(root, env)
    calls = []

    def fail(*args):
        calls.append(args)
        return dict(result(context), **changed)

    with pytest.raises(transport.TransportError):
        transport.prepare(root, env, probe=fail)
    assert len(calls) == 1
    assert not (root / "proof" / transport.PIN_NAME).exists()


@pytest.mark.parametrize("header", transport.IDENTITY_HEADERS)
def test_identity_mismatch_is_rejected_before_pinning(scope, header):
    root, env = scope
    context = transport.context_for(root, env)
    response = result(context)
    response["identity"][header] = "different"
    with pytest.raises(transport.TransportError, match="identity"):
        transport.prepare(root, env, probe=lambda *_: response)


def test_pinned_confirmation_must_retain_discovered_peer(scope):
    root, env = scope
    context = transport.context_for(root, env)
    calls = []

    def changed(url, token, peer, environment):
        calls.append(peer)
        return result(context, peer="1.1.1.1" if peer is None else "8.8.8.8")

    with pytest.raises(transport.TransportError, match="pinned peer"):
        transport.prepare(root, env, probe=changed)
    assert calls == [None, "1.1.1.1"]
    assert not (root / "proof" / transport.PIN_NAME).exists()


@pytest.mark.parametrize(
    "file,change",
    [
        ("request.json", lambda doc: doc.update(commit="2" * 40)),
        ("request.json", lambda doc: doc.update(activation_receipt_sha256="b" * 64)),
        (
            "proof/runtime.json",
            lambda doc: doc["data"]["deployment"].update(status="REMOVED"),
        ),
        (
            "proof/runtime.json",
            lambda doc: doc["data"]["deployment"].update(serviceId="other"),
        ),
        (
            "proof/runtime.json",
            lambda doc: doc["data"]["deployment"]["instances"][0].update(id="other"),
        ),
    ],
)
def test_existing_request_activation_provider_and_replica_bindings_remain_required(
    scope, file, change
):
    root, env = scope
    path = root / file
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))
    with pytest.raises(transport.TransportError):
        transport.prepare(
            root, env, probe=lambda *_: pytest.fail("network request attempted")
        )


def test_pins_bind_one_run_and_keep_credentials_out_of_evidence(scope):
    root, env = scope
    context = transport.context_for(root, env)
    calls = []
    digest = transport.prepare(root, env, probe=successful_probe(context, calls))
    assert [call[2] for call in calls] == [None, "1.1.1.1", None, "1.1.1.1"]
    assert all(set(call[3]) == {"PATH", "LANG", "LC_ALL"} for call in calls)
    assert calls[2][1] == calls[3][1] == ""
    path = root / "proof" / transport.PIN_NAME
    raw = path.read_bytes()
    assert env["RAILWAY_EDGE_TOKEN"].encode() not in raw
    assert hashlib.sha256(raw).hexdigest() == digest
    pins = transport.load_pins(root, env, digest)
    assert pins["context"] == context
    assert pins["context"]["controller_source"] != pins["context"]["runtime_source"]
    with pytest.raises(transport.TransportError, match="already exist"):
        transport.prepare(
            root, env, probe=lambda *_: pytest.fail("pins must not be replaced")
        )
    with pytest.raises(transport.TransportError, match="scope differs"):
        transport.load_pins(root, dict(env, GITHUB_RUN_ATTEMPT="2"), digest)
    path.chmod(0o600)
    path.write_bytes(raw + b" ")
    with pytest.raises(transport.TransportError, match="digest differs"):
        transport.load_pins(root, env, digest)


def test_route_substitution_is_rejected_even_with_a_matching_file_digest(scope):
    root, env = scope
    context = transport.context_for(root, env)
    transport.prepare(root, env, probe=successful_probe(context, []))
    path = root / "proof" / transport.PIN_NAME
    document = json.loads(path.read_text())
    document["peers"]["origin"]["url"] = "https://other.example/api/health"
    raw = transport.canonical(document)
    path.chmod(0o600)
    path.write_bytes(raw)
    with pytest.raises(transport.TransportError, match="route identity"):
        transport.load_pins(root, env, hashlib.sha256(raw).hexdigest())


def test_every_pair_is_retained_and_later_success_never_clears_failure(scope):
    root, env = scope
    context = transport.context_for(root, env)
    digest = transport.prepare(root, env, probe=successful_probe(context, []))
    pins = transport.load_pins(root, env, digest)
    calls = []

    def one_failure(url, token, peer, environment):
        calls.append(url)
        return result(context, status="503" if len(calls) == 1 else "200")

    transport.sample(
        root, env, pins, probe=one_failure, observed_at="2026-10-29T00:00:00Z"
    )
    transport.sample(
        root,
        env,
        pins,
        probe=successful_probe(context, calls),
        observed_at="2026-10-29T00:00:02Z",
    )
    assert len(calls) == 4
    assert (root / "proof/api-continuity.tsv").read_text().splitlines() == [
        "2026-10-29T00:00:00Z\t503\t200",
        "2026-10-29T00:00:02Z\t200\t200",
    ]
    assert (root / "proof/api-continuity.failed").exists()
    diagnostics = (root / "proof/continuity-transport-samples.jsonl").read_text()
    assert env["RAILWAY_EDGE_TOKEN"] not in diagnostics
    assert len(diagnostics.splitlines()) == 2


def test_monitor_keeps_both_routes_two_second_cadence_and_never_resolves_again(scope):
    root, env = scope
    context = transport.context_for(root, env)
    digest = transport.prepare(root, env, probe=successful_probe(context, []))
    calls, sleeps = [], []

    def pinned(url, token, peer, environment):
        assert peer == "1.1.1.1", "a new DNS discovery would fail"
        calls.append(url)
        return result(context)

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            (root / "proof/stop-continuity").touch()

    transport.monitor(root, env, digest, probe=pinned, sleep=sleep)
    assert calls == [url for _, url in transport.ROUTES] * 2
    assert sleeps == [2, 2]
    assert len((root / "proof/api-continuity.tsv").read_text().splitlines()) == 2


def curl_output(context, *, mixed_case=False, duplicate=False, peer="1.1.1.1"):
    lines = [b"HTTP/2 200"]
    for key, value in transport.identity(context).items():
        lines.append(
            (key.title() if mixed_case else key).encode() + b": " + value.encode()
        )
    if duplicate:
        lines.append(b"X-Seiche-Release-Sha: substituted")
    return (
        b"\r\n".join(lines)
        + b"\r\n\r\n"
        + transport.META
        + (f"200\t{peer}\t443\t0\t000\n".encode())
    )


def test_curl_identity_headers_are_case_insensitive_and_duplicates_fail(scope):
    root, env = scope
    context = transport.context_for(root, env)

    def run(*args, **kwargs):
        return SimpleNamespace(
            stdout=curl_output(context, mixed_case=True), stderr=b"", returncode=0
        )

    actual = transport.curl_probe(
        transport.ROUTES[0][1], env["RAILWAY_EDGE_TOKEN"], "1.1.1.1", {}, runner=run
    )
    assert transport.validate_probe(actual, context, "1.1.1.1") == "1.1.1.1"

    def duplicate(*args, **kwargs):
        return SimpleNamespace(
            stdout=curl_output(context, duplicate=True), stderr=b"", returncode=0
        )

    actual = transport.curl_probe(
        transport.ROUTES[0][1], "", "1.1.1.1", {}, runner=duplicate
    )
    with pytest.raises(transport.TransportError):
        transport.validate_probe(actual, context)


def test_ipv6_pin_and_original_url_timeouts_and_configuration_are_preserved(scope):
    root, env = scope
    context = transport.context_for(root, env)
    address = "2606:4700:4700::1111"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            stdout=curl_output(context, peer=address), stderr=b"", returncode=0
        )

    actual = transport.curl_probe(transport.ROUTES[1][1], "", address, {}, runner=run)
    assert transport.validate_probe(actual, context, address) == address
    command, kwargs = calls[0]
    assert command[:2] == ["curl", "-q"]
    assert command[-1] == "https://api.seiche.info/api/health"
    assert (
        command[command.index("--resolve") + 1]
        == "api.seiche.info:443:[2606:4700:4700::1111]"
    )
    assert command[command.index("--noproxy") + 1] == "api.seiche.info"
    assert command[command.index("--connect-timeout") + 1] == "5"
    assert command[command.index("--max-time") + 1] == "15"
    assert kwargs["timeout"] == 15
    assert not set(command) & {
        "-k",
        "--insecure",
        "--retry",
        "--location",
        "--connect-to",
    }


def test_curl_timeout_is_recorded_without_replay_or_error_credential_disclosure(scope):
    root, env = scope
    context = transport.context_for(root, env)
    calls = []

    def timed_out(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 15, stderr=env["RAILWAY_EDGE_TOKEN"])

    actual = transport.curl_probe(
        transport.ROUTES[0][1],
        env["RAILWAY_EDGE_TOKEN"],
        "1.1.1.1",
        {},
        runner=timed_out,
    )
    assert actual["status"] == "000" and len(calls) == 1
    assert env["RAILWAY_EDGE_TOKEN"] not in json.dumps(actual)
    with pytest.raises(transport.TransportError):
        transport.validate_probe(actual, context)


@pytest.mark.skipif(
    not shutil.which("curl") or not shutil.which("openssl"),
    reason="curl/OpenSSL required",
)
def test_real_curl_pin_preserves_host_sni_and_rejects_wrong_certificate_hostname(
    tmp_path,
):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=continuity.test",
            "-addext",
            "subjectAltName=DNS:continuity.test",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    received_hosts, sni_names = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received_hosts.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    tls.set_servername_callback(lambda _socket, name, _context: sni_names.append(name))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def trust_only_fixture_ca(command, **kwargs):
        # Test-only local trust injection at the subprocess boundary; production
        # has no custom-CA option and explicitly rejects ambient trust overrides.
        return subprocess.run(
            [*command[:-1], "--cacert", str(cert), command[-1]], **kwargs
        )

    try:
        port = server.server_address[1]
        url = f"https://continuity.test:{port}/api/health"
        response = transport.curl_probe(
            url,
            "",
            "127.0.0.1",
            {"PATH": os.environ["PATH"]},
            runner=trust_only_fixture_ca,
        )
        assert response["exit_code"] == 0 and response["status"] == "200"
        assert response["tls_verify_result"] == 0
        assert received_hosts == [f"continuity.test:{port}"]
        assert sni_names == ["continuity.test"]
        wrong = transport.curl_probe(
            f"https://wrong.test:{port}/api/health",
            "",
            "127.0.0.1",
            {"PATH": os.environ["PATH"]},
            runner=trust_only_fixture_ca,
        )
        assert wrong["exit_code"] != 0 and wrong["status"] == "000"
        assert len(received_hosts) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_workflow_qualifies_transport_before_export_and_keeps_final_failure_guard():
    workflow = (ROOT / ".github/workflows/railway-stateful-recovery.yml").read_text()
    preflight = workflow.index(
        'continuity_pins_sha256=$(python3 -B "$continuity_helper" prepare)'
    )
    monitor = workflow.index('"$continuity_helper" monitor --pins-sha256', preflight)
    recheck = workflow.index('source_health_claim.py" --recheck', monitor)
    export = workflow.index(
        '"$RAILWAY_ORIGIN/api/internal/v1/railway-control/commands"', recheck
    )
    stop = workflow.index("touch proof/stop-continuity", export)
    guard = workflow.index("test ! -e proof/api-continuity.failed", stop)
    minimum = workflow.index('test "$(wc -l <proof/api-continuity.tsv)" -ge 2', guard)
    assert preflight < monitor < recheck < export < stop < guard < minimum
    assert "recovery_ready_deadline=$((SECONDS + 4500))" in workflow
    assert "download_expires_at=$(date -u -d '+110 minutes'" in workflow


def test_failure_artifact_retains_exact_noncredential_transport_evidence():
    workflow = (ROOT / ".github/workflows/railway-stateful-recovery.yml").read_text()
    failure_upload = workflow.split(
        "      - name: Retain bounded recovery failure diagnostics\n", 1
    )[1].split("\n  #", 1)[0]
    assert "if: failure()" in failure_upload
    assert "railway-recovery-export/proof/continuity-transport.json" in failure_upload
    assert (
        "railway-recovery-export/proof/continuity-transport-samples.jsonl"
        in failure_upload
    )


@pytest.mark.parametrize("failure_kind", ["transport", "identity"])
def test_http_200_with_transport_or_identity_failure_is_sticky_and_keeps_public_sample(
    scope, failure_kind
):
    root, env = scope
    context = transport.context_for(root, env)
    digest = transport.prepare(root, env, probe=successful_probe(context, []))
    pins = transport.load_pins(root, env, digest)
    calls = []

    def probe(url, token, peer, environment):
        calls.append(url)
        response = result(context)
        if len(calls) == 1:
            if failure_kind == "transport":
                response["exit_code"] = 28
            else:
                response["identity"]["x-seiche-release-sha"] = "2" * 40
        return response

    transport.sample(root, env, pins, probe=probe)
    assert calls == [url for _, url in transport.ROUTES]
    assert (root / "proof/api-continuity.tsv").read_text().endswith("\t200\t200\n")
    assert (root / "proof/api-continuity.failed").exists()
    diagnostics = json.loads(
        (root / "proof/continuity-transport-samples.jsonl").read_text()
    )
    assert diagnostics["outcomes"]["origin"]["error"] is not None
    assert diagnostics["outcomes"]["public"]["error"] is None
