"""Authenticated REST/MCP adapters for the private, non-executable Agent Room."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from seiche import (
    accounts,
    agent_room,
    api,
    attest,
    mcp_server,
    stateful_migration,
    usage,
)


def _private_key(seed: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)


def _public_hex(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes_raw().hex()


def _token(username: str) -> str:
    return accounts.issue_token(username, "pro")["token"]


def _headers(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(username)}"}


def _production_startup(data: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SEICHE_ENV": "production",
            "SEICHE_RUNTIME_DATA_DIR": str(data),
            "SEICHE_AGENT_ROOM_DB_PATH": str(
                data / "_agent_room" / "agent-room.sqlite"
            ),
            "SEICHE_ATTEST_DIR": str(data / "_attest"),
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from seiche import mcp_server; "
            "mcp_server.initialize_agent_room_readiness(); "
            "assert mcp_server.agent_room_release_ready()",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _rpc(method: str, params: dict | None = None, *, msg_id: int = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _event(
    private: Ed25519PrivateKey,
    *,
    actor: str,
    room: str,
    sequence: int,
    expected_head_hash: str,
    nonce: str,
    kind: str = "proposal",
    in_reply_to: str | None = None,
    payload: dict | None = None,
) -> tuple[dict, str]:
    event = agent_room.build_client_event(
        room_id=room,
        actor_id=actor,
        client_key_id=agent_room.ed25519_key_id(_public_hex(private)),
        kind=kind,
        expected_sequence=sequence,
        expected_head_hash=expected_head_hash,
        nonce=nonce,
        client_created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        in_reply_to=in_reply_to,
        payload=(
            {"instrument": "UST-10Y", "size_usd": "25000000"}
            if payload is None
            else payload
        ),
    )
    return event, private.sign(agent_room.client_signing_bytes(event)).hex()


@pytest.fixture()
def room_client(tmp_path, monkeypatch):
    private_dir = tmp_path / "agent-room"
    private_dir.mkdir(mode=0o700)
    private_dir.chmod(0o700)
    database_path = private_dir / "agent-room.sqlite"
    store = agent_room.AgentRoomStore(
        database_path, server_private_key=_private_key(99)
    )
    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", store)
    # The transport fixture is intentionally non-production and owns its exact
    # private path; neither a clean checkout nor ambient deployment controls may
    # redirect its store lookup into the packaged/runtime data directory.
    monkeypatch.setenv("SEICHE_ENV", "test")
    monkeypatch.setenv("SEICHE_AGENT_ROOM_DB_PATH", str(database_path))
    # Keep the commercial meter out of the packaged-data path. A clean checkout
    # has no backend/data directory, and the release workflow deliberately runs
    # under umask 0077 to match the hardened host service.
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "agent-room-transport-test-secret")
    accounts.add_user("alice", "correct horse battery", tier="pro")
    accounts.add_user("bob", "correct horse battery", tier="pro")
    return TestClient(api.app), store


def test_default_agent_room_database_directory_is_exactly_private(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("SEICHE_AGENT_ROOM_DB_PATH", raising=False)
    monkeypatch.setattr(mcp_server, "DATA_DIR", tmp_path)

    database_path = mcp_server._agent_room_database_path()

    assert database_path == tmp_path / "_agent_room" / "agent-room.sqlite"
    assert database_path.parent.stat().st_mode & 0o777 == 0o700


def test_default_agent_room_database_rejects_preexisting_non_private_mode(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("SEICHE_AGENT_ROOM_DB_PATH", raising=False)
    monkeypatch.setattr(mcp_server, "DATA_DIR", tmp_path)
    private_dir = tmp_path / "_agent_room"
    private_dir.mkdir(mode=0o700)
    private_dir.chmod(0o755)

    with pytest.raises(
        agent_room.AgentRoomValidationError,
        match="Agent Room database directory must have mode 0700",
    ):
        mcp_server._agent_room_database_path()


@pytest.mark.parametrize("kind", ["custom", "traversal"])
def test_production_agent_room_database_rejects_noncanonical_override(
    tmp_path, monkeypatch, kind
):
    data = tmp_path / "runtime-data"
    data.mkdir()
    canonical = data / "_agent_room" / "agent-room.sqlite"
    override = (
        tmp_path / "other" / "agent-room.sqlite"
        if kind == "custom"
        else Path(f"{data}/nested/../_agent_room/agent-room.sqlite")
    )
    monkeypatch.setattr(mcp_server, "DATA_DIR", data)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setenv("SEICHE_AGENT_ROOM_DB_PATH", str(override))

    with pytest.raises(
        agent_room.AgentRoomValidationError, match="canonical runtime path"
    ):
        mcp_server._agent_room_database_path()

    monkeypatch.setenv("SEICHE_AGENT_ROOM_DB_PATH", str(canonical))
    assert mcp_server._agent_room_database_path() == canonical

    canonical.parent.chmod(0o755)
    with pytest.raises(
        agent_room.AgentRoomValidationError,
        match="Agent Room database directory must have mode 0700",
    ):
        mcp_server._agent_room_database_path()


def test_production_agent_room_database_rejects_canonical_symlink(
    tmp_path, monkeypatch
):
    data = tmp_path / "runtime-data"
    data.mkdir()
    outside = tmp_path / "outside-room"
    outside.mkdir(mode=0o700)
    (data / "_agent_room").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(mcp_server, "DATA_DIR", data)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.delenv("SEICHE_AGENT_ROOM_DB_PATH", raising=False)

    with pytest.raises(agent_room.AgentRoomValidationError, match="symlink"):
        mcp_server._agent_room_database_path()


def test_startup_audit_caches_only_agent_room_readiness(monkeypatch):
    class AuditedStore:
        @staticmethod
        def audit_all_rooms():
            return {
                "ok": True,
                "schema": agent_room.AGENT_ROOM_AUDIT_SCHEMA,
                "participant_count": 7,
                "room_count": 3,
                "state_sha256": "private",
            }

    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    monkeypatch.setattr(mcp_server, "_unchecked_agent_room_store", AuditedStore)

    assert mcp_server.initialize_agent_room_readiness() is None
    assert mcp_server.agent_room_release_ready() is True
    assert isinstance(mcp_server._agent_room_readiness_passed, bool)


def test_production_startup_bootstraps_and_audits_canonical_store(
    tmp_path, monkeypatch
):
    data = tmp_path / "runtime-data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    monkeypatch.setattr(mcp_server, "DATA_DIR", data)
    monkeypatch.setattr(attest, "DATA_DIR", data)
    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", None)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setenv(
        "SEICHE_AGENT_ROOM_DB_PATH",
        str(data / "_agent_room" / "agent-room.sqlite"),
    )
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(data / "_attest"))

    mcp_server.initialize_agent_room_readiness()

    assert mcp_server.agent_room_release_ready() is True
    assert (data / "_agent_room" / "agent-room.sqlite").stat().st_mode & 0o777 == 0o600
    assert (data / "_attest" / "operator_key.pem").stat().st_mode & 0o777 == 0o600
    assert (data / "_attest").stat().st_mode & 0o777 == 0o700
    seal = data / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    assert seal.stat().st_mode & 0o777 == 0o600
    assert mcp_server._agent_room_store().verify_initialization_seal(seal)


@pytest.mark.parametrize("mode", ["shadow", "cutover_candidate"])
def test_preactivation_startup_audits_absence_without_creating_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    data = tmp_path / "runtime-data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    monkeypatch.setattr(mcp_server, "DATA_DIR", data)
    monkeypatch.setattr(attest, "DATA_DIR", data)
    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", None)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", mode)
    monkeypatch.setenv(
        "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID",
        stateful_migration.AGENT_ROOM_UNPROVISIONED_KEY,
    )
    monkeypatch.setenv(
        "SEICHE_AGENT_ROOM_DB_PATH",
        str(data / "_agent_room" / "agent-room.sqlite"),
    )
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(data / "_attest"))

    mcp_server.initialize_agent_room_readiness()

    assert mcp_server.agent_room_release_ready() is True
    assert not (data / "_agent_room").exists()
    assert not (data / "_attest").exists()
    with pytest.raises(
        agent_room.AgentRoomIntegrityError,
        match="not provisioned by the immutable runtime receipt",
    ):
        mcp_server._agent_room_store()
    assert not (data / "_agent_room").exists()
    assert not (data / "_attest").exists()


@pytest.mark.parametrize("mode", ["shadow", "cutover_candidate"])
def test_preactivation_existing_store_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    data = tmp_path / "runtime-data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    monkeypatch.setattr(mcp_server, "DATA_DIR", data)
    monkeypatch.setattr(attest, "DATA_DIR", data)
    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", None)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setenv(
        "SEICHE_AGENT_ROOM_DB_PATH",
        str(data / "_agent_room" / "agent-room.sqlite"),
    )
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(data / "_attest"))
    mcp_server.initialize_agent_room_readiness()
    expected_key_id = mcp_server._agent_room_store().server_key_id
    database = data / "_agent_room" / "agent-room.sqlite"
    seal = data / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    database_before = database.read_bytes()
    seal_before = seal.read_bytes()

    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", mode)
    monkeypatch.setenv("SEICHE_AGENT_ROOM_EXPECTED_KEY_ID", expected_key_id)
    monkeypatch.setattr(mcp_server, "_agent_room_store_instance", None)
    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", False)
    mcp_server.initialize_agent_room_readiness()

    assert database.read_bytes() == database_before
    assert seal.read_bytes() == seal_before
    with pytest.raises(
        agent_room.AgentRoomIntegrityError,
        match="read-only Agent Room store cannot mutate",
    ):
        mcp_server._agent_room_store().provision_participant(
            "blocked-before-activation",
            _public_hex(_private_key(88)),
        )
    assert database.read_bytes() == database_before
    assert seal.read_bytes() == seal_before


def test_production_restart_rejects_initialized_database_disappearance(
    tmp_path: Path,
) -> None:
    data = tmp_path / "runtime-data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    first = _production_startup(data)
    assert first.returncode == 0, first.stderr
    database = data / "_agent_room" / "agent-room.sqlite"
    seal = data / "_attest" / agent_room.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME
    displaced = tmp_path / "displaced-agent-room.sqlite"
    database.rename(displaced)

    restarted = _production_startup(data)

    assert restarted.returncode != 0
    assert "Agent Room database is unavailable" in restarted.stderr
    assert seal.is_file()
    assert displaced.is_file()
    assert not database.exists()


def test_production_startup_rejects_unexpected_agent_room_member(
    tmp_path: Path,
) -> None:
    data = tmp_path / "runtime-data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    room_root = data / "_agent_room"
    room_root.mkdir(mode=0o700)
    (room_root / "stale-agent-room.sqlite").write_bytes(b"not canonical state")

    started = _production_startup(data)

    assert started.returncode != 0
    assert "database directory contains unexpected state" in started.stderr
    assert not (room_root / "agent-room.sqlite").exists()
    assert not (data / "_attest").exists()


def test_failed_startup_audit_blocks_production_agent_room_use(monkeypatch):
    class BrokenStore:
        @staticmethod
        def audit_all_rooms():
            raise agent_room.AgentRoomIntegrityError("private audit detail")

    monkeypatch.setattr(mcp_server, "_agent_room_readiness_passed", True)
    monkeypatch.setattr(mcp_server, "_unchecked_agent_room_store", BrokenStore)

    with pytest.raises(
        agent_room.AgentRoomIntegrityError, match="private audit detail"
    ):
        mcp_server.initialize_agent_room_readiness()
    assert mcp_server.agent_room_release_ready() is False

    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setattr(
        mcp_server,
        "_agent_room_database_path",
        lambda: Path("/canonical/agent-room.sqlite"),
    )
    monkeypatch.setattr(
        attest,
        "_attest_dir_path",
        lambda: Path("/canonical/_attest"),
    )
    with pytest.raises(agent_room.AgentRoomIntegrityError, match="has not passed"):
        mcp_server._agent_room_store()


def test_agent_room_tools_are_identity_bound(room_client):
    client, _store = room_client

    anonymous = client.post("/mcp", json=_rpc("tools/list")).json()["result"]["tools"]
    assert len(anonymous) == 12
    assert not ({tool["name"] for tool in anonymous} & mcp_server.AGENT_ROOM_TOOLS)

    authenticated = client.post(
        "/mcp", json=_rpc("tools/list"), headers=_headers("alice")
    ).json()["result"]["tools"]
    by_name = {tool["name"]: tool for tool in authenticated}
    assert len(authenticated) == 22
    assert set(mcp_server.AGENT_ROOM_TOOLS) <= set(by_name)
    assert by_name["agent_room_append_event"]["annotations"]["readOnlyHint"] is False
    assert by_name["agent_room_list_events"]["annotations"]["readOnlyHint"] is True
    assert all(
        by_name[name].get("outputSchema") for name in mcp_server.AGENT_ROOM_TOOLS
    )

    paid_without_identity = mcp_server.dispatch(
        _rpc("tools/list"), public=False, identity=None
    )["result"]["tools"]
    assert not (
        {tool["name"] for tool in paid_without_identity} & mcp_server.AGENT_ROOM_TOOLS
    )

    initialize = _rpc("initialize", {"protocolVersion": mcp_server.PROTOCOL_VERSION})
    for public, identity in ((True, None), (False, None)):
        instructions = mcp_server.dispatch(
            initialize, public=public, identity=identity
        )["result"]["instructions"]
        assert "agent_room_" not in instructions
        assert "Agent Room" not in instructions
    authenticated_instructions = mcp_server.dispatch(
        initialize, public=False, identity={"username": "alice"}
    )["result"]["instructions"]
    assert "agent_room_*" in authenticated_instructions
    assert "Agent Room identity" in authenticated_instructions


def test_normal_mcp_dispatch_enforces_complete_published_input_schema(room_client):
    _client, store = room_client
    alice = _private_key(31)
    base_event, signature = _event(
        alice,
        actor="alice",
        room="room_" + "a" * 64,
        sequence=0,
        expected_head_hash="b" * 64,
        nonce="complete_schema_nonce_001",
    )

    invalid_events = (
        ({**base_event, "non_executable": False}, "required constant"),
        ({**base_event, "expected_sequence": 4096}, "must be at most 4095"),
        ({**base_event, "expected_head_hash": "B" * 64}, "required pattern"),
        ({**base_event, "in_reply_to": 7}, "one of the types"),
    )
    for invalid_event, expected_message in invalid_events:
        response = mcp_server.dispatch(
            _rpc(
                "tools/call",
                {
                    "name": "agent_room_append_event",
                    "arguments": {
                        "room_id": invalid_event["room_id"],
                        "event": invalid_event,
                        "client_signature_hex": signature,
                    },
                },
            ),
            public=False,
            identity={"username": "alice"},
        )
        assert response["error"]["code"] == mcp_server.INVALID_PARAMS
        assert expected_message in response["error"]["message"]

    duplicate_participants = mcp_server.dispatch(
        _rpc(
            "tools/call",
            {
                "name": "agent_room_create",
                "arguments": {
                    "room_id": "duplicate-invitees",
                    "participant_ids": ["bob", "bob"],
                },
            },
        ),
        public=False,
        identity={"username": "alice"},
    )
    assert duplicate_participants["error"]["code"] == mcp_server.INVALID_PARAMS
    assert "unique items" in duplicate_participants["error"]["message"]
    assert store.audit_all_rooms()["participant_count"] == 0


def test_rest_lifecycle_is_signed_private_and_non_executable(room_client, monkeypatch):
    client, store = room_client
    alice = _private_key(1)
    bob = _private_key(2)

    assert (
        client.post(
            "/api/agent-room/participants/self",
            json={"public_key_hex": _public_hex(alice)},
            headers=_headers("alice"),
        ).json()["private_key_stored"]
        is False
    )
    client.post(
        "/api/agent-room/participants/self",
        json={"public_key_hex": _public_hex(bob)},
        headers=_headers("bob"),
    ).raise_for_status()

    created = client.post(
        "/api/agent-room/rooms",
        json={"room_id": "block-ust-001", "participant_ids": ["bob"]},
        headers=_headers("alice"),
    )
    assert created.status_code == 200
    room_id = created.json()["room_id"]
    assert room_id.startswith("room_")
    assert room_id != "block-ust-001"
    assert created.json()["non_executable"] is True
    assert created.json()["execution_authority"] == "none"
    assert created.headers["X-Seiche-Execution-Authority"] == "none"

    event, signature = _event(
        alice,
        actor="alice",
        room=room_id,
        sequence=0,
        expected_head_hash=created.json()["genesis_hash"],
        nonce="alice_proposal_nonce_001",
    )
    appended = client.post(
        f"/api/agent-room/rooms/{room_id}/events",
        json={"event": event, "client_signature_hex": signature},
        headers=_headers("alice"),
    )
    assert appended.status_code == 200
    record = appended.json()
    assert record["schema"] == agent_room.AGENT_ROOM_EVENT_SCHEMA
    assert record["client_event"]["kind"] == "proposal"
    assert record["can_execute"] is False
    assert record["can_settle"] is False

    def reject_split_read(*_args, **_kwargs):
        raise AssertionError("transport must use one atomic room_page read")

    monkeypatch.setattr(store, "room_state", reject_split_read)
    monkeypatch.setattr(store, "list_events", reject_split_read)

    listed = client.get(
        f"/api/agent-room/rooms/{room_id}/events",
        headers=_headers("bob"),
    )
    assert listed.status_code == 200
    assert [row["event_id"] for row in listed.json()["events"]] == [record["event_id"]]

    verified = client.get(
        f"/api/agent-room/rooms/{room_id}/verify", headers=_headers("bob")
    )
    assert verified.json() == {
        "ok": True,
        "schema": agent_room.AGENT_ROOM_ROOM_SCHEMA,
        "room_id": room_id,
        "status": "open",
        "event_count": 1,
        "genesis_hash": created.json()["genesis_hash"],
        "head_hash": record["record_sha256"],
        "server_key_id": created.json()["server_key_id"],
        "non_executable": True,
        "execution_authority": "none",
    }


def test_mcp_lifecycle_and_sequence_conflict_are_machine_readable(room_client):
    client, _store = room_client
    alice = _private_key(3)
    headers = _headers("alice")

    register = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "agent_room_register_key",
                "arguments": {"public_key_hex": _public_hex(alice)},
            },
        ),
        headers=headers,
    )
    assert register.json()["result"]["structuredContent"]["participant_id"] == "alice"

    rejected_identity_override = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "agent_room_create",
                "arguments": {
                    "room_id": "mcp-room",
                    "participant_ids": [],
                    "owner_id": "mallory",
                },
            },
            msg_id=20,
        ),
        headers=headers,
    ).json()
    assert rejected_identity_override["error"]["code"] == -32602
    assert (
        "unknown argument(s): owner_id"
        in rejected_identity_override["error"]["message"]
    )

    create = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "agent_room_create",
                "arguments": {"room_id": "mcp-room", "participant_ids": []},
            },
            msg_id=2,
        ),
        headers=headers,
    )
    created = create.json()["result"]["structuredContent"]
    assert created["owner_id"] == "alice"
    room_id = created["room_id"]
    assert room_id.startswith("room_")

    first, first_signature = _event(
        alice,
        actor="alice",
        room=room_id,
        sequence=0,
        expected_head_hash=created["genesis_hash"],
        nonce="mcp_first_nonce_value_001",
        payload={"reason": "https://example.com/quote"},
    )
    append = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "agent_room_append_event",
                "arguments": {
                    "room_id": room_id,
                    "event": first,
                    "client_signature_hex": first_signature,
                },
            },
            msg_id=3,
        ),
        headers=headers,
    )
    record = append.json()["result"]["structuredContent"]
    assert record["sequence"] == 0
    assert record["client_event"]["payload"] == {"reason": "https://example.com/quote"}
    alice.public_key().verify(
        bytes.fromhex(record["client_signature"]),
        agent_room.client_signing_bytes(record["client_event"]),
    )

    listed = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "agent_room_list_events",
                "arguments": {"room_id": room_id},
            },
            msg_id=30,
        ),
        headers=headers,
    ).json()["result"]["structuredContent"]
    assert listed["events"] == [record]
    alice.public_key().verify(
        bytes.fromhex(listed["events"][0]["client_signature"]),
        agent_room.client_signing_bytes(listed["events"][0]["client_event"]),
    )

    stale, stale_signature = _event(
        alice,
        actor="alice",
        room=room_id,
        sequence=0,
        expected_head_hash=created["genesis_hash"],
        nonce="mcp_stale_nonce_value_002",
    )
    conflict = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "agent_room_append_event",
                "arguments": {
                    "room_id": room_id,
                    "event": stale,
                    "client_signature_hex": stale_signature,
                },
            },
            msg_id=4,
        ),
        headers=headers,
    ).json()["result"]
    assert conflict["isError"] is True
    assert conflict["structuredContent"]["category"] == "sequence_conflict"
    assert conflict["structuredContent"]["current_sequence"] == 1
    assert len(conflict["structuredContent"]["current_head"]) == 64


def test_bearer_actor_cannot_be_overridden_and_private_routes_stay_undisclosed(
    room_client,
):
    client, _store = room_client
    alice = _private_key(4)

    missing = client.post(
        "/api/agent-room/participants/self", json={"public_key_hex": _public_hex(alice)}
    )
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == 'Bearer realm="seiche-agent-room"'

    client.post(
        "/api/agent-room/participants/self",
        json={"public_key_hex": _public_hex(alice)},
        headers=_headers("alice"),
    ).raise_for_status()
    created = client.post(
        "/api/agent-room/rooms",
        json={"room_id": "identity-room"},
        headers=_headers("alice"),
    )
    created.raise_for_status()
    room_id = created.json()["room_id"]
    event, signature = _event(
        alice,
        actor="alice",
        room=room_id,
        sequence=0,
        expected_head_hash=created.json()["genesis_hash"],
        nonce="identity_override_nonce_001",
    )
    denied = client.post(
        f"/api/agent-room/rooms/{room_id}/events",
        json={"event": event, "client_signature_hex": signature},
        headers=_headers("bob"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["category"] == "not_authorized"

    paths = client.get("/api/openapi.json").json()["paths"]
    assert not any(path.startswith("/api/agent-room/") for path in paths)


def test_rest_agent_room_mutations_share_durable_quota(room_client, monkeypatch):
    client, store = room_client
    alice = _private_key(5)
    monkeypatch.setattr(
        api.usage,
        "charge",
        lambda _key, limit: {
            "allowed": False,
            "used": limit,
            "limit": limit,
            "remaining": 0,
        },
    )

    denied = client.post(
        "/api/agent-room/participants/self",
        json={"public_key_hex": _public_hex(alice)},
        headers=_headers("alice"),
    )
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "86400"
    assert denied.headers["Cache-Control"] == "no-store, no-transform"
    assert store.audit_all_rooms()["participant_count"] == 0


def test_caddy_keeps_agent_room_out_of_public_allowlist_and_caps_body():
    caddy = (Path(__file__).resolve().parents[2] / "ops" / "Caddyfile").read_text()
    block = caddy.split("@agent_room {", 1)[1].split("# OpenAI", 1)[0]
    public = caddy.split("@public {", 1)[1].split("}", 1)[0]

    assert "path /api/agent-room/*" in block
    assert "method GET POST" in block
    assert "max_size 64KiB" in block
    assert 'X-Seiche-Execution-Authority "none"' in block
    assert "/api/agent-room" not in public

    mcp_block = caddy.split("handle @mcp {", 1)[1].split("# Glama", 1)[0]
    assert "max_size 1MiB" in mcp_block
