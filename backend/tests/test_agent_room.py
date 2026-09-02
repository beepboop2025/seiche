"""Adversarial coverage for the private, non-executable Agent Room core."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import seiche.agent_room as agent_room_module
from seiche.agent_room import (
    AGENT_ROOM_AUDIT_SCHEMA,
    AGENT_ROOM_CLIENT_EVENT_SCHEMA,
    AGENT_ROOM_EVENT_SCHEMA,
    AGENT_ROOM_ROOM_SCHEMA,
    EVENT_KINDS,
    MAX_EVENTS_PER_ROOM,
    AgentRoomAuthorizationError,
    AgentRoomCapacityError,
    AgentRoomClosedError,
    AgentRoomIntegrityError,
    AgentRoomReplayError,
    AgentRoomSequenceConflict,
    AgentRoomSignatureError,
    AgentRoomStore,
    AgentRoomValidationError,
    build_client_event,
    client_signing_bytes,
    derive_external_room_id,
    ed25519_key_id,
)

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _key(label: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(label.encode("ascii")).digest()
    )


def _public(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        .hex()
    )


def _timestamp(value: datetime = NOW) -> str:
    normalized = value.astimezone(UTC)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _nonce(number: int) -> str:
    return f"N{number:021d}"


@pytest.fixture
def room(tmp_path: Path) -> dict[str, object]:
    tmp_path.chmod(0o700)
    keys = {
        "server": _key("server"),
        "alice": _key("alice"),
        "bob": _key("bob"),
        "mallory": _key("mallory"),
    }
    clock = MutableClock()
    database = tmp_path / "agent-room.sqlite3"
    store = AgentRoomStore(
        database,
        server_private_key=keys["server"],
        clock=clock,
    )
    alice = store.provision_participant("alice", _public(keys["alice"]))
    bob = store.provision_participant("bob", _public(keys["bob"]))
    store.provision_participant("mallory", _public(keys["mallory"]))
    created = store.create_room("room-1", owner_id="alice", participant_ids=("bob",))
    return {
        "store": store,
        "keys": keys,
        "clock": clock,
        "database": database,
        "alice": alice,
        "bob": bob,
        "created": created,
    }


def _event(
    room: dict[str, object],
    *,
    actor: str = "alice",
    kind: str = "proposal",
    sequence: int = 0,
    nonce: int = 0,
    payload: dict[str, object] | None = None,
    in_reply_to: str | None = None,
    evidence: dict[str, object] | None = None,
    created_at: datetime = NOW,
    expected_head_hash: str | None = None,
) -> dict[str, object]:
    keys = room["keys"]
    assert isinstance(keys, dict)
    key = keys[actor]
    assert isinstance(key, Ed25519PrivateKey)
    if expected_head_hash is None:
        created = room["created"]
        store = room["store"]
        assert isinstance(created, dict)
        assert isinstance(store, AgentRoomStore)
        if sequence == 0:
            expected_head_hash = str(created["genesis_hash"])
        else:
            records = store.list_events("room-1", requester_id=actor)
            expected_head_hash = (
                str(records[sequence - 1]["record_sha256"])
                if sequence <= len(records)
                else str(store.room_state("room-1", requester_id=actor)["head_hash"])
            )
    return build_client_event(
        room_id="room-1",
        actor_id=actor,
        client_key_id=ed25519_key_id(_public(key)),
        kind=kind,
        expected_sequence=sequence,
        expected_head_hash=expected_head_hash,
        nonce=_nonce(nonce),
        client_created_at=_timestamp(created_at),
        payload={} if payload is None else payload,
        in_reply_to=in_reply_to,
        evidence=evidence,
    )


def _signed_append(
    room: dict[str, object], event: dict[str, object]
) -> dict[str, object]:
    keys = room["keys"]
    store = room["store"]
    assert isinstance(keys, dict)
    assert isinstance(store, AgentRoomStore)
    key = keys[event["actor_id"]]
    assert isinstance(key, Ed25519PrivateKey)
    return store.append_event(
        event,
        client_signature_hex=key.sign(client_signing_bytes(event)).hex(),
    )


def _public_evidence(
    *,
    evidence_as_of: datetime = NOW - timedelta(minutes=2),
    knowledge_at: datetime = NOW - timedelta(minutes=1),
) -> dict[str, object]:
    return {
        "source_id": "official-source",
        "source_url": "https://example.gov/series/1",
        "evidence_as_of": _timestamp(evidence_as_of),
        "knowledge_at": _timestamp(knowledge_at),
        "evidence_class": "observed",
        "rights": {
            "status": "public",
            "redistributable": True,
            "license": "Public data terms v1",
            "attribution": "Example authority",
        },
        "content_sha256": "a" * 64,
    }


def test_provisioning_room_manifest_and_idempotency(room: dict[str, object]) -> None:
    store = room["store"]
    alice = room["alice"]
    created = room["created"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(alice, dict)
    assert isinstance(created, dict)
    assert isinstance(database, Path)

    assert store.provision_participant("alice", alice["public_key_hex"]) == alice
    assert alice["private_key_stored"] is False
    assert (
        alice["key_id"]
        == hashlib.sha256(bytes.fromhex(str(alice["public_key_hex"]))).hexdigest()
    )
    assert set(created) == {
        "schema",
        "room_id",
        "owner_id",
        "created_at",
        "participants",
        "server_key_id",
        "server_public_key_hex",
        "non_executable",
        "execution_authority",
        "can_accept",
        "can_order",
        "can_execute",
        "can_settle",
        "can_custody",
        "status",
        "next_sequence",
        "genesis_hash",
        "head_hash",
        "genesis_signature",
    }
    assert created["schema"] == AGENT_ROOM_ROOM_SCHEMA
    assert created["status"] == "open"
    assert created["next_sequence"] == 0
    assert created["head_hash"] == created["genesis_hash"]
    assert [row["participant_id"] for row in created["participants"]] == [
        "alice",
        "bob",
    ]
    assert os.stat(database).st_mode & 0o777 == 0o600


def test_atomic_room_page_and_operator_audit_bind_the_verified_state(
    room: dict[str, object],
) -> None:
    store = room["store"]
    keys = room["keys"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(keys, dict)

    record = _signed_append(room, _event(room, nonce=81))
    page = store.room_page("room-1", requester_id="bob")
    assert page["room"]["next_sequence"] == 1
    assert page["room"]["head_hash"] == record["record_sha256"]
    assert [event["event_id"] for event in page["events"]] == [record["event_id"]]

    audit = store.audit_all_rooms()
    assert audit == {
        "ok": True,
        "schema": AGENT_ROOM_AUDIT_SCHEMA,
        "server_key_id": store.server_key_id,
        "participant_count": 3,
        "room_count": 1,
        "event_count": 1,
        "state_sha256": audit["state_sha256"],
        "non_executable": True,
        "execution_authority": "none",
    }
    assert len(audit["state_sha256"]) == 64
    reopened = AgentRoomStore(
        room["database"], server_private_key=keys["server"], clock=room["clock"]
    )
    assert reopened.audit_all_rooms() == audit


def test_operator_audit_rejects_unreachable_rows_and_capacity_drift(
    room: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO agent_room_memberships (room_id, participant_id, role) "
            "VALUES ('orphan-room', 'alice', 'participant')"
        )
    with pytest.raises(AgentRoomIntegrityError, match="foreign-key violation"):
        store.audit_all_rooms()

    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM agent_room_memberships WHERE room_id='orphan-room'"
        )
    monkeypatch.setattr(agent_room_module, "MAX_TOTAL_ROOMS", 0)
    with pytest.raises(AgentRoomIntegrityError, match="exceeds room capacity"):
        store.audit_all_rooms()


def test_read_only_existing_audit_never_repairs_or_mutates_bytes(
    room: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = room["database"]
    keys = room["keys"]
    assert isinstance(database, Path)
    assert isinstance(keys, dict)
    before = database.read_bytes()

    verifier = AgentRoomStore.open_existing(
        database,
        server_private_key=keys["server"],
        expected_owner_uid=os.geteuid(),
    )
    assert verifier.audit_all_rooms()["room_count"] == 1
    with pytest.raises(AgentRoomIntegrityError, match="read-only"):
        verifier.provision_participant("new-participant", _public(_key("new")))
    assert database.read_bytes() == before

    with pytest.raises(AgentRoomIntegrityError, match="signing identity differs"):
        AgentRoomStore.open_existing(
            database, server_private_key=_key("replacement-server")
        )
    assert database.read_bytes() == before

    partial = tmp_path / "partial.sqlite3"
    partial.touch(mode=0o600)
    partial.chmod(0o600)
    partial_before = partial.read_bytes()
    with pytest.raises(AgentRoomIntegrityError, match="schema differs"):
        AgentRoomStore.open_existing(partial, server_private_key=keys["server"])
    assert partial.read_bytes() == partial_before

    runtime_partial = tmp_path / "runtime-partial.sqlite3"
    runtime_partial.touch(mode=0o600)
    runtime_partial.chmod(0o600)
    runtime_partial_before = runtime_partial.read_bytes()
    with pytest.raises(AgentRoomIntegrityError, match="schema differs"):
        AgentRoomStore(runtime_partial, server_private_key=keys["server"])
    assert runtime_partial.read_bytes() == runtime_partial_before

    with pytest.raises(
        AgentRoomValidationError, match="parent must be owner-controlled"
    ):
        AgentRoomStore.open_existing(
            database,
            server_private_key=keys["server"],
            expected_owner_uid=os.geteuid() + 1,
        )
    assert database.read_bytes() == before

    runtime_uid = os.geteuid()
    monkeypatch.setattr(agent_room_module.os, "geteuid", lambda: runtime_uid + 1)
    cross_uid_verifier = AgentRoomStore.open_existing(
        database,
        server_private_key=keys["server"],
        expected_owner_uid=runtime_uid,
    )
    assert (
        cross_uid_verifier.audit_all_rooms()["state_sha256"]
        == verifier.audit_all_rooms()["state_sha256"]
    )
    assert database.read_bytes() == before


def test_external_room_ids_are_deterministic_opaque_and_owner_scoped() -> None:
    alice = derive_external_room_id("alice", "local-block-alias")
    retry = derive_external_room_id("alice", "local-block-alias")
    bob = derive_external_room_id("bob", "local-block-alias")

    assert alice == retry
    assert alice != bob
    assert alice.startswith("room_")
    assert len(alice) == len("room_") + 64
    assert "local-block-alias" not in alice


def test_exact_sqlite_schema_rejects_shadow_objects_and_triggers(
    room: dict[str, object],
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)

    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE shadow_agent_room_state (value TEXT NOT NULL);
            INSERT INTO shadow_agent_room_state VALUES ('unreachable');
            CREATE TRIGGER shadow_agent_room_trigger
            AFTER INSERT ON agent_room_participants
            BEGIN
                INSERT INTO shadow_agent_room_state VALUES (NEW.participant_id);
            END;
        """)

    with pytest.raises(AgentRoomIntegrityError, match="schema differs"):
        store.audit_all_rooms()
    with pytest.raises(AgentRoomIntegrityError, match="schema differs"):
        store.provision_participant("charlie", _public(_key("charlie")))


