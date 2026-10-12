"""Transactional relay store; SQLite for local use, PostgreSQL for hosted use.

Authorization changes take an exclusive policy lock. PostgreSQL readers and
independent data writes share that lock, with ordered transaction-scoped locks
for conflicting data. A revocation therefore cannot race an accepted write.
"""
from __future__ import annotations

import json
import re
import math
import os
import threading
import stat
import time
import uuid
import hashlib
from contextlib import contextmanager, nullcontext
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Boolean, Column, Float, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, and_, create_engine,
                        delete, event, func, insert, inspect, or_, select, text, update)
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import make_url

from .crypto import Crypto, canonical, digest, new_token
from .models import MESSAGE_KINDS, NetworkError, Principal

meta = MetaData()
locks = Table("network_lock", meta, Column("id", Integer, primary_key=True), Column("key_fingerprint", String(64)))
orgs = Table("organizations", meta, Column("id", String(36), primary_key=True), Column("label", String(120)),
             Column("verification", String(32)), Column("status", String(16)), Column("created", Float))
agents = Table("agents", meta, Column("id", String(36), primary_key=True), Column("org_id", String(36), index=True),
               Column("label", String(120)), Column("capabilities", Text), Column("public", Boolean),
               Column("status", String(16)), Column("created", Float))
credentials = Table("credentials", meta, Column("id", String(36), primary_key=True),
                    Column("org_id", String(36), index=True), Column("agent_id", String(36), nullable=True),
                    Column("token_hash", String(64), unique=True), Column("active", Boolean), Column("created", Float))
contacts = Table("contacts", meta, Column("id", String(36), primary_key=True), Column("sender", String(36)),
                 Column("recipient", String(36)), Column("pair", String(73), unique=True), Column("status", String(16)),
                 Column("created", Float))
blocks = Table("blocks", meta, Column("id", String(73), primary_key=True), Column("sender", String(36)),
               Column("recipient", String(36)))
rooms = Table("rooms", meta, Column("id", String(36), primary_key=True), Column("label", String(120)),
              Column("creator", String(36)), Column("sequence", Integer), Column("head", String(64)), Column("created", Float))
members = Table("members", meta, Column("room_id", String(36), primary_key=True), Column("agent_id", String(36), primary_key=True),
                Column("status", String(16)), Column("joined_sequence", Integer), Column("joined", Float))
messages = Table("messages", meta, Column("id", String(36), primary_key=True), Column("room_id", String(36), index=True),
                 Column("sequence", Integer), Column("sender", String(36)), Column("kind", String(24)),
                 Column("parent_id", String(36), nullable=True), Column("accepted_at", Float), Column("expires_at", Float),
                 Column("payload_hash", String(64)), Column("payload", Text, nullable=True),
                 Column("idempotency_key", String(160)), Column("request_hash", String(64)),
                 Column("previous_hash", String(64)), Column("event_hash", String(64)), Column("signature", Text),
                 UniqueConstraint("room_id", "sequence"), UniqueConstraint("sender", "idempotency_key"))
deliveries = Table("deliveries", meta, Column("id", Integer, primary_key=True, autoincrement=True),
                   Column("message_id", String(36)), Column("recipient", String(36), index=True),
                   Column("contact_id", String(36)),
                   Column("acked_at", Float, nullable=True), UniqueConstraint("message_id", "recipient"), sqlite_autoincrement=True)
tasks = Table("tasks", meta, Column("id", String(36), primary_key=True), Column("state", String(16)),
              Column("updated_at", Float), Column("useful", Boolean, nullable=True), Column("useful_at", Float, nullable=True),
              Column("assigned_agent_id", String(36), nullable=True))
quotas = Table("quotas", meta, Column("bucket", String(120), primary_key=True), Column("window", Integer), Column("count", Integer))
reports = Table("reports", meta, Column("id", String(36), primary_key=True), Column("org_id", String(36)),
                Column("sender", String(36)), Column("target", String(36)), Column("reason", Text), Column("created", Float))


def _id() -> str:
    return str(uuid.uuid4())


def _pair(a, b):
    return ":".join(sorted([a, b]))


def _one(db, table, *conditions):
    return db.execute(select(table).where(*conditions)).mappings().first()


