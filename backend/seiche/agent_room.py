"""Private, signed, non-executable Agent Room event log.

The module is deliberately transport-neutral.  REST and MCP adapters may pass
authenticated participant identities into :class:`AgentRoomStore`, but this
core never accepts an order, calls a broker, settles value, or holds custody.
Client signatures authenticate an append request; the server signature and
per-room hash chain make accepted history tamper-evident.  Neither signature is
evidence that a proposal is true, lawful, suitable, or accepted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, cast
from urllib.parse import parse_qsl, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

AGENT_ROOM_CLIENT_EVENT_SCHEMA = "seiche.agent-room.client-event.v1"
AGENT_ROOM_EVENT_SCHEMA = "seiche.agent-room.event.v1"
AGENT_ROOM_ROOM_SCHEMA = "seiche.agent-room.room.v1"
AGENT_ROOM_SERVER_ATTESTATION_SCHEMA = "seiche.agent-room.server-attestation.v1"
AGENT_ROOM_AUDIT_SCHEMA = "seiche.agent-room.audit.v1"
AGENT_ROOM_INITIALIZATION_SEAL_SCHEMA = "seiche.agent-room.initialization-seal.v1"
AGENT_ROOM_INITIALIZATION_SEAL_FILENAME = "agent-room-initialized.json"

EVENT_KINDS = frozenset(
    {
        "proposal",
        "counter",
        "question",
        "evidence",
        "acknowledge",
        "decline",
        "withdraw",
        "close",
    }
)
EVIDENCE_CLASSES = frozenset(
    {
        "observed",
        "derived",
        "inferred",
        "provisional",
        "unknown",
        "unavailable",
        "restricted",
    }
)
RIGHTS_STATUSES = frozenset({"public", "licensed", "restricted", "unknown"})

MAX_PARTICIPANTS = 32
MAX_EVENTS_PER_ROOM = 4_096
MAX_ROOMS_PER_OWNER = 16
MAX_TOTAL_ROOMS = 256
MAX_TOTAL_PARTICIPANTS = 4_096
MAX_EVENTS_PER_PARTICIPANT = 1_024
MAX_TOTAL_DISCUSSION_EVENTS = 8_192
MAX_PAYLOAD_BYTES = 16 * 1024
MAX_STORED_CLIENT_EVENT_BYTES = 32 * 1024
MAX_STORED_EVENT_ROW_BYTES = 64 * 1024
MAX_JSON_DEPTH = 12
MAX_JSON_NODES = 512
MAX_JSON_CONTAINER_ITEMS = 64
MAX_STRING_BYTES = 4_096
MAX_NONCE_CHARS = 128
MIN_NONCE_CHARS = 22
MAX_LIST_LIMIT = 200
MAX_CLIENT_EVENT_AGE_SECONDS = 15 * 60
MAX_CLIENT_CLOCK_SKEW_SECONDS = 5 * 60

_DATABASE_SCHEMA_VERSION = "1"
_GENESIS_DOMAIN = b"seiche.agent-room.genesis.v1\x00"
_CLIENT_SIGNATURE_DOMAIN = b"seiche.agent-room.client-signature.v1\x00"
_RECORD_HASH_DOMAIN = b"seiche.agent-room.record-hash.v1\x00"
_SERVER_SIGNATURE_DOMAIN = b"seiche.agent-room.server-signature.v1\x00"
_AUDIT_DOMAIN = b"seiche.agent-room.audit.v1\x00"
_EXTERNAL_ROOM_ID_DOMAIN = b"seiche.agent-room.external-room-id.v1\x00"
_INITIALIZATION_SEAL_DOMAIN = b"seiche.agent-room.initialization-seal.v1\x00"
_MAX_INITIALIZATION_SEAL_BYTES = 4 * 1024

_SCHEMA_DDL = (
    (
        "table",
        "agent_room_meta",
        "agent_room_meta",
        """CREATE TABLE agent_room_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
    ),
    (
        "table",
        "agent_room_participants",
        "agent_room_participants",
        """CREATE TABLE agent_room_participants (
            participant_id TEXT PRIMARY KEY,
            public_key_hex TEXT NOT NULL UNIQUE,
            key_id TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL CHECK (enabled = 1),
            created_at TEXT NOT NULL
        )""",
    ),
    (
        "table",
        "agent_rooms",
        "agent_rooms",
        """CREATE TABLE agent_rooms (
            room_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES agent_room_participants(participant_id),
            created_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
            next_sequence INTEGER NOT NULL CHECK (
                next_sequence >= 0 AND next_sequence <= 4096
            ),
            genesis_hash TEXT NOT NULL,
            head_hash TEXT NOT NULL,
            server_key_id TEXT NOT NULL,
            server_public_key_hex TEXT NOT NULL,
            genesis_signature TEXT NOT NULL
        )""",
    ),
    (
        "table",
        "agent_room_memberships",
        "agent_room_memberships",
        """CREATE TABLE agent_room_memberships (
            room_id TEXT NOT NULL REFERENCES agent_rooms(room_id),
            participant_id TEXT NOT NULL REFERENCES agent_room_participants(participant_id),
            role TEXT NOT NULL CHECK (role IN ('owner', 'participant')),
            PRIMARY KEY (room_id, participant_id)
        )""",
    ),
    (
        "table",
        "agent_room_events",
        "agent_room_events",
        """CREATE TABLE agent_room_events (
            room_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (
                sequence >= 0 AND sequence < 4096
            ),
            event_id TEXT NOT NULL UNIQUE,
            actor_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (
                kind IN (
                    'proposal', 'counter', 'question', 'evidence',
                    'acknowledge', 'decline', 'withdraw', 'close'
                )
            ),
            nonce TEXT NOT NULL,
            in_reply_to TEXT,
            client_event_json TEXT NOT NULL,
            client_public_key_hex TEXT NOT NULL,
            client_signature TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            record_sha256 TEXT NOT NULL UNIQUE,
            server_received_at TEXT NOT NULL,
            server_key_id TEXT NOT NULL,
            server_public_key_hex TEXT NOT NULL,
            server_signature TEXT NOT NULL,
            non_executable INTEGER NOT NULL CHECK (non_executable = 1),
            execution_authority TEXT NOT NULL CHECK (execution_authority = 'none'),
            can_accept INTEGER NOT NULL CHECK (can_accept = 0),
            can_order INTEGER NOT NULL CHECK (can_order = 0),
            can_execute INTEGER NOT NULL CHECK (can_execute = 0),
            can_settle INTEGER NOT NULL CHECK (can_settle = 0),
            can_custody INTEGER NOT NULL CHECK (can_custody = 0),
            PRIMARY KEY (room_id, sequence),
            UNIQUE (actor_id, nonce),
            FOREIGN KEY (room_id, actor_id)
                REFERENCES agent_room_memberships(room_id, participant_id)
        )""",
    ),
    (
        "index",
        "agent_room_events_reply_idx",
        "agent_room_events",
        """CREATE INDEX agent_room_events_reply_idx
            ON agent_room_events(room_id, in_reply_to)""",
    ),
)

_SCHEMA_AUTO_INDEXES = frozenset(
    {
        ("index", "sqlite_autoindex_agent_room_events_1", "agent_room_events"),
        ("index", "sqlite_autoindex_agent_room_events_2", "agent_room_events"),
        ("index", "sqlite_autoindex_agent_room_events_3", "agent_room_events"),
        ("index", "sqlite_autoindex_agent_room_events_4", "agent_room_events"),
        (
            "index",
            "sqlite_autoindex_agent_room_memberships_1",
            "agent_room_memberships",
        ),
        ("index", "sqlite_autoindex_agent_room_meta_1", "agent_room_meta"),
        (
            "index",
            "sqlite_autoindex_agent_room_participants_1",
            "agent_room_participants",
        ),
        (
            "index",
            "sqlite_autoindex_agent_room_participants_2",
            "agent_room_participants",
        ),
        (
            "index",
            "sqlite_autoindex_agent_room_participants_3",
            "agent_room_participants",
        ),
        ("index", "sqlite_autoindex_agent_rooms_1", "agent_rooms"),
    }
)
_EXPECTED_SCHEMA_OBJECTS = {
    **{
        (object_type, name, table_name): " ".join(sql.split())
        for object_type, name, table_name, sql in _SCHEMA_DDL
    },
    **{key: None for key in _SCHEMA_AUTO_INDEXES},
}
_MAX_SCHEMA_SQL_BYTES = 32 * 1024

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")
_PAYLOAD_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}")
_NONCE_RE = re.compile(rf"[A-Za-z0-9_-]{{{MIN_NONCE_CHARS},{MAX_NONCE_CHARS}}}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PUBLIC_KEY_RE = re.compile(r"[0-9a-f]{64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_EVENT_ID_RE = re.compile(r"evt_[0-9a-f]{64}")

_CLIENT_EVENT_FIELDS = frozenset(
    {
        "schema",
        "room_id",
        "actor_id",
        "client_key_id",
        "kind",
        "expected_sequence",
        "expected_head_hash",
        "nonce",
        "client_created_at",
        "in_reply_to",
        "non_executable",
        "payload",
        "evidence",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "source_id",
        "source_url",
        "evidence_as_of",
        "knowledge_at",
        "evidence_class",
        "rights",
        "content_sha256",
    }
)
_RIGHTS_FIELDS = frozenset({"status", "redistributable", "license", "attribution"})
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passphrase",
        "private_key",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
        "access_token",
    }
)
_PROHIBITED_ACTION_ROOTS = frozenset(
    {"accept", "order", "execute", "execution", "settle", "settlement", "custody"}
)
_PROHIBITED_ACTION_VALUES = frozenset(
    {
        "accept",
        "accepted",
        "acceptance",
        "order",
        "ordered",
        "execute",
        "executed",
        "execution",
        "settle",
        "settled",
        "settlement",
        "custody",
    }
)
_RESERVED_PAYLOAD_FIELDS = frozenset(
    {
        "non_executable",
        "execution_authority",
        "can_accept",
        "can_order",
        "can_execute",
        "can_settle",
        "can_custody",
        "client_signature",
        "server_signature",
    }
)
_SENSITIVE_KEY_IDENTITIES = frozenset(
    name.replace("_", "") for name in _SENSITIVE_KEY_NAMES
)
_RESERVED_PAYLOAD_IDENTITIES = frozenset(
    name.replace("_", "") for name in _RESERVED_PAYLOAD_FIELDS
)


class AgentRoomError(ValueError):
    """Base class for a fail-closed Agent Room rejection."""