def test_metadata_cardinality_and_size_are_bounded_before_fetch(
    room: dict[str, object],
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO agent_room_meta (key, value) VALUES ('unexpected', 'value')"
        )
    with pytest.raises(AgentRoomIntegrityError, match="signing identity differs"):
        store.audit_all_rooms()


def test_participant_and_authorization_values_are_bounded_before_fetch(
    room: dict[str, object],
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE agent_room_memberships SET role=? "
            "WHERE room_id='room-1' AND participant_id='alice'",
            ("x" * 12,),
        )
    with pytest.raises(AgentRoomIntegrityError, match="authorization is invalid"):
        store.room_state("room-1", requester_id="alice")


def test_recovery_caps_are_checked_before_unbounded_fetches(
    room: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)

    monkeypatch.setattr(agent_room_module, "MAX_TOTAL_PARTICIPANTS", 2)
    with pytest.raises(AgentRoomIntegrityError, match="participant capacity"):
        store.audit_all_rooms()
    monkeypatch.setattr(agent_room_module, "MAX_TOTAL_PARTICIPANTS", 3)
    with pytest.raises(AgentRoomCapacityError, match="global participant"):
        store.provision_participant("charlie", _public(_key("charlie")))


def test_oversized_stored_event_is_rejected_before_json_parsing(
    room: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)
    _signed_append(room, _event(room))

    monkeypatch.setattr(agent_room_module, "MAX_STORED_CLIENT_EVENT_BYTES", 8)
    with pytest.raises(AgentRoomIntegrityError, match="safe bounds"):
        store.verify_room("room-1", requester_id="alice")


