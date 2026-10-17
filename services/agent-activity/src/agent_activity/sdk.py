"""Explicit contributor client with encrypted durable delivery and exact-id retries."""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
import httpx

from .contracts import Contribution
from .store import canonical, digest, Rejected


class Client:
    def __init__(self, endpoint, credential):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})) or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use an HTTPS service endpoint; loopback HTTP is allowed for local testing")
        if not credential:
            raise ValueError("A separately enrolled contributor credential is required")
        self.endpoint, self.credential = endpoint.rstrip("/"), credential

    def call(self, method, path, payload=None):
        if path not in {"/v1/events", "/v1/identity-claims", "/v1/me"}:
            raise ValueError("Unsupported contributor operation")
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, self.endpoint+path, json=payload, headers={"Authorization": "Bearer "+self.credential}) as response:
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise Rejected("response_size_limit", 502)
                if not 200 <= response.status_code < 300:
                    # Never propagate response bodies that could contain credentials or request echoes.
                    raise Rejected("delivery_http_"+str(response.status_code), response.status_code)
                return json.loads(data)

    def send(self, event):
        event = Contribution.model_validate(event).model_dump(mode="json")
        return self.call("POST", "/v1/events", event)

    def report(self):
        return self.call("GET", "/v1/me")

    def withdraw(self):
        return self.call("DELETE", "/v1/me")


class Outbox:
    def __init__(self, directory, client):
        self.root, self.client = Path(directory).absolute(), client
        if self.root.is_symlink():
            raise ValueError("Outbox cannot be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077 or self.root.stat().st_uid != os.getuid():
            raise ValueError("Outbox must be an owned 0700 directory")
        self.path = self.root / "outbox.sqlite3"
        key = self.root / "outbox.key"
        if not key.exists():
            with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                f.write(Fernet.generate_key())
        for p in [key, self.path]:
            if p.exists() and (p.is_symlink() or p.stat().st_mode & 0o077 or p.stat().st_uid != os.getuid()):
                raise ValueError("Outbox files must be owned and private")
        self.cipher = Fernet(key.read_bytes())
        if not self.path.exists():
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS binding(value TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY,digest TEXT NOT NULL,payload BLOB,
              created REAL NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL,error TEXT);""")
            binding = digest({"endpoint": client.endpoint, "credential_hash": digest(client.credential.encode())})
            old = db.execute("SELECT value FROM binding").fetchone()
            if old and old[0] != binding:
                raise ValueError("Outbox is bound to a different source or destination")
            db.execute("INSERT OR IGNORE INTO binding VALUES(?)", (binding,))

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def enqueue(self, event):
        value = Contribution.model_validate(event).model_dump(mode="json")
        hashed, now = digest(value), time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT digest FROM pending WHERE id=?", (value["event_id"],)).fetchone()
            if old:
                if old[0] != hashed:
                    raise ValueError("Immutable event ID conflict")
                return value["event_id"]
            if db.execute("SELECT count(*) FROM pending WHERE payload IS NOT NULL").fetchone()[0] >= 10000:
                raise ValueError("Outbox full; delivery must be repaired")
            db.execute("INSERT INTO pending VALUES(?,?,?,?,?,0,?,NULL)",
                (value["event_id"], hashed, self.cipher.encrypt(canonical(value)), now, "pending", now))
        return value["event_id"]

    def flush(self, limit=100):
        now, delivered = time.time(), 0
        with self.db() as db:
            db.execute("UPDATE pending SET payload=NULL,state='expired' WHERE payload IS NOT NULL AND created<?", (now-7*86400,))
            db.execute("DELETE FROM pending WHERE payload IS NULL AND created<?", (now-30*86400,))
            rows = db.execute("SELECT id,payload,attempts,digest FROM pending WHERE state='pending' AND due<=? ORDER BY created LIMIT ?", (now, min(max(limit, 1), 100))).fetchall()
        for identifier, payload, attempts, hashed in rows:
            try:
                event = json.loads(self.cipher.decrypt(payload))
                result = self.client.send(event)
                if result.get("event_id") != identifier or result.get("sha256") != hashed:
                    raise Rejected("receipt_mismatch", 502)
                with self.db() as db:
                    db.execute("UPDATE pending SET state='delivered',payload=NULL,error=NULL WHERE id=?", (identifier,))
                delivered += 1
            except (httpx.HTTPError, Rejected, ValueError) as exc:
                terminal = isinstance(exc, Rejected) and 400 <= exc.status < 500 and exc.status not in {408, 429}
                with self.db() as db:
                    db.execute("UPDATE pending SET state=?,attempts=attempts+1,due=?,error=? WHERE id=?",
                        ("rejected" if terminal else "pending", time.time()+min(3600, 2**min(attempts+1, 11)), type(exc).__name__, identifier))
        return {"delivered": delivered, "status": self.status()}

    def status(self):
        with self.db() as db:
            return dict(db.execute("SELECT state,count(*) FROM pending GROUP BY state").fetchall())

    def erase(self):
        with self.db() as db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("DELETE FROM pending")