class AgentRoomValidationError(AgentRoomError):
    """An input violates the closed Agent Room contract."""


class AgentRoomAuthorizationError(AgentRoomError):
    """The authenticated participant is not authorized for the room action."""


class AgentRoomSignatureError(AgentRoomError):
    """An Ed25519 identity or signature does not verify."""


class AgentRoomSequenceConflict(AgentRoomError):
    """The signed optimistic-concurrency cursor is stale or premature."""

    def __init__(self, current_sequence: int, current_head: str) -> None:
        super().__init__("agent room sequence conflict")
        self.current_sequence = current_sequence
        self.current_head = current_head


class AgentRoomReplayError(AgentRoomError):
    """A participant nonce has already been consumed."""


class AgentRoomClosedError(AgentRoomError):
    """The discussion has been closed and cannot accept another event."""


class AgentRoomCapacityError(AgentRoomError):
    """A persistent preview storage or lifecycle bound has been reached."""


class AgentRoomIntegrityError(AgentRoomError):
    """Stored membership, history, signatures, or chain state is inconsistent."""


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise AgentRoomValidationError(f"{field} is invalid")
    return value


def _public_key_hex(value: object, *, field: str = "public_key_hex") -> str:
    if not isinstance(value, str) or _PUBLIC_KEY_RE.fullmatch(value) is None:
        raise AgentRoomSignatureError(f"{field} must be canonical Ed25519 hex")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))
    except (TypeError, ValueError) as exc:
        raise AgentRoomSignatureError(f"{field} is not an Ed25519 public key") from exc
    return value


def ed25519_key_id(public_key_hex: str) -> str:
    """Return the stable SHA-256 identity of a canonical raw Ed25519 key."""

    canonical = _public_key_hex(public_key_hex)
    return hashlib.sha256(bytes.fromhex(canonical)).hexdigest()