def test_persistent_caps_bound_rooms_and_reserve_terminal_close(
    room: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)

    monkeypatch.setattr(agent_room_module, "MAX_ROOMS_PER_OWNER", 1)
    with pytest.raises(AgentRoomCapacityError, match="participant room limit"):
        store.create_room("owner-overflow", owner_id="alice")

    monkeypatch.setattr(agent_room_module, "MAX_ROOMS_PER_OWNER", 16)
    monkeypatch.setattr(agent_room_module, "MAX_TOTAL_ROOMS", 1)
    with pytest.raises(AgentRoomCapacityError, match="global room limit"):
        store.create_room("global-overflow", owner_id="bob")

    monkeypatch.setattr(agent_room_module, "MAX_EVENTS_PER_PARTICIPANT", 1)
    _signed_append(room, _event(room, nonce=91))
    with pytest.raises(AgentRoomCapacityError, match="participant discussion"):
        _signed_append(room, _event(room, sequence=1, nonce=92))
    closed = _signed_append(
        room,
        _event(
            room,
            kind="close",
            sequence=1,
            nonce=93,
        ),
    )
    assert closed["client_event"]["kind"] == "close"


def test_final_room_slot_cannot_be_consumed_before_close(
    room: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_room_module, "MAX_EVENTS_PER_ROOM", 2)
    _signed_append(room, _event(room, nonce=94))
    with pytest.raises(AgentRoomCapacityError, match="reserved for close"):
        _signed_append(room, _event(room, sequence=1, nonce=95))
    close = _signed_append(
        room,
        _event(
            room,
            kind="close",
            sequence=1,
            nonce=96,
        ),
    )
    assert close["sequence"] == 1
    assert room["store"].room_state("room-1", requester_id="alice")["status"] == (
        "closed"
    )


