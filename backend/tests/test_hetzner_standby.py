"""A restored standby cannot become a writer or relabel its parent's evidence."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import time
import urllib.error
import urllib.request

from fastapi.testclient import TestClient
import pytest
from psycopg.conninfo import make_conninfo

from seiche import api, assemble, mcp_server, repository, standby, store
from seiche.stateful_migration import absent_agent_room_audit
from test_api_caching import _release_receipt


def seal(path: Path, value: dict) -> str:
    body = standby.canonical(value) + b"\n"
    path.write_bytes(body)
    path.chmod(0o644)
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def accepted(tmp_path, monkeypatch, fake_snap):
    monkeypatch.setattr(standby, "ROOT_UID", os.geteuid())
    # Pytest may live beneath world-writable /tmp; it is not a deployment
    # receipt root. Test the real ancestry rejection separately below.
    monkeypatch.setattr(standby, "_root_controlled_ancestry", lambda _path: None)
    monkeypatch.setattr(standby, "_initialized_digest", None)
    monkeypatch.setattr(standby, "_network_guard_installed", False)
    for key in tuple(os.environ):
        if key.startswith(("SEICHE_STANDBY_", "SEICHE_RAILWAY_", "RAILWAY_")):
            monkeypatch.delenv(key)
    root = tmp_path.resolve()
    generation = root / "generation"
    generation.mkdir()
    socket_dir = root / "postgres"
    socket_dir.mkdir()
    envelope = assemble._build_handoff(
        deepcopy(fake_snap), _release_receipt(fake_snap), "a" * 40
    )
    data = {
        "generation_root": str(generation), "database_name": "seiche_candidate",
        "postgres_socket": str(socket_dir), "postgres_port": 5432,
        "postgres_role": "seiche_reader",
    }
    restore = {
        "schema": "seiche.hetzner-candidate-restore.v1", "status": "PASS",
        "candidate_only": True, "authority_changed": False,
        "filesystem": {"generation_root": str(generation), "agent_room_audit": absent_agent_room_audit()},
        "postgres": {key: value for key, value in data.items() if key != "generation_root"},
    }
    restore_path = root / "restore.json"
    token = "private_standby_capability_" + "t" * 32
    receipt = {
        "schema": standby.SCHEMA, "provider": "hetzner", "authority": "standby",
        "candidate_id": "1" * 64, "code": {"commit": "b" * 40, "tree": "c" * 40},
        "restore": {"receipt_path": str(restore_path), "receipt_sha256": seal(restore_path, restore)},
        "parent": {
            "commit": "a" * 40, "handoff_id": envelope["handoff_id"],
            "envelope_sha256": standby.digest(envelope),
            "payload_sha256": envelope["payload_sha256"],
        },
        "data": data, "agent_room": {"expected_key_id": "unprovisioned"},
        "edge_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
    }
    restore["source_backup"] = {"source": receipt["parent"]["commit"]}
    restore["filesystem"]["validated_parent_handoff"] = receipt["parent"]
    restore["postgres"]["parent"] = receipt["parent"]
    receipt["restore"]["receipt_sha256"] = seal(restore_path, restore)
    receipt_path = root / "standby.json"
    environment = {
        "SEICHE_ENV": "production", "SEICHE_RELEASE_SHA": receipt["code"]["commit"],
        "SEICHE_STANDBY_RECEIPT_PATH": str(receipt_path),
        "SEICHE_STANDBY_RECEIPT_SHA256": seal(receipt_path, receipt),
        "SEICHE_STANDBY_EDGE_TOKEN": token,
        "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "0",
        "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "0", "SEICHE_BOARD_AUTH": "0",
        "SEICHE_DATABASE_URL": make_conninfo(
            dbname=data["database_name"], host=data["postgres_socket"],
            port=data["postgres_port"], user=data["postgres_role"],
            options="-c default_transaction_read_only=on",
        ),
        **standby.runtime_paths(receipt),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in ("SEICHE_PREBUILT_HANDOFF_ID", "SEICHE_PREBUILT_PAYLOAD_SHA256"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(assemble, "_process_release_sha", receipt["code"]["commit"])
    monkeypatch.setattr(assemble, "_cache", {
        "at": 0.0, "payload": None, "source": None, "release_receipt": None,
        "release_handoff_id": None, "producer_sha": None,
    })
    pg = repository.PostgresMarketRepository(environment["SEICHE_DATABASE_URL"])
    calls = []

    def load():
        calls.append("read_only_parent")
        return deepcopy(envelope)

    monkeypatch.setattr(pg, "load_active_release_handoff_read_only", load)
    monkeypatch.setattr(repository, "get_repository", lambda: pg)
    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", True)
    monkeypatch.setattr(standby, "_initialized_digest", standby.digest(receipt))
    return receipt, envelope, pg, calls


def test_receipt_binds_real_files_and_private_runtime(accepted):
    receipt, _, _, _ = accepted
    assert standby.require_initialized() == receipt
    assert standby.enabled()


def test_receipt_ancestry_rejects_an_attacker_writable_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(standby, "ROOT_UID", os.geteuid())
    writable = tmp_path / "writable"
    writable.mkdir()
    writable.chmod(0o777)
    path = writable / "receipt.json"
    expected = seal(path, {"fixture": True})
    with pytest.raises(standby.StandbyError, match="ancestry"):
        standby._document(path.resolve(), expected)


@pytest.mark.parametrize("setting,value", [
    ("SEICHE_ENV", "development"),
    ("SEICHE_RELEASE_SHA", "d" * 40),
    ("RAILWAY_DEPLOYMENT_ID", "not-a-railway-deployment"),
    ("SEICHE_RAILWAY_STATEFUL_MODE", "production"),
    ("SEICHE_RAILWAY_CONTROL_ENABLED", "1"),
    ("SEICHE_PREBUILT_HANDOFF_ID", "f" * 64),
    ("SEICHE_SOURCE_HEARTBEAT_REQUIRED", "1"),
    ("SEICHE_BOARD_AUTH", "1"),
    ("SEICHE_RUNTIME_DATA_DIR", "/var/lib/another-owner"),
    ("SEICHE_STANDBY_EDGE_TOKEN", "x" * 64),
    ("SEICHE_DATABASE_URL", "postgresql://seiche_reader@127.0.0.1/seiche_candidate"),
    ("PGHOSTADDR", "203.0.113.10"),
])
def test_runtime_drift_cannot_escape_standby(accepted, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(standby.StandbyError):
        standby.require_initialized()


@pytest.mark.parametrize("change", ["hash", "canonical", "permissions", "symlink", "hardlink"])
def test_receipt_tampering_is_rejected_before_serving(accepted, monkeypatch, change):
    path = Path(os.environ["SEICHE_STANDBY_RECEIPT_PATH"])
    if change == "hash":
        path.write_bytes(path.read_bytes().replace(b'"standby"', b'"production"'))
    elif change == "canonical":
        body = json.dumps(json.loads(path.read_bytes()), indent=2).encode()
        path.write_bytes(body)
        monkeypatch.setenv("SEICHE_STANDBY_RECEIPT_SHA256", hashlib.sha256(body).hexdigest())
    elif change == "permissions":
        path.chmod(0o666)
    elif change == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    else:
        os.link(path, path.with_suffix(".copy"))
    with pytest.raises(standby.StandbyError):
        standby.require_initialized()


@pytest.mark.parametrize("field", ["authority", "provider", "extra"])
def test_closed_authority_contract(accepted, field):
    receipt = deepcopy(accepted[0])
    receipt[field] = "production"
    with pytest.raises(standby.StandbyError):
        standby.validate_receipt(receipt)


def test_incomplete_configuration_never_becomes_normal_production(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("SEICHE_STANDBY_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("SEICHE_STANDBY_RECEIPT_PATH", "/missing/receipt")
    assert standby.enabled()
    with pytest.raises(standby.StandbyError):
        standby.load_receipt()


def test_startup_binds_restore_and_verifies_source_database_before_guard(accepted, monkeypatch):
    receipt = accepted[0]
    calls = []
    monkeypatch.setattr(standby, "_verify_source", lambda value: calls.append(("source", value)))
    monkeypatch.setattr(standby, "_verify_database", lambda value: calls.append(("database", value)))
    monkeypatch.setattr(standby, "_install_network_guard", lambda value: calls.append(("guard", value)))
    assert standby.initialize() == receipt
    assert calls == [(name, receipt) for name in ("source", "database", "guard")]


@pytest.mark.parametrize("field", ["database_name", "postgres_socket", "postgres_port", "postgres_role"])
def test_rebound_restore_cannot_select_another_database(accepted, monkeypatch, field):
    receipt = deepcopy(accepted[0])
    path = Path(receipt["restore"]["receipt_path"])
    restore = json.loads(path.read_bytes())
    restore["postgres"][field] = 5433 if field == "postgres_port" else "other"
    receipt["restore"]["receipt_sha256"] = seal(path, restore)
    receipt_path = Path(os.environ["SEICHE_STANDBY_RECEIPT_PATH"])
    monkeypatch.setenv("SEICHE_STANDBY_RECEIPT_SHA256", seal(receipt_path, receipt))
    with pytest.raises(standby.StandbyError, match="database binding"):
        standby.initialize()


@pytest.mark.parametrize("field", ["commit", "handoff_id", "envelope_sha256", "payload_sha256", "key"])
def test_operator_receipt_cannot_select_another_valid_parent_or_key(accepted, monkeypatch, field):
    receipt = deepcopy(accepted[0])
    if field == "key":
        receipt["agent_room"]["expected_key_id"] = "e" * 64
        monkeypatch.setenv("SEICHE_AGENT_ROOM_EXPECTED_KEY_ID", "e" * 64)
    else:
        receipt["parent"][field] = "e" * (40 if field == "commit" else 64)
    receipt_path = Path(os.environ["SEICHE_STANDBY_RECEIPT_PATH"])
    monkeypatch.setenv("SEICHE_STANDBY_RECEIPT_SHA256", seal(receipt_path, receipt))
    with pytest.raises(standby.StandbyError, match="restored (parent|Agent Room)"):
        standby.initialize()


def test_restore_source_provenance_cannot_be_rebound_to_another_parent(accepted, monkeypatch):
    receipt = deepcopy(accepted[0])
    path = Path(receipt["restore"]["receipt_path"])
    restore = json.loads(path.read_bytes())
    restore["source_backup"]["source"] = "f" * 40
    receipt["restore"]["receipt_sha256"] = seal(path, restore)
    receipt_path = Path(os.environ["SEICHE_STANDBY_RECEIPT_PATH"])
    monkeypatch.setenv("SEICHE_STANDBY_RECEIPT_SHA256", seal(receipt_path, receipt))
    with pytest.raises(standby.StandbyError, match="restored parent"):
        standby.initialize()


def test_source_check_rejects_tracked_and_untracked_changes(tmp_path, monkeypatch):
    checkout = tmp_path / "source"
    checkout.mkdir()
    source = checkout / "backend/seiche/standby.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass\n")
    monkeypatch.setattr(standby, "__file__", str(source))
    command = ["git", "-C", str(checkout)]
    subprocess.run([*command, "init", "-q"], check=True)
    subprocess.run([*command, "add", "."], check=True)
    subprocess.run([
        *command, "-c", "user.name=Standby test", "-c", "user.email=standby@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "standby fixture",
    ], check=True)
    code = {
        field: subprocess.check_output([*command, "rev-parse", ref], text=True).strip()
        for field, ref in (("commit", "HEAD"), ("tree", "HEAD^{tree}"))
    }
    standby._verify_source({"code": code})
    source.write_text("raise RuntimeError('changed')\n")
    with pytest.raises(standby.StandbyError, match="not clean"):
        standby._verify_source({"code": code})
    source.write_text("pass\n")
    (source.parent / "injected.py").write_text("pass\n")
    with pytest.raises(standby.StandbyError, match="not clean"):
        standby._verify_source({"code": code})
    with pytest.raises(standby.StandbyError, match="identity"):
        standby._verify_source({"code": {**code, "commit": "f" * 40}})


@pytest.mark.parametrize("field,value", [
    ("status", "PENDING"), ("authority_changed", True), ("candidate_only", False),
    ("schema", "seiche.railway-activation-receipt.v1"),
])
def test_rebound_but_unaccepted_restore_is_rejected(accepted, monkeypatch, field, value):
    receipt = deepcopy(accepted[0])
    path = Path(receipt["restore"]["receipt_path"])
    restore = json.loads(path.read_bytes())
    restore[field] = value
    receipt["restore"]["receipt_sha256"] = seal(path, restore)
    receipt_path = Path(os.environ["SEICHE_STANDBY_RECEIPT_PATH"])
    monkeypatch.setenv("SEICHE_STANDBY_RECEIPT_SHA256", seal(receipt_path, receipt))
    with pytest.raises(standby.StandbyError, match="restore acceptance"):
        standby.initialize()


def test_exact_parent_hydrates_without_fallback_or_identity_rewrite(accepted, monkeypatch):
    receipt, envelope, pg, calls = accepted
    monkeypatch.setattr(pg, "_ensure_schema", lambda: pytest.fail("schema convergence"))
    monkeypatch.setattr(pg, "load_active_release_handoff", lambda: pytest.fail("ordinary reader"))
    monkeypatch.setattr(store, "load_blob", lambda *_: pytest.fail("legacy SQLite"))
    assert assemble.restore_cached_snapshot(read_only=True) == "standby_parent"
    assert calls == ["read_only_parent"]
    assert assemble.cached_snapshot() == envelope["payload"]
    assert assemble._cache["producer_sha"] == receipt["parent"]["commit"]
    assert not assemble.cached_snapshot_was_rebuilt()


def test_release_capture_reuses_verified_identity_without_post_guard_git(accepted, monkeypatch):
    monkeypatch.setattr(assemble, "_process_release_sha", None)
    monkeypatch.setattr(assemble.subprocess, "run", lambda *_a, **_kw: pytest.fail("Git after guard"))
    assert assemble.capture_process_release_sha() == accepted[0]["code"]["commit"]
    monkeypatch.setattr(assemble, "_process_release_sha", None)
    monkeypatch.setattr(standby, "_initialized_digest", None)
    with pytest.raises(standby.StandbyError, match="startup acceptance"):
        assemble.capture_process_release_sha()


@pytest.mark.parametrize("change", ["missing", "producer", "clock", "receipt", "content"])
def test_parent_drift_fails_without_static_or_legacy_fallback(accepted, monkeypatch, change):
    _, envelope, pg, _ = accepted
    altered = deepcopy(envelope)
    if change == "missing":
        altered = None
    elif change == "producer":
        altered["producer_sha"] = "b" * 40
    elif change == "clock":
        altered["payload"]["generated_at"] = "2099-01-01T00:00:00Z"
    elif change == "receipt":
        altered["release_receipt"]["generated_at"] = "2099-01-01T00:00:00Z"
    else:
        altered["payload"]["faults"] = [{"source": "forged"}]
    monkeypatch.setattr(pg, "load_active_release_handoff_read_only", lambda: altered)
    monkeypatch.setattr(store, "load_blob", lambda *_: pytest.fail("legacy fallback"))
    with pytest.raises(standby.StandbyError):
        assemble.restore_cached_snapshot()
    assert assemble.cached_snapshot() is None


@pytest.mark.asyncio
async def test_forced_or_stale_reads_cannot_collect_or_promote(accepted, monkeypatch):
    _, envelope, _, _ = accepted
    assemble.restore_cached_snapshot()
    monkeypatch.setattr(assemble, "_gather_sources", lambda: pytest.fail("collection"))
    for force in (False, True):
        assert await assemble.snapshot(force=force) == envelope["payload"]
    with pytest.raises(standby.StandbyError, match="cannot build"):
        await assemble.refresh_snapshot()
    with pytest.raises(standby.StandbyError, match="cannot build"):
        await assemble.prebuild_snapshot_payload()
    assert assemble._cache["source"] == "standby_parent"


@pytest.mark.asyncio
async def test_mutated_memory_cannot_serve_as_receipted_parent(accepted):
    assemble.restore_cached_snapshot()
    assemble._cache["payload"]["generated_at"] = "2099-01-01T00:00:00Z"
    with pytest.raises(standby.StandbyError, match="not hydrated"):
        await assemble.snapshot()


@pytest.mark.asyncio
async def test_lifespan_initializes_standby_without_background_owner(accepted, monkeypatch):
    calls = []
    monkeypatch.setattr(standby, "initialize", lambda: calls.append("standby"))
    monkeypatch.setattr(mcp_server, "initialize_agent_room_readiness", lambda: calls.append("audit"))

    async def prohibited():
        pytest.fail("background refresh was started")

    monkeypatch.setattr(api, "_keep_warm", prohibited)
    async with api._lifespan(api.app):
        assert calls == ["standby", "audit"]
        assert assemble._cache["source"] == "standby_parent"


def test_http_allowlist_and_identity(accepted):
    receipt, envelope, _, _ = accepted
    assemble.restore_cached_snapshot()
    headers = {"x-seiche-standby-token": os.environ["SEICHE_STANDBY_EDGE_TOKEN"]}
    client = TestClient(api.app)  # Startup receipt was accepted above.
    denied = client.get("/api/health")
    assert denied.status_code == 404
    denied = client.get("/api/health", headers={"x-seiche-standby-token": "wrong"})
    assert denied.status_code == 404
    health = client.get("/api/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["generated_at"] == envelope["payload"]["generated_at"]
    assert health.json()["version"] == envelope["payload"]["version"]
    assert health.json()["production_ready"] is False
    assert health.headers["x-seiche-provider"] == "hetzner"
    assert health.headers["x-seiche-authority"] == "standby"
    assert health.headers["x-seiche-release-sha"] == receipt["code"]["commit"]
    assert health.headers["x-seiche-snapshot-producer"] == receipt["parent"]["commit"]
    assert "x-seiche-railway-deployment" not in health.headers
    assert health.headers["cache-control"] == "no-store"
    strict = client.get("/api/health?require_rebuilt=true", headers=headers)
    assert strict.status_code == 503
    assert strict.json()["status"] == "standby_not_production_ready"
    overview = client.get("/api/overview?force=true", headers=headers)
    assert overview.status_code == 403
    assert overview.json()["availability"] == "unavailable"
    assert overview.headers["cache-control"] == "no-store"
    for method, path in (
        ("POST", "/mcp"), ("POST", "/api/overview"), ("GET", "/api/ask"),
        ("POST", "/api/internal/v1/railway-control/commands"),
        ("GET", "/api/internal/v1/railway-control/recovery/id/member"),
        ("GET", "/api/v2/markets"), ("GET", "/healthz"),
    ):
        response = client.request(method, path, headers=headers)
        assert response.status_code == 503
        assert response.json()["status"] == "standby_read_only"
    assert client.get(
        "/api/overview", headers={**headers, "Authorization": "Bearer unused"}
    ).status_code == 400
    assert assemble.cached_snapshot() == envelope["payload"]


def test_standby_schema_reader_never_executes_ddl(accepted, monkeypatch):
    pg = accepted[2]
    statements = []

    class Connection:
        @contextmanager
        def transaction(self):
            yield

        def execute(self, statement):
            statements.append(statement)
            return self

        def fetchone(self):
            return ("on", False, False, False, False, False)

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(pg, "_connect", connect)
    pg._ensure_schema()
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert len(statements) == 2
    assert statements[1].startswith("SELECT ")
    assert not pg._initialized


def test_standby_schema_reader_rejects_superuser(accepted, monkeypatch):
    pg = accepted[2]

    class Connection:
        @contextmanager
        def transaction(self):
            yield

        def execute(self, statement):
            return self

        def fetchone(self):
            return ("on", True, False, False, False, False)

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(pg, "_connect", connect)
    with pytest.raises(standby.StandbyError, match="not restricted"):
        pg._ensure_schema()


def test_agent_room_receipt_is_read_only_and_not_a_provisioning_grant(accepted):
    assert mcp_server._stateful_preactivation_environment()
    assert mcp_server._stateful_agent_room_expected_key_id() == ""


def test_network_guard_blocks_tcp_dns_and_children_in_real_subprocess(tmp_path):
    script = """