def _signature_hex(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SIGNATURE_RE.fullmatch(value) is None:
        raise AgentRoomSignatureError(f"{field} must be canonical Ed25519 hex")
    return value


def _timestamp(value: datetime, *, field: str) -> str:
    if not isinstance(value, datetime):
        raise AgentRoomValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AgentRoomValidationError(f"{field} must be timezone-aware")
    normalized = value.astimezone(UTC)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 27 or not value.endswith("Z"):
        raise AgentRoomValidationError(f"{field} must be a canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AgentRoomValidationError(f"{field} is not an ISO-8601 timestamp") from exc
    if _timestamp(parsed, field=field) != value:
        raise AgentRoomValidationError(f"{field} is not in canonical UTC form")
    return parsed.astimezone(UTC)


def _key_tokens(key: str) -> tuple[str, ...]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return tuple(token for token in re.split(r"[^a-z0-9]+", separated.lower()) if token)


def _key_identity(key: str) -> str:
    """Collapse snake, kebab, dotted, camel, and acronym spellings alike."""

    return re.sub(r"[^a-z0-9]+", "", key.casefold())


def _guard_payload_key(key: str, *, field: str) -> None:
    if _PAYLOAD_KEY_RE.fullmatch(key) is None:
        raise AgentRoomValidationError(f"{field} contains an invalid object key")
    identity = _key_identity(key)
    if identity in _SENSITIVE_KEY_IDENTITIES:
        raise AgentRoomValidationError(f"{field} must not contain credential fields")
    if identity in _RESERVED_PAYLOAD_IDENTITIES:
        raise AgentRoomValidationError(f"{field} must not shadow authority fields")
    tokens = _key_tokens(key)
    if any(
        token == root or token.startswith(root)
        for token in tokens
        for root in _PROHIBITED_ACTION_ROOTS
    ):
        raise AgentRoomValidationError(f"{field} must not contain executable fields")


def _guard_string(value: str, *, field: str) -> None:
    if len(value.encode("utf-8")) > MAX_STRING_BYTES:
        raise AgentRoomValidationError(f"{field} exceeds the string size limit")
    folded = value.strip().casefold()
    if folded in _PROHIBITED_ACTION_VALUES:
        raise AgentRoomValidationError(f"{field} contains a prohibited action")
    if "-----begin " in folded and "private key-----" in folded:
        raise AgentRoomValidationError(f"{field} must not contain private key material")
    if folded.startswith("bearer "):
        raise AgentRoomValidationError(f"{field} must not contain bearer credentials")


def _copy_json(
    value: object,
    *,
    field: str,
    guard_payload: bool,
    depth: int = 0,
    budget: list[int] | None = None,
) -> object:
    if depth > MAX_JSON_DEPTH:
        raise AgentRoomValidationError(f"{field} exceeds JSON depth {MAX_JSON_DEPTH}")
    nodes = [0] if budget is None else budget
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES:
        raise AgentRoomValidationError(
            f"{field} exceeds JSON node limit {MAX_JSON_NODES}"
        )

    if value is None or type(value) is bool or type(value) is int:
        if type(value) is int and not -(2**63) <= value <= 2**63 - 1:
            raise AgentRoomValidationError(
                f"{field} integer is outside signed 64-bit range"
            )
        return value
    if type(value) is float:
        raise AgentRoomValidationError(
            f"{field} must encode decimal values as strings, not binary floats"
        )
    if type(value) is str:
        _guard_string(value, field=field)
        return value
    if type(value) is list:
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise AgentRoomValidationError(f"{field} has too many array items")
        return [
            _copy_json(
                item,
                field=f"{field}[{index}]",
                guard_payload=guard_payload,
                depth=depth + 1,
                budget=nodes,
            )
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise AgentRoomValidationError(f"{field} has too many object fields")
        copied: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise AgentRoomValidationError(f"{field} object keys must be strings")
            if guard_payload:
                _guard_payload_key(key, field=field)
            elif not key or len(key.encode("utf-8")) > 128:
                raise AgentRoomValidationError(
                    f"{field} contains an invalid object key"
                )
            copied[key] = _copy_json(
                item,
                field=f"{field}.{key}",
                guard_payload=guard_payload,
                depth=depth + 1,
                budget=nodes,
            )
        return dict(sorted(copied.items()))
    raise AgentRoomValidationError(
        f"{field} contains unsupported value {type(value).__name__}"
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json_object(value: str, *, field: str) -> dict[str, object]:
    def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in rows:
            if key in result:
                raise AgentRoomIntegrityError(f"{field} contains duplicate JSON keys")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AgentRoomIntegrityError(f"{field} contains a non-finite number")
            ),
        )
    except AgentRoomIntegrityError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AgentRoomIntegrityError(f"{field} is not strict JSON") from exc
    if type(parsed) is not dict:
        raise AgentRoomIntegrityError(f"{field} is not a JSON object")
    return parsed


def _digest(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + _canonical_json_bytes(value)).hexdigest()


def derive_external_room_id(owner_id: str, room_alias: str) -> str:
    """Derive an opaque owner-scoped room ID from a caller-local alias."""

    owner = _identifier(owner_id, field="owner_id")
    alias = _identifier(room_alias, field="room_id")
    return f"room_{_digest(_EXTERNAL_ROOM_ID_DOMAIN, [owner, alias])}"


def _validate_source_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2_048:
        raise AgentRoomValidationError("evidence.source_url is invalid")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise AgentRoomValidationError("evidence.source_url is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise AgentRoomValidationError(
            "evidence.source_url must be credential-free HTTPS without a fragment"
        )
    for key, _item in parse_qsl(parsed.query, keep_blank_values=True):
        if _key_identity(key) in _SENSITIVE_KEY_IDENTITIES:
            raise AgentRoomValidationError(
                "evidence.source_url must not contain credential query fields"
            )
    return value


def _optional_bounded_text(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AgentRoomValidationError(
            f"{field} must be null or non-blank trimmed text"
        )
    if len(value.encode("utf-8")) > maximum:
        raise AgentRoomValidationError(f"{field} exceeds its size limit")
    _guard_string(value, field=field)
    return value


def _normalize_evidence(
    value: object, *, client_created_at: datetime
) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _EVIDENCE_FIELDS:
        raise AgentRoomValidationError("evidence must contain exactly the v1 fields")
    source_id = _identifier(value["source_id"], field="evidence.source_id")
    source_url = _validate_source_url(value["source_url"])
    evidence_at = _parse_timestamp(
        value["evidence_as_of"], field="evidence.evidence_as_of"
    )
    knowledge_at = _parse_timestamp(
        value["knowledge_at"], field="evidence.knowledge_at"
    )
    if evidence_at > knowledge_at or knowledge_at > client_created_at:
        raise AgentRoomValidationError(
            "evidence clocks must satisfy evidence_as_of <= knowledge_at <= client_created_at"
        )
    evidence_class = value["evidence_class"]
    if not isinstance(evidence_class, str) or evidence_class not in EVIDENCE_CLASSES:
        raise AgentRoomValidationError("evidence.evidence_class is invalid")

    rights = value["rights"]
    if type(rights) is not dict or set(rights) != _RIGHTS_FIELDS:
        raise AgentRoomValidationError(
            "evidence.rights must contain exactly the v1 fields"
        )
    rights_status = rights["status"]
    if not isinstance(rights_status, str) or rights_status not in RIGHTS_STATUSES:
        raise AgentRoomValidationError("evidence.rights.status is invalid")
    redistributable = rights["redistributable"]
    if type(redistributable) is not bool:
        raise AgentRoomValidationError(
            "evidence.rights.redistributable must be boolean"
        )
    license_text = _optional_bounded_text(
        rights["license"], field="evidence.rights.license", maximum=512
    )
    attribution = _optional_bounded_text(
        rights["attribution"], field="evidence.rights.attribution", maximum=512
    )
    if redistributable and (
        rights_status != "public" or license_text is None or attribution is None
    ):
        raise AgentRoomValidationError(
            "redistributable evidence requires public rights, license, and attribution"
        )
    if evidence_class == "restricted" and rights_status != "restricted":
        raise AgentRoomValidationError(
            "restricted evidence class requires restricted rights status"
        )

    content_sha256 = value["content_sha256"]
    if content_sha256 is not None and (
        not isinstance(content_sha256, str)
        or _SHA256_RE.fullmatch(content_sha256) is None
    ):
        raise AgentRoomValidationError("evidence.content_sha256 is invalid")

    return {
        "source_id": source_id,
        "source_url": source_url,
        "evidence_as_of": value["evidence_as_of"],
        "knowledge_at": value["knowledge_at"],
        "evidence_class": evidence_class,
        "rights": {
            "status": rights_status,
            "redistributable": redistributable,
            "license": license_text,
            "attribution": attribution,
        },
        "content_sha256": content_sha256,
    }


def _normalize_client_event(event: object) -> dict[str, object]:
    if type(event) is not dict or set(event) != _CLIENT_EVENT_FIELDS:
        raise AgentRoomValidationError(
            "client event must contain exactly the v1 signed fields"
        )
    if event["schema"] != AGENT_ROOM_CLIENT_EVENT_SCHEMA:
        raise AgentRoomValidationError("client event schema is invalid")
    room_id = _identifier(event["room_id"], field="room_id")
    actor_id = _identifier(event["actor_id"], field="actor_id")
    client_key_id = event["client_key_id"]
    if (
        not isinstance(client_key_id, str)
        or _SHA256_RE.fullmatch(client_key_id) is None
    ):
        raise AgentRoomValidationError("client_key_id is invalid")
    kind = event["kind"]
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise AgentRoomValidationError("event kind is not allowed")
    expected_sequence = event["expected_sequence"]
    if (
        isinstance(expected_sequence, bool)
        or not isinstance(expected_sequence, int)
        or not 0 <= expected_sequence < MAX_EVENTS_PER_ROOM
    ):
        raise AgentRoomValidationError("expected_sequence is outside the room limit")
    expected_head_hash = event["expected_head_hash"]
    if (
        not isinstance(expected_head_hash, str)
        or _SHA256_RE.fullmatch(expected_head_hash) is None
    ):
        raise AgentRoomValidationError("expected_head_hash is invalid")
    nonce = event["nonce"]
    if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
        raise AgentRoomValidationError(
            f"nonce must be {MIN_NONCE_CHARS}-{MAX_NONCE_CHARS} base64url characters"
        )
    client_created_at = _parse_timestamp(
        event["client_created_at"], field="client_created_at"
    )
    in_reply_to = event["in_reply_to"]
    if in_reply_to is not None and (
        not isinstance(in_reply_to, str) or _EVENT_ID_RE.fullmatch(in_reply_to) is None
    ):
        raise AgentRoomValidationError("in_reply_to is invalid")
    if event["non_executable"] is not True:
        raise AgentRoomValidationError("non_executable must be true")
    if type(event["payload"]) is not dict:
        raise AgentRoomValidationError("payload must be a JSON object")
    payload = _copy_json(event["payload"], field="payload", guard_payload=True)
    if len(_canonical_json_bytes(payload)) > MAX_PAYLOAD_BYTES:
        raise AgentRoomValidationError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    evidence = _normalize_evidence(
        event["evidence"], client_created_at=client_created_at
    )
    if kind == "evidence" and evidence is None:
        raise AgentRoomValidationError("evidence events require evidence metadata")

    return {
        "schema": AGENT_ROOM_CLIENT_EVENT_SCHEMA,
        "room_id": room_id,
        "actor_id": actor_id,
        "client_key_id": client_key_id,
        "kind": kind,
        "expected_sequence": expected_sequence,
        "expected_head_hash": expected_head_hash,
        "nonce": nonce,
        "client_created_at": event["client_created_at"],
        "in_reply_to": in_reply_to,
        "non_executable": True,
        "payload": payload,
        "evidence": evidence,
    }


def build_client_event(
    *,
    room_id: str,
    actor_id: str,
    client_key_id: str,
    kind: str,
    expected_sequence: int,
    expected_head_hash: str,
    nonce: str,
    client_created_at: str,
    payload: Mapping[str, object],
    non_executable: bool = True,
    in_reply_to: str | None = None,
    evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build and validate the exact object a client must sign."""

    if type(payload) is not dict:
        raise AgentRoomValidationError("payload must be a plain JSON object")
    if evidence is not None and type(evidence) is not dict:
        raise AgentRoomValidationError("evidence must be a plain JSON object")
    return _normalize_client_event(
        {
            "schema": AGENT_ROOM_CLIENT_EVENT_SCHEMA,
            "room_id": room_id,
            "actor_id": actor_id,
            "client_key_id": client_key_id,
            "kind": kind,
            "expected_sequence": expected_sequence,
            "expected_head_hash": expected_head_hash,
            "nonce": nonce,
            "client_created_at": client_created_at,
            "in_reply_to": in_reply_to,
            "non_executable": non_executable,
            "payload": payload,
            "evidence": evidence,
        }
    )


def client_signing_bytes(event: Mapping[str, object]) -> bytes:
    """Canonical, domain-separated bytes for the client Ed25519 signature."""

    normalized = _normalize_client_event(event)
    return _CLIENT_SIGNATURE_DOMAIN + _canonical_json_bytes(normalized)


def _record_client_event(record: Mapping[str, object]) -> Mapping[str, object]:
    event = record.get("client_event")
    if not isinstance(event, Mapping):
        raise AgentRoomIntegrityError("verified record lacks its client event")
    return event


def _raw_public_key(private_key: Ed25519PrivateKey) -> str:
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )


def _initialization_seal_document(
    server_private_key: Ed25519PrivateKey,
) -> dict[str, object]:
    """Build the immutable, key-bound proof that this store was initialized."""

    if not isinstance(server_private_key, Ed25519PrivateKey):
        raise TypeError("server_private_key must be an Ed25519PrivateKey")
    server_key_id = ed25519_key_id(_raw_public_key(server_private_key))
    payload: dict[str, object] = {
        "schema": AGENT_ROOM_INITIALIZATION_SEAL_SCHEMA,
        "state": "initialized",
        "database": "_agent_room/agent-room.sqlite",
        "server_key_id": server_key_id,
        "non_executable": True,
        "execution_authority": "none",
    }
    return {
        **payload,
        "signature": server_private_key.sign(
            _INITIALIZATION_SEAL_DOMAIN + _canonical_json_bytes(payload)
        ).hex(),
    }


def _seal_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def verify_initialization_seal(
    seal_path: str | os.PathLike[str],
    *,
    server_private_key: Ed25519PrivateKey,
    expected_owner_uid: int | None = None,
) -> dict[str, object]:
    """Verify one stable owner-only initialization seal under the active key."""

    if not isinstance(server_private_key, Ed25519PrivateKey):
        raise TypeError("server_private_key must be an Ed25519PrivateKey")
    owner_uid = os.geteuid() if expected_owner_uid is None else expected_owner_uid
    if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or owner_uid < 0:
        raise TypeError("expected_owner_uid must be a non-negative integer")
    path = Path(seal_path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        visible = path.lstat()
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(visible.st_mode)
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
            or opened.st_nlink != 1
            or opened.st_uid != owner_uid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or not 0 < opened.st_size <= _MAX_INITIALIZATION_SEAL_BYTES
        ):
            raise AgentRoomIntegrityError(
                "Agent Room initialization seal is not a private regular file"
            )
        body = bytearray()
        while len(body) <= _MAX_INITIALIZATION_SEAL_BYTES:
            chunk = os.read(
                descriptor, min(4096, _MAX_INITIALIZATION_SEAL_BYTES + 1 - len(body))
            )
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
        if len(body) > _MAX_INITIALIZATION_SEAL_BYTES or _seal_file_identity(
            opened
        ) != _seal_file_identity(after):
            raise AgentRoomIntegrityError(
                "Agent Room initialization seal changed while it was read"
            )
    except AgentRoomError:
        raise
    except OSError as exc:
        raise AgentRoomIntegrityError(
            "Agent Room initialization seal is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        text = bytes(body).decode("utf-8")
        document = _strict_json_object(text, field="initialization seal")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentRoomIntegrityError(
            "Agent Room initialization seal is malformed"
        ) from exc
    expected_fields = {
        "schema",
        "state",
        "database",
        "server_key_id",
        "non_executable",
        "execution_authority",
        "signature",
    }
    if set(document) != expected_fields or _canonical_json_bytes(document) != bytes(
        body
    ):
        raise AgentRoomIntegrityError(
            "Agent Room initialization seal fields are invalid"
        )
    public_key_hex = _raw_public_key(server_private_key)
    server_key_id = ed25519_key_id(public_key_hex)
    signature = document.get("signature")
    if (
        document.get("schema") != AGENT_ROOM_INITIALIZATION_SEAL_SCHEMA
        or document.get("state") != "initialized"
        or document.get("database") != "_agent_room/agent-room.sqlite"
        or document.get("server_key_id") != server_key_id
        or document.get("non_executable") is not True
        or document.get("execution_authority") != "none"
        or not isinstance(signature, str)
        or _SIGNATURE_RE.fullmatch(signature) is None
    ):
        raise AgentRoomIntegrityError(
            "Agent Room initialization seal identity is invalid"
        )
    payload = {key: document[key] for key in expected_fields - {"signature"}}
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            bytes.fromhex(signature),
            _INITIALIZATION_SEAL_DOMAIN + _canonical_json_bytes(payload),
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise AgentRoomIntegrityError(
            "Agent Room initialization seal signature is invalid"
        ) from exc
    return dict(document)


def create_initialization_seal(
    seal_path: str | os.PathLike[str],
    *,
    server_private_key: Ed25519PrivateKey,
    expected_owner_uid: int | None = None,
) -> dict[str, object]:
    """Atomically publish the immutable seal, or verify the one already present."""

    owner_uid = os.geteuid() if expected_owner_uid is None else expected_owner_uid
    if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or owner_uid < 0:
        raise TypeError("expected_owner_uid must be a non-negative integer")
    path = Path(seal_path)
    if path.exists() or path.is_symlink():
        return verify_initialization_seal(
            path,
            server_private_key=server_private_key,
            expected_owner_uid=owner_uid,
        )
    document = _initialization_seal_document(server_private_key)
    body = _canonical_json_bytes(document)
    parent = path.parent
    directory_descriptor = -1
    temporary_name = f".{path.name}.{os.urandom(16).hex()}.tmp"
    temporary_descriptor = -1
    temporary_exists = False
    try:
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_parent = os.fstat(directory_descriptor)
        visible_parent = parent.lstat()
        if (
            not stat.S_ISDIR(opened_parent.st_mode)
            or (opened_parent.st_dev, opened_parent.st_ino)
            != (visible_parent.st_dev, visible_parent.st_ino)
            or opened_parent.st_uid != owner_uid
            or stat.S_IMODE(opened_parent.st_mode) & 0o022
        ):
            raise AgentRoomIntegrityError(
                "Agent Room initialization seal directory is unsafe"
            )
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_exists = True
        written = 0
        while written < len(body):
            count = os.write(temporary_descriptor, body[written:])
            if count <= 0:
                raise OSError("initialization seal write made no progress")
            written += count
        os.fchmod(temporary_descriptor, 0o600)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.rename(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_exists = False
        os.fsync(directory_descriptor)
    except AgentRoomError:
        raise
    except OSError as exc:
        raise AgentRoomIntegrityError(
            "Agent Room initialization seal could not be published"
        ) from exc
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_exists and directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
    return verify_initialization_seal(
        path,
        server_private_key=server_private_key,
        expected_owner_uid=owner_uid,
    )


def _verify_ed25519(public_key_hex: str, signature_hex: str, message: bytes) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            bytes.fromhex(signature_hex), message
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise AgentRoomSignatureError("client Ed25519 signature is invalid") from exc


class AgentRoomStore:
    """SQLite-backed private room with signed, serialized appends.

    Provisioning methods are trusted-control-plane operations and must not be
    exposed as anonymous REST or MCP tools. Runtime read methods still require
    the authenticated participant identity, and every write requires both that
    membership and a valid client event signature.
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        server_private_key: Ed25519PrivateKey,
        clock: Callable[[], datetime] | None = None,
        require_existing: bool = False,
        _open_existing: bool = False,
        _expected_owner_uid: int | None = None,
    ) -> None:
        if not isinstance(server_private_key, Ed25519PrivateKey):
            raise TypeError("server_private_key must be an Ed25519PrivateKey")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(require_existing, bool):
            raise TypeError("require_existing must be a boolean")
        if not isinstance(_open_existing, bool):
            raise TypeError("_open_existing must be a boolean")
        if _expected_owner_uid is not None and (
            isinstance(_expected_owner_uid, bool)
            or not isinstance(_expected_owner_uid, int)
            or _expected_owner_uid < 0
        ):
            raise TypeError("_expected_owner_uid must be a non-negative integer")
        if not _open_existing and _expected_owner_uid is not None:
            raise TypeError("_expected_owner_uid is only valid for read-only audit")
        self._server_private_key = server_private_key
        self._server_public_key_hex = _raw_public_key(server_private_key)
        self._server_key_id = ed25519_key_id(self._server_public_key_hex)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._read_only = _open_existing
        self._expected_owner_uid = (
            os.geteuid() if _expected_owner_uid is None else _expected_owner_uid
        )
        self._db_path, created = self._prepare_database_path(
            db_path,
            create=not (_open_existing or require_existing),
            expected_owner_uid=self._expected_owner_uid,
        )
        if _open_existing or not created:
            # Runtime ``require_existing`` opens writable once so SQLite can
            # complete rollback-journal recovery after an unclean process exit.
            # The offline recovery classmethod remains strictly query-only.
            connection = self._connect(read_only=_open_existing or not require_existing)
            try:
                self._assert_metadata(connection)
                self._assert_database_integrity(connection)
            finally:
                connection.close()
        else:
            self._initialize()

    @classmethod
    def open_existing(
        cls,
        db_path: str | os.PathLike[str],
        *,
        server_private_key: Ed25519PrivateKey,
        clock: Callable[[], datetime] | None = None,
        expected_owner_uid: int | None = None,
    ) -> AgentRoomStore:
        """Open an existing database read-only under an expected file owner."""

        return cls(
            db_path,
            server_private_key=server_private_key,
            clock=clock,
            require_existing=True,
            _open_existing=True,
            _expected_owner_uid=expected_owner_uid,
        )

    @property
    def server_public_key_hex(self) -> str:
        return self._server_public_key_hex

    @property
    def server_key_id(self) -> str:
        return self._server_key_id

    def verify_initialization_seal(
        self,
        seal_path: str | os.PathLike[str],
        *,
        expected_owner_uid: int | None = None,
    ) -> dict[str, object]:
        """Verify the external initialization seal under this store's key."""

        return verify_initialization_seal(
            seal_path,
            server_private_key=self._server_private_key,
            expected_owner_uid=(
                self._expected_owner_uid
                if expected_owner_uid is None
                else expected_owner_uid
            ),
        )

    def _now(self) -> datetime:
        current = self._clock()
        if not isinstance(current, datetime):
            raise AgentRoomIntegrityError("server clock did not return a datetime")
        if current.tzinfo is None or current.utcoffset() is None:
            raise AgentRoomIntegrityError("server clock must be timezone-aware")
        return current.astimezone(UTC)

    @staticmethod
    def _prepare_database_path(
        db_path: str | os.PathLike[str], *, create: bool, expected_owner_uid: int
    ) -> tuple[Path, bool]:
        if (
            not isinstance(db_path, (str, os.PathLike))
            or os.fspath(db_path) == ":memory:"
        ):
            raise AgentRoomValidationError(
                "Agent Room requires a private on-disk database"
            )
        raw = Path(db_path)
        if raw.name in {"", ".", ".."}:
            raise AgentRoomValidationError("Agent Room database path is invalid")
        try:
            parent = raw.parent.resolve(strict=True)
        except OSError as exc:
            raise AgentRoomValidationError(
                "Agent Room database parent is unavailable"
            ) from exc
        parent_meta = parent.stat()
        if (
            not stat.S_ISDIR(parent_meta.st_mode)
            or parent_meta.st_uid != expected_owner_uid
            or stat.S_IMODE(parent_meta.st_mode) & 0o022
        ):
            raise AgentRoomValidationError(
                "Agent Room database parent must be owner-controlled and private"
            )
        path = parent / raw.name
        if raw.is_symlink() or path.is_symlink():
            raise AgentRoomValidationError("Agent Room database must not be a symlink")
        if not path.exists() and not create:
            raise AgentRoomIntegrityError("Agent Room database is unavailable")
        created = False
        if not path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags, 0o600)
            except OSError as exc:
                raise AgentRoomValidationError(
                    "Agent Room database could not be created safely"
                ) from exc
            os.close(descriptor)
            created = True
        AgentRoomStore._assert_private_database_file(
            path, expected_owner_uid=expected_owner_uid
        )
        return path, created

    @staticmethod
    def _assert_private_database_file(path: Path, *, expected_owner_uid: int) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AgentRoomIntegrityError("Agent Room database is unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != expected_owner_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise AgentRoomIntegrityError(
                "Agent Room database must be a private owner-only regular file"
            )

    def _connect(self, *, read_only: bool | None = None) -> sqlite3.Connection:
        self._assert_private_database_file(
            self._db_path, expected_owner_uid=self._expected_owner_uid
        )
        effective_read_only = self._read_only if read_only is None else read_only
        try:
            target = (
                f"{self._db_path.as_uri()}?mode=ro"
                if effective_read_only
                else str(self._db_path)
            )
            connection = sqlite3.connect(
                target,
                timeout=5.0,
                isolation_level=None,
                uri=effective_read_only,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            if effective_read_only:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA trusted_schema=OFF")
            return connection
        except sqlite3.Error as exc:
            raise AgentRoomIntegrityError(
                "Agent Room database could not be opened"
            ) from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for _object_type, _name, _table_name, sql in _SCHEMA_DDL:
                connection.execute(sql)
            connection.execute("PRAGMA user_version=1")
            expected_meta = {
                "schema_version": _DATABASE_SCHEMA_VERSION,
                "server_key_id": self._server_key_id,
                "server_public_key_hex": self._server_public_key_hex,
            }
            for key, value in expected_meta.items():
                connection.execute(
                    "INSERT INTO agent_room_meta (key, value) VALUES (?, ?)",
                    (key, value),
                )
            self._assert_metadata(connection)
            connection.commit()
        except AgentRoomError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise AgentRoomIntegrityError(
                "Agent Room schema initialization failed"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _assert_schema(connection: sqlite3.Connection) -> None:
        try:
            oversized = connection.execute(
                "SELECT 1 FROM sqlite_schema "
                "WHERE sql IS NOT NULL AND length(CAST(sql AS BLOB))>? LIMIT 1",
                (_MAX_SCHEMA_SQL_BYTES,),
            ).fetchone()
            rows = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE type IN ('table', 'index', 'view', 'trigger') "
                "ORDER BY type, name LIMIT ?",
                (len(_EXPECTED_SCHEMA_OBJECTS) + 1,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise AgentRoomIntegrityError("Agent Room schema is unavailable") from exc
        if oversized is not None:
            raise AgentRoomIntegrityError("Agent Room schema differs from the release")
        actual = {
            (row["type"], row["name"], row["tbl_name"]): (
                None if row["sql"] is None else " ".join(row["sql"].split())
            )
            for row in rows
        }
        if actual != _EXPECTED_SCHEMA_OBJECTS:
            raise AgentRoomIntegrityError("Agent Room schema differs from the release")

    @staticmethod
    def _assert_database_integrity(connection: sqlite3.Connection) -> None:
        try:
            quick_check = connection.execute("PRAGMA quick_check(1)").fetchmany(2)
            foreign_key_violation = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchone()
        except sqlite3.Error as exc:
            raise AgentRoomIntegrityError(
                "Agent Room database integrity check failed"
            ) from exc
        if len(quick_check) != 1 or quick_check[0][0] != "ok":
            raise AgentRoomIntegrityError("Agent Room database integrity check failed")
        if foreign_key_violation is not None:
            raise AgentRoomIntegrityError(
                "Agent Room database contains a foreign-key violation"
            )

    @staticmethod
    def _assert_participant_storage_bounds(connection: sqlite3.Connection) -> None:
        try:
            participant_count = connection.execute(
                "SELECT COUNT(*) FROM agent_room_participants"
            ).fetchone()[0]
            invalid_participant = connection.execute(
                "SELECT 1 FROM agent_room_participants WHERE "
                "typeof(participant_id)<>'text' OR "
                "length(CAST(participant_id AS BLOB))>128 OR "
                "typeof(public_key_hex)<>'text' OR "
                "length(CAST(public_key_hex AS BLOB))>64 OR "
                "typeof(key_id)<>'text' OR length(CAST(key_id AS BLOB))>64 OR "
                "typeof(enabled)<>'integer' OR typeof(created_at)<>'text' OR "
                "length(CAST(created_at AS BLOB))>64 LIMIT 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise AgentRoomIntegrityError(
                "Agent Room participant storage is unavailable"
            ) from exc
        if participant_count > MAX_TOTAL_PARTICIPANTS:
            raise AgentRoomIntegrityError(
                "Agent Room database exceeds participant capacity"
            )
        if invalid_participant is not None:
            raise AgentRoomIntegrityError("stored participant identity is invalid")

    def _assert_metadata(self, connection: sqlite3.Connection) -> None:
        self._assert_schema(connection)
        self._assert_participant_storage_bounds(connection)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            metadata_count = connection.execute(
                "SELECT COUNT(*) FROM agent_room_meta"
            ).fetchone()[0]
            invalid_metadata = connection.execute(
                "SELECT 1 FROM agent_room_meta WHERE "
                "typeof(key)<>'text' OR length(CAST(key AS BLOB))>64 OR "
                "typeof(value)<>'text' OR length(CAST(value AS BLOB))>128 LIMIT 1"
            ).fetchone()
            rows = connection.execute(
                "SELECT key, value FROM agent_room_meta ORDER BY key LIMIT 4"
            ).fetchall()
        except sqlite3.Error as exc:
            raise AgentRoomIntegrityError("Agent Room metadata is unavailable") from exc
        metadata = {row["key"]: row["value"] for row in rows}
        if invalid_metadata is not None or metadata_count != 3:
            raise AgentRoomIntegrityError(
                "Agent Room schema or server signing identity differs"
            )
        if version != 1 or metadata != {
            "schema_version": _DATABASE_SCHEMA_VERSION,
            "server_key_id": self._server_key_id,
            "server_public_key_hex": self._server_public_key_hex,
        }:
            raise AgentRoomIntegrityError(
                "Agent Room schema or server signing identity differs"
            )

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if write and self._read_only:
            raise AgentRoomIntegrityError("read-only Agent Room store cannot mutate")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._assert_metadata(connection)
            yield connection
            connection.commit()
        except AgentRoomError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise AgentRoomIntegrityError(
                "Agent Room database constraint rejected state"
            ) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise AgentRoomIntegrityError(
                "Agent Room database operation failed"
            ) from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def provision_participant(
        self, participant_id: str, public_key_hex: str
    ) -> dict[str, object]:
        """Trusted control-plane registration; no private key is stored."""

        participant = _identifier(participant_id, field="participant_id")
        public_key = _public_key_hex(public_key_hex)
        key_id = ed25519_key_id(public_key)
        created_at = _timestamp(self._now(), field="server clock")
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT public_key_hex, key_id, enabled, created_at "
                "FROM agent_room_participants WHERE participant_id=?",
                (participant,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["public_key_hex"] != public_key
                    or existing["key_id"] != key_id
                    or existing["enabled"] != 1
                ):
                    raise AgentRoomAuthorizationError(
                        "participant identity is already bound to another key"
                    )
                created_at = existing["created_at"]
            else:
                collision = connection.execute(
                    "SELECT 1 FROM agent_room_participants "
                    "WHERE public_key_hex=? OR key_id=?",
                    (public_key, key_id),
                ).fetchone()
                if collision is not None:
                    raise AgentRoomAuthorizationError(
                        "Ed25519 key is already bound to another participant"
                    )
                participant_count = connection.execute(
                    "SELECT COUNT(*) FROM agent_room_participants"
                ).fetchone()[0]
                if participant_count >= MAX_TOTAL_PARTICIPANTS:
                    raise AgentRoomCapacityError("global participant limit reached")
                connection.execute(
                    "INSERT INTO agent_room_participants "
                    "(participant_id, public_key_hex, key_id, enabled, created_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (participant, public_key, key_id, created_at),
                )
        return {
            "participant_id": participant,
            "public_key_hex": public_key,
            "key_id": key_id,
            "created_at": created_at,
            "private_key_stored": False,
        }

    def create_room(
        self,
        room_id: str,
        *,
        owner_id: str,
        participant_ids: Sequence[str] = (),
    ) -> dict[str, object]:
        """Create one immutable membership set under the trusted control plane."""

        room = _identifier(room_id, field="room_id")
        owner = _identifier(owner_id, field="owner_id")
        if isinstance(participant_ids, (str, bytes)) or not isinstance(
            participant_ids, Sequence
        ):
            raise AgentRoomValidationError("participant_ids must be a sequence")
        requested = [owner]
        requested.extend(
            _identifier(value, field="participant_id") for value in participant_ids
        )
        if len(requested) != len(set(requested)):
            raise AgentRoomValidationError("room participants must be unique")
        if not requested or len(requested) > MAX_PARTICIPANTS:
            raise AgentRoomValidationError(
                f"room must contain 1-{MAX_PARTICIPANTS} participants"
            )
        created_at = _timestamp(self._now(), field="server clock")

        with self._transaction(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM agent_rooms WHERE room_id=?", (room,)
            ).fetchone():
                raise AgentRoomValidationError("room_id already exists")
            total_rooms = connection.execute(
                "SELECT COUNT(*) FROM agent_rooms"
            ).fetchone()[0]
            owner_rooms = connection.execute(
                "SELECT COUNT(*) FROM agent_rooms WHERE owner_id=?", (owner,)
            ).fetchone()[0]
            if total_rooms >= MAX_TOTAL_ROOMS:
                raise AgentRoomCapacityError("Agent Room global room limit reached")
            if owner_rooms >= MAX_ROOMS_PER_OWNER:
                raise AgentRoomCapacityError("participant room limit reached")
            placeholders = ",".join("?" for _ in requested)
            participant_rows = connection.execute(
                "SELECT participant_id, public_key_hex, key_id, enabled "
                f"FROM agent_room_participants WHERE participant_id IN ({placeholders})",
                tuple(requested),
            ).fetchall()
            by_id = {row["participant_id"]: row for row in participant_rows}
            if set(by_id) != set(requested) or any(
                row["enabled"] != 1 for row in participant_rows
            ):
                raise AgentRoomAuthorizationError(
                    "every room participant must be provisioned and enabled"
                )
            participants = [
                {
                    "participant_id": participant,
                    "key_id": by_id[participant]["key_id"],
                    "public_key_hex": by_id[participant]["public_key_hex"],
                    "role": "owner" if participant == owner else "participant",
                }
                for participant in sorted(requested)
            ]
            manifest = self._room_manifest(
                room_id=room,
                owner_id=owner,
                created_at=created_at,
                participants=participants,
            )
            genesis_hash = _digest(_GENESIS_DOMAIN, manifest)
            genesis_signature = self._server_private_key.sign(
                _SERVER_SIGNATURE_DOMAIN + _canonical_json_bytes(manifest)
            ).hex()
            connection.execute(
                "INSERT INTO agent_rooms "
                "(room_id, owner_id, created_at, status, next_sequence, "
                "genesis_hash, head_hash, server_key_id, server_public_key_hex, "
                "genesis_signature) VALUES (?, ?, ?, 'open', 0, ?, ?, ?, ?, ?)",
                (
                    room,
                    owner,
                    created_at,
                    genesis_hash,
                    genesis_hash,
                    self._server_key_id,
                    self._server_public_key_hex,
                    genesis_signature,
                ),
            )
            for participant in participants:
                connection.execute(
                    "INSERT INTO agent_room_memberships "
                    "(room_id, participant_id, role) VALUES (?, ?, ?)",
                    (room, participant["participant_id"], participant["role"]),
                )
        return {
            **manifest,
            "status": "open",
            "next_sequence": 0,
            "genesis_hash": genesis_hash,
            "head_hash": genesis_hash,
            "genesis_signature": genesis_signature,
        }

    def _room_manifest(
        self,
        *,
        room_id: str,
        owner_id: str,
        created_at: str,
        participants: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        return {
            "schema": AGENT_ROOM_ROOM_SCHEMA,
            "room_id": room_id,
            "owner_id": owner_id,
            "created_at": created_at,
            "participants": [dict(row) for row in participants],
            "server_key_id": self._server_key_id,
            "server_public_key_hex": self._server_public_key_hex,
            "non_executable": True,
            "execution_authority": "none",
            "can_accept": False,
            "can_order": False,
            "can_execute": False,
            "can_settle": False,
            "can_custody": False,
        }

    @staticmethod
    def _authorization_row(
        connection: sqlite3.Connection, room_id: str, participant_id: str
    ) -> sqlite3.Row:
        invalid = connection.execute(
            "SELECT 1 FROM agent_room_memberships AS m "
            "JOIN agent_room_participants AS p "
            "ON p.participant_id=m.participant_id "
            "WHERE m.room_id=? AND m.participant_id=? AND ("
            "typeof(m.room_id)<>'text' OR length(CAST(m.room_id AS BLOB))>128 OR "
            "typeof(m.participant_id)<>'text' OR "
            "length(CAST(m.participant_id AS BLOB))>128 OR "
            "typeof(m.role)<>'text' OR length(CAST(m.role AS BLOB))>11 OR "
            "typeof(p.public_key_hex)<>'text' OR "
            "length(CAST(p.public_key_hex AS BLOB))>64 OR "
            "typeof(p.key_id)<>'text' OR length(CAST(p.key_id AS BLOB))>64 OR "
            "typeof(p.enabled)<>'integer') LIMIT 1",
            (room_id, participant_id),
        ).fetchone()
        if invalid is not None:
            raise AgentRoomIntegrityError("stored participant authorization is invalid")
        row = connection.execute(
            "SELECT m.role, p.public_key_hex, p.key_id, p.enabled "
            "FROM agent_room_memberships AS m "
            "JOIN agent_room_participants AS p "
            "ON p.participant_id=m.participant_id "
            "WHERE m.room_id=? AND m.participant_id=?",
            (room_id, participant_id),
        ).fetchone()
        if row is None or row["enabled"] != 1:
            raise AgentRoomAuthorizationError(
                "participant is not authorized for this room"
            )
        return row

    def _manifest_from_database(
        self, connection: sqlite3.Connection, room: sqlite3.Row
    ) -> dict[str, object]:
        membership_count = connection.execute(
            "SELECT COUNT(*) FROM agent_room_memberships WHERE room_id=?",
            (room["room_id"],),
        ).fetchone()[0]
        if not 1 <= membership_count <= MAX_PARTICIPANTS:
            raise AgentRoomIntegrityError("room membership manifest is invalid")
        oversized = connection.execute(
            "SELECT 1 FROM agent_room_memberships AS m "
            "JOIN agent_room_participants AS p "
            "ON p.participant_id=m.participant_id "
            "WHERE m.room_id=? AND ("
            "typeof(m.room_id)<>'text' OR length(CAST(m.room_id AS BLOB))>128 OR "
            "typeof(m.participant_id)<>'text' OR "
            "length(CAST(m.participant_id AS BLOB))>128 OR "
            "typeof(m.role)<>'text' OR length(CAST(m.role AS BLOB))>11 OR "
            "typeof(p.public_key_hex)<>'text' OR "
            "length(CAST(p.public_key_hex AS BLOB))>64 OR "
            "typeof(p.key_id)<>'text' OR length(CAST(p.key_id AS BLOB))>64) "
            "LIMIT 1",
            (room["room_id"],),
        ).fetchone()
        if oversized is not None:
            raise AgentRoomIntegrityError("room membership manifest is invalid")
        participant_rows = connection.execute(
            "SELECT m.participant_id, m.role, p.key_id, p.public_key_hex, p.enabled "
            "FROM agent_room_memberships AS m "
            "JOIN agent_room_participants AS p "
            "ON p.participant_id=m.participant_id "
            "WHERE m.room_id=? ORDER BY m.participant_id LIMIT ?",
            (room["room_id"], MAX_PARTICIPANTS + 1),
        ).fetchall()
        if (
            not participant_rows
            or len(participant_rows) > MAX_PARTICIPANTS
            or any(row["enabled"] != 1 for row in participant_rows)
            or sum(row["role"] == "owner" for row in participant_rows) != 1
            or next(
                row["participant_id"]
                for row in participant_rows
                if row["role"] == "owner"
            )
            != room["owner_id"]
        ):
            raise AgentRoomIntegrityError("room membership manifest is invalid")
        return self._room_manifest(
            room_id=room["room_id"],
            owner_id=room["owner_id"],
            created_at=room["created_at"],
            participants=[
                {
                    "participant_id": row["participant_id"],
                    "key_id": row["key_id"],
                    "public_key_hex": row["public_key_hex"],
                    "role": row["role"],
                }
                for row in participant_rows
            ],
        )

    @staticmethod
    def _server_claim(
        *,
        room_id: str,
        sequence: int,
        previous_hash: str,
        record_sha256: str,
        event_id: str,
        server_key_id: str,
    ) -> dict[str, object]:
        return {
            "schema": AGENT_ROOM_SERVER_ATTESTATION_SCHEMA,
            "room_id": room_id,
            "sequence": sequence,
            "previous_hash": previous_hash,
            "record_sha256": record_sha256,
            "event_id": event_id,
            "server_key_id": server_key_id,
            "non_executable": True,
            "execution_authority": "none",
        }

    @staticmethod
    def _record_material(
        *,
        sequence: int,
        previous_hash: str,
        client_event: Mapping[str, object],
        client_public_key_hex: str,
        client_signature: str,
        request_sha256: str,
        server_received_at: str,
        server_key_id: str,
        server_public_key_hex: str,
    ) -> dict[str, object]:
        return {
            "schema": AGENT_ROOM_EVENT_SCHEMA,
            "room_id": client_event["room_id"],
            "sequence": sequence,
            "previous_hash": previous_hash,
            "client_event": dict(client_event),
            "client_public_key_hex": client_public_key_hex,
            "client_signature": client_signature,
            "request_sha256": request_sha256,
            "server_received_at": server_received_at,
            "server_key_id": server_key_id,
            "server_public_key_hex": server_public_key_hex,
            "non_executable": True,
            "execution_authority": "none",
            "can_accept": False,
            "can_order": False,
            "can_execute": False,
            "can_settle": False,
            "can_custody": False,
        }

    @staticmethod
    def _validate_temporal_boundary(
        event: Mapping[str, object], server_received_at: datetime
    ) -> None:
        created_at = _parse_timestamp(
            event["client_created_at"], field="client_created_at"
        )
        if created_at > server_received_at + timedelta(
            seconds=MAX_CLIENT_CLOCK_SKEW_SECONDS
        ):
            raise AgentRoomValidationError(
                "client event clock is too far in the future"
            )
        if created_at < server_received_at - timedelta(
            seconds=MAX_CLIENT_EVENT_AGE_SECONDS
        ):
            raise AgentRoomValidationError("client event is too old")

    @staticmethod
    def _validate_transition(
        event: Mapping[str, object],
        *,
        role: str,
        prior_records: Sequence[Mapping[str, object]],
    ) -> None:
        kind = event["kind"]
        target_id = event["in_reply_to"]
        if kind in {"proposal", "close"} and target_id is not None:
            raise AgentRoomValidationError(f"{kind} must not use in_reply_to")
        if kind in {"counter", "acknowledge", "decline", "withdraw"} and (
            target_id is None
        ):
            raise AgentRoomValidationError(f"{kind} requires in_reply_to")
        if kind == "close" and role != "owner":
            raise AgentRoomAuthorizationError("only the room owner may close the room")
        if target_id is None:
            return

        by_id = {record["event_id"]: record for record in prior_records}
        target = by_id.get(target_id)
        if target is None:
            raise AgentRoomValidationError(
                "in_reply_to does not identify an earlier event"
            )
        target_event = _record_client_event(target)
        target_kind = target_event["kind"]
        withdrawn = {
            _record_client_event(record)["in_reply_to"]
            for record in prior_records
            if _record_client_event(record)["kind"] == "withdraw"
        }
        if target_id in withdrawn or target_kind in {"withdraw", "close"}:
            raise AgentRoomValidationError("in_reply_to targets an inactive event")
        if kind == "counter" and target_kind not in {"proposal", "counter"}:
            raise AgentRoomValidationError("counter must target a proposal or counter")
        if kind == "withdraw":
            if target_event["actor_id"] != event["actor_id"]:
                raise AgentRoomAuthorizationError(
                    "participants may withdraw only their own event"
                )
            if target_kind not in {"proposal", "counter", "question", "evidence"}:
                raise AgentRoomValidationError("target event cannot be withdrawn")

    def append_event(
        self,
        event: Mapping[str, object],
        *,
        client_signature_hex: str,
    ) -> dict[str, object]:
        """Verify and append one event under a serialized optimistic cursor."""

        normalized = _normalize_client_event(event)
        signature = _signature_hex(client_signature_hex, field="client_signature")
        signing_bytes = _CLIENT_SIGNATURE_DOMAIN + _canonical_json_bytes(normalized)
        room_id = str(normalized["room_id"])
        actor_id = str(normalized["actor_id"])

        with self._transaction(write=True) as connection:
            authorization = self._authorization_row(connection, room_id, actor_id)
            if normalized["client_key_id"] != authorization["key_id"]:
                raise AgentRoomSignatureError(
                    "signed client key identity does not match participant"
                )
            _verify_ed25519(authorization["public_key_hex"], signature, signing_bytes)
            verified = self._verify_room_locked(connection, room_id)
            room = verified["room"]
            records = verified["records"]
            if room["status"] != "open":
                raise AgentRoomClosedError("agent room is closed")
            if len(records) >= MAX_EVENTS_PER_ROOM:
                raise AgentRoomCapacityError("agent room event limit is exhausted")
            if (
                len(records) == MAX_EVENTS_PER_ROOM - 1
                and normalized["kind"] != "close"
            ):
                raise AgentRoomCapacityError(
                    "final room event capacity is reserved for close"
                )
            if connection.execute(
                "SELECT 1 FROM agent_room_events WHERE actor_id=? AND nonce=?",
                (actor_id, normalized["nonce"]),
            ).fetchone():
                raise AgentRoomReplayError("participant nonce has already been used")
            current_sequence = room["next_sequence"]
            current_head = room["head_hash"]
            if (
                normalized["expected_sequence"] != current_sequence
                or normalized["expected_head_hash"] != current_head
            ):
                raise AgentRoomSequenceConflict(current_sequence, current_head)
            if normalized["kind"] != "close":
                participant_events = connection.execute(
                    "SELECT COUNT(*) FROM agent_room_events "
                    "WHERE actor_id=? AND kind<>'close'",
                    (actor_id,),
                ).fetchone()[0]
                total_discussion_events = connection.execute(
                    "SELECT COUNT(*) FROM agent_room_events WHERE kind<>'close'"
                ).fetchone()[0]
                if participant_events >= MAX_EVENTS_PER_PARTICIPANT:
                    raise AgentRoomCapacityError(
                        "participant discussion event limit reached"
                    )
                if total_discussion_events >= MAX_TOTAL_DISCUSSION_EVENTS:
                    raise AgentRoomCapacityError(
                        "Agent Room global discussion event limit reached"
                    )

            received_at = self._now()
            if records:
                previous_received = _parse_timestamp(
                    records[-1]["server_received_at"], field="server_received_at"
                )
                if received_at < previous_received:
                    raise AgentRoomIntegrityError("server clock moved backwards")
            self._validate_temporal_boundary(normalized, received_at)
            self._validate_transition(
                normalized, role=authorization["role"], prior_records=records
            )
            received_text = _timestamp(received_at, field="server clock")
            request_sha256 = hashlib.sha256(signing_bytes).hexdigest()
            material = self._record_material(
                sequence=current_sequence,
                previous_hash=current_head,
                client_event=normalized,
                client_public_key_hex=authorization["public_key_hex"],
                client_signature=signature,
                request_sha256=request_sha256,
                server_received_at=received_text,
                server_key_id=self._server_key_id,
                server_public_key_hex=self._server_public_key_hex,
            )
            record_sha256 = _digest(_RECORD_HASH_DOMAIN, material)
            event_id = f"evt_{record_sha256}"
            server_claim = self._server_claim(
                room_id=room_id,
                sequence=current_sequence,
                previous_hash=current_head,
                record_sha256=record_sha256,
                event_id=event_id,
                server_key_id=self._server_key_id,
            )
            server_signature = self._server_private_key.sign(
                _SERVER_SIGNATURE_DOMAIN + _canonical_json_bytes(server_claim)
            ).hex()
            connection.execute(
                "INSERT INTO agent_room_events "
                "(room_id, sequence, event_id, actor_id, kind, nonce, in_reply_to, "
                "client_event_json, client_public_key_hex, client_signature, "
                "request_sha256, previous_hash, record_sha256, server_received_at, "
                "server_key_id, server_public_key_hex, server_signature, "
                "non_executable, execution_authority, can_accept, can_order, "
                "can_execute, can_settle, can_custody) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "1, 'none', 0, 0, 0, 0, 0)",
                (
                    room_id,
                    current_sequence,
                    event_id,
                    actor_id,
                    normalized["kind"],
                    normalized["nonce"],
                    normalized["in_reply_to"],
                    _canonical_json_bytes(normalized).decode("utf-8"),
                    authorization["public_key_hex"],
                    signature,
                    request_sha256,
                    current_head,
                    record_sha256,
                    received_text,
                    self._server_key_id,
                    self._server_public_key_hex,
                    server_signature,
                ),
            )
            next_status = "closed" if normalized["kind"] == "close" else "open"
            updated = connection.execute(
                "UPDATE agent_rooms SET next_sequence=?, head_hash=?, status=? "
                "WHERE room_id=? AND next_sequence=? AND head_hash=? AND status='open'",
                (
                    current_sequence + 1,
                    record_sha256,
                    next_status,
                    room_id,
                    current_sequence,
                    current_head,
                ),
            )
            if updated.rowcount != 1:
                raise AgentRoomSequenceConflict(current_sequence, current_head)
            checked = self._verify_room_locked(connection, room_id)
            return dict(checked["records"][-1])

    def _verify_room_locked(
        self, connection: sqlite3.Connection, room_id: str
    ) -> dict[str, Any]:
        oversized_room = connection.execute(
            "SELECT 1 FROM agent_rooms WHERE room_id=? AND ("
            "typeof(room_id)<>'text' OR length(CAST(room_id AS BLOB))>128 OR "
            "typeof(owner_id)<>'text' OR length(CAST(owner_id AS BLOB))>128 OR "
            "typeof(created_at)<>'text' OR length(CAST(created_at AS BLOB))>64 OR "
            "typeof(status)<>'text' OR length(CAST(status AS BLOB))>6 OR "
            "typeof(next_sequence)<>'integer' OR "
            "typeof(genesis_hash)<>'text' OR length(CAST(genesis_hash AS BLOB))>64 OR "
            "typeof(head_hash)<>'text' OR length(CAST(head_hash AS BLOB))>64 OR "
            "typeof(server_key_id)<>'text' OR "
            "length(CAST(server_key_id AS BLOB))>64 OR "
            "typeof(server_public_key_hex)<>'text' OR "
            "length(CAST(server_public_key_hex AS BLOB))>64 OR "
            "typeof(genesis_signature)<>'text' OR "
            "length(CAST(genesis_signature AS BLOB))>128) LIMIT 1",
            (room_id,),
        ).fetchone()
        if oversized_room is not None:
            raise AgentRoomIntegrityError("stored room fields are invalid")
        room = connection.execute(
            "SELECT * FROM agent_rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        if room is None:
            raise AgentRoomAuthorizationError(
                "participant is not authorized for this room"
            )
        if (
            room["server_key_id"] != self._server_key_id
            or room["server_public_key_hex"] != self._server_public_key_hex
        ):
            raise AgentRoomIntegrityError("room server signing identity differs")
        manifest = self._manifest_from_database(connection, room)
        genesis_hash = _digest(_GENESIS_DOMAIN, manifest)
        if room["genesis_hash"] != genesis_hash:
            raise AgentRoomIntegrityError("room genesis hash does not verify")
        try:
            Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(self._server_public_key_hex)
            ).verify(
                bytes.fromhex(
                    _signature_hex(room["genesis_signature"], field="genesis_signature")
                ),
                _SERVER_SIGNATURE_DOMAIN + _canonical_json_bytes(manifest),
            )
        except AgentRoomSignatureError as exc:
            raise AgentRoomIntegrityError(
                "room genesis signature is malformed"
            ) from exc
        except (InvalidSignature, TypeError, ValueError) as exc:
            raise AgentRoomIntegrityError(
                "room genesis signature does not verify"
            ) from exc

        stored_event_count = connection.execute(
            "SELECT COUNT(*) FROM agent_room_events WHERE room_id=?", (room_id,)
        ).fetchone()[0]
        if stored_event_count > MAX_EVENTS_PER_ROOM:
            raise AgentRoomIntegrityError("room exceeds the event limit")
        oversized_event = connection.execute(
            "SELECT 1 FROM agent_room_events WHERE room_id=? AND ("
            "typeof(room_id)<>'text' OR typeof(sequence)<>'integer' OR "
            "typeof(event_id)<>'text' OR typeof(actor_id)<>'text' OR "
            "typeof(kind)<>'text' OR typeof(nonce)<>'text' OR "
            "typeof(in_reply_to) NOT IN ('null', 'text') OR "
            "typeof(client_event_json)<>'text' OR "
            "length(CAST(client_event_json AS BLOB))>? OR "
            "typeof(client_public_key_hex)<>'text' OR "
            "typeof(client_signature)<>'text' OR typeof(request_sha256)<>'text' OR "
            "typeof(previous_hash)<>'text' OR typeof(record_sha256)<>'text' OR "
            "typeof(server_received_at)<>'text' OR typeof(server_key_id)<>'text' OR "
            "typeof(server_public_key_hex)<>'text' OR "
            "typeof(server_signature)<>'text' OR "
            "typeof(non_executable)<>'integer' OR "
            "typeof(execution_authority)<>'text' OR "
            "typeof(can_accept)<>'integer' OR typeof(can_order)<>'integer' OR "
            "typeof(can_execute)<>'integer' OR typeof(can_settle)<>'integer' OR "
            "typeof(can_custody)<>'integer' OR "
            "length(CAST(room_id AS BLOB)) + length(CAST(sequence AS BLOB)) + "
            "length(CAST(event_id AS BLOB)) + length(CAST(actor_id AS BLOB)) + "
            "length(CAST(kind AS BLOB)) + length(CAST(nonce AS BLOB)) + "
            "length(CAST(COALESCE(in_reply_to, '') AS BLOB)) + "
            "length(CAST(client_event_json AS BLOB)) + "
            "length(CAST(client_public_key_hex AS BLOB)) + "
            "length(CAST(client_signature AS BLOB)) + "
            "length(CAST(request_sha256 AS BLOB)) + "
            "length(CAST(previous_hash AS BLOB)) + "
            "length(CAST(record_sha256 AS BLOB)) + "
            "length(CAST(server_received_at AS BLOB)) + "
            "length(CAST(server_key_id AS BLOB)) + "
            "length(CAST(server_public_key_hex AS BLOB)) + "
            "length(CAST(server_signature AS BLOB)) + "
            "length(CAST(non_executable AS BLOB)) + "
            "length(CAST(execution_authority AS BLOB)) + "
            "length(CAST(can_accept AS BLOB)) + length(CAST(can_order AS BLOB)) + "
            "length(CAST(can_execute AS BLOB)) + length(CAST(can_settle AS BLOB)) + "
            "length(CAST(can_custody AS BLOB))>?) LIMIT 1",
            (
                room_id,
                MAX_STORED_CLIENT_EVENT_BYTES,
                MAX_STORED_EVENT_ROW_BYTES,
            ),
        ).fetchone()
        if oversized_event is not None:
            raise AgentRoomIntegrityError("stored event fields exceed safe bounds")
        rows = connection.execute(
            "SELECT * FROM agent_room_events WHERE room_id=? ORDER BY sequence LIMIT ?",
            (room_id, MAX_EVENTS_PER_ROOM + 1),
        ).fetchall()
        previous_hash = genesis_hash
        records: list[dict[str, object]] = []
        for expected_sequence, row in enumerate(rows):
            if (
                row["sequence"] != expected_sequence
                or row["previous_hash"] != previous_hash
            ):
                raise AgentRoomIntegrityError(
                    "room event sequence or previous hash differs"
                )
            raw_event = _strict_json_object(
                row["client_event_json"], field="stored client event"
            )
            try:
                event = _normalize_client_event(raw_event)
            except AgentRoomValidationError as exc:
                raise AgentRoomIntegrityError("stored client event is invalid") from exc
            if row["client_event_json"] != _canonical_json_bytes(event).decode("utf-8"):
                raise AgentRoomIntegrityError(
                    "stored client event is not canonical JSON"
                )
            if (
                event["room_id"] != room_id
                or event["expected_sequence"] != expected_sequence
                or event["expected_head_hash"] != previous_hash
                or row["actor_id"] != event["actor_id"]
                or row["kind"] != event["kind"]
                or row["nonce"] != event["nonce"]
                or row["in_reply_to"] != event["in_reply_to"]
            ):
                raise AgentRoomIntegrityError("stored event index fields differ")
            authorization = self._authorization_row(
                connection, room_id, str(event["actor_id"])
            )
            if (
                row["client_public_key_hex"] != authorization["public_key_hex"]
                or event["client_key_id"] != authorization["key_id"]
            ):
                raise AgentRoomIntegrityError("stored client signing identity differs")
            try:
                signature = _signature_hex(
                    row["client_signature"], field="client_signature"
                )
                signing_bytes = _CLIENT_SIGNATURE_DOMAIN + _canonical_json_bytes(event)
                _verify_ed25519(row["client_public_key_hex"], signature, signing_bytes)
            except AgentRoomSignatureError as exc:
                raise AgentRoomIntegrityError(
                    "stored client signature does not verify"
                ) from exc
            request_sha256 = hashlib.sha256(signing_bytes).hexdigest()
            if row["request_sha256"] != request_sha256:
                raise AgentRoomIntegrityError("stored request digest differs")
            received_at = _parse_timestamp(
                row["server_received_at"], field="server_received_at"
            )
            try:
                self._validate_temporal_boundary(event, received_at)
                self._validate_transition(
                    event, role=authorization["role"], prior_records=records
                )
            except (AgentRoomValidationError, AgentRoomAuthorizationError) as exc:
                raise AgentRoomIntegrityError(
                    "stored event transition is invalid"
                ) from exc
            if records and received_at < _parse_timestamp(
                records[-1]["server_received_at"], field="server_received_at"
            ):
                raise AgentRoomIntegrityError("stored server clocks are not monotonic")
            if (
                row["server_key_id"] != self._server_key_id
                or row["server_public_key_hex"] != self._server_public_key_hex
                or row["non_executable"] != 1
                or row["execution_authority"] != "none"
                or any(
                    row[field] != 0
                    for field in (
                        "can_accept",
                        "can_order",
                        "can_execute",
                        "can_settle",
                        "can_custody",
                    )
                )
            ):
                raise AgentRoomIntegrityError("stored event authority boundary differs")
            material = self._record_material(
                sequence=expected_sequence,
                previous_hash=previous_hash,
                client_event=event,
                client_public_key_hex=row["client_public_key_hex"],
                client_signature=signature,
                request_sha256=request_sha256,
                server_received_at=row["server_received_at"],
                server_key_id=row["server_key_id"],
                server_public_key_hex=row["server_public_key_hex"],
            )
            record_sha256 = _digest(_RECORD_HASH_DOMAIN, material)
            event_id = f"evt_{record_sha256}"
            if row["record_sha256"] != record_sha256 or row["event_id"] != event_id:
                raise AgentRoomIntegrityError("stored event record hash differs")
            server_claim = self._server_claim(
                room_id=room_id,
                sequence=expected_sequence,
                previous_hash=previous_hash,
                record_sha256=record_sha256,
                event_id=event_id,
                server_key_id=self._server_key_id,
            )
            try:
                server_signature = _signature_hex(
                    row["server_signature"], field="server_signature"
                )
                Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(self._server_public_key_hex)
                ).verify(
                    bytes.fromhex(server_signature),
                    _SERVER_SIGNATURE_DOMAIN + _canonical_json_bytes(server_claim),
                )
            except AgentRoomSignatureError as exc:
                raise AgentRoomIntegrityError(
                    "server co-signature is malformed"
                ) from exc
            except (InvalidSignature, TypeError, ValueError) as exc:
                raise AgentRoomIntegrityError(
                    "server co-signature does not verify"
                ) from exc
            record = {
                **material,
                "event_id": event_id,
                "record_sha256": record_sha256,
                "server_signature": server_signature,
            }
            records.append(record)
            previous_hash = record_sha256

        expected_status = (
            "closed"
            if records and _record_client_event(records[-1])["kind"] == "close"
            else "open"
        )
        if (
            room["next_sequence"] != len(records)
            or room["head_hash"] != previous_hash
            or room["status"] != expected_status
        ):
            raise AgentRoomIntegrityError(
                "room head or status differs from event history"
            )
        return {"room": room, "manifest": manifest, "records": records}

    def room_state(self, room_id: str, *, requester_id: str) -> dict[str, object]:
        room = _identifier(room_id, field="room_id")
        requester = _identifier(requester_id, field="requester_id")
        with self._transaction(write=False) as connection:
            self._authorization_row(connection, room, requester)
            verified = self._verify_room_locked(connection, room)
            row = verified["room"]
            manifest = verified["manifest"]
            return {
                **manifest,
                "status": row["status"],
                "next_sequence": row["next_sequence"],
                "genesis_hash": row["genesis_hash"],
                "head_hash": row["head_hash"],
                "genesis_signature": row["genesis_signature"],
            }

    def room_page(
        self,
        room_id: str,
        *,
        requester_id: str,
        after_sequence: int = -1,
        limit: int = 100,
    ) -> dict[str, object]:
        """Return one verified room cursor and page from one SQLite snapshot."""

        room = _identifier(room_id, field="room_id")
        requester = _identifier(requester_id, field="requester_id")
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or not -1 <= after_sequence < MAX_EVENTS_PER_ROOM
        ):
            raise AgentRoomValidationError("after_sequence is invalid")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_LIST_LIMIT
        ):
            raise AgentRoomValidationError(f"limit must be 1-{MAX_LIST_LIMIT}")
        with self._transaction(write=False) as connection:
            self._authorization_row(connection, room, requester)
            verified = self._verify_room_locked(connection, room)
            row = verified["room"]
            manifest = verified["manifest"]
            records = verified["records"]
            selected = [
                record for record in records if record["sequence"] > after_sequence
            ][:limit]
            return {
                "schema": "seiche.agent-room.events.v1",
                "room": {
                    **manifest,
                    "status": row["status"],
                    "next_sequence": row["next_sequence"],
                    "genesis_hash": row["genesis_hash"],
                    "head_hash": row["head_hash"],
                    "genesis_signature": row["genesis_signature"],
                },
                "after_sequence": after_sequence,
                "events": [
                    json.loads(_canonical_json_bytes(record).decode("utf-8"))
                    for record in selected
                ],
                "non_executable": True,
                "execution_authority": "none",
            }

    def list_events(
        self,
        room_id: str,
        *,
        requester_id: str,
        after_sequence: int = -1,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        return cast(
            list[dict[str, object]],
            self.room_page(
                room_id,
                requester_id=requester_id,
                after_sequence=after_sequence,
                limit=limit,
            )["events"],
        )

    def verify_room(self, room_id: str, *, requester_id: str) -> dict[str, object]:
        room = _identifier(room_id, field="room_id")
        requester = _identifier(requester_id, field="requester_id")
        with self._transaction(write=False) as connection:
            self._authorization_row(connection, room, requester)
            verified = self._verify_room_locked(connection, room)
            row = verified["room"]
            return {
                "ok": True,
                "schema": AGENT_ROOM_ROOM_SCHEMA,
                "room_id": room,
                "status": row["status"],
                "event_count": len(verified["records"]),
                "genesis_hash": row["genesis_hash"],
                "head_hash": row["head_hash"],
                "server_key_id": self._server_key_id,
                "non_executable": True,
                "execution_authority": "none",
            }

    def audit_all_rooms(self) -> dict[str, object]:
        """Verify all durable identities and room chains for operator recovery."""

        with self._transaction(write=False) as connection:
            self._assert_database_integrity(connection)
            participant_rows = connection.execute(
                "SELECT participant_id, public_key_hex, key_id, enabled, created_at "
                "FROM agent_room_participants ORDER BY participant_id LIMIT ?",
                (MAX_TOTAL_PARTICIPANTS + 1,),
            ).fetchall()
            participants: list[dict[str, str]] = []
            for row in participant_rows:
                try:
                    participant_id = _identifier(
                        row["participant_id"], field="participant_id"
                    )
                    public_key = _public_key_hex(row["public_key_hex"])
                    created_at = _parse_timestamp(
                        row["created_at"], field="participant.created_at"
                    )
                except AgentRoomError as exc:
                    raise AgentRoomIntegrityError(
                        "stored participant identity is invalid"
                    ) from exc
                key_id = ed25519_key_id(public_key)
                if row["enabled"] != 1 or row["key_id"] != key_id:
                    raise AgentRoomIntegrityError(
                        "stored participant key binding is invalid"
                    )
                participants.append(
                    {
                        "participant_id": participant_id,
                        "public_key_hex": public_key,
                        "key_id": key_id,
                        "created_at": _timestamp(
                            created_at, field="participant.created_at"
                        ),
                    }
                )

            room_count = connection.execute(
                "SELECT COUNT(*) FROM agent_rooms"
            ).fetchone()[0]
            if room_count > MAX_TOTAL_ROOMS:
                raise AgentRoomIntegrityError(
                    "Agent Room database exceeds room capacity"
                )
            invalid_room_id = connection.execute(
                "SELECT 1 FROM agent_rooms WHERE typeof(room_id)<>'text' OR "
                "length(CAST(room_id AS BLOB))>128 LIMIT 1"
            ).fetchone()
            if invalid_room_id is not None:
                raise AgentRoomIntegrityError("stored room fields are invalid")
            room_ids = [
                row["room_id"]
                for row in connection.execute(
                    "SELECT room_id FROM agent_rooms ORDER BY room_id LIMIT ?",
                    (MAX_TOTAL_ROOMS + 1,),
                ).fetchall()
            ]
            if connection.execute(
                "SELECT 1 FROM agent_rooms GROUP BY owner_id HAVING COUNT(*)>? LIMIT 1",
                (MAX_ROOMS_PER_OWNER,),
            ).fetchone():
                raise AgentRoomIntegrityError(
                    "Agent Room owner exceeds persistent room capacity"
                )
            rooms: list[dict[str, object]] = []
            event_count = 0
            for room_id in room_ids:
                verified = self._verify_room_locked(connection, room_id)
                room = verified["room"]
                records = verified["records"]
                if (
                    len(records) == MAX_EVENTS_PER_ROOM
                    and _record_client_event(records[-1])["kind"] != "close"
                ):
                    raise AgentRoomIntegrityError(
                        "Agent Room final event slot is not a terminal close"
                    )
                event_count += len(records)
                rooms.append(
                    {
                        "room_id": room["room_id"],
                        "status": room["status"],
                        "event_count": len(records),
                        "genesis_hash": room["genesis_hash"],
                        "head_hash": room["head_hash"],
                    }
                )

            discussion_count = connection.execute(
                "SELECT COUNT(*) FROM agent_room_events WHERE kind<>'close'"
            ).fetchone()[0]
            if discussion_count > MAX_TOTAL_DISCUSSION_EVENTS:
                raise AgentRoomIntegrityError(
                    "Agent Room database exceeds discussion event capacity"
                )
            if connection.execute(
                "SELECT 1 FROM agent_room_events WHERE kind<>'close' "
                "GROUP BY actor_id HAVING COUNT(*)>? LIMIT 1",
                (MAX_EVENTS_PER_PARTICIPANT,),
            ).fetchone():
                raise AgentRoomIntegrityError(
                    "Agent Room participant exceeds discussion event capacity"
                )
            stored_event_count = connection.execute(
                "SELECT COUNT(*) FROM agent_room_events"
            ).fetchone()[0]
            if stored_event_count != event_count:
                raise AgentRoomIntegrityError(
                    "Agent Room database contains an unbound event"
                )

            state_sha256 = _digest(
                _AUDIT_DOMAIN,
                {"participants": participants, "rooms": rooms},
            )
            return {
                "ok": True,
                "schema": AGENT_ROOM_AUDIT_SCHEMA,
                "server_key_id": self._server_key_id,
                "participant_count": len(participants),
                "room_count": len(rooms),
                "event_count": event_count,
                "state_sha256": state_sha256,
                "non_executable": True,
                "execution_authority": "none",
            }


__all__ = [
    "AGENT_ROOM_AUDIT_SCHEMA",
    "AGENT_ROOM_CLIENT_EVENT_SCHEMA",
    "AGENT_ROOM_EVENT_SCHEMA",
    "AGENT_ROOM_INITIALIZATION_SEAL_FILENAME",
    "AGENT_ROOM_INITIALIZATION_SEAL_SCHEMA",
    "AGENT_ROOM_ROOM_SCHEMA",
    "AGENT_ROOM_SERVER_ATTESTATION_SCHEMA",
    "EVENT_KINDS",
    "EVIDENCE_CLASSES",
    "RIGHTS_STATUSES",
    "MAX_PARTICIPANTS",
    "MAX_EVENTS_PER_ROOM",
    "MAX_ROOMS_PER_OWNER",
    "MAX_TOTAL_ROOMS",
    "MAX_EVENTS_PER_PARTICIPANT",
    "MAX_TOTAL_DISCUSSION_EVENTS",
    "MAX_PAYLOAD_BYTES",
    "MAX_LIST_LIMIT",
    "AgentRoomError",
    "AgentRoomValidationError",
    "AgentRoomAuthorizationError",
    "AgentRoomSignatureError",
    "AgentRoomSequenceConflict",
    "AgentRoomReplayError",
    "AgentRoomClosedError",
    "AgentRoomCapacityError",
    "AgentRoomIntegrityError",
    "AgentRoomStore",
    "ed25519_key_id",
    "derive_external_room_id",
    "build_client_event",
    "client_signing_bytes",
    "create_initialization_seal",
    "verify_initialization_seal",
]