def test_happy_path_covers_exact_event_vocabulary_and_terminal_close(
    room: dict[str, object],
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)
    proposal = _signed_append(
        room,
        _event(room, payload={"summary": "Indicative discussion only"}),
    )
    counter = _signed_append(
        room,
        _event(
            room,
            actor="bob",
            kind="counter",
            sequence=1,
            nonce=1,
            in_reply_to=str(proposal["event_id"]),
            payload={"summary": "Alternative parameters"},
        ),
    )
    question = _signed_append(
        room,
        _event(room, kind="question", sequence=2, nonce=2),
    )
    evidence = _signed_append(
        room,
        _event(
            room,
            actor="bob",
            kind="evidence",
            sequence=3,
            nonce=3,
            in_reply_to=str(question["event_id"]),
            evidence=_public_evidence(),
        ),
    )
    _signed_append(
        room,
        _event(
            room,
            kind="acknowledge",
            sequence=4,
            nonce=4,
            in_reply_to=str(evidence["event_id"]),
        ),
    )
    _signed_append(
        room,
        _event(
            room,
            actor="bob",
            kind="decline",
            sequence=5,
            nonce=5,
            in_reply_to=str(counter["event_id"]),
        ),
    )
    _signed_append(
        room,
        _event(
            room,
            kind="withdraw",
            sequence=6,
            nonce=6,
            in_reply_to=str(question["event_id"]),
        ),
    )
    closed = _signed_append(
        room,
        _event(room, kind="close", sequence=7, nonce=7),
    )

    records = store.list_events("room-1", requester_id="bob")
    assert {record["client_event"]["kind"] for record in records} == EVENT_KINDS
    assert [record["sequence"] for record in records] == list(range(8))
    assert all(record["non_executable"] is True for record in records)
    assert all(record["execution_authority"] == "none" for record in records)
    for record in records:
        assert record["schema"] == AGENT_ROOM_EVENT_SCHEMA
        assert record["can_accept"] is False
        assert record["can_order"] is False
        assert record["can_execute"] is False
        assert record["can_settle"] is False
        assert record["can_custody"] is False
    assert store.room_state("room-1", requester_id="alice")["status"] == "closed"
    assert store.verify_room("room-1", requester_id="bob") == {
        "ok": True,
        "schema": AGENT_ROOM_ROOM_SCHEMA,
        "room_id": "room-1",
        "status": "closed",
        "event_count": 8,
        "genesis_hash": room["created"]["genesis_hash"],
        "head_hash": closed["record_sha256"],
        "server_key_id": store.server_key_id,
        "non_executable": True,
        "execution_authority": "none",
    }


def test_record_is_bound_to_request_chain_and_server_signature(
    room: dict[str, object],
) -> None:
    store = room["store"]
    keys = room["keys"]
    created = room["created"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(keys, dict)
    assert isinstance(created, dict)
    event = _event(room, payload={"nested": {"value": 4}})
    signature = keys["alice"].sign(client_signing_bytes(event)).hex()
    record = store.append_event(event, client_signature_hex=signature)

    assert event["schema"] == AGENT_ROOM_CLIENT_EVENT_SCHEMA
    assert record["client_event"] == event
    assert record["client_signature"] == signature
    assert (
        record["request_sha256"]
        == hashlib.sha256(client_signing_bytes(event)).hexdigest()
    )
    assert record["previous_hash"] == created["genesis_hash"]
    assert record["event_id"] == f"evt_{record['record_sha256']}"

    claim = {
        "schema": "seiche.agent-room.server-attestation.v1",
        "room_id": "room-1",
        "sequence": 0,
        "previous_hash": created["genesis_hash"],
        "record_sha256": record["record_sha256"],
        "event_id": record["event_id"],
        "server_key_id": store.server_key_id,
        "non_executable": True,
        "execution_authority": "none",
    }
    canonical_claim = json.dumps(
        claim,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(store.server_public_key_hex)
    ).verify(
        bytes.fromhex(str(record["server_signature"])),
        b"seiche.agent-room.server-signature.v1\x00" + canonical_claim,
    )


def test_builder_copies_input_and_signed_mutation_is_rejected(
    room: dict[str, object],
) -> None:
    keys = room["keys"]
    store = room["store"]
    assert isinstance(keys, dict)
    assert isinstance(store, AgentRoomStore)
    payload = {"nested": {"values": [1, 2]}}
    event = _event(room, payload=payload)
    payload["nested"]["values"].append(3)
    assert event["payload"] == {"nested": {"values": [1, 2]}}

    signature = keys["alice"].sign(client_signing_bytes(event)).hex()
    event["payload"]["nested"]["values"].append(9)
    with pytest.raises(AgentRoomSignatureError):
        store.append_event(event, client_signature_hex=signature)
    assert store.verify_room("room-1", requester_id="alice")["event_count"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda event: event.update(kind="execute"),
        lambda event: event.update(non_executable=False),
        lambda event: event.update(schema="seiche.agent-room.client-event.v2"),
        lambda event: event.update(unexpected=True),
        lambda event: event.pop("payload"),
    ],
)
def test_client_envelope_is_closed_schema(
    room: dict[str, object], mutation: object
) -> None:
    event = _event(room)
    mutation(event)
    with pytest.raises(AgentRoomValidationError):
        client_signing_bytes(event)


