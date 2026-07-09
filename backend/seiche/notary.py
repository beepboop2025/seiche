"""seiche.notary — a tamper-evident, Bitcoin-anchorable ledger of the record.

The whole business is "trust in the record". This turns that from a claim into
proof. Every as-published PIT reading is committed here in two layers:

  1. A hash chain (stdlib only). Each reading is canonicalised, SHA-256'd, and
     chained to the one before it, so the ledger is append-only and
     tamper-evident: anyone who kept an old copy can prove no past call was
     altered, reordered, or deleted. This layer always runs, needs no network,
     and is what the pull cycle writes on every snapshot.

  2. A Bitcoin anchor (OpenTimestamps). Each digest is submitted to the OTS
     calendars and settled into the Bitcoin chain, so even a fresh observer with
     no prior copy can prove a reading existed by a given block time — it cannot
     be backdated. This uses the reference `opentimestamps` library, which is an
     OPTIONAL dependency (pip install "seiche[notary]"); without it the chain
     still runs and stamping is a logged no-op.

A competitor can clone the code in a weekend. They cannot clone a Bitcoin-
anchored, honest record that started years earlier. Time is the moat; this makes
it provable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

from seiche.config import DB_PATH

logger = logging.getLogger("seiche.notary")

# The chain's root. Every first commitment links to this constant, so the whole
# chain is anchored to a fixed, published starting point.
GENESIS = "seiche-notary-genesis-v1"

# Public OpenTimestamps calendars (Bitcoin-anchored, free, aggregated).
CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_digest(record: dict) -> str:
    """Deterministic SHA-256 of a reading. Key order and whitespace are fixed so
    the same reading always hashes the same, on any machine, forever."""
    blob = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def chain_hash(prev_hash: str, record_sha256: str, utc: str, pit_date: str) -> str:
    """One link. Binding prev_hash in is what makes the past immutable: change
    any earlier link and every later chain_hash no longer reproduces."""
    payload = f"{prev_hash}|{record_sha256}|{utc}|{pit_date}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notary_log (
               seq           INTEGER PRIMARY KEY AUTOINCREMENT,
               utc           TEXT NOT NULL,
               pit_date      TEXT NOT NULL,
               record_sha256 TEXT NOT NULL UNIQUE,
               prev_hash     TEXT NOT NULL,
               chain_hash    TEXT NOT NULL,
               ots           BLOB
           )"""
    )
    return conn


def head(conn: sqlite3.Connection | None = None) -> str:
    """The current tip of the chain (GENESIS if empty). Publish this anywhere
    (a tweet, a commit) and you have externally timestamped the whole prefix."""
    own = conn is None
    conn = conn or _conn()
    try:
        row = conn.execute(
            "SELECT chain_hash FROM notary_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    finally:
        if own:
            conn.close()
    return row[0] if row else GENESIS


def commit(pit_date: str, record: dict) -> dict:
    """Append one reading to the chain. Idempotent on content: re-committing an
    identical reading (the pull cycle rewrites intraday) is a no-op, while any
    changed reading appends a new link — so the ledger honestly records every
    distinct state the published record ever held."""
    digest = canonical_digest(record)
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT seq, utc, prev_hash, chain_hash FROM notary_log WHERE record_sha256=?",
            (digest,),
        ).fetchone()
        if existing:
            return {"seq": existing[0], "utc": existing[1], "pit_date": pit_date,
                    "record_sha256": digest, "prev_hash": existing[2],
                    "chain_hash": existing[3], "new": False}
        prev = head(conn)
        utc = _now_iso()
        ch = chain_hash(prev, digest, utc, pit_date)
        cur = conn.execute(
            "INSERT INTO notary_log (utc, pit_date, record_sha256, prev_hash, chain_hash) "
            "VALUES (?,?,?,?,?)",
            (utc, pit_date, digest, prev, ch),
        )
        conn.commit()
        return {"seq": cur.lastrowid, "utc": utc, "pit_date": pit_date,
                "record_sha256": digest, "prev_hash": prev, "chain_hash": ch, "new": True}
    finally:
        conn.close()


