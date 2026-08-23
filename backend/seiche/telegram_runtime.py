"""Root supervisor for the dedicated Railway Telegram service."""

from __future__ import annotations

import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from seiche import telegram_migration as migration


HEALTH_SCHEMA = "seiche.railway-telegram-health.v1"
HEARTBEAT_SCHEMA = "seiche.railway-telegram-heartbeat.v1"


class TelegramRuntimeError(RuntimeError):
    """The Telegram service cannot prove a unique safe runtime state."""


class Health:
    def __init__(self, *, commit: str, deployment_id: str) -> None:
        self._lock = threading.Lock()
        self._value: dict[str, Any] = {
            "schema": HEALTH_SCHEMA,
            "status": "starting",
            "mode": "initializing",
            "commit": commit,
            "deployment_id": deployment_id,
            "request_id": None,
            "faults": ["initializing"],
        }

    def update(self, **values: Any) -> None:
        with self._lock:
            self._value.update(values)

    def response(self) -> tuple[int, bytes]:
        with self._lock:
            status = 200 if self._value.get("status") == "ready" else 503
            return status, migration.canonical(self._value)


def _health_server(port: int, health: Health) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/healthz":
                self.send_response(404)
                self.end_headers()
                return
            status, body = health.response()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_arguments: object) -> None:
            return

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def _documents(path: Path, pattern: re.Pattern[str]) -> list[Path]:
    try:
        entries = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TelegramRuntimeError(
            "Telegram authority directory is unavailable"
        ) from exc
    for entry in entries:
        if pattern.fullmatch(entry.name) is None:
            raise TelegramRuntimeError("Telegram authority directory is not closed")
    return entries


def _document_signature(
    paths: list[Path],
) -> tuple[tuple[str, int, int, int, int, int, int], ...]:
    try:
        value = []
        for path in paths:
            metadata = path.lstat()
            value.append(
                (
                    path.name,
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_nlink,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                )
            )
        return tuple(value)
    except OSError as exc:
        raise TelegramRuntimeError("Telegram authority document changed") from exc


def _load_transfer(
    path: Path,
    *,
    environment: Mapping[str, str],
    image: Mapping[str, Any],
    railway: Mapping[str, str],
) -> dict[str, Any]:
    _body, request = migration.load_document(path, label="Telegram transfer request")
    return migration.validate_transfer(
        request,
        image_request=image,
        railway=railway,
        require_fresh=False,
        expected_lab_channel_id=migration.lab_channel_identity(environment),
    )


def reconcile_candidates(
    environment: Mapping[str, str],
    *,
    root: Path,
    image: Mapping[str, Any],
    railway: Mapping[str, str],
) -> list[dict[str, Any]]:
    transfers = _documents(root / "transfers", re.compile(r"[0-9a-f]{64}\.json"))
    values: list[dict[str, Any]] = []
    for path in transfers:
        request = _load_transfer(
            path,
            environment=environment,
            image=image,
            railway=railway,
        )
        archive = root / "incoming" / f"{request['request_id']}.tgz"
        candidate = migration.restore_candidate(
            request,
            archive,
            environment,
            root=root,
        )
        migration.validate_candidate(
            candidate.receipt,
            request=request,
            railway=railway,
            state_root=candidate.state_root,
        )
        values.append(request)
    return values