@pytest.mark.parametrize(
    "payload",
    [
        {"order": "discussion"},
        {"settlementWindow": "later"},
        {"result": "accepted"},
        {"private_key": "redacted"},
        {"privateKey": "redacted"},
        {"APIKey": "redacted"},
        {"accessToken": "redacted"},
        {"authorization": "redacted"},
        {"note": "Bearer abc"},
        {"note": "-----BEGIN PRIVATE KEY-----\nabc"},
        {"can_execute": False},
        {"amount": float("nan")},
        {"amount": float("inf")},
        {"amount": 1.25},
        {"integer": 2**63},
    ],
)
def test_payload_rejects_authority_credentials_and_non_json_values(
    room: dict[str, object], payload: dict[str, object]
) -> None:
    with pytest.raises(AgentRoomValidationError):
        _event(room, payload=payload)


def test_payload_depth_node_and_byte_limits(room: dict[str, object]) -> None:
    deep: dict[str, object] = {"leaf": 1}
    for index in range(14):
        deep = {f"level{index}": deep}
    too_many_nodes = {f"group{index}": [None] * 64 for index in range(8)}
    too_many_bytes = {f"part{index}": "x" * 4096 for index in range(4)}
    for payload in (deep, too_many_nodes, too_many_bytes):
        with pytest.raises(AgentRoomValidationError):
            _event(room, payload=payload)