def _label(value, field="label", maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise NetworkError("invalid_input", f"{field} must contain 1–{maximum} characters")
    return value.strip()


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise NetworkError("invalid_input", "Identifier is invalid")
    return value


class Store:
    def __init__(self, database_url_or_path, encryption_key, signing_key, *, clock=time.time,
                 max_body_bytes=16384, max_turns=128, rate_limit_per_minute=60, retention_days=30, metadata_retention_days=90):
        self.crypto = Crypto(encryption_key, signing_key)
        self.clock = clock
        self.max_body_bytes, self.max_turns = max_body_bytes, max_turns
        self.rate_limit, self.retention_days = rate_limit_per_minute, retention_days
        self.metadata_retention_days = metadata_retention_days
        if min(max_body_bytes, max_turns, rate_limit_per_minute, retention_days) <= 0:
            raise ValueError("Network limits must be positive")
        if metadata_retention_days < retention_days:
            raise ValueError("Receipt retention must be at least payload retention")
        url = str(database_url_or_path)
        if "://" not in url:
            url = "sqlite:///:memory:" if url == ":memory:" else "sqlite:///" + str(Path(url).resolve())
        self.sqlite = url.startswith("sqlite:")
        if self.sqlite and make_url(url).database not in {None, "", ":memory:"}:
            # Protect relationship metadata as well as encrypted private content.
            path = Path(make_url(url).database)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
            except FileExistsError:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise ValueError("Existing database must be a private regular file (0600)")
        options = {"connect_args": {"check_same_thread": False, "timeout": 30}} if self.sqlite else {}
        if url.endswith(":memory:"):
            options["poolclass"] = StaticPool
        self.engine = create_engine(url, hide_parameters=True, **options)
        self._mutex = threading.RLock()
        if self.sqlite:
            @event.listens_for(self.engine, "connect")
            def pragmas(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("PRAGMA busy_timeout=30000")
        meta.create_all(self.engine)
        # Initial schema provisioning is a single operator action, before workers.
        with self._transaction(initializing=True) as db:
            row = _one(db, locks, locks.c.id == 1)
            if row is None:
                db.execute(insert(locks).values(id=1, key_fingerprint=self.crypto.fingerprint))
            elif row["key_fingerprint"] != self.crypto.fingerprint:
                raise ValueError("Keys do not match this network database; restore the original service keys")

    @contextmanager
    def _transaction(self, initializing=False, *, shared=False, keys=()):
        # SQLite retains its single-writer contract. PostgreSQL uses database
        # locks across workers; a Python mutex would defeat independent writes.
        guard = self._mutex if self.sqlite else nullcontext()
        with guard, self.engine.connect() as db:
            try:
                if self.sqlite:
                    db.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    db.begin()
                    if not initializing:
                        db.execute(select(locks).where(locks.c.id == 1).with_for_update(read=shared))
                        self._data_locks(db, keys)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _data_locks(self, db, keys):
        if not self.sqlite:
            # Sorting numeric lock IDs gives every process the same order.
            ids = sorted({int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],
                                         "big", signed=True) for key in keys})
            for key in ids:
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def close(self):
        self.engine.dispose()

    def _principal(self, db, p, *, owner=False, agent=False):
        if not isinstance(p, Principal):
            raise NetworkError("unauthorized", "Authentication required", 401)
        c = _one(db, credentials, credentials.c.id == p.credential_id, credentials.c.active.is_(True))
        if c is None or c["org_id"] != p.org_id or c["agent_id"] != p.agent_id:
            raise NetworkError("unauthorized", "Credential is revoked", 401)
        org = _one(db, orgs, orgs.c.id == p.org_id, orgs.c.status == "active")
        if org is None:
            raise NetworkError("unauthorized", "Organization is inactive", 401)
        if p.agent_id:
            a = _one(db, agents, agents.c.id == p.agent_id, agents.c.org_id == p.org_id, agents.c.status == "active")
            if a is None:
                raise NetworkError("unauthorized", "Agent is inactive", 401)
        if owner and p.agent_id is not None:
            raise NetworkError("forbidden", "Owner credential required", 403)
        if agent and p.agent_id is None:
            raise NetworkError("forbidden", "Delegated agent credential required", 403)
        return org

    def _credential(self, db, org_id, agent_id=None):
        token = new_token()
        db.execute(insert(credentials).values(id=_id(), org_id=org_id, agent_id=agent_id,
                   token_hash=digest(token.encode()), active=True, created=self.clock()))
        return token

    def bootstrap_org(self, label):
        label = _label(label)
        with self._transaction() as db:
            org = dict(id=_id(), label=label, verification="self_declared", status="active", created=self.clock())
            db.execute(insert(orgs).values(**org))
            token = self._credential(db, org["id"])
            return {"organization": org, "owner_token": token}

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) > 512:
            raise NetworkError("unauthorized", "Invalid credential", 401)
        with self._transaction(shared=True) as db:
            c = _one(db, credentials, credentials.c.token_hash == digest(token.encode()), credentials.c.active.is_(True))
            if c is None:
                raise NetworkError("unauthorized", "Invalid credential", 401)
            p = Principal(c["org_id"], c["agent_id"], c["id"])
            self._principal(db, p)
            return p

    def me(self, p):
        with self._transaction(shared=True) as db:
            org = self._principal(db, p)
            return {"org_id": p.org_id, "agent_id": p.agent_id, "role": "agent" if p.agent_id else "owner",
                    "organization": dict(org), "shared_training": False}

    @staticmethod
    def _capabilities(values):
        if not isinstance(values, (list, tuple)) or len(values) > 20:
            raise NetworkError("invalid_input", "At most 20 capability labels are allowed")
        result = [_label(x, "capability", 80) for x in values]
        if any("://" in x or "@" in x for x in result):
            raise NetworkError("invalid_input", "Capabilities must be labels, not endpoints or credentials")
        return json.dumps(sorted(set(result)))

    def _agent_view(self, db, row):
        org = _one(db, orgs, orgs.c.id == row["org_id"])
        return {"id": row["id"], "agent_id": row["id"], "org_id": row["org_id"], "label": row["label"],
                "capabilities": json.loads(row["capabilities"]), "public": row["public"], "status": row["status"],
                "organization_verification": org["verification"]}

    def register_agent(self, p, label, capabilities=None, public=False):
        if not isinstance(public, bool):
            raise NetworkError("invalid_input", "Directory visibility must be a boolean")
        label, caps = _label(label), self._capabilities(capabilities or [])
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            count = db.scalar(select(func.count()).select_from(agents).where(agents.c.org_id == p.org_id))
            if count >= 100:
                raise NetworkError("limit_exceeded", "Organization agent limit reached", 429)
            row = dict(id=_id(), org_id=p.org_id, label=label, capabilities=caps, public=bool(public), status="active", created=self.clock())
            db.execute(insert(agents).values(**row))
            return {"agent": self._agent_view(db, row), "token": self._credential(db, p.org_id, row["id"])}

    def list_owned_agents(self, p):
        with self._transaction(shared=True) as db:
            self._principal(db, p, owner=True)
            return [self._agent_view(db, a) for a in db.execute(select(agents).where(agents.c.org_id == p.org_id)).mappings()]

    def update_agent(self, p, agent_id, *, label=None, capabilities=None, public=None):
        values = {}
        if label is not None:
            values["label"] = _label(label)
        if capabilities is not None:
            values["capabilities"] = self._capabilities(capabilities)
        if public is not None:
            if not isinstance(public, bool):
                raise NetworkError("invalid_input", "Directory visibility must be a boolean")
            values["public"] = public
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            a = self._owned(db, p, agent_id)
            if values:
                db.execute(update(agents).where(agents.c.id == agent_id).values(**values))
            return self._agent_view(db, dict(a) | values)

    def _owned(self, db, p, agent_id):
        _identifier(agent_id)
        a = _one(db, agents, agents.c.id == agent_id, agents.c.org_id == p.org_id)
        if a is None:
            raise NetworkError("not_found", "Owned agent not found", 404)
        return a

    def rotate_agent_token(self, p, agent_id):
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            a = self._owned(db, p, agent_id)
            if a["status"] == "revoked":
                raise NetworkError("conflict", "Revoked agents cannot receive new credentials", 409)
            db.execute(update(credentials).where(credentials.c.agent_id == agent_id).values(active=False))
            return {"agent_id": agent_id, "token": self._credential(db, p.org_id, agent_id)}

    def rotate_owner_token(self, p):
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            db.execute(update(credentials).where(credentials.c.org_id == p.org_id, credentials.c.agent_id.is_(None)).values(active=False))
            return {"org_id": p.org_id, "owner_token": self._credential(db, p.org_id)}

    def set_agent_status(self, p, agent_id, status):
        if status not in {"active", "paused", "revoked"}:
            raise NetworkError("invalid_input", "Invalid agent status")
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            a = self._owned(db, p, agent_id)
            if a["status"] == "revoked":
                raise NetworkError("conflict", "Agent revocation is permanent", 409)
            db.execute(update(agents).where(agents.c.id == agent_id).values(status=status))
            if status == "revoked":
                db.execute(update(credentials).where(credentials.c.agent_id == agent_id).values(active=False))
                db.execute(update(members).where(members.c.agent_id == agent_id).values(status="removed"))
                db.execute(update(contacts).where(or_(contacts.c.sender == agent_id, contacts.c.recipient == agent_id)).values(status="revoked"))
            return {"agent_id": agent_id, "status": status}

    def verify_organization(self, org_id, *, verified=True):
        """Operator-only administration. Intentionally not exposed as an owner API."""
        _identifier(org_id)
        with self._transaction() as db:
            if _one(db, orgs, orgs.c.id == org_id) is None:
                raise NetworkError("not_found", "Organization not found", 404)
            status = "operator_verified" if verified else "self_declared"
            db.execute(update(orgs).where(orgs.c.id == org_id).values(verification=status))
            return {"org_id": org_id, "verification": status}

    def _blocked(self, db, a, b):
        return _one(db, blocks, or_(and_(blocks.c.sender == a, blocks.c.recipient == b),
                                   and_(blocks.c.sender == b, blocks.c.recipient == a))) is not None

    def _connected(self, db, a, b):
        if a == b:
            return True
        return not self._blocked(db, a, b) and _one(db, contacts, contacts.c.pair == _pair(a, b), contacts.c.status == "accepted") is not None

    def directory(self, p, query=None):
        if query is not None and (not isinstance(query, str) or len(query) > 200):
            raise NetworkError("invalid_input", "Directory query must be at most 200 characters")
        with self._transaction(shared=True) as db:
            self._principal(db, p)
            rows = db.execute(select(agents).where(agents.c.public.is_(True), agents.c.status == "active").order_by(agents.c.id)).mappings()
            result = []
            for a in rows:
                if p.agent_id and self._blocked(db, p.agent_id, a["id"]):
                    continue
                view = self._agent_view(db, a)
                if query is None or str(query).casefold() in (view["label"] + " " + " ".join(view["capabilities"])).casefold():
                    result.append(view)
                if len(result) == 100:
                    break
            return result

    def get_agent(self, p, agent_id):
        _identifier(agent_id)
        with self._transaction(shared=True) as db:
            self._principal(db, p)
            a = _one(db, agents, agents.c.id == agent_id)
            if a is None or (a["org_id"] != p.org_id and (a["status"] != "active" or
                not (a["public"] or (p.agent_id and self._connected(db, p.agent_id, agent_id))) or
                (p.agent_id and self._blocked(db, p.agent_id, agent_id)))):
                raise NetworkError("not_found", "Agent is not visible", 404)
            return self._agent_view(db, a)

    def _quota(self, db, bucket, limit=None):
        limit = limit or self.rate_limit
        window = int(self.clock() // 60)
        row = _one(db, quotas, quotas.c.bucket == bucket)
        count = row["count"] if row and row["window"] == window else 0
        if count >= limit:
            raise NetworkError("rate_limited", "Per-minute operating limit reached", 429)
        if row:
            db.execute(update(quotas).where(quotas.c.bucket == bucket).values(window=window, count=count + 1))
        else:
            db.execute(insert(quotas).values(bucket=bucket, window=window, count=1))

    def invite_contact(self, p, target_agent_id):
        _identifier(target_agent_id)
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            a = _one(db, agents, agents.c.id == target_agent_id, agents.c.status == "active")
            if a is None or p.agent_id == target_agent_id or self._blocked(db, p.agent_id, target_agent_id):
                raise NetworkError("not_found", "Target is unavailable", 404)
            # A private identity can be contacted by an out-of-band identifier,
            # but is never made searchable by that invitation.
            row = _one(db, contacts, contacts.c.pair == _pair(p.agent_id, target_agent_id))
            if row and row["status"] in {"pending", "accepted"}:
                return dict(row)
            self._quota(db, "invite:" + p.agent_id, 20)
            self._quota(db, "invite-org:" + p.org_id, 100)
            self._quota(db, "invite-recipient:" + target_agent_id, 40)
            data = dict(id=_id(), sender=p.agent_id, recipient=target_agent_id, pair=_pair(p.agent_id, target_agent_id), status="pending", created=self.clock())
            if row:
                db.execute(delete(contacts).where(contacts.c.id == row["id"]))
            db.execute(insert(contacts).values(**data))
            return data

    def list_contacts(self, p):
        with self._transaction(shared=True) as db:
            self._principal(db, p)
            ids = [p.agent_id] if p.agent_id else list(db.scalars(select(agents.c.id).where(agents.c.org_id == p.org_id)))
            return [dict(r) for r in db.execute(select(contacts).where(or_(contacts.c.sender.in_(ids), contacts.c.recipient.in_(ids)))).mappings()]

    def accept_contact(self, p, contact_id):
        _identifier(contact_id)
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            row = _one(db, contacts, contacts.c.id == contact_id, contacts.c.recipient == p.agent_id)
            if row is None or self._blocked(db, row["sender"], p.agent_id):
                raise NetworkError("not_found", "Invitation not found", 404)
            if row["status"] not in {"pending", "accepted"}:
                raise NetworkError("conflict", "Invitation is no longer pending", 409)
            if _one(db, agents, agents.c.id == row["sender"], agents.c.status == "active") is None:
                raise NetworkError("conflict", "Inviting agent is inactive", 409)
            db.execute(update(contacts).where(contacts.c.id == contact_id).values(status="accepted"))
            return dict(row) | {"status": "accepted"}

    def revoke_contact(self, p, contact_id):
        _identifier(contact_id)
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            row = _one(db, contacts, contacts.c.id == contact_id, or_(contacts.c.sender == p.agent_id, contacts.c.recipient == p.agent_id))
            if row is None:
                raise NetworkError("not_found", "Contact not found", 404)
            db.execute(update(contacts).where(contacts.c.id == contact_id).values(status="revoked"))
            return {"id": contact_id, "status": "revoked"}

    def block_agent(self, p, target_agent_id):
        _identifier(target_agent_id)
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            if target_agent_id == p.agent_id or _one(db, agents, agents.c.id == target_agent_id) is None:
                raise NetworkError("not_found", "Target unavailable", 404)
            block_id = p.agent_id + ":" + target_agent_id
            if _one(db, blocks, blocks.c.id == block_id) is None:
                db.execute(insert(blocks).values(id=block_id, sender=p.agent_id, recipient=target_agent_id))
            db.execute(update(contacts).where(contacts.c.pair == _pair(p.agent_id, target_agent_id)).values(status="revoked"))
            return {"agent_id": target_agent_id, "blocked": True}

    def create_room(self, p, label, participant_ids):
        label = _label(label)
        if not isinstance(participant_ids, (list, tuple)) or not 1 <= len(participant_ids) <= 15:
            raise NetworkError("invalid_input", "Rooms need 1–15 distinct invited agents")
        for agent_id in participant_ids:
            _identifier(agent_id)
        if len(set(participant_ids)) != len(participant_ids):
            raise NetworkError("invalid_input", "Room participants must be distinct")
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            if p.agent_id in participant_ids:
                raise NetworkError("invalid_input", "Creator is included automatically")
            for agent_id in participant_ids:
                if not self._connected(db, p.agent_id, agent_id) or _one(db, agents, agents.c.id == agent_id, agents.c.status == "active") is None:
                    raise NetworkError("forbidden", "Every invited participant must be an active accepted contact", 403)
            self._quota(db, "room:" + p.agent_id, 10)
            self._quota(db, "room-org:" + p.org_id, 50)
            owned_ids = select(agents.c.id).where(agents.c.org_id == p.org_id)
            if db.scalar(select(func.count()).select_from(rooms).where(rooms.c.creator.in_(owned_ids))) >= 1000:
                raise NetworkError("limit_exceeded", "Organization room limit reached", 429)
            room = dict(id=_id(), label=label, creator=p.agent_id, sequence=0, head="0" * 64, created=self.clock())
            db.execute(insert(rooms).values(**room))
            for a in [p.agent_id] + list(participant_ids):
                db.execute(insert(members).values(room_id=room["id"], agent_id=a, status="active" if a == p.agent_id else "pending", joined_sequence=0, joined=self.clock()))
            return room | {"participant_ids": [p.agent_id] + list(participant_ids), "pending": list(participant_ids),
                           "members": [{"agent_id": a, "status": "active" if a == p.agent_id else "pending"} for a in [p.agent_id] + list(participant_ids)]}

    def _member(self, db, p, room_id, pending=False):
        _identifier(room_id)
        self._principal(db, p, agent=True)
        r = _one(db, rooms, rooms.c.id == room_id)
        m = _one(db, members, members.c.room_id == room_id, members.c.agent_id == p.agent_id)
        if r is None or m is None or m["status"] not in ({"active", "pending"} if pending else {"active"}):
            raise NetworkError("not_found", "Room is not accessible", 404)
        if r["created"] <= self.clock() - self.metadata_retention_days * 86400:
            raise NetworkError("expired", "Room retention period has ended", 410)
        if p.agent_id != r["creator"] and not self._connected(db, p.agent_id, r["creator"]):
            raise NetworkError("not_found", "Room permission has been revoked", 404)
        return r, m

    def accept_room(self, p, room_id):
        with self._transaction() as db:
            r, m = self._member(db, p, room_id, pending=True)
            if m["status"] == "pending":
                db.execute(update(members).where(members.c.room_id == room_id, members.c.agent_id == p.agent_id).values(status="active", joined_sequence=r["sequence"], joined=self.clock()))
            return {"room_id": room_id, "agent_id": p.agent_id, "status": "active"}

    def list_rooms(self, p):
        with self._transaction(shared=True) as db:
            self._principal(db, p)
            ids = [p.agent_id] if p.agent_id else list(db.scalars(select(agents.c.id).where(agents.c.org_id == p.org_id)))
            result, seen = [], set()
            for row in db.execute(select(rooms, members.c.status.label("membership_status"), members.c.agent_id).join(members, members.c.room_id == rooms.c.id).where(members.c.agent_id.in_(ids), members.c.status.in_(["active", "pending"]))).mappings():
                if row["created"] > self.clock() - self.metadata_retention_days * 86400 and row["id"] not in seen and self._connected(db, row["agent_id"], row["creator"]):
                    seen.add(row["id"])
                    member_rows = [dict(m) for m in db.execute(select(members.c.agent_id, members.c.status).where(members.c.room_id == row["id"])).mappings()]
                    result.append(dict(row) | {"members": member_rows})
            return result

    def remove_member(self, p, room_id, agent_id):
        _identifier(room_id)
        _identifier(agent_id)
        with self._transaction() as db:
            self._principal(db, p)
            room = _one(db, rooms, rooms.c.id == room_id)
            if room is None:
                raise NetworkError("not_found", "Room not found", 404)
            creator = _one(db, agents, agents.c.id == room["creator"])
            owner = not p.agent_id and creator["org_id"] == p.org_id
            own_removal = not p.agent_id and _one(db, agents, agents.c.id == agent_id, agents.c.org_id == p.org_id)
            if not owner and not own_removal and p.agent_id not in {room["creator"], agent_id}:
                raise NetworkError("forbidden", "Only creator or participant owner can remove membership", 403)
            db.execute(update(members).where(members.c.room_id == room_id, members.c.agent_id == agent_id).values(status="removed"))
            return {"room_id": room_id, "agent_id": agent_id, "status": "removed"}

    @staticmethod
    def _signed_fields(row):
        return {k: row[k] for k in ("id", "room_id", "sequence", "sender", "kind", "parent_id", "accepted_at", "expires_at", "payload_hash", "previous_hash")}

    def _message_view(self, row, *, contents=True):
        result = self._signed_fields(row) | {"message_id": row["id"], "created_at": row["accepted_at"], "event_hash": row["event_hash"], "signature": row["signature"], "public_key": self.crypto.public_key}
        if contents:
            available = row["payload"] is not None and row["expires_at"] > self.clock() and row["accepted_at"] > self.clock() - self.retention_days * 86400
            result["payload"] = self.crypto.decrypt(row["payload"]) if available else None
            result["expired"] = not available
        return result

    def send_message(self, p, room_id, kind, payload, idempotency_key, expires_in=86400, parent_id=None):
        if kind not in MESSAGE_KINDS:
            raise NetworkError("invalid_input", "Unsupported message kind")
        idempotency_key = _label(idempotency_key, "idempotency_key", 160)
        if parent_id is not None:
            _identifier(parent_id)
        if not isinstance(payload, dict):
            raise NetworkError("invalid_input", "Structured payload must be an object")
        try:
            raw = canonical(payload)
        except (TypeError, ValueError, RecursionError) as exc:
            raise NetworkError("invalid_input", "Payload must be finite JSON") from exc
        if len(raw) > self.max_body_bytes:
            raise NetworkError("payload_too_large", "Payload exceeds configured limit", 413)
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or not math.isfinite(expires_in) or not 1 <= expires_in <= self.retention_days * 86400:
            raise NetworkError("invalid_input", "Expiry must be within the configured retention period")
        request_hash = digest(canonical({"room_id": room_id, "kind": kind, "payload": payload, "expires_in": expires_in, "parent_id": parent_id}))
        with self._transaction(shared=True) as db:
            # Membership cannot change while the shared policy lock is held.
            # Lock every mutable quota and sequence before reading their values.
            self._principal(db, p, agent=True)
            participants = db.execute(select(members.c.agent_id).where(
                members.c.room_id == room_id, members.c.status == "active")).scalars().all()
            self._data_locks(db, ["room:" + room_id, "send:" + p.agent_id,
                                 *("receive:" + a for a in participants)])
            room, _ = self._member(db, p, room_id)
            existing = _one(db, messages, messages.c.sender == p.agent_id, messages.c.idempotency_key == idempotency_key)
            if existing:
                if existing["request_hash"] != request_hash:
                    raise NetworkError("idempotency_conflict", "Idempotency key is bound to a different request", 409)
                return self._message_view(existing, contents=False) | {"duplicate": True}
            if room["sequence"] >= self.max_turns:
                raise NetworkError("turn_limit", "Conversation turn limit reached; owner review required", 429)
            recipients = []
            for m in db.execute(select(members).where(members.c.room_id == room_id, members.c.status == "active", members.c.agent_id != p.agent_id)).mappings():
                active = _one(db, agents, agents.c.id == m["agent_id"], agents.c.status == "active")
                if active and self._connected(db, p.agent_id, m["agent_id"]):
                    recipients.append(m["agent_id"])
            if not recipients:
                raise NetworkError("no_recipients", "No active consenting recipient is available", 409)
            if parent_id:
                parent = _one(db, messages, messages.c.id == parent_id, messages.c.room_id == room_id)
                if parent is None or not self._can_read(db, p, parent):
                    raise NetworkError("not_found", "Parent message is not accessible", 404)
            self._quota(db, "send:" + p.agent_id)
            for a in recipients:
                self._quota(db, "receive:" + a, self.rate_limit * 3)
            now = float(self.clock())
            row = dict(id=_id(), room_id=room_id, sequence=room["sequence"] + 1, sender=p.agent_id, kind=kind,
                       parent_id=parent_id, accepted_at=now, expires_at=now + expires_in, payload_hash=digest(raw),
                       payload=self.crypto.encrypt(payload), idempotency_key=idempotency_key, request_hash=request_hash,
                       previous_hash=room["head"])
            signed = self._signed_fields(row)
            row["event_hash"], row["signature"] = digest(canonical(signed)), self.crypto.sign(signed)
            db.execute(insert(messages).values(**row))
            for a in recipients:
                contact = _one(db, contacts, contacts.c.pair == _pair(p.agent_id, a), contacts.c.status == "accepted")
                db.execute(insert(deliveries).values(message_id=row["id"], recipient=a, contact_id=contact["id"], acked_at=None))
            db.execute(update(rooms).where(rooms.c.id == room_id).values(sequence=row["sequence"], head=row["event_hash"]))
            if kind == "question":
                db.execute(insert(tasks).values(id=row["id"], state="submitted", updated_at=now, useful=None, useful_at=None, assigned_agent_id=None))
            # Organization quotas serialize only the final acceptance step.
            # All room/sender/recipient locks are already held; nothing takes
            # a new data lock after this one. A rejected quota rolls back the
            # message, deliveries, chain head and all quota increments together.
            self._data_locks(db, ["org:" + p.org_id])
            self._quota(db, "org:" + p.org_id, self.rate_limit * 10)
            return self._message_view(row, contents=False) | {"duplicate": False, "recipient_count": len(recipients)}

    def _can_read(self, db, p, row):
        if not p.agent_id:
            return False
        m = _one(db, members, members.c.room_id == row["room_id"], members.c.agent_id == p.agent_id, members.c.status == "active")
        room = _one(db, rooms, rooms.c.id == row["room_id"])
        if not m or not room or room["created"] <= self.clock() - self.metadata_retention_days * 86400 or row["sequence"] <= m["joined_sequence"] or not self._connected(db, p.agent_id, room["creator"]):
            return False
        if row["sender"] == p.agent_id:
            return True
        contact = _one(db, contacts, contacts.c.pair == _pair(p.agent_id, row["sender"]), contacts.c.status == "accepted")
        return bool(contact and not self._blocked(db, p.agent_id, row["sender"]) and
                    _one(db, deliveries, deliveries.c.message_id == row["id"], deliveries.c.recipient == p.agent_id,
                         deliveries.c.contact_id == contact["id"]) is not None)

    @staticmethod
    def _paging(after, limit):
        if isinstance(after, bool) or not isinstance(after, int) or after < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise NetworkError("invalid_input", "Cursor must be nonnegative and limit between 1 and 100")

    def list_messages(self, p, room_id, after=0, limit=100):
        self._paging(after, limit)
        with self._transaction(shared=True) as db:
            self._member(db, p, room_id)
            result = []
            for row in db.execute(select(messages).where(messages.c.room_id == room_id, messages.c.sequence > after).order_by(messages.c.sequence)).mappings():
                if self._can_read(db, p, row):
                    result.append(self._message_view(row))
                if len(result) >= limit:
                    break
            return result

    def poll_deliveries(self, p, after=0, limit=100):
        self._paging(after, limit)
        with self._transaction(shared=True) as db:
            self._principal(db, p, agent=True)
            items, cursor = [], after
            for delivery in db.execute(select(deliveries).where(deliveries.c.recipient == p.agent_id, deliveries.c.id > after).order_by(deliveries.c.id)).mappings():
                cursor = delivery["id"]
                row = _one(db, messages, messages.c.id == delivery["message_id"])
                if row and row["expires_at"] > self.clock() and row["payload"] and self._can_read(db, p, row):
                    items.append({"cursor": cursor, "message": self._message_view(row), "acked_at": delivery["acked_at"]})
                if len(items) >= limit:
                    break
            return {"items": items, "next_cursor": cursor}

    def acknowledge(self, p, message_id):
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            row = self._accessible_message(db, p, message_id)
            d = _one(db, deliveries, deliveries.c.message_id == message_id, deliveries.c.recipient == p.agent_id)
            if d is None:
                raise NetworkError("forbidden", "Only a recipient can acknowledge delivery", 403)
            now = d["acked_at"] or self.clock()
            db.execute(update(deliveries).where(deliveries.c.id == d["id"]).values(acked_at=now))
            return {"message_id": row["id"], "acked_at": now, "meaning": "receipt_only"}

    def _accessible_message(self, db, p, message_id, *, unexpired=True):
        _identifier(message_id)
        row = _one(db, messages, messages.c.id == message_id)
        if row is None or not self._can_read(db, p, row):
            raise NetworkError("not_found", "Message not accessible", 404)
        if unexpired and (row["expires_at"] <= self.clock() or row["payload"] is None):
            raise NetworkError("expired", "Message has expired", 410)
        return row

    def get_task(self, p, message_id):
        with self._transaction(shared=True) as db:
            self._principal(db, p, agent=True)
            row = self._accessible_message(db, p, message_id)
            task = _one(db, tasks, tasks.c.id == message_id)
            if task is None:
                raise NetworkError("not_found", "Task not found", 404)
            return dict(task) | {"message_id": message_id, "room_id": row["room_id"], "requester": row["sender"], "status": task["state"],
                                 "created_at": row["accepted_at"], "payload": self.crypto.decrypt(row["payload"])}

    def set_task_status(self, p, message_id, status):
        if status not in {"accepted", "running", "completed", "failed", "cancelled"}:
            raise NetworkError("invalid_input", "Invalid task status")
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            row = self._accessible_message(db, p, message_id)
            task = _one(db, tasks, tasks.c.id == message_id)
            if task is None:
                raise NetworkError("not_found", "Task not found", 404)
            if status == "cancelled":
                if row["sender"] != p.agent_id:
                    raise NetworkError("forbidden", "Only requester can cancel a task", 403)
            elif row["sender"] == p.agent_id:
                raise NetworkError("forbidden", "Recipient must report work status", 403)
            elif task["assigned_agent_id"] is not None and task["assigned_agent_id"] != p.agent_id:
                raise NetworkError("forbidden", "Another recipient already owns this task", 403)
            allowed = {"submitted": {"accepted", "running", "completed", "failed", "cancelled"},
                       "accepted": {"running", "completed", "failed", "cancelled"},
                       "running": {"completed", "failed", "cancelled"}}
            if status != task["state"] and status not in allowed.get(task["state"], set()):
                raise NetworkError("conflict", "Terminal task status cannot be changed", 409)
            now = self.clock()
            assignee = task["assigned_agent_id"] if status == "cancelled" else p.agent_id
            db.execute(update(tasks).where(tasks.c.id == message_id).values(state=status, updated_at=now, assigned_agent_id=assignee))
            return dict(task) | {"state": status, "status": status, "updated_at": now, "message_id": message_id, "assigned_agent_id": assignee}

    def cancel_task(self, p, message_id):
        return self.set_task_status(p, message_id, "cancelled")

    def complete_task(self, p, message_id, useful):
        if not isinstance(useful, bool):
            raise NetworkError("invalid_input", "Usefulness must be a boolean")
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            row = self._accessible_message(db, p, message_id)
            task = _one(db, tasks, tasks.c.id == message_id)
            if task is None or task["state"] != "completed":
                raise NetworkError("conflict", "Only completed tasks can receive usefulness feedback", 409)
            if p.agent_id != row["sender"]:
                raise NetworkError("forbidden", "Only requester can confirm usefulness", 403)
            now = self.clock()
            db.execute(update(tasks).where(tasks.c.id == message_id).values(useful=useful, useful_at=now))
            return {"message_id": message_id, "state": "completed", "useful": useful, "useful_at": now}

    def verify_room(self, p, room_id):
        with self._transaction() as db:
            room, _ = self._member(db, p, room_id)
            head, seq = "0" * 64, 0
            for row in db.execute(select(messages).where(messages.c.room_id == room_id).order_by(messages.c.sequence)).mappings():
                seq += 1
                signed = self._signed_fields(row)
                if row["sequence"] != seq or row["previous_hash"] != head or digest(canonical(signed)) != row["event_hash"] or not self.crypto.verify(signed, row["signature"]):
                    return {"room_id": room_id, "valid": False, "failed_sequence": seq}
                if row["payload"] is not None:
                    try:
                        if digest(canonical(self.crypto.decrypt(row["payload"]))) != row["payload_hash"]:
                            return {"room_id": room_id, "valid": False, "failed_sequence": seq}
                    except Exception:
                        return {"room_id": room_id, "valid": False, "failed_sequence": seq}
                head = row["event_hash"]
            return {"room_id": room_id, "valid": head == room["head"] and seq == room["sequence"], "sequence": seq, "head": head, "public_key": self.crypto.public_key}

    def report(self, p, target_agent_id, reason):
        _identifier(target_agent_id)
        reason = _label(reason, "reason", 2000)
        with self._transaction() as db:
            self._principal(db, p, agent=True)
            if _one(db, agents, agents.c.id == target_agent_id) is None:
                raise NetworkError("not_found", "Agent not found", 404)
            self._quota(db, "report:" + p.agent_id, 10)
            report_id = _id()
            db.execute(insert(reports).values(id=report_id, org_id=p.org_id, sender=p.agent_id, target=target_agent_id,
                                             reason=self.crypto.encrypt({"reason": reason}), created=self.clock()))
            return {"id": report_id, "status": "recorded", "reviewed": False}

    def metrics(self, p):
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            ids = list(db.scalars(select(agents.c.id).where(agents.c.org_id == p.org_id)))
            start = self.clock() - 7 * 86400
            sent = db.scalar(select(func.count()).select_from(messages).where(messages.c.sender.in_(ids), messages.c.accepted_at >= start))
            acked = db.scalar(select(func.count()).select_from(deliveries.join(messages, deliveries.c.message_id == messages.c.id)).where(messages.c.sender.in_(ids), deliveries.c.acked_at.is_not(None), messages.c.accepted_at >= start))
            useful = db.scalar(select(func.count()).select_from(tasks.join(messages, tasks.c.id == messages.c.id)).where(messages.c.sender.in_(ids), tasks.c.useful.is_(True), tasks.c.useful_at >= start))
            return {"org_id": p.org_id, "window_days": 7, "owned_agents": len(ids), "accepted_messages": sent,
                    "recipient_receipts": acked, "requester_confirmed_useful_tasks": useful,
                    "verified_external_adoption": None, "shared_training": False,
                    "interpretation": "Operational counts and participant feedback; not customers, trades, or model training data"}

    def purge_expired(self):
        """Operator job. Removes encrypted bodies and private reports.

        Signed hashes/metadata remain until the room's bounded retention age
        (90 days by default) or earlier explicit deletion. Backup copies require
        the same operator-managed policy; this cannot erase external backups.
        """
        with self._transaction() as db:
            now, cutoff = self.clock(), self.clock() - self.retention_days * 86400
            expired_ids = list(db.scalars(select(messages.c.id).where(or_(messages.c.expires_at <= now, messages.c.accepted_at <= cutoff))))
            count = 0
            if expired_ids:
                count = db.execute(update(messages).where(messages.c.id.in_(expired_ids), messages.c.payload.is_not(None)).values(payload=None)).rowcount
            report_count = db.execute(delete(reports).where(reports.c.created <= cutoff)).rowcount
            expired_rooms = list(db.scalars(select(rooms.c.id).where(rooms.c.created <= now - self.metadata_retention_days * 86400)))
            for room_id in expired_rooms:
                self._delete_room(db, room_id)
            db.execute(delete(quotas).where(quotas.c.window < int(now // 60) - 1))
            if inspect(db).has_table("network_enrollment_claims"):
                claim_table = Table("network_enrollment_claims", MetaData(), autoload_with=db)
                db.execute(delete(claim_table).where(claim_table.c.created < now - 86400))
            # Extension aggregates also expire during idle periods/CLI maintenance.
            analytics_count = 0
            aggregate_cutoff = (datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=29)).isoformat()
            for table_name in ("network_analytics_daily", "network_source_freshness_daily"):
                if inspect(db).has_table(table_name):
                    aggregate_table = Table(table_name, MetaData(), autoload_with=db)
                    analytics_count += db.execute(delete(aggregate_table).where(aggregate_table.c.day < aggregate_cutoff)).rowcount
            return {"purged_payloads": count, "purged_reports": report_count, "purged_rooms": len(expired_rooms), "purged_aggregate_buckets": analytics_count}

    def _delete_room(self, db, room_id):
        ids = select(messages.c.id).where(messages.c.room_id == room_id)
        db.execute(delete(deliveries).where(deliveries.c.message_id.in_(ids)))
        db.execute(delete(tasks).where(tasks.c.id.in_(ids)))
        db.execute(delete(messages).where(messages.c.room_id == room_id))
        db.execute(delete(members).where(members.c.room_id == room_id))
        db.execute(delete(rooms).where(rooms.c.id == room_id))

    def delete_room(self, p, room_id):
        _identifier(room_id)
        with self._transaction() as db:
            self._principal(db, p)
            room = _one(db, rooms, rooms.c.id == room_id)
            if room is None:
                raise NetworkError("not_found", "Room not found", 404)
            creator = _one(db, agents, agents.c.id == room["creator"])
            if p.agent_id != room["creator"] and not (p.agent_id is None and creator["org_id"] == p.org_id):
                raise NetworkError("forbidden", "Only room creator or its owner may delete the room", 403)
            self._delete_room(db, room_id)
            return {"room_id": room_id, "deleted": True}

    def delete_organization(self, p):
        with self._transaction() as db:
            self._principal(db, p, owner=True)
            ids = list(db.scalars(select(agents.c.id).where(agents.c.org_id == p.org_id)))
            for room_id in list(db.scalars(select(rooms.c.id).where(rooms.c.creator.in_(ids)))):
                self._delete_room(db, room_id)
            # Other owners retain their signed receipts, without deleted bodies.
            db.execute(update(messages).where(messages.c.sender.in_(ids)).values(payload=None))
            db.execute(delete(deliveries).where(deliveries.c.recipient.in_(ids)))
            db.execute(delete(members).where(members.c.agent_id.in_(ids)))
            db.execute(delete(contacts).where(or_(contacts.c.sender.in_(ids), contacts.c.recipient.in_(ids))))
            db.execute(delete(blocks).where(or_(blocks.c.sender.in_(ids), blocks.c.recipient.in_(ids))))
            db.execute(delete(reports).where(reports.c.org_id == p.org_id))
            if inspect(db).has_table("network_enrollment_claims"):
                claims_table = Table("network_enrollment_claims", MetaData(), autoload_with=db)
                # Keep only the spent nonce for its bounded anti-replay period.
                db.execute(update(claims_table).where(claims_table.c.org_id == p.org_id).values(org_id=""))
            if inspect(db).has_table("network_analytics_daily"):
                aggregate_table = Table("network_analytics_daily", MetaData(), autoload_with=db)
                db.execute(delete(aggregate_table).where(aggregate_table.c.org_id == p.org_id))
            db.execute(delete(credentials).where(credentials.c.org_id == p.org_id))
            db.execute(delete(agents).where(agents.c.org_id == p.org_id))
            db.execute(delete(orgs).where(orgs.c.id == p.org_id))
            return {"org_id": p.org_id, "deleted": True, "receipt_metadata_may_remain": True}

    def delete_org(self, p):
        return self.delete_organization(p)