def _authority_context(
    environment: Mapping[str, str],
    *,
    root: Path,
    image: Mapping[str, Any],
    railway: Mapping[str, str],
) -> dict[str, Any] | None:
    grants = _documents(root / "grants", re.compile(r"[0-9a-f]{64}\.json"))
    activations = _documents(
        root / "activations",
        re.compile(r"20[0-9]{6}T[0-9]{6}Z-[0-9a-f]{64}\.json"),
    )
    if len(grants) > 1 or len(activations) > 1:
        raise TelegramRuntimeError("Telegram authority history is ambiguous")
    if not grants:
        if activations:
            raise TelegramRuntimeError("Telegram activation exists without its grant")
        return None
    request_id = grants[0].stem
    _request_body, request = migration.load_document(
        root / "transfers" / f"{request_id}.json",
        label="Telegram transfer request",
    )
    migration.validate_transfer(
        request,
        image_request=image,
        railway=railway,
        require_fresh=False,
        expected_lab_channel_id=migration.lab_channel_identity(environment),
    )
    candidate_file = migration.candidate_path(root, request)
    _candidate_body, candidate = migration.load_document(
        candidate_file,
        label="Telegram candidate receipt",
    )
    state_root = migration.generation_path(root, request) / "seiche-bot"
    migration.validate_candidate(
        candidate,
        request=request,
        railway=railway,
    )
    migration.recover_live_state_temps(state_root)
    migration.validate_live_state(state_root, baseline=request["state"])
    _grant_body, grant = migration.load_document(
        grants[0], label="Telegram authority grant"
    )
    migration.validate_grant(
        grant,
        request=request,
        candidate=candidate,
        require_fresh=False,
    )
    token = environment.get("SEICHE_BOT_TOKEN", "")
    if (
        not token
        or hashlib.sha256(token.encode()).hexdigest() != grant["bot_token_sha256"]
    ):
        raise TelegramRuntimeError("Telegram runtime token differs from its grant")
    proof_path = root / "worker-proofs" / f"{request_id}.json"
    activation_path = (
        root / "activations" / (f"{request['snapshot_id']}-{request_id}.json")
    )
    if activations and activations[0] != activation_path:
        raise TelegramRuntimeError("Telegram activation identity is ambiguous")
    return {
        "request": request,
        "request_path": root / "transfers" / f"{request_id}.json",
        "candidate": candidate,
        "candidate_path": candidate_file,
        "grant": grant,
        "grant_path": grants[0],
        "state_root": state_root,
        "proof_path": proof_path,
        "activation_path": activation_path,
    }


def _finalize_if_proven(
    context: Mapping[str, Any],
    *,
    railway: Mapping[str, str],
    root: Path,
) -> dict[str, Any] | None:
    proof_path = context["proof_path"]
    if not proof_path.exists() and not proof_path.is_symlink():
        return None
    _proof_body, proof = migration.load_document(
        proof_path, label="Telegram worker proof"
    )
    migration.validate_worker_proof(
        proof,
        request=context["request"],
        candidate=context["candidate"],
        grant=context["grant"],
        railway=railway,
    )
    _require_state_offset(context, proof["observed_offset"])
    activation_path, activation = migration.finalize_activation(
        context["request"],
        context["candidate"],
        context["grant"],
        proof,
        root=root,
    )
    if activation_path != context["activation_path"]:
        raise TelegramRuntimeError("Telegram activation path differs")
    os.chown(proof_path, os.geteuid(), migration.RUNTIME_GID)
    os.chmod(proof_path, 0o440)
    os.chmod(root / "worker-proofs", 0o750)
    return activation


def _validate_existing_activation(
    context: Mapping[str, Any],
    *,
    railway: Mapping[str, str],
) -> dict[str, Any] | None:
    activation_path = context["activation_path"]
    if not activation_path.exists() and not activation_path.is_symlink():
        return None
    _proof_body, proof = migration.load_document(
        context["proof_path"], label="Telegram worker proof"
    )
    migration.validate_worker_proof(
        proof,
        request=context["request"],
        candidate=context["candidate"],
        grant=context["grant"],
        railway=railway,
    )
    _activation_body, activation = migration.load_document(
        activation_path, label="Telegram activation receipt"
    )
    activation = migration.validate_activation(
        activation,
        request=context["request"],
        candidate=context["candidate"],
        grant=context["grant"],
        proof=proof,
    )
    _require_state_offset(context, activation["state"]["observed_offset"])
    return activation