def test_signature_key_identity_and_domain_separation_fail_closed(
    room: dict[str, object],
) -> None:
    store = room["store"]
    keys = room["keys"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(keys, dict)
    event = _event(room)

    wrong_signature = keys["bob"].sign(client_signing_bytes(event)).hex()
    with pytest.raises(AgentRoomSignatureError):
        store.append_event(event, client_signature_hex=wrong_signature)

    event["client_key_id"] = ed25519_key_id(_public(keys["bob"]))
    signature = keys["alice"].sign(client_signing_bytes(event)).hex()
    with pytest.raises(AgentRoomSignatureError):
        store.append_event(event, client_signature_hex=signature)

    event = _event(room)
    bare_json_signature = (
        keys["alice"]
        .sign(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        .hex()
    )
    with pytest.raises(AgentRoomSignatureError):
        store.append_event(event, client_signature_hex=bare_json_signature)


def test_private_membership_is_required_for_every_read_and_write(
    room: dict[str, object],
) -> None:
    store = room["store"]
    keys = room["keys"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(keys, dict)
    for operation in (
        lambda: store.room_state("room-1", requester_id="mallory"),
        lambda: store.list_events("room-1", requester_id="mallory"),
        lambda: store.verify_room("room-1", requester_id="mallory"),
        lambda: store.room_state("not-a-room", requester_id="mallory"),
    ):
        with pytest.raises(AgentRoomAuthorizationError):
            operation()

    outsider = build_client_event(
        room_id="room-1",
        actor_id="mallory",
        client_key_id=ed25519_key_id(_public(keys["mallory"])),
        kind="proposal",
        expected_sequence=0,
        expected_head_hash=str(room["created"]["genesis_hash"]),
        nonce=_nonce(90),
        client_created_at=_timestamp(),
        payload={},
    )
    with pytest.raises(AgentRoomAuthorizationError):
        store.append_event(
            outsider,
            client_signature_hex=keys["mallory"]
            .sign(client_signing_bytes(outsider))
            .hex(),
        )


def test_nonce_replay_and_sequence_conflict_do_not_append(
    room: dict[str, object],
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)
    _signed_append(room, _event(room, nonce=10))

    replay = _event(room, sequence=1, nonce=10)
    with pytest.raises(AgentRoomReplayError):
        _signed_append(room, replay)

    stale = _event(room, actor="bob", sequence=0, nonce=11)
    with pytest.raises(AgentRoomSequenceConflict) as raised:
        _signed_append(room, stale)
    assert raised.value.current_sequence == 1
    assert (
        raised.value.current_head
        == store.room_state("room-1", requester_id="bob")["head_hash"]
    )
    assert store.verify_room("room-1", requester_id="alice")["event_count"] == 1


def test_signed_cursor_rejects_same_height_divergent_history(
    room: dict[str, object], tmp_path: Path
) -> None:
    keys = room["keys"]
    clock = room["clock"]
    created_a = room["created"]
    assert isinstance(keys, dict)
    assert isinstance(created_a, dict)

    branch_b_store = AgentRoomStore(
        tmp_path / "branch-b.sqlite3",
        server_private_key=keys["server"],
        clock=clock,
    )
    for participant in ("alice", "bob", "mallory"):
        branch_b_store.provision_participant(participant, _public(keys[participant]))
    created_b = branch_b_store.create_room(
        "room-1", owner_id="alice", participant_ids=("bob",)
    )
    assert created_b["genesis_hash"] == created_a["genesis_hash"]
    branch_b = {**room, "store": branch_b_store, "created": created_b}

    branch_a_first = _signed_append(
        room, _event(room, nonce=110, payload={"branch": "A"})
    )
    prepared_for_a = _event(
        room, sequence=1, nonce=111, payload={"summary": "prepared on A"}
    )
    signature_for_a = keys["alice"].sign(client_signing_bytes(prepared_for_a)).hex()
    branch_b_first = _signed_append(
        branch_b, _event(branch_b, nonce=112, payload={"branch": "B"})
    )
    assert branch_a_first["record_sha256"] != branch_b_first["record_sha256"]

    with pytest.raises(AgentRoomSequenceConflict) as raised:
        branch_b_store.append_event(
            prepared_for_a, client_signature_hex=signature_for_a
        )
    assert raised.value.current_sequence == 1
    assert raised.value.current_head == branch_b_first["record_sha256"]
    assert (
        branch_b_store.verify_room("room-1", requester_id="alice")["event_count"] == 1
    )


def test_concurrent_same_cursor_has_exactly_one_winner(
    room: dict[str, object],
) -> None:
    store = room["store"]
    keys = room["keys"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(keys, dict)
    alice_event = _event(room, actor="alice", nonce=20)
    bob_event = _event(room, actor="bob", nonce=21)
    candidates = [(alice_event, keys["alice"]), (bob_event, keys["bob"])]
    barrier = threading.Barrier(2)
    results: list[object] = []

    def append(candidate: tuple[dict[str, object], Ed25519PrivateKey]) -> None:
        event, key = candidate
        signature = key.sign(client_signing_bytes(event)).hex()
        barrier.wait()
        try:
            results.append(store.append_event(event, client_signature_hex=signature))
        except AgentRoomSequenceConflict as exc:
            results.append(exc)

    threads = [
        threading.Thread(target=append, args=(candidate,)) for candidate in candidates
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, AgentRoomSequenceConflict) for result in results) == 1
    assert store.verify_room("room-1", requester_id="alice")["event_count"] == 1


def test_transition_rules_owner_close_withdraw_and_terminal_state(
    room: dict[str, object],
) -> None:
    proposal = _signed_append(room, _event(room, nonce=30))

    with pytest.raises(AgentRoomAuthorizationError):
        _signed_append(
            room,
            _event(room, actor="bob", kind="close", sequence=1, nonce=31),
        )
    with pytest.raises(AgentRoomAuthorizationError):
        _signed_append(
            room,
            _event(
                room,
                actor="bob",
                kind="withdraw",
                sequence=1,
                nonce=32,
                in_reply_to=str(proposal["event_id"]),
            ),
        )
    withdrawn = _signed_append(
        room,
        _event(
            room,
            kind="withdraw",
            sequence=1,
            nonce=33,
            in_reply_to=str(proposal["event_id"]),
        ),
    )
    with pytest.raises(AgentRoomValidationError):
        _signed_append(
            room,
            _event(
                room,
                actor="bob",
                kind="counter",
                sequence=2,
                nonce=34,
                in_reply_to=str(proposal["event_id"]),
            ),
        )
    _signed_append(room, _event(room, kind="close", sequence=2, nonce=35))
    with pytest.raises(AgentRoomClosedError):
        _signed_append(
            room,
            _event(
                room,
                actor="bob",
                kind="acknowledge",
                sequence=3,
                nonce=36,
                in_reply_to=str(withdrawn["event_id"]),
            ),
        )


@pytest.mark.parametrize(
    ("kind", "target", "expected"),
    [
        ("counter", None, "requires in_reply_to"),
        ("acknowledge", None, "requires in_reply_to"),
        ("decline", None, "requires in_reply_to"),
        ("withdraw", None, "requires in_reply_to"),
        ("proposal", "evt_" + "0" * 64, "must not use in_reply_to"),
        ("close", "evt_" + "0" * 64, "must not use in_reply_to"),
    ],
)
def test_required_and_forbidden_reply_edges(
    room: dict[str, object], kind: str, target: str | None, expected: str
) -> None:
    with pytest.raises(AgentRoomValidationError, match=expected):
        _signed_append(
            room,
            _event(room, kind=kind, in_reply_to=target),
        )


def test_evidence_preserves_clocks_rights_and_restrictions(
    room: dict[str, object],
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)
    metadata = {
        **_public_evidence(),
        "evidence_class": "restricted",
        "rights": {
            "status": "restricted",
            "redistributable": False,
            "license": None,
            "attribution": None,
        },
    }
    record = _signed_append(
        room,
        _event(room, kind="evidence", evidence=metadata),
    )
    assert record["client_event"]["evidence"] == metadata
    assert (
        store.list_events("room-1", requester_id="bob")[0]["client_event"]["evidence"]
        == metadata
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence.update(
            evidence_as_of=_timestamp(NOW + timedelta(minutes=1))
        ),
        lambda evidence: evidence.update(
            knowledge_at=_timestamp(NOW + timedelta(seconds=1))
        ),
        lambda evidence: evidence["rights"].update(
            redistributable=True, status="licensed"
        ),
        lambda evidence: evidence["rights"].update(license=None),
        lambda evidence: evidence.update(evidence_class="restricted"),
        lambda evidence: evidence.update(content_sha256="A" * 64),
        lambda evidence: evidence.update(source_url="http://example.gov/data"),
        lambda evidence: evidence.update(source_url="https://u:p@example.gov/data"),
        lambda evidence: evidence.update(
            source_url="https://example.gov/data#fragment"
        ),
        lambda evidence: evidence.update(
            source_url="https://example.gov/data?token=secret"
        ),
        lambda evidence: evidence.update(
            source_url="https://example.gov/data?accessToken=secret"
        ),
    ],
)
def test_invalid_evidence_clocks_rights_digest_and_url_fail_closed(
    room: dict[str, object], mutate: object
) -> None:
    evidence = _public_evidence()
    mutate(evidence)
    with pytest.raises(AgentRoomValidationError):
        _event(room, kind="evidence", evidence=evidence)


def test_evidence_event_requires_metadata(room: dict[str, object]) -> None:
    with pytest.raises(AgentRoomValidationError):
        _event(room, kind="evidence")


@pytest.mark.parametrize(
    "created_at",
    [
        NOW - timedelta(minutes=15, microseconds=1),
        NOW + timedelta(minutes=5, microseconds=1),
    ],
)
def test_stale_or_future_client_clock_is_rejected(
    room: dict[str, object], created_at: datetime
) -> None:
    with pytest.raises(AgentRoomValidationError):
        _signed_append(room, _event(room, created_at=created_at))


def test_server_clock_must_be_aware_and_monotonic(room: dict[str, object]) -> None:
    clock = room["clock"]
    assert isinstance(clock, MutableClock)
    _signed_append(room, _event(room))
    clock.value = NOW - timedelta(seconds=1)
    with pytest.raises(AgentRoomIntegrityError, match="moved backwards"):
        _signed_append(room, _event(room, actor="bob", sequence=1, nonce=1))
    clock.value = datetime(2026, 9, 2, 12, 0, 0)
    with pytest.raises(AgentRoomIntegrityError, match="timezone-aware"):
        _signed_append(room, _event(room, actor="bob", sequence=1, nonce=2))


def test_pagination_is_bounded_ordered_and_defensively_copied(
    room: dict[str, object],
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)
    for sequence in range(3):
        _signed_append(room, _event(room, sequence=sequence, nonce=sequence))
    page = store.list_events("room-1", requester_id="alice", after_sequence=0, limit=1)
    assert [record["sequence"] for record in page] == [1]
    page[0]["client_event"]["payload"]["local"] = "mutation"
    assert (
        "local"
        not in store.list_events(
            "room-1", requester_id="alice", after_sequence=0, limit=1
        )[0]["client_event"]["payload"]
    )

    for after, limit in [(-2, 1), (MAX_EVENTS_PER_ROOM, 1), (-1, 0), (-1, 201)]:
        with pytest.raises(AgentRoomValidationError):
            store.list_events(
                "room-1",
                requester_id="alice",
                after_sequence=after,
                limit=limit,
            )


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        (
            "UPDATE agent_room_events SET previous_hash=? WHERE room_id=? AND sequence=0",
            ("f" * 64, "room-1"),
        ),
        (
            "UPDATE agent_room_events SET server_signature=? WHERE room_id=? AND sequence=0",
            ("0" * 128, "room-1"),
        ),
        (
            "UPDATE agent_rooms SET head_hash=? WHERE room_id=?",
            ("e" * 64, "room-1"),
        ),
    ],
)
def test_database_tampering_is_detected_before_read_or_append(
    room: dict[str, object], sql: str, params: tuple[str, ...]
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)
    _signed_append(room, _event(room))
    with sqlite3.connect(database) as connection:
        connection.execute(sql, params)

    with pytest.raises(AgentRoomIntegrityError):
        store.list_events("room-1", requester_id="alice")
    with pytest.raises(AgentRoomIntegrityError):
        _signed_append(room, _event(room, actor="bob", sequence=1, nonce=1))


