#!/usr/bin/env python3
"""Pin validated, direct HTTPS peers for one recovery continuity interval.

Transport evidence only: existing provider, activation, source-health, restore,
off-site and attestation gates remain authoritative. Never retry a failed probe.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time
from urllib.parse import urlsplit

ORIGIN = "https://seiche-stateful-core-production.up.railway.app"
PUBLIC = "https://api.seiche.info"
ROUTES = (("origin", ORIGIN + "/api/health"), ("public", PUBLIC + "/api/health"))
IDENTITY_HEADERS = (
    "x-seiche-release-sha",
    "x-seiche-railway-deployment",
    "x-seiche-railway-authority",
)
CONNECT_TIMEOUT = 5
MAX_TIME = 15
SAMPLE_SLEEP = 2
META = b"\nSEICHE_CONTINUITY_CURL_META\t"
WRITE_OUT = "\nSEICHE_CONTINUITY_CURL_META\t%{http_code}\t%{remote_ip}\t%{remote_port}\t%{ssl_verify_result}\t%{http_connect}\n"
PIN_NAME = "continuity-transport.json"


class TransportError(ValueError):
    """A failed transport qualification; never include credentials in errors."""


def require(condition, message):
    if not condition:
        raise TransportError(message)


def timestamp():
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def bounded(path, maximum):
    require(not path.is_symlink(), "evidence must not be a symlink")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "evidence exceeds its bound")
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def global_peer(value):
    require(
        isinstance(value, str) and "%" not in value,
        "peer must be a numeric global address",
    )
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise TransportError("peer must be a numeric global address") from error
    require(
        address.is_global and not address.is_multicast and not address.is_reserved,
        "peer must be a numeric global address",
    )
    return str(address)


def direct_environment(environment):
    """Reject ambient routing/trust ambiguity; do not read config contents."""
    for name, value in environment.items():
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy"} and value.strip():
            raise TransportError("proxy environment is not permitted")
    for name in (
        "CURL_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_SSL_BACKEND",
        "SSLKEYLOGFILE",
    ):
        require(
            not environment.get(name, "").strip(),
            "ambient curl/TLS overrides are not permitted",
        )
    candidates = set()
    for variable, filename in (
        ("CURL_HOME", ".curlrc"),
        ("XDG_CONFIG_HOME", "curlrc"),
        ("HOME", ".curlrc"),
    ):
        if environment.get(variable):
            candidates.add(Path(environment[variable]) / filename)
    candidates.add(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".curlrc")
    for path in candidates:
        require(
            not path.exists() and not path.is_symlink(),
            "default curl configuration is not permitted",
        )
    token = environment.get("RAILWAY_EDGE_TOKEN", "")
    require(
        0 < len(token) <= 4096 and all(32 <= ord(char) < 127 for char in token),
        "edge credential is absent or malformed",
    )
    require(
        environment.get("RAILWAY_ORIGIN") == ORIGIN,
        "origin differs from fixed production target",
    )
    # No credential, proxy, curl config, or trust override reaches the subprocess environment.
    return {
        "PATH": environment.get("PATH", "/usr/bin:/bin"),
        "LANG": "C",
        "LC_ALL": "C",
    }


def identity(context):
    return dict(
        zip(
            IDENTITY_HEADERS,
            (
                context["runtime_source"],
                context["deployment_id"],
                "production",
            ),
        )
    )


def context_for(root, environment):
    request_raw = bounded(root / "request.json", 65536)
    request = json.loads(request_raw)
    proof = root / "proof"
    activation = bounded(proof / "activation.sha256", 128).decode().strip()
    replica = bounded(proof / "submission-replica-id.txt", 128).decode().strip()
    provider = json.loads(bounded(proof / "runtime.json", 512 * 1024))["data"][
        "deployment"
    ]
    controller = environment["GITHUB_SHA"]
    runtime = environment["RELEASE_SHA"]
    deployment = environment["DEPLOYMENT_ID"]
    require(
        all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (controller, runtime)),
        "exact controller and runtime sources are required",
    )
    require(
        re.fullmatch(r"[0-9a-f]{64}", activation) is not None,
        "activation pin is invalid",
    )
    require(
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            deployment,
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            replica,
        )
        is not None,
        "deployment/replica pin is invalid",
    )
    require(
        environment["GITHUB_REPOSITORY"] == "beepboop2025/seiche"
        and environment["GITHUB_REF"] == "refs/heads/main",
        "controller scope differs",
    )
    require(
        all(
            re.fullmatch(r"[1-9][0-9]*", environment[key])
            for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
        ),
        "run scope is invalid",
    )
    required_request = {
        "schema": "seiche.railway-recovery-export-request.v2",
        "repository": environment["GITHUB_REPOSITORY"],
        "workflow": "beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml",
        "commit": runtime,
        "deployment_id": deployment,
        "activation_receipt_sha256": activation,
        "confirmation": "EXPORT_WITHOUT_AUTHORITY_CHANGE",
    }
    require(
        all(request.get(key) == value for key, value in required_request.items()),
        "request/activation scope differs",
    )
    expected_provider = {
        "id": deployment,
        "projectId": environment["RAILWAY_PROJECT_ID"],
        "environmentId": environment["RAILWAY_ENVIRONMENT_ID"],
        "serviceId": environment["RAILWAY_SERVICE_ID"],
        "status": "SUCCESS",
    }
    require(
        all(provider.get(key) == value for key, value in expected_provider.items()),
        "provider deployment scope differs",
    )
    instances = provider.get("instances")
    require(
        isinstance(instances, list)
        and len(instances) == 1
        and instances[0].get("id") == replica
        and instances[0].get("status") == "RUNNING",
        "submission replica scope differs",
    )
    claim_path = proof / "source-health-claim.json"
    return {
        "repository": environment["GITHUB_REPOSITORY"],
        "ref": environment["GITHUB_REF"],
        "run_id": environment["GITHUB_RUN_ID"],
        "run_attempt": environment["GITHUB_RUN_ATTEMPT"],
        "controller_source": controller,
        "runtime_source": runtime,
        "deployment_id": deployment,
        "submission_replica_id": replica,
        "project_id": expected_provider["projectId"],
        "environment_id": expected_provider["environmentId"],
        "service_id": expected_provider["serviceId"],
        "activation_sha256": activation,
        "request_sha256": sha(request_raw),
        "source_health_claim_sha256": sha(bounded(claim_path, 1024 * 1024))
        if claim_path.exists()
        else None,
    }


def curl_probe(url, token, peer, environment, *, runner=subprocess.run):
    """The low-level transport; callers restrict URLs to the two fixed routes."""
    parsed = urlsplit(url)
    port = parsed.port or 443
    command = [
        "curl",
        "-q",
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--tlsv1.2",
        "--noproxy",
        parsed.hostname,
        "--connect-timeout",
        str(CONNECT_TIMEOUT),
        "--max-time",
        str(MAX_TIME),
        "--dump-header",
        "-",
        "--output",
        "/dev/null",
        "--write-out",
        WRITE_OUT,
    ]
    if peer is not None:
        address = "[" + peer + "]" if ":" in peer else peer
        command += ["--resolve", f"{parsed.hostname}:{port}:{address}"]
    if token:
        command += ["--header", "X-Seiche-Edge-Token: " + token]
    command.append(url)
    try:
        result = runner(
            command, env=environment, capture_output=True, timeout=MAX_TIME, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "000", "exit_code": -1, "error": "curl process failed"}
    # Do not persist stderr, full response headers, or credential-bearing argv.
    try:
        require(len(result.stdout) <= 65536, "curl metadata exceeds its bound")
        headers_raw, metadata = result.stdout.rsplit(META, 1)
        status, peer_ip, peer_port, tls_result, connect_status = (
            metadata.decode("ascii").strip().split("\t")
        )
        require(re.fullmatch(r"[0-9]{3}", status) is not None, "invalid HTTP status")
        blocks = [
            block
            for block in headers_raw.replace(b"\r\n", b"\n").strip().split(b"\n\n")
            if block
        ]
        headers = {}
        if blocks:
            require(len(blocks) == 1, "ambiguous response header blocks")
            lines = blocks[0].decode("iso-8859-1").splitlines()
            require(
                lines[0].startswith("HTTP/") and lines[0].split()[1] == status,
                "response status metadata differs",
            )
            for line in lines[1:]:
                require(
                    ":" in line and not line[0].isspace(), "malformed response headers"
                )
                name, value = line.split(":", 1)
                name = name.lower()
                if name in IDENTITY_HEADERS:
                    require(name not in headers, "duplicate identity header")
                    headers[name] = value.strip()
        return {
            "status": status,
            "exit_code": result.returncode,
            "remote_ip": peer_ip,
            "remote_port": int(peer_port),
            "tls_verify_result": int(tls_result),
            "http_connect": connect_status,
            "identity": headers,
        }
    except (TransportError, ValueError, UnicodeError, IndexError):
        return {
            "status": "000",
            "exit_code": result.returncode or -1,
            "error": "invalid curl metadata",
        }


def validate_probe(result, context, peer=None):
    require(result.get("exit_code") == 0, "curl transfer failed")
    require(result.get("status") == "200", "health status is not 200")
    require(
        result.get("remote_port") == 443 and result.get("http_connect") == "000",
        "connection is not the direct HTTPS target",
    )
    require(result.get("tls_verify_result") == 0, "TLS verification failed")
    address = global_peer(result.get("remote_ip"))
    require(peer is None or address == peer, "pinned peer changed")
    require(result.get("identity") == identity(context), "health identity differs")
    return address


def prepare(root, environment, *, probe=curl_probe):
    curl_environment = direct_environment(environment)
    context = context_for(root, environment)
    proof = root / "proof"
    path = proof / PIN_NAME
    require(
        not path.exists() and not path.is_symlink(), "continuity pins already exist"
    )
    peers = {}
    for name, url in ROUTES:
        token = environment["RAILWAY_EDGE_TOKEN"] if name == "origin" else ""
        discovered = probe(url, token, None, curl_environment)
        address = validate_probe(discovered, context)
        # Confirmation is only attempted after successful discovery, never as a retry.
        confirmed = probe(url, token, address, curl_environment)
        validate_probe(confirmed, context, address)
        peers[name] = {
            "url": url,
            "remote_ip": address,
            "remote_port": 443,
            "status": "200",
            "tls_verify_result": 0,
            "identity": identity(context),
            "confirmed_at": timestamp(),
        }
    value = {
        "schema": "seiche.recovery-continuity-transport.v1",
        "context": context,
        "peers": peers,
        "prepared_at": timestamp(),
        "connect_timeout_seconds": CONNECT_TIMEOUT,
        "max_time_seconds": MAX_TIME,
        "sample_sleep_seconds": SAMPLE_SLEEP,
        "proxy_environment_clear": True,
        "default_config_disabled": True,
        "retry_count": 0,
        "scope": "Per-run transport evidence; not recovery acceptance.",
    }
    raw = canonical(value)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return sha(raw)


def load_pins(root, environment, expected_sha):
    direct_environment(environment)
    raw = bounded(root / "proof" / PIN_NAME, 65536)
    require(
        re.fullmatch(r"[0-9a-f]{64}", expected_sha) is not None
        and sha(raw) == expected_sha,
        "continuity pin digest differs",
    )
    value = json.loads(raw)
    require(
        value["schema"] == "seiche.recovery-continuity-transport.v1"
        and value["context"] == context_for(root, environment),
        "continuity pin scope differs",
    )
    require(
        set(value["peers"]) == {name for name, _ in ROUTES}, "continuity routes differ"
    )
    for name, url in ROUTES:
        row = value["peers"][name]
        require(
            row["url"] == url
            and row["remote_port"] == 443
            and row["identity"] == identity(value["context"]),
            "continuity route identity differs",
        )
        global_peer(row["remote_ip"])
    return value


def sample(root, environment, pins, *, probe=curl_probe, observed_at=None):
    """Always retain both outcomes; a later success never clears failure."""
    curl_environment = direct_environment(environment)
    observed_at = observed_at or timestamp()
    outcomes = {}
    failed = False
    for name, url in ROUTES:
        token = environment["RAILWAY_EDGE_TOKEN"] if name == "origin" else ""
        peer = pins["peers"][name]["remote_ip"]
        result = probe(url, token, peer, curl_environment)
        try:
            validate_probe(result, pins["context"], peer)
            error = None
        except TransportError as failure:
            failed = True
            error = str(failure)
        outcomes[name] = {
            "status": result.get("status", "000"),
            "exit_code": result.get("exit_code", -1),
            "error": error,
        }
    proof = root / "proof"
    with (proof / "api-continuity.tsv").open("a", encoding="ascii") as stream:
        stream.write(
            f"{observed_at}\t{outcomes['origin']['status']}\t{outcomes['public']['status']}\n"
        )
    with (proof / "continuity-transport-samples.jsonl").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write(
            json.dumps(
                {"observed_at": observed_at, "outcomes": outcomes}, sort_keys=True
            )
            + "\n"
        )
    if failed:
        (proof / "api-continuity.failed").touch()


def monitor(root, environment, expected_sha, *, probe=curl_probe, sleep=time.sleep):
    proof = root / "proof"
    try:
        pins = load_pins(root, environment, expected_sha)
        while not (proof / "stop-continuity").exists():
            sample(root, environment, pins, probe=probe)
            sleep(SAMPLE_SLEEP)
    except Exception:
        (proof / "api-continuity.failed").touch()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "monitor"))
    parser.add_argument("--pins-sha256")
    args = parser.parse_args()
    root = Path.cwd()
    try:
        if args.operation == "prepare":
            require(args.pins_sha256 is None, "prepare does not accept existing pins")
            print(prepare(root, dict(os.environ)))
        else:
            require(
                args.pins_sha256 is not None, "monitor requires the prepared pin digest"
            )
            monitor(root, dict(os.environ), args.pins_sha256)
    except (TransportError, OSError, ValueError, KeyError, TypeError):
        # Deliberately do not expose argv, credentials, untrusted headers, or environment values.
        raise SystemExit("recovery continuity transport qualification failed") from None


if __name__ == "__main__":
    main()