def _require_state_offset(context: Mapping[str, Any], minimum: int) -> None:
    observed = migration.inspect_state(context["state_root"])["offset"]
    if observed < minimum:
        raise TelegramRuntimeError("Telegram state predates its proven offset")


def _has_fresh_worker_heartbeat(
    context: Mapping[str, Any],
    *,
    railway: Mapping[str, str],
    root: Path,
    not_before: datetime,
) -> bool:
    path = root / "runtime" / (f"{context['request']['request_id']}.heartbeat.json")
    if not path.exists() and not path.is_symlink():
        return False
    _body, heartbeat = migration.load_document(path, label="Telegram worker heartbeat")
    expected_keys = {
        "schema",
        "commit",
        "deployment_id",
        "request_id",
        "observed_at",
        "offset",
        "mode",
        "faults",
    }
    if (
        set(heartbeat) != expected_keys
        or heartbeat.get("schema") != HEARTBEAT_SCHEMA
        or heartbeat.get("commit") != context["request"]["commit"]
        or heartbeat.get("deployment_id") != railway["deployment_id"]
        or heartbeat.get("request_id") != context["request"]["request_id"]
        or heartbeat.get("mode") != "production"
        or heartbeat.get("faults") != []
        or not isinstance(heartbeat.get("offset"), int)
        or isinstance(heartbeat.get("offset"), bool)
        or heartbeat["offset"] < context["request"]["state"]["offset"]
    ):
        raise TelegramRuntimeError("Telegram worker heartbeat is invalid")
    observed = migration._utc(
        heartbeat.get("observed_at"), label="Telegram heartbeat observed_at"
    )
    now = datetime.now(UTC).replace(microsecond=0)
    if observed > now + timedelta(seconds=5):
        raise TelegramRuntimeError("Telegram worker heartbeat is in the future")
    age = now - observed
    return observed >= not_before and timedelta(0) <= age <= timedelta(minutes=2)


def _worker_environment(
    environment: Mapping[str, str], context: Mapping[str, Any], *, root: Path
) -> dict[str, str]:
    value = dict(environment)
    value.update(
        {
            "SEICHE_TELEGRAM_TRANSFER_PATH": str(context["request_path"]),
            "SEICHE_TELEGRAM_CANDIDATE_PATH": str(context["candidate_path"]),
            "SEICHE_TELEGRAM_GRANT_PATH": str(context["grant_path"]),
            "SEICHE_BOT_STATE": str(context["state_root"]),
            "SEICHE_TELEGRAM_WORKER_PROOF_PATH": str(context["proof_path"]),
            "SEICHE_TELEGRAM_HEARTBEAT_PATH": str(
                root / "runtime" / f"{context['request']['request_id']}.heartbeat.json"
            ),
        }
    )
    return value


def _spawn_worker(
    environment: Mapping[str, str], context: Mapping[str, Any], *, root: Path
) -> subprocess.Popen[bytes]:
    proof_dir = root / "worker-proofs"
    if not context["activation_path"].exists():
        os.chmod(proof_dir, 0o770)
    child = subprocess.Popen(
        [sys.executable, "-m", "seiche.telegram_worker"],
        cwd="/workspace",
        env=_worker_environment(environment, context, root=root),
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        user=migration.RUNTIME_UID,
        group=migration.RUNTIME_GID,
        extra_groups=[migration.RUNTIME_GID],
    )
    time.sleep(1)
    if child.poll() is not None:
        raise TelegramRuntimeError("Telegram worker exited during startup")
    return child


def _stop_worker(worker: subprocess.Popen[bytes] | None, signum: int) -> None:
    if worker is None or worker.poll() is not None:
        return
    try:
        os.killpg(worker.pid, signum)
    except ProcessLookupError:
        return


def _terminate_worker(worker: subprocess.Popen[bytes] | None) -> None:
    if worker is None:
        return
    _stop_worker(worker, signal.SIGTERM)
    if worker.poll() is None:
        try:
            worker.wait(timeout=40)
        except subprocess.TimeoutExpired:
            _stop_worker(worker, signal.SIGKILL)
            worker.wait()


