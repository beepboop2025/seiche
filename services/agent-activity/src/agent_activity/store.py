"""Private encrypted contributions and independently captured public observations."""

import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
import zlib

from cryptography.fernet import Fernet

from .contracts import Contribution, IdentityClaim


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


class Rejected(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


SOURCES = {
    "erc8004": ("ERC-8004 registry sample", "https://8004scan.io/developers", "Registry metadata; not verified active agents", 86400),
    "x402": ("x402 service catalog", "https://docs.cdp.coinbase.com/x402/buyer/discover-services", "Listed services and facilitator-reported activity; not unique AI buyers", 3600),
    "virtuals_acp": ("Virtuals ACP on Base", "https://os.virtuals.io/acp/overview", "Finalized contract events in the captured block range; AI authorship unknown", 1800),
    "hyperliquid": ("Hyperliquid account activity", "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint", "Reviewed public account panel only; incomplete portfolio performance", 900),
}


class Store:
    def __init__(self, directory):
        self.root = Path(directory).absolute()
        if self.root.is_symlink():
            raise Rejected("linked_state_directory")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077 or self.root.stat().st_uid != os.getuid():
            raise Rejected("state_directory_must_be_owned_and_private")
        self.path = self.root / "activity.sqlite3"
        key = self.root / "private.key"
        if not key.exists():
            try:
                fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
            except FileExistsError:
                pass
        for p in [key, self.path]:
            if p.exists() and (p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077 or p.stat().st_uid != os.getuid()):
                raise Rejected("state_files_must_be_owned_and_private")
        self.cipher = Fernet(key.read_bytes())
        # Create the database privately before SQLite opens it (also avoids umask races).
        try:
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            pass
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reads(id TEXT PRIMARY KEY, source TEXT NOT NULL, captured REAL NOT NULL,
              url TEXT NOT NULL, sha256 TEXT NOT NULL, body BLOB, status TEXT NOT NULL, error TEXT);
            CREATE INDEX IF NOT EXISTS reads_source ON reads(source,captured DESC);
            CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
              name TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, receipt TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS public_events(id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
              occurred REAL NOT NULL, captured REAL NOT NULL, receipt TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_time ON public_events(occurred DESC);
            CREATE TABLE IF NOT EXISTS checkpoints(source TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contributors(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
              organization TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
              authority TEXT NOT NULL, grant_ref TEXT NOT NULL, resale_ref TEXT, independent_ref TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contributions(source TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
              received REAL NOT NULL, expires REAL NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(source,id));
            CREATE TABLE IF NOT EXISTS identity_claims(id TEXT PRIMARY KEY, source TEXT NOT NULL, payload BLOB NOT NULL,
              status TEXT NOT NULL, review_ref TEXT, reviewed REAL, public INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS reviews(source TEXT NOT NULL, event_id TEXT NOT NULL, public_event TEXT NOT NULL,
              reviewed REAL NOT NULL, evidence_digest TEXT NOT NULL, PRIMARY KEY(source,event_id));
            CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, received REAL NOT NULL, payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS quotas(bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY, created REAL NOT NULL, rights_hash TEXT NOT NULL,
              manifest TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")

    @contextlib.contextmanager
    def db(self, write=False):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA secure_delete=ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def encrypt(self, value):
        return self.cipher.encrypt(canonical(value))

    def decrypt(self, value):
        return json.loads(self.cipher.decrypt(value))

    def checkpoint(self, source):
        with self.db() as db:
            row = db.execute("SELECT value FROM checkpoints WHERE source=?", (source,)).fetchone()
            return json.loads(row[0]) if row else None

    def capture(self, source, url, body, entities=(), events=(), checkpoint=None, error=None):
        now, receipt = time.time(), uuid.uuid4().hex
        raw = canonical(body)
        if source not in SOURCES or len(raw) > 8_000_000:
            raise Rejected("invalid_capture")
        with self.db(write=True) as db:
            db.execute("INSERT INTO reads VALUES(?,?,?,?,?,?,?,?)", (receipt, source, now, url, digest(raw),
                       zlib.compress(raw), "error" if error else "ok", error))
            if error:
                return receipt
            for item in entities:
                db.execute("""INSERT INTO entities VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,last_seen=excluded.last_seen,receipt=excluded.receipt,data=excluded.data""",
                    (item["id"], source, item["kind"], item["name"][:150], now, now, receipt, canonical(item).decode()))
            for item in events:
                old = db.execute("SELECT data FROM public_events WHERE id=?", (item["id"],)).fetchone()
                encoded = canonical(item).decode()
                if old and old[0] != encoded:
                    raise Rejected("conflicting_source_event")
                db.execute("INSERT OR IGNORE INTO public_events VALUES(?,?,?,?,?,?,?)",
                           (item["id"], source, item["kind"], item["occurred_at"], now, receipt, encoded))
            if checkpoint is not None:
                db.execute("INSERT INTO checkpoints VALUES(?,?) ON CONFLICT(source) DO UPDATE SET value=excluded.value",
                           (source, canonical(checkpoint).decode()))
        return receipt

    def source_status(self):
        result = []
        with self.db() as db:
            for key, (name, url, scope, interval) in SOURCES.items():
                r = db.execute("SELECT * FROM reads WHERE source=? ORDER BY captured DESC LIMIT 1", (key,)).fetchone()
                n = db.execute("SELECT count(*) FROM entities WHERE source=?", (key,)).fetchone()[0]
                status = "not_connected" if not r else r["status"]
                if r and status == "ok" and time.time() - r["captured"] > interval * 2:
                    status = "stale"
                result.append({"id": key, "name": name, "documentation_url": url, "scope": scope,
                    "status": status, "captured_at": r["captured"] if r else None,
                    "receipt": r["id"] if r else None, "sha256": r["sha256"] if r else None,
                    "error": r["error"] if r else None, "retained_entities": n,
                    "redistribution": "source_metadata_and_collection_counts_only", "bulk_resale": False,
                    "collection_window": self.checkpoint(key) if key != "hyperliquid" else {"scope": "explicitly reviewed public account panel"}})
        return result

    def summary(self):
        with self.db() as db:
            kinds = dict(db.execute("SELECT kind,count(*) FROM public_events GROUP BY kind").fetchall())
            entities = dict(db.execute("SELECT kind,count(*) FROM entities GROUP BY kind").fetchall())
            span = db.execute("SELECT min(occurred),max(occurred) FROM public_events").fetchone()
        return {"schema": "agent-activity.coverage.v1", "as_of": time.time(), "sources": self.source_status(),
            "retained_entities": entities, "observed_events": kinds, "event_window": {"start": span[0], "end": span[1]},
            "coverage": {"global_agent_population": None, "global_coverage_percent": None,
                "economic_value_coverage_percent": None, "sampling": "bounded source samples and explicit account panel",
                "unique_active_ai_agents": None, "performance": "not_established",
                "registry_entries_are_active_agents": False, "service_payers_are_ai_agents": False},
            "private_contributions": "Available only to the enrolled contributor; qualified aggregate exports are separately gated"}

    def entities(self, limit=50, offset=0, kind=None, private=False):
        where, params = (" WHERE kind=?", [kind]) if kind else ("", [])
        with self.db() as db:
            rows = db.execute("SELECT * FROM entities" + where + " ORDER BY last_seen DESC,id LIMIT ? OFFSET ?",
                              [*params, limit, offset]).fetchall()
            total = db.execute("SELECT count(*) FROM entities" + where, params).fetchone()[0]
        items = []
        for r in rows:
            if private:
                value = json.loads(r["data"])
            else:
                value = {"id": r["id"], "kind": r["kind"], "name": r["name"],
                         "attribution": "registry_or_service_declaration", "economic_activity_verified": False}
            value.update(source=r["source"], first_seen=r["first_seen"], last_seen=r["last_seen"], receipt=r["receipt"])
            items.append(value)
        return {"items": items, "total_retained": total, "limit": limit, "offset": offset,
                "scope": "metadata discovery; not a global census or verified performance ranking"}

    def public_events(self, limit=50, source=None):
        where, params = (" WHERE source=?", [source]) if source else ("", [])
        with self.db() as db:
            rows = db.execute("SELECT data,receipt,captured FROM public_events" + where + " ORDER BY occurred DESC,id LIMIT ?",
                              [*params, limit]).fetchall()
        return [dict(json.loads(r["data"]), receipt=r["receipt"], captured_at=r["captured"]) for r in rows]

    def receipt(self, receipt_id, raw=False):
        with self.db() as db:
            r = db.execute("SELECT * FROM reads WHERE id=?", (receipt_id,)).fetchone()
        if not r:
            raise Rejected("receipt_not_found", 404)
        value = {k: r[k] for k in ["id", "source", "captured", "url", "sha256", "status", "error"]}
        if raw:
            data = zlib.decompress(r["body"])
            if digest(data) != r["sha256"]:
                raise Rejected("evidence_integrity_failure", 503)
            value["body"] = json.loads(data)
        return value

    def enroll(self, organization, authority, grant_ref, independent_ref, days=90, resale_ref=None):
        if authority not in {"operator", "broker", "merchant", "payment"} or not 1 <= days <= 365:
            raise Rejected("invalid_enrollment")
        if not all(isinstance(x, str) and 1 <= len(x) <= 150 for x in [organization, grant_ref, independent_ref]):
            raise Rejected("reviewed_enrollment_references_required")
        source, token, now = uuid.uuid4().hex, secrets.token_urlsafe(36), time.time()
        with self.db(write=True) as db:
            db.execute("INSERT INTO contributors VALUES(?,?,?,?,?,0,?,?,?,?)",
                (source, digest(token.encode()), organization, now, now+days*86400, authority, grant_ref, resale_ref, independent_ref))
        return {"contributor_id": source, "credential": token, "expires_at": now+days*86400,
                "authority": authority, "shared_training": False, "resale_permission": bool(resale_ref)}

    def authorized(self, db, token):
        r = db.execute("SELECT * FROM contributors WHERE token_hash=?", (digest(token.encode()),)).fetchone()
        if not r or r["revoked"] or r["expires"] <= time.time():
            raise Rejected("invalid_or_expired_credential", 401)
        return r

    def quota(self, db, bucket, maximum, expires):
        db.execute("INSERT INTO quotas VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (bucket, expires))
        if db.execute("SELECT count FROM quotas WHERE bucket=?", (bucket,)).fetchone()[0] > maximum:
            raise Rejected("quota_exceeded", 429)

    def contribute(self, token, event):
        event = Contribution.model_validate(event)
        payload, now = event.model_dump(mode="json"), time.time()
        event_hash = digest(payload)
        with self.db(write=True) as db:
            c = self.authorized(db, token)
            old = db.execute("SELECT digest,received FROM contributions WHERE source=? AND id=?", (c["id"], event.event_id)).fetchone()
            if old:
                if old["digest"] != event_hash:
                    raise Rejected("event_id_conflict", 409)
                return {"event_id": event.event_id, "sha256": event_hash, "received_at": old["received"], "duplicate": True}
            if event.occurred_at.timestamp() < c["created"] or event.occurred_at.timestamp() > now+60:
                raise Rejected("event_outside_permission_window")
            allowed = {"operator": {"decision", "tool_call", "order", "position_snapshot", "cash_flow", "fill", "failed"},
                       "broker": {"order", "fill", "position_snapshot", "cash_flow", "failed"},
                       "merchant": {"order", "fulfilled", "refunded", "failed"},
                       "payment": {"payment_authorized", "payment_settled", "refunded", "failed"}}
            if event.kind not in allowed[c["authority"]]:
                raise Rejected("source_authority_does_not_admit_event", 403)
            self.quota(db, f"events:{c['id']}:{int(now//86400)}", 10000, (int(now//86400)+1)*86400)
            db.execute("INSERT INTO contributions VALUES(?,?,?,?,?,?)",
                       (c["id"], event.event_id, event_hash, now, min(c["expires"], now+90*86400), self.encrypt(payload)))
        return {"event_id": event.event_id, "sha256": event_hash, "received_at": now, "duplicate": False,
                "verification": "source_reported", "ai_authorship": "operator_declared"}

    def claim(self, token, claim):
        claim = IdentityClaim.model_validate(claim)
        now = time.time()
        with self.db(write=True) as db:
            c = self.authorized(db, token)
            if claim.valid_from.timestamp() < c["created"] or claim.valid_until.timestamp() > c["expires"]:
                raise Rejected("identity_outside_permission_window")
            self.quota(db, f"claims:{c['id']}:{int(now//86400)}", 100, (int(now//86400)+1)*86400)
            identifier = digest({"source": c["id"], "claim": claim.model_dump(mode="json")})
            db.execute("INSERT OR IGNORE INTO identity_claims VALUES(?,?,?,'declared',NULL,NULL,0)",
                       (identifier, c["id"], self.encrypt(claim.model_dump(mode="json"))))
        return {"claim_id": identifier, "status": "declared", "requires_independent_review": True}

    def review_identity(self, claim_id, review_ref, public=False):
        if not review_ref or len(review_ref) > 200:
            raise Rejected("independent_review_reference_required")
        with self.db(write=True) as db:
            r = db.execute("SELECT i.*,c.revoked,c.expires FROM identity_claims i JOIN contributors c ON c.id=i.source WHERE i.id=?", (claim_id,)).fetchone()
            if not r or r["revoked"] or r["expires"] <= time.time():
                raise Rejected("claim_not_active", 404)
            db.execute("UPDATE identity_claims SET status='reviewer_attested',review_ref=?,reviewed=?,public=? WHERE id=?",
                       (review_ref, time.time(), int(public), claim_id))

    def links(self, public_only=False):
        with self.db() as db:
            rows = db.execute("""SELECT i.*,c.expires,c.revoked FROM identity_claims i JOIN contributors c ON c.id=i.source
                WHERE i.status='reviewer_attested' AND c.revoked=0 AND c.expires>?""", (time.time(),)).fetchall()
        result = []
        for r in rows:
            if public_only and not r["public"]:
                continue
            value = self.decrypt(r["payload"])
            if IdentityClaim.model_validate(value).valid_until.timestamp() <= time.time():
                continue
            result.append(dict(value, claim_id=r["id"], contributor=r["source"], public=bool(r["public"]), review_ref=r["review_ref"]))
        return result

    def private_report(self, token):
        now = time.time()
        with self.db() as db:
            c = self.authorized(db, token)
            rows = db.execute("SELECT * FROM contributions WHERE source=? AND expires>? ORDER BY received DESC LIMIT 200", (c["id"], now)).fetchall()
            total = db.execute("SELECT count(*) FROM contributions WHERE source=? AND expires>?", (c["id"], now)).fetchone()[0]
            reviews = {}
            for r in db.execute("SELECT r.*,p.data FROM reviews r JOIN public_events p ON p.id=r.public_event WHERE r.source=?", (c["id"],)):
                if digest(json.loads(r["data"])) == r["evidence_digest"]:
                    reviews[r["event_id"]] = {k: r[k] for k in r.keys() if k != "data"}
            claims = [{"claim_id": r["id"], "status": r["status"], "claim": self.decrypt(r["payload"])}
                      for r in db.execute("SELECT * FROM identity_claims WHERE source=?", (c["id"],))]
        return {"contributor_id": c["id"], "authority": c["authority"], "expires_at": c["expires"],
                "retained_events": total, "returned_limit": 200, "claims": claims,
                "events": [{"event": self.decrypt(r["payload"]), "sha256": r["digest"], "received_at": r["received"],
                            "verification": "independently_reconciled" if r["id"] in reviews else "source_reported",
                            "review": reviews.get(r["id"])} for r in rows],
                "performance": "requires_complete_positions_cashflows_and_costs", "shared_training": False}

    def withdraw(self, token):
        with self.db(write=True) as db:
            c = self.authorized(db, token)
            db.execute("UPDATE contributors SET revoked=1 WHERE id=?", (c["id"],))
            for table in ["contributions", "identity_claims", "reviews"]:
                db.execute(f"DELETE FROM {table} WHERE source=?", (c["id"],))
        return {"withdrawn": True, "active_records_deleted": True, "exports_require_revalidation": True}

    def apply(self, application):
        now, identifier = time.time(), uuid.uuid4().hex
        with self.db(write=True) as db:
            self.quota(db, f"applications:{int(now//86400)}", 100, (int(now//86400)+1)*86400)
            db.execute("INSERT INTO applications VALUES(?,?,?)", (identifier, now, self.encrypt(application)))
        return {"request_id": identifier, "status": "awaiting_source_review", "expires_at": now+30*86400,
                "contributor_access_granted": False, "message_sent": False}

    def expire(self):
        now = time.time()
        with self.db(write=True) as db:
            db.execute("DELETE FROM reviews WHERE (source,event_id) IN (SELECT source,id FROM contributions WHERE expires<=?)", (now,))
            db.execute("DELETE FROM contributions WHERE expires<=?", (now,))
            db.execute("DELETE FROM identity_claims WHERE source IN (SELECT id FROM contributors WHERE revoked=1 OR expires<=?)", (now,))
            db.execute("DELETE FROM applications WHERE received<?", (now-30*86400,))
            db.execute("DELETE FROM quotas WHERE expires<?", (now,))
            db.execute("DELETE FROM public_events WHERE captured<?", (now-90*86400,))
            db.execute("DELETE FROM entities WHERE last_seen<?", (now-90*86400,))
            db.execute("DELETE FROM reads WHERE captured<? AND id NOT IN (SELECT receipt FROM entities UNION SELECT receipt FROM public_events)", (now-90*86400,))
