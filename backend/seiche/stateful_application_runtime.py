"""Root supervisor for signed application updates on existing Railway state."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import tempfile
import time
from typing import Any, Mapping

from seiche import stateful_application as application
from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration


def _seal(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        if application.read_document(path) != value:
            raise application.ApplicationContractError(
                "application receipt already differs"
            )
    else:
        migration._write_receipt(path, value, gid=migration.RUNTIME_GID)


def _pointer(path: Path, value: Mapping[str, Any]) -> None:
    """Durably replace a root-owned authority pointer on the same filesystem."""
    if path.is_symlink():
        raise application.ApplicationContractError(
            "application authority pointer is unsafe"
        )
    descriptor, stage = tempfile.mkstemp(prefix=".application-active-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(application.canonical(dict(value)))
            handle.flush()
            os.fchmod(handle.fileno(), 0o440)
            os.fchown(handle.fileno(), os.geteuid(), migration.RUNTIME_GID)
            os.fsync(handle.fileno())
        os.replace(stage, path)
        migration._fsync_directory(path.parent)
    finally:
        Path(stage).unlink(missing_ok=True)


def accept_grant(
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    envelope: Mapping[str, Any],
    parent: Mapping[str, Any],
    *,
    platform: Path,
) -> Path:
    """Retire predecessor authority before durably accepting a successor grant."""
    authority = platform / "authority"
    accepted = authority / "application-grants" / f"{request['request_id']}.json"
    if accepted.exists():
        if application.read_document(accepted) != envelope:
            raise application.ApplicationContractError(
                "accepted application grant differs"
            )
        application.validate_grant(
            envelope, request=request, candidate=candidate, current=False
        )
        active = application.read_document(authority / "application-active.json")
        if active != pointer_value(
            request, candidate, envelope, active.get("state")
        ) or active.get("state") not in {"pending", "active"}:
            raise application.ApplicationContractError(
                "accepted application was superseded"
            )
        return accepted
    application.validate_grant(envelope, request=request, candidate=candidate)
    predecessor = parent["activation"]
    active_path = authority / "application-active.json"
    pending = pointer_value(request, candidate, envelope, "pending")
    if active_path.exists():
        active = application.read_document(active_path)
        old = {
            "request_id": predecessor["request_id"],
            "commit": predecessor["commit"],
            "deployment_id": predecessor["railway"]["deployment_id"],
            "grant_sha256": predecessor["grant_sha256"],
            "state": "active",
        }
        if active not in (old, pending):
            raise application.ApplicationContractError(
                "application predecessor is no longer active"
            )
    elif predecessor["schema"] == application.ACTIVATION_SCHEMA:
        raise application.ApplicationContractError(
            "application predecessor pointer is absent"
        )
    _pointer(active_path, pending)
    if predecessor["schema"] == cutover.ACTIVATION_RECEIPT_SCHEMA:
        old_grant = authority / "activation-grant.json"
        retired = authority / "superseded" / f"{predecessor['grant_sha256']}.json"
        if old_grant.exists():
            if (
                retired.exists()
                or application.digest(application.read_document(old_grant))
                != predecessor["grant_sha256"]
            ):
                raise application.ApplicationContractError(
                    "migration predecessor grant differs"
                )
            os.replace(old_grant, retired)
            migration._fsync_directory(retired.parent)
            migration._fsync_directory(authority)
        elif (
            not retired.exists()
            or application.digest(application.read_document(retired))
            != predecessor["grant_sha256"]
        ):
            raise application.ApplicationContractError(
                "migration predecessor retirement is unproven"
            )
    # A crash before this immutable write has not accepted new write authority.
    # After it, only this request/deployment may resume, including after expiry.
    _seal(accepted, envelope)
    return accepted


def pointer_value(request, candidate, grant, state):
    return {
        "request_id": request["request_id"],
        "commit": request["commit"],
        "deployment_id": candidate["railway"]["deployment_id"],
        "grant_sha256": application.digest(dict(grant)),
        "state": state,
    }


def audit_current_state(request, parent, dsn):
    """Inspect live state in place. This code has no restore or database-create path."""
    generation = (
        migration.PLATFORM_ROOT / "generations" / request["parent"]["generation"]
    )
    # The latest recovery supplies a newer Agent Room/count floor; the original
    # candidate continues to bind the immutable NBS/Palimpsest trees and paths.
    baseline = {
        **parent["candidate"],
        "filesystem": {
            **parent["candidate"]["filesystem"],
            "agent_room_audit": parent["recovery"]["filesystem"]["agent_room_audit"],
        },
    }
    audit = migration.validate_active_generation(generation, baseline)
    counts = list(migration.inspect_postgres_counts(dsn))
    floor = parent["recovery"]["snapshot"]["critical_table_count_floor"]
    if any(observed < minimum for observed, minimum in zip(counts, floor, strict=True)):
        raise application.ApplicationContractError(
            "current data regressed behind recovery"
        )
    return {
        "generation": request["parent"]["generation"],
        "database": request["parent"]["database"],
        "critical_table_counts": counts,
        "agent_room_audit": audit,
        "restored_from_backup": False,
    }


def runtime_environment(base, request, parent, dsn):
    original = parent["candidate"]
    restore = cutover.CutoverRestore(
        original,
        dsn,
        migration.PLATFORM_ROOT
        / "cutover-receipts"
        / f"{original['request']['id']}.candidate.json",
        migration.PLATFORM_ROOT / "generations" / request["parent"]["generation"],
    )
    environment = cutover.candidate_environment(
        base,
        restore,
        edge_token=base.get("SEICHE_RAILWAY_EDGE_TOKEN", ""),
    )
    environment.update(
        {
            "SEICHE_RELEASE_SHA": request["commit"],
            "SEICHE_RAILWAY_APPLICATION_REQUEST_ID": request["request_id"],
            "SEICHE_RAILWAY_CUTOVER_REQUEST_ID": request["request_id"],
            # Match the bounded numerical worker configuration of the source host.
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    return environment


def render_activation(request, candidate, grant, parent, started):
    payload = application.validate_grant(
        grant, request=request, candidate=candidate, current=False
    )
    return {
        "schema": application.ACTIVATION_SCHEMA,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "candidate_receipt_sha256": request["parent"]["candidate_sha256"],
        "fence_sha256": parent["migration_activation"]["fence_sha256"],
        "grant_sha256": application.digest(grant),
        "railway": candidate["railway"],
        "authority": application.PRODUCTION_AUTHORITY,
        "workers": {
            name: {"command": command, "process_started": True}
            for name, command in cutover.worker_commands().items()
        },
        "public": {
            "base_url": payload["public_base_url"],
            "probe_sha256": payload["public_probe_sha256"],
        },
        "activated_at": payload["requested_at"],
        "workers_started_at": started,
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
        "application": {
            "request": dict(request),
            "candidate": dict(candidate),
            "grant": dict(grant),
            "migration_activation": parent["migration_activation"],
        },
    }


def _activate(environment, request, candidate, grant, parent, activation_path, started):
    payload = application.validate_grant(
        grant, request=request, candidate=candidate, current=False
    )
    if payload["edge_token_sha256"] != environment["SEICHE_RAILWAY_EDGE_TOKEN_SHA256"]:
        raise application.ApplicationContractError(
            "application grant edge token differs"
        )
    audit_current_state(request, parent, environment["SEICHE_DATABASE_URL"])
    accept_grant(request, candidate, grant, parent, platform=migration.PLATFORM_ROOT)
    writers = []
    api = None
    try:
        writers = cutover._start_writer_children(
            cutover.writer_environment(environment),
            cutover.worker_commands(),
            poll_seconds=2,
        )
        activation = render_activation(
            request, candidate, grant, parent, migration._iso_now()
        )
        application.validate_activation(activation)
        _seal(activation_path, activation)
        _pointer(
            migration.PLATFORM_ROOT / "authority" / "application-active.json",
            pointer_value(request, candidate, grant, "active"),
        )
        production = cutover.production_environment(
            environment, activation, receipt_path=activation_path
        )
        application.validate_runtime(production, production=True)
        cutover._emit_stateful_log_result(
            activation,
            kind="activation",
            lifecycle="created",
            request_id=request["request_id"],
            environment=production,
            runtime_started_at=started,
        )
        api = cutover._spawn(
            cutover.api_command(production.get("PORT", "")), production
        )
    except BaseException:
        cutover._terminate_children([*writers, *([api] if api else [])])
        raise
    return cutover._serve_production(
        production,
        writers=writers,
        api=api,
        commands=cutover.worker_commands(),
        poll_seconds=2,
        runtime_started_at=started,
    )


def _run_locked(request, railway):
    platform = migration.PLATFORM_ROOT
    uid = request["request_id"]
    approvals = platform / "application-approvals"
    receipts = platform / "cutover-receipts"
    authority = platform / "authority"
    for directory in (
        approvals,
        receipts,
        authority / "superseded",
        authority / "application-grants",
    ):
        cutover._prepare_authority_directory(directory)
    accepted_path = authority / "application-grants" / f"{uid}.json"
    activation_path = receipts / f"{uid}.activation.json"
    candidate_path = receipts / f"{uid}.application-candidate.json"
    accepted = accepted_path.exists()
    parent = application.load_parent(request, current=not accepted)
    current_parent = (
        receipts / f"{request['parent']['activation_request_id']}.activation.json"
    )
    if (
        application.digest(application.read_document(current_parent))
        != request["parent"]["activation_sha256"]
    ):
        raise application.ApplicationContractError("volume parent activation differs")
    base_dsn = os.environ.get("DATABASE_URL", "")
    if not base_dsn:
        raise application.ApplicationContractError(
            "application PostgreSQL URL is absent"
        )
    dsn = migration._target_dsn(base_dsn, request["parent"]["database"])
    environment = runtime_environment(os.environ, request, parent, dsn)
    started = migration._iso_now()
    if activation_path.exists():
        if not accepted:
            raise application.ApplicationContractError(
                "application activation exists without grant"
            )
        activation = application.validate_activation(
            application.read_document(activation_path)
        )
        grant = application.read_document(accepted_path)
        if (
            activation["application"]["grant"] != grant
            or activation["application"]["request"] != request
        ):
            raise application.ApplicationContractError(
                "application resume grant differs"
            )
        accept_grant(
            request,
            activation["application"]["candidate"],
            grant,
            parent,
            platform=platform,
        )
        _pointer(
            authority / "application-active.json",
            pointer_value(
                request, activation["application"]["candidate"], grant, "active"
            ),
        )
        production = cutover.production_environment(
            environment, activation, receipt_path=activation_path
        )
        application.validate_runtime(production, production=True)
        audit_current_state(request, parent, dsn)
        cutover._emit_stateful_log_result(
            activation,
            kind="activation",
            lifecycle="reused",
            request_id=uid,
            environment=production,
            runtime_started_at=started,
        )
        return cutover.supervise_production(production, runtime_started_at=started)
    stopping = False
    api = None

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        if api is not None:
            cutover._stop_children([api], signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if candidate_path.exists():
            candidate = application.validate_candidate(
                application.read_document(candidate_path),
                request=request,
                current=not accepted,
            )
        else:
            fence_path = approvals / f"{uid}.source-stopped.json"
            print(
                "seiche application: waiting for signed stopped-source proof",
                flush=True,
            )
            while not fence_path.exists() and not stopping:
                application.validate_request(request)
                time.sleep(2)
            if stopping:
                return 0
            fence = application.read_document(fence_path)
            application.validate_source_fence(fence, request=request)
            data = audit_current_state(request, parent, dsn)
            candidate = {
                "schema": application.CANDIDATE_SCHEMA,
                "request": {
                    "id": uid,
                    "sha256": application.digest(request),
                    "commit": request["commit"],
                    "tree": request["tree"],
                },
                "railway": railway,
                "source_fence": fence,
                "data": data,
                "authority": application.CANDIDATE_AUTHORITY,
                "validated_at": migration._iso_now(),
                "research_only": True,
                "can_publish": False,
                "can_execute": False,
            }
            application.validate_candidate(candidate, request=request, current=True)
            _seal(candidate_path, candidate)
        application.validate_runtime(environment, production=False)
        if accepted:
            return _activate(
                environment,
                request,
                candidate,
                application.read_document(accepted_path),
                parent,
                activation_path,
                started,
            )
        home = Path("/tmp/seiche-home")
        home.mkdir(mode=0o700, exist_ok=True)
        os.chown(home, migration.RUNTIME_UID, migration.RUNTIME_GID)
        api = cutover._spawn(
            cutover.api_command(environment.get("PORT", "")), environment
        )
        cutover._emit_stateful_log_result(
            candidate,
            kind="candidate",
            lifecycle="created",
            request_id=uid,
            environment=environment,
            runtime_started_at=started,
        )
        grant_path = approvals / f"{uid}.activate.json"
        print(
            "seiche application: current-state candidate serving read-only", flush=True
        )
        while not stopping:
            if api.poll() is not None:
                return api.returncode or 1
            if grant_path.exists():
                grant = application.read_document(grant_path)
                application.validate_grant(grant, request=request, candidate=candidate)
                cutover._terminate_children([api])
                api = None
                return _activate(
                    environment,
                    request,
                    candidate,
                    grant,
                    parent,
                    activation_path,
                    started,
                )
            application.validate_request(request)
            time.sleep(2)
    finally:
        if api is not None:
            cutover._terminate_children([api])
    return 0


def run() -> int:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise application.ApplicationContractError(
            "application supervisor must start as root"
        )
    request = application.load_request()
    railway = migration.railway_identity(os.environ)
    if {k: v for k, v in railway.items() if k != "deployment_id"} != request[
        "railway"
    ] or railway["deployment_id"] == request["parent"]["deployment_id"]:
        raise application.ApplicationContractError(
            "application provider target differs"
        )
    authority = migration.PLATFORM_ROOT / "authority"
    cutover._prepare_authority_directory(authority)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(authority / "application-runtime.lock", flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_locked(request, railway)
    finally:
        os.close(descriptor)