def test_stored_client_json_tampering_and_duplicate_keys_are_detected(
    room: dict[str, object],
) -> None:
    store = room["store"]
    database = room["database"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(database, Path)
    _signed_append(room, _event(room))
    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT client_event_json FROM agent_room_events"
        ).fetchone()[0]
        connection.execute(
            "UPDATE agent_room_events SET client_event_json=?",
            (raw[:-1] + ',"payload":{}}',),
        )
    with pytest.raises(AgentRoomIntegrityError, match="duplicate JSON keys"):
        store.verify_room("room-1", requester_id="alice")


def test_server_key_substitution_is_rejected(room: dict[str, object]) -> None:
    database = room["database"]
    assert isinstance(database, Path)
    with pytest.raises(AgentRoomIntegrityError, match="signing identity differs"):
        AgentRoomStore(database, server_private_key=_key("replacement-server"))


def test_initialization_seal_is_private_atomic_and_key_bound(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    server_key = _key("initialization-seal")
    seal = tmp_path / agent_room_module.AGENT_ROOM_INITIALIZATION_SEAL_FILENAME

    created = agent_room_module.create_initialization_seal(
        seal,
        server_private_key=server_key,
    )

    assert created["schema"] == agent_room_module.AGENT_ROOM_INITIALIZATION_SEAL_SCHEMA
    assert created["server_key_id"] == ed25519_key_id(_public(server_key))
    assert stat.S_IMODE(seal.stat().st_mode) == 0o600
    assert (
        agent_room_module.verify_initialization_seal(
            seal,
            server_private_key=server_key,
        )
        == created
    )
    with pytest.raises(AgentRoomIntegrityError, match="identity is invalid"):
        agent_room_module.verify_initialization_seal(
            seal,
            server_private_key=_key("replacement-server"),
        )


def test_require_existing_never_recreates_a_missing_database(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(AgentRoomIntegrityError, match="database is unavailable"):
        AgentRoomStore(
            database,
            server_private_key=_key("server"),
            require_existing=True,
        )

    assert not database.exists()


def test_database_path_rejects_public_files_symlinks_and_memory(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    private_key = _key("separate-server")
    public_file = tmp_path / "public.sqlite3"
    public_file.touch(mode=0o644)
    public_file.chmod(0o644)
    with pytest.raises(AgentRoomIntegrityError, match="owner-only"):
        AgentRoomStore(public_file, server_private_key=private_key)

    target = tmp_path / "target.sqlite3"
    target.touch(mode=0o600)
    symlink = tmp_path / "link.sqlite3"
    symlink.symlink_to(target)
    with pytest.raises(AgentRoomValidationError, match="symlink"):
        AgentRoomStore(symlink, server_private_key=private_key)

    with pytest.raises(AgentRoomValidationError, match="on-disk"):
        AgentRoomStore(":memory:", server_private_key=private_key)


def test_participant_and_room_control_plane_collisions_fail_closed(
    room: dict[str, object],
) -> None:
    store = room["store"]
    keys = room["keys"]
    assert isinstance(store, AgentRoomStore)
    assert isinstance(keys, dict)
    with pytest.raises(AgentRoomAuthorizationError):
        store.provision_participant("alice", _public(keys["bob"]))
    with pytest.raises(AgentRoomAuthorizationError):
        store.provision_participant("charlie", _public(keys["alice"]))
    with pytest.raises(AgentRoomValidationError, match="already exists"):
        store.create_room("room-1", owner_id="alice")
    with pytest.raises(AgentRoomValidationError, match="unique"):
        store.create_room("room-2", owner_id="alice", participant_ids=("alice",))
    with pytest.raises(AgentRoomAuthorizationError, match="provisioned"):
        store.create_room("room-3", owner_id="alice", participant_ids=("nobody",))


def test_malformed_signature_nonce_timestamp_and_plain_mapping_rejected(
    room: dict[str, object],
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)
    event = _event(room)
    with pytest.raises(AgentRoomSignatureError):
        store.append_event(event, client_signature_hex="0" * 126)
    for invalid_nonce in ("short", "+" * 22, "A" * 129):
        invalid_event = {**event, "nonce": invalid_nonce}
        with pytest.raises(AgentRoomValidationError):
            client_signing_bytes(invalid_event)
    with pytest.raises(AgentRoomValidationError):
        build_client_event(
            room_id="room-1",
            actor_id="alice",
            client_key_id=str(event["client_key_id"]),
            kind="proposal",
            expected_sequence=0,
            expected_head_hash=str(event["expected_head_hash"]),
            nonce=_nonce(50),
            client_created_at="2026-09-02T12:00:00+00:00",
            payload={},
        )
    with pytest.raises(AgentRoomValidationError, match="plain JSON object"):
        build_client_event(
            room_id="room-1",
            actor_id="alice",
            client_key_id=str(event["client_key_id"]),
            kind="proposal",
            expected_sequence=0,
            expected_head_hash=str(event["expected_head_hash"]),
            nonce=_nonce(51),
            client_created_at=_timestamp(),
            payload=dict,
        )


def test_invalid_server_signature_cannot_be_mistaken_for_valid_record(
    room: dict[str, object],
) -> None:
    store = room["store"]
    assert isinstance(store, AgentRoomStore)
    record = _signed_append(room, _event(room))
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(store.server_public_key_hex)
        ).verify(
            bytes.fromhex("0" * 128),
            bytes.fromhex(str(record["record_sha256"])),
        )