def run(
    environment: Mapping[str, str] | None = None,
    *,
    root: Path = migration.ROOT,
    poll_seconds: int = 2,
) -> int:
    if not 1 <= poll_seconds <= 30:
        raise TelegramRuntimeError("Telegram supervisor poll interval is invalid")
    if os.geteuid() != 0 or os.getegid() != 0:
        raise TelegramRuntimeError("Telegram supervisor must start as root")
    env = dict(environment or os.environ)
    _image_body, image = migration.image_context(env)
    railway = migration.railway_identity(env)
    migration.lab_channel_identity(env)
    port_text = env.get("PORT", "")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise TelegramRuntimeError("Telegram health port is invalid")
    migration.prepare_root(root)
    health = Health(commit=str(image["commit"]), deployment_id=railway["deployment_id"])
    server = _health_server(int(port_text), health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stopping = False
    worker: subprocess.Popen[bytes] | None = None
    context: dict[str, Any] | None = None
    activation: dict[str, Any] | None = None
    heartbeat_not_before: datetime | None = None
    candidate_requests: list[dict[str, Any]] = []
    candidate_signature: tuple[tuple[str, int, int, int, int, int, int], ...] | None = (
        None
    )

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        _stop_worker(worker, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            if context is None:
                grants = _documents(root / "grants", re.compile(r"[0-9a-f]{64}\.json"))
                if not grants:
                    transfers = _documents(
                        root / "transfers", re.compile(r"[0-9a-f]{64}\.json")
                    )
                    observed_signature = _document_signature(transfers)
                    if observed_signature != candidate_signature:
                        candidate_requests = reconcile_candidates(
                            env,
                            root=root,
                            image=image,
                            railway=railway,
                        )
                        candidate_signature = observed_signature
                context = _authority_context(
                    env,
                    root=root,
                    image=image,
                    railway=railway,
                )
                if context is None:
                    pending_request_id = (
                        candidate_requests[-1]["request_id"]
                        if candidate_requests
                        else None
                    )
                    health.update(
                        status="ready",
                        mode="candidate",
                        request_id=pending_request_id,
                        faults=[],
                    )
                    time.sleep(poll_seconds)
                    continue
                activation = _validate_existing_activation(context, railway=railway)
                request_id = context["request"]["request_id"]
                health.update(
                    status="starting",
                    mode="granted",
                    request_id=request_id,
                    faults=["worker-heartbeat-pending"],
                )
                worker = _spawn_worker(env, context, root=root)
                heartbeat_not_before = datetime.now(UTC).replace(
                    microsecond=0
                ) + timedelta(seconds=1)
            if worker is None or heartbeat_not_before is None:
                raise TelegramRuntimeError("Telegram worker lifecycle is incomplete")
            if worker.poll() is not None:
                return worker.returncode or 1
            heartbeat_ready = _has_fresh_worker_heartbeat(
                context,
                railway=railway,
                root=root,
                not_before=heartbeat_not_before,
            )
            if activation is None and heartbeat_ready:
                activation = _finalize_if_proven(
                    context,
                    railway=railway,
                    root=root,
                )
            request_id = context["request"]["request_id"]
            production_ready = activation is not None and heartbeat_ready
            health.update(
                status="ready" if production_ready else "starting",
                mode="production" if production_ready else "granted",
                request_id=request_id,
                faults=[] if production_ready else ["worker-heartbeat-pending"],
            )
            time.sleep(poll_seconds)
    finally:
        _terminate_worker(worker)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (
        TelegramRuntimeError,
        migration.TelegramMigrationError,
        json.JSONDecodeError,
    ) as error:
        print(f"seiche Railway Telegram runtime: {error}", file=sys.stderr)
        raise SystemExit(1) from None