import socket, subprocess
from seiche import standby
standby._install_network_guard({'data': {'postgres_socket': '/candidate/socket', 'postgres_port': 5432}})
attempts = [
    lambda: socket.getaddrinfo('example.com', 443),
    lambda: socket.socket().connect(('127.0.0.1', 9)),
    lambda: subprocess.run(['/usr/bin/true']),
    lambda: socket.socket(socket.AF_UNIX).connect('/another-owner/.s.PGSQL.5432'),
]
for attempt in attempts:
    try:
        attempt()
    except standby.StandbyError:
        continue
    raise SystemExit('an outbound attempt was not denied')
print('four outbound attempts denied')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], capture_output=True, text=True,
        check=True, timeout=20,
    )
    assert result.stdout.strip() == "four outbound attempts denied"


def test_real_uvicorn_can_listen_after_guard_installed_during_lifespan():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    script = """
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
from seiche import standby
@asynccontextmanager
async def lifespan(app):
    standby._install_network_guard({'data': {'postgres_socket': '/candidate/socket', 'postgres_port': 5432}})
    yield
app = FastAPI(lifespan=lifespan)
@app.get('/health')
async def health():
    return {'standby': True}
uvicorn.run(app,host='127.0.0.1',port=PORT,loop='asyncio',http='h11',
            log_level='warning',limit_max_requests=1,timeout_graceful_shutdown=1)
""".replace("PORT", str(port))
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    assert json.load(response) == {"standby": True}
                break
            except urllib.error.URLError:
                if time.monotonic() > deadline or process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    pytest.fail(f"guarded uvicorn did not start: {stdout} {stderr}")
                time.sleep(0.05)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
