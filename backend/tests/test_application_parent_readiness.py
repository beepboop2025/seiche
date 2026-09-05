"""Candidate cache hydration preserves the signed parent's state and authority."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import os

import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble, mcp_server, repository, store
from seiche import stateful_application as app
from seiche import stateful_application_runtime as runtime
from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery
from test_api_caching import _release_receipt
from test_railway_stateful_recovery import _request
from test_stateful_application import iso, signed, transition  # noqa: F401


@pytest.fixture
def parent_candidate(transition, signed, monkeypatch, fake_snap):  # noqa: F811
    platform, base, request, candidate, _, parent = transition
    now = datetime.now(UTC).replace(microsecond=0)
    recovery_request = _request(parent["activation"], now=now)
    monkeypatch.setattr(
        migration, "inspect_postgres_counts", lambda _: (10, 20, 30, 40)
    )
    monkeypatch.setattr(migration, "_audit_nbs", lambda _: "verified_head")

    def dump(path, _dsn):
        path.write_bytes(b"PGDMP" + b"x" * 2048)
        return (10, 20, 30, 40)

    monkeypatch.setattr(recovery, "_snapshot_postgres", dump)
    exported = recovery.export_snapshot(
        base,
        recovery_request,
        platform_root=platform,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    _, receipt = recovery.finalize_receipt(
        base,
        recovery_request,
        exported,
        writers_stopped_at=exported.started_at,
        writers_restarted_at=iso(datetime.now(UTC) + timedelta(seconds=1)),
        worker_commands=cutover.worker_commands(),
        platform_root=platform,
        runtime_gid=os.getegid(),
    )
    sealed_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=3)
    digests = {
        "activation-receipt.json": receipt["activation_receipt_sha256"],
        "candidate-receipt.json": receipt["candidate_receipt_sha256"],
        "shadow-receipt.json": receipt["shadow_receipt_sha256"],
        "request.json": receipt["request_sha256"],
        "recovery-receipt.json": app.digest(receipt),
        "SHA256SUMS": receipt["snapshot"]["inventory_sha256"],
        "proof/reverse-restore.json": "e" * 64,
        **receipt["snapshot"]["member_sha256"],
    }
    key_root = (
        f"seiche/recovery/{recovery_request['snapshot_id']}/"
        f"{recovery_request['request_id']}"
    )
    offsite = {
        "schema": recovery.OFFSITE_RECEIPT_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": recovery.WORKFLOW,
        "commit": receipt["commit"],
        "request_id": recovery_request["request_id"],
        "snapshot_id": recovery_request["snapshot_id"],
        "recovery_receipt_sha256": app.digest(receipt),
        "reverse_restore_proof_sha256": "e" * 64,
        "palimpsest_china_state": receipt["palimpsest_china_state"],
        "bucket": "seiche-recovery-evidence",
        "prefix": "seiche/recovery",
        "object_lock_mode": "COMPLIANCE",
        "retain_until": iso(sealed_at + timedelta(days=30)),
        "objects": {
            name: {
                "key": f"{key_root}/{name}",
                "sha256": digest,
                "size": 1024,
                "version_id": f"version-{index}",
            }
            for index, (name, digest) in enumerate(digests.items())
        },
        "sealed_at": iso(sealed_at),
        "authority_changed": False,
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    parent.update(
        {"recovery-request": recovery_request, "recovery": receipt, "offsite": offsite}
    )
    for name, field in (
        ("recovery-request", "recovery_request_sha256"),
        ("recovery", "recovery_sha256"),
        ("offsite", "offsite_sha256"),
    ):
        request["parent"][field] = app.digest(parent[name])
    request.pop("request_id")
    request["request_id"] = app.digest(request)
    candidate["request"].update(id=request["request_id"], sha256=app.digest(request))
    fence = deepcopy(candidate["source_fence"]["payload"])
    fence["request_id"] = request["request_id"]
    candidate["source_fence"] = signed("source_stopped", fence)
    image = platform.parent / "image"
    (image / "parent").mkdir(parents=True)
    for name, value in parent.items():
        if name != "migration_activation":
            (image / "parent" / f"{name}.json").write_bytes(app.canonical(value))
    (image / "request.json").write_bytes(app.canonical(request))
    monkeypatch.setattr(app, "REQUEST_PATH", image / "request.json")
    monkeypatch.setattr(
        cutover.candidate_environment,
        "__kwdefaults__",
        {
            **cutover.candidate_environment.__kwdefaults__,
            "runtime_uid": os.geteuid(),
            "runtime_gid": os.getegid(),
        },
    )
    base["RAILWAY_DEPLOYMENT_ID"] = candidate["railway"]["deployment_id"]
    environment = runtime.runtime_environment(
        base,
        request,
        parent,
        migration._target_dsn(
            "postgresql://test/postgres", request["parent"]["database"]
        ),
    )
    candidate_path = (
        platform
        / "cutover-receipts"
        / f"{request['request_id']}.application-candidate.json"
    )
    candidate_path.write_bytes(app.canonical(candidate))
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        assemble, "capture_process_release_sha", lambda: request["commit"]
    )
    monkeypatch.setattr(assemble, "_cache", {"payload": None, "source": None, "at": 0})
    monkeypatch.setattr(
        assemble, "STATIC_SNAPSHOT_PATH", platform / "absent-static.json"
    )
    monkeypatch.setattr(mcp_server, "agent_room_release_ready", lambda: True)
    snapshot = deepcopy(fake_snap)
    snapshot["version"] = "parent release"
    snapshot["faults"] = []
    envelope = assemble._build_handoff(
        snapshot, _release_receipt(snapshot), request["parent"]["commit"]
    )
    pg = repository.PostgresMarketRepository(environment["SEICHE_DATABASE_URL"])
    calls = []

    def read_only():
        calls.append("read_only")
        return deepcopy(envelope)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("candidate must not converge, refresh, write or activate")

    monkeypatch.setattr(pg, "load_active_release_handoff_read_only", read_only)
    monkeypatch.setattr(pg, "_ensure_schema", forbidden)
    monkeypatch.setattr(pg, "load_active_release_handoff", forbidden)
    monkeypatch.setattr(pg, "activate_release_handoff", forbidden)
    monkeypatch.setattr(repository, "get_repository", lambda: pg)
    monkeypatch.setattr(store, "load_blob", forbidden)
    monkeypatch.setattr(assemble, "_build_snapshot", forbidden)
    monkeypatch.setattr(assemble, "_seal_release_evidence", forbidden)
    return platform, environment, request, candidate_path, envelope, calls


def test_signed_parent_hydrates_candidate_health_without_rebuild_or_writes(
    parent_candidate,
):
    platform, environment, request, _, envelope, calls = parent_candidate
    before = {str(p): p.read_bytes() for p in platform.rglob("*") if p.is_file()}
    assert assemble.restore_cached_snapshot(read_only=True) == "application_parent"
    assert calls == ["read_only"]
    assert assemble.cached_snapshot() == envelope["payload"]
    assert assemble._cache["producer_sha"] == request["parent"]["commit"]
    assert assemble._cache["release_handoff_id"] == envelope["handoff_id"]
    assert assemble._cache["release_receipt"] == envelope["release_receipt"]
    assert assemble.cached_application_parent_ready()
    assert not assemble.cached_snapshot_was_rebuilt()
    assert assemble.cached_snapshot_release_handoff() is None
    with TestClient(
        api.app, headers={cutover.EDGE_HEADER: environment["SEICHE_RAILWAY_EDGE_TOKEN"]}
    ) as client:
        ready = client.get("/healthz")
        assert ready.status_code == 200, ready.text
        assert ready.json()["version"] == "parent release"
        assert ready.json()["generated_at"] == envelope["payload"]["generated_at"]
        health = client.get("/api/health")
        assert health.json()["faults"] == []
        assert health.headers["X-Seiche-Release-SHA"] == request["commit"]
        assert health.headers["X-Seiche-Railway-Authority"] == "candidate"
        assert client.get("/api/health?require_rebuilt=true").status_code == 503
        assert client.get("/api/internal/v1/release-health").status_code == 503
    after = {str(p): p.read_bytes() for p in platform.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize(
    "change", ["signature", "parent", "recovery", "process", "producer"]
)
def test_candidate_hydration_rejects_broken_parent_binding(
    parent_candidate, monkeypatch, change
):
    _, _, _, candidate_path, envelope, calls = parent_candidate
    if change == "signature":
        candidate = app.read_document(candidate_path)
        candidate["source_fence"]["signature"] = "unsigned"
        candidate_path.write_bytes(app.canonical(candidate))
    elif change in {"parent", "recovery"}:
        name = "activation" if change == "parent" else "recovery"
        path = app.REQUEST_PATH.parent / "parent" / f"{name}.json"
        value = app.read_document(path)
        value["commit"] = "f" * 40
        path.write_bytes(app.canonical(value))
    elif change == "process":
        monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: "f" * 40)
    else:
        envelope.update(
            assemble._build_handoff(
                envelope["payload"], envelope["release_receipt"], "f" * 40
            )
        )
    assert assemble.restore_cached_snapshot(read_only=True) is None
    assert calls == (["read_only"] if change == "producer" else [])
    assert not assemble.cached_application_parent_ready()


@pytest.mark.parametrize(
    "change",
    ["payload", "receipt", "handoff", "signature", "production", "shadow", "legacy"],
)
def test_borrowed_cache_cannot_outlive_its_binding_or_grant_other_readiness(
    parent_candidate, monkeypatch, change
):
    _, _, _, candidate_path, _, _ = parent_candidate
    assert assemble.restore_cached_snapshot(read_only=True) == "application_parent"
    if change == "payload":
        assemble._cache["payload"]["version"] = "substituted"
    elif change == "receipt":
        assemble._cache["release_receipt"]["products"]["overview"]["snapshot_id"] = (
            "f" * 64
        )
    elif change == "handoff":
        assemble._cache["release_handoff_id"] = "f" * 64
    elif change == "signature":
        candidate = app.read_document(candidate_path)
        candidate["source_fence"]["signature"] = "unsigned"
        candidate_path.write_bytes(app.canonical(candidate))
    elif change == "legacy":
        monkeypatch.delenv("SEICHE_RAILWAY_APPLICATION_REQUEST_ID")
    else:
        monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", change)
    assert not assemble.cached_application_parent_ready()
    assert not assemble.cached_snapshot_was_rebuilt()
    assert TestClient(api.app).get("/healthz").status_code == 503


def test_parent_loader_binds_a_read_only_transaction_without_schema_convergence(
    monkeypatch,
):
    pg = repository.PostgresMarketRepository("postgresql://test/fixture")
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            calls.append("close")

        @contextmanager
        def transaction(self):
            calls.append("begin")
            yield
            calls.append("end")

        def execute(self, sql):
            if sql == "SET TRANSACTION READ ONLY":
                assert calls == ["begin"]
                calls.append("read_only")
            else:
                assert calls == ["begin", "read_only"]
                assert sql.strip().startswith("SELECT handoff.envelope")
                calls.append("select")
            return self

        def fetchone(self):
            return ('{"fixture":true}',)

    monkeypatch.setattr(pg, "_connect", Connection)
    monkeypatch.setattr(pg, "_ensure_schema", lambda: pytest.fail("DDL forbidden"))
    assert pg.load_active_release_handoff_read_only() == {"fixture": True}
    assert calls == ["begin", "read_only", "select", "end", "close"]
    assert not pg._initialized


@pytest.mark.skipif(
    not os.getenv("SEICHE_TEST_POSTGRES_URL"),
    reason="SEICHE_TEST_POSTGRES_URL is not configured",
)
def test_parent_loader_postgres_rejects_writes_inside_its_actual_transaction(
    monkeypatch,
):
    from uuid import uuid4

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    base_dsn = os.environ["SEICHE_TEST_POSTGRES_URL"]
    schema = "parent_readiness_" + uuid4().hex
    with psycopg.connect(base_dsn, autocommit=True) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(base_dsn, options=f"-c search_path={schema}")
    try:
        with psycopg.connect(dsn) as setup:
            setup.execute(
                "CREATE TABLE release_snapshot_handoffs (handoff_id text, envelope jsonb)"
            )
            setup.execute(
                "CREATE TABLE active_release_snapshot_handoff (singleton int, handoff_id text)"
            )
            setup.execute(
                "INSERT INTO release_snapshot_handoffs VALUES ('parent', '{\"fixture\": true}')"
            )
            setup.execute(
                "INSERT INTO active_release_snapshot_handoff VALUES (1, 'parent')"
            )
        pg = repository.PostgresMarketRepository(dsn)
        connect = pg._connect
        checked = []

        class ReadOnlyProbe:
            def __enter__(self):
                self.connection = connect().__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def transaction(self):
                return self.connection.transaction()

            def execute(self, query):
                if query.lstrip().startswith("SELECT"):
                    assert self.connection.execute(
                        "SHOW transaction_read_only"
                    ).fetchone() == ("on",)
                    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                        with self.connection.transaction():
                            self.connection.execute(
                                "DELETE FROM active_release_snapshot_handoff"
                            )
                    checked.append(True)
                return self.connection.execute(query)

        monkeypatch.setattr(pg, "_connect", ReadOnlyProbe)
        monkeypatch.setattr(pg, "_ensure_schema", lambda: pytest.fail("DDL forbidden"))
        assert pg.load_active_release_handoff_read_only() == {"fixture": True}
        assert checked == [True]
        assert not pg._initialized
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as cleanup:
            cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