def entries(limit: int = 500) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT seq, utc, pit_date, record_sha256, prev_hash, chain_hash, "
            "(ots IS NOT NULL) FROM notary_log ORDER BY seq DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"seq": r[0], "utc": r[1], "pit_date": r[2], "record_sha256": r[3],
         "prev_hash": r[4], "chain_hash": r[5], "anchored": bool(r[6])}
        for r in rows
    ]


def verify_chain() -> dict:
    """Recompute every link from GENESIS. Returns ok=False and the first seq that
    breaks if anyone tampered with a past reading, timestamp, or ordering."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT seq, utc, pit_date, record_sha256, prev_hash, chain_hash "
            "FROM notary_log ORDER BY seq ASC"
        ).fetchall()
    finally:
        conn.close()
    prev = GENESIS
    for seq, utc, pit_date, digest, prev_hash, ch in rows:
        if prev_hash != prev:
            return {"ok": False, "n": len(rows), "break_at": seq, "reason": "prev_hash mismatch"}
        if chain_hash(prev_hash, digest, utc, pit_date) != ch:
            return {"ok": False, "n": len(rows), "break_at": seq, "reason": "chain_hash mismatch"}
        prev = ch
    return {"ok": True, "n": len(rows), "head": prev}


# ---------------------------------------------------------------------------
# Bitcoin anchoring via OpenTimestamps (optional dependency).
# ---------------------------------------------------------------------------


def ots_available() -> bool:
    try:
        import opentimestamps  # noqa: F401
        return True
    except Exception:
        return False


def _stamp_digest(digest_hex: str) -> bytes | None:
    """Submit one digest to the OTS calendars and return a serialized .ots proof
    (pending until Bitcoin confirms; upgrade later with `ots upgrade`). Returns
    None if the library is absent or every calendar is unreachable — never
    raises, so a network blip can't stall the pull cycle."""
    try:
        from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
        from opentimestamps.core.op import OpSHA256
        from opentimestamps.core.serialize import BytesSerializationContext
        from opentimestamps.calendar import RemoteCalendar
    except Exception:
        logger.info("opentimestamps not installed; skipping Bitcoin anchor")
        return None

    digest = bytes.fromhex(digest_hex)
    detached = DetachedTimestampFile(OpSHA256(), Timestamp(digest))
    got = False
    for url in CALENDARS:
        try:
            result = RemoteCalendar(url).submit(digest, timeout=15)
            detached.timestamp.merge(result)
            got = True
        except Exception as exc:  # try the next calendar
            logger.warning("OTS calendar %s failed: %s", url, exc)
    if not got:
        return None
    ctx = BytesSerializationContext()
    detached.serialize(ctx)
    return ctx.getbytes()


def stamp_pending(limit: int = 200) -> dict:
    """Anchor every not-yet-anchored commitment. Run this from a timer, off the
    hot path — commit() stays local and instant; the network lives here."""
    if not ots_available():
        return {"ok": False, "reason": "opentimestamps not installed "
                "(pip install 'seiche[notary]')", "anchored": 0}
    conn = _conn()
    try:
        pending = conn.execute(
            "SELECT seq, record_sha256 FROM notary_log WHERE ots IS NULL "
            "ORDER BY seq ASC LIMIT ?", (limit,)
        ).fetchall()
        anchored = 0
        for seq, digest in pending:
            proof = _stamp_digest(digest)
            if proof is not None:
                conn.execute("UPDATE notary_log SET ots=? WHERE seq=?", (proof, seq))
                conn.commit()
                anchored += 1
    finally:
        conn.close()
    return {"ok": True, "anchored": anchored, "pending_seen": len(pending)}


def proof_for(record_sha256: str) -> bytes | None:
    """The raw .ots proof for a digest, for `ots verify`."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT ots FROM notary_log WHERE record_sha256=?", (record_sha256,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] is not None else None
