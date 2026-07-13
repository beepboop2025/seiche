"""seiche.attest — the signed, Bitcoin-anchored as-published record.

The notary (seiche/notary.py) hash-chains every published reading, which makes
the record *internally* consistent: edit any past reading and every link after
it breaks. A chain alone still has two holes a skeptical reader will find in
minutes:

  1. **Authorship** — anyone with file access can rewrite the whole chain from
     genesis and recompute every hash. The chain proves order, not who wrote it.
  2. **Time** — nothing stops the operator from regenerating a flattering
     history yesterday and claiming it is a year old.

This module closes both, deliberately staying at the evidence layer (it never
changes what the record says, only makes it provable):

  * **A daily PIT ledger**: one append-only JSONL chain per stream
    ("stress_readings" for the daily regime call, "proof_scoreboard" for the
    PROOF backtest artifacts). One committed record per day per stream; each
    record hashes its own (day, payload, prev_hash), so editing any past day
    breaks every hash after it. Pure stdlib, verifiable anywhere.
  * **Signatures**: each committed record's hash is signed with an Ed25519
    operator key (domain-separated over stream + day + hash, so a signature
    can never be replayed onto another stream or day). Rewriting history now
    requires the private key, and a leaked rewrite is attributable.
  * **Anchoring**: each day's record hash is submitted to the public
    OpenTimestamps calendar servers, which aggregate digests into a Merkle
    tree committed to the Bitcoin blockchain. Once anchored, *nobody* —
    including us — can backdate a record: the proof is verifiable against
    Bitcoin block headers with the standard `ots` tooling, no trust in Seiche
    required. Submission is a real network call to the public calendars (this
    is not simulated); until the calendar's aggregation lands in a block
    (typically a few hours) the stored proof is honestly marked "pending" and
    `upgrade` completes it later.

Storage follows the data-dir convention: ledger JSONL under
backend/data/_pit_ledger/ (SEICHE_PIT_LEDGER_DIR overrides), signature and
anchor sidecars plus the operator keypair and run receipts under
backend/data/_attest/ (SEICHE_ATTEST_DIR overrides). All files are
append-only. Chain verification is pure stdlib; signatures need
`cryptography` (a pinned dependency); anchoring needs network and is gated
(SEICHE_ATTEST_OTS=1, or the CLI which is always explicit). The snapshot hook
itself is gated by SEICHE_ATTEST=1 and must never break a reading.

CLI (idempotent, cron-friendly):
    python -m seiche.attest status                # what is committed / signed / anchored
    python -m seiche.attest sign [--stream S]     # catch-up sign committed records
    python -m seiche.attest anchor [--stream S]   # submit unanchored days to OTS
    python -m seiche.attest upgrade [--stream S]  # complete pending OTS proofs
    python -m seiche.attest verify [--stream S]   # full independent verification
    python -m seiche.attest prove-scoreboard      # sign + anchor the PROOF scoreboard
    python -m seiche.attest pubkey                # operator public key (hex)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

from seiche.config import DATA_DIR, DB_PATH

logger = logging.getLogger("seiche.attest")

DOMAIN = "seiche-pit-v1"
ALGO = "ed25519"
DEFAULT_STREAM = "stress_readings"
SCOREBOARD_STREAM = "proof_scoreboard"

# Public OpenTimestamps calendar servers (free; aggregate into Bitcoin).
CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
)

# OpenTimestamps wire-format constants (see the OTS spec / python-opentimestamps).
_OTS_OP_SHA256 = 0x08
_OTS_OP_SHA1 = 0x02
_OTS_OP_RIPEMD160 = 0x03
_OTS_OP_APPEND = 0xF0
_OTS_OP_PREPEND = 0xF1
_OTS_ATTESTATION = 0x00
_OTS_FORK = 0xFF
_OTS_TAG_PENDING = bytes.fromhex("83dfe30d2ef90c8e")
_OTS_TAG_BITCOIN = bytes.fromhex("0588960d73d71901")

GENESIS = "0" * 64

_ledger_lock = threading.Lock()
_attest_lock = threading.Lock()
_STREAM_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _jsonable(obj):
    """Stable fallback for non-JSON values in hashed manifests: numpy arrays
    and scalars via tolist()/item(), everything else via repr(). Deterministic
    for the same input is all a content hash needs."""
    for attr in ("tolist", "item"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return repr(obj)


def _canonical_hash(obj: dict) -> str:
    body = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_jsonable)
    return hashlib.sha256(body.encode()).hexdigest()


def _append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# The PIT ledger: append-only hash-chained daily record, one JSONL per stream.
# ---------------------------------------------------------------------------
def _ledger_dir(ledger_dir: str | None = None) -> Path:
    p = Path(ledger_dir
             or os.getenv("SEICHE_PIT_LEDGER_DIR")
             or (DATA_DIR / "_pit_ledger"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stream_path(stream: str, ledger_dir: str | None = None) -> Path:
    if not _STREAM_RE.match(stream):
        raise ValueError(f"invalid stream name: {stream!r}")
    return _ledger_dir(ledger_dir) / f"{stream}.jsonl"


def canonical(day: str, payload: dict, prev_hash: str) -> str:
    """Deterministic JSON for hashing — sorted keys, no whitespace drift."""
    return json.dumps({"day": day, "payload": payload, "prev_hash": prev_hash},
                      sort_keys=True, separators=(",", ":"))


def record_hash(record: dict) -> str:
    """SHA-256 over the canonical (day, payload, prev_hash) — the chain commits
    to content AND order."""
    body = canonical(record["day"], record["payload"], record["prev_hash"])
    return hashlib.sha256(body.encode()).hexdigest()


def read_records(stream: str = DEFAULT_STREAM, ledger_dir: str | None = None) -> list[dict]:
    """All records of a stream, in append order. Missing stream -> []."""
    path = _stream_path(stream, ledger_dir)
    if not path.exists():
        return []
    records = []
    with path.open() as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"stream '{stream}' has an unparseable record at {path}:{lineno} "
                    f"(torn write?); restore the file from backup or truncate the bad "
                    f"tail before appending"
                ) from exc
    return records


def verify_chain(stream: str = DEFAULT_STREAM, ledger_dir: str | None = None) -> tuple[bool, int]:
    """Walk the stream's chain from genesis. Returns (ok, first_bad_index);
    first_bad_index is -1 when the chain is intact."""
    prev = GENESIS
    for i, rec in enumerate(read_records(stream, ledger_dir)):
        if rec.get("prev_hash") != prev or rec.get("hash") != record_hash(rec):
            return False, i
        prev = rec["hash"]
    return True, -1


def append_record(day: str, payload: dict, stream: str = DEFAULT_STREAM,
                  ledger_dir: str | None = None) -> dict:
    """Append one day's as-published payload to a stream's chain.

    Returns the committed record {day, payload, prev_hash, hash}. Raises
    ValueError on a duplicate day (one committed record per day per stream —
    the whole point is that the day's record cannot be re-issued) and on a
    corrupted chain (never silently extend a broken history).
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    day = str(day)
    with _ledger_lock:
        records = read_records(stream, ledger_dir)
        ok, bad = verify_chain(stream, ledger_dir)
        if not ok:
            raise ValueError(f"stream '{stream}' chain broken at record {bad}; refusing to append")
        if any(r["day"] == day for r in records):
            raise ValueError(f"stream '{stream}' already has a committed record for day {day}")
        rec = {
            "day": day,
            "payload": payload,
            "prev_hash": records[-1]["hash"] if records else GENESIS,
        }
        rec["hash"] = record_hash(rec)
        path = _stream_path(stream, ledger_dir)
        # fsync before returning: a torn tail line would fail verify_chain and
        # freeze the stream, so the append must be durable, not just buffered.
        with path.open("a") as fh:
            fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        logger.info("PIT ledger '%s': committed day %s (%d records)",
                    stream, day, len(records) + 1)
        return rec


# ---------------------------------------------------------------------------
# Paths and keys
# ---------------------------------------------------------------------------
def _attest_dir(attest_dir: str | None = None) -> Path:
    p = Path(attest_dir
             or os.getenv("SEICHE_ATTEST_DIR")
             or (DATA_DIR / "_attest"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_or_create_keypair(attest_dir: str | None = None):
    """The operator's Ed25519 keypair. Generated once, private key written
    0600; the public key is published (hex) for independent verification.
    Rotate by moving the old pair aside; old signatures verify against the
    public key recorded inside each signature line, so rotation never
    invalidates history."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    d = _attest_dir(attest_dir)
    priv_path, pub_path = d / "operator_key.pem", d / "operator_key.pub"
    if priv_path.exists():
        private = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    else:
        private = Ed25519PrivateKey.generate()
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        priv_path.write_bytes(pem)
        os.chmod(priv_path, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("attest: generated new Ed25519 operator key at %s", priv_path)
    pub_hex = private.public_key().public_bytes_raw().hex()
    if not pub_path.exists() or pub_path.read_text().strip() != pub_hex:
        pub_path.write_text(pub_hex + "\n")
    return private, pub_hex


def public_key_hex(attest_dir: str | None = None) -> str:
    _, pub = load_or_create_keypair(attest_dir)
    return pub


def _sig_message(stream: str, day: str, record_hash_hex: str) -> bytes:
    return f"{DOMAIN}:{stream}:{day}:{record_hash_hex}".encode()


def _run_message(kind: str, manifest_hash: str) -> bytes:
    return f"{DOMAIN}:run:{kind}:{manifest_hash}".encode()


# ---------------------------------------------------------------------------
# Signing the ledger
# ---------------------------------------------------------------------------
def read_signatures(stream: str = DEFAULT_STREAM, attest_dir: str | None = None) -> list[dict]:
    return _read_jsonl(_attest_dir(attest_dir) / f"{stream}.sig.jsonl")


def sign_stream(stream: str = DEFAULT_STREAM, ledger_dir: str | None = None,
                attest_dir: str | None = None) -> dict:
    """Catch-up signer: sign every committed ledger record that does not yet
    have a signature. Idempotent — safe from cron, from the snapshot hook, or
    by hand. Refuses to sign on top of a broken chain (a signature must never
    launder a corrupt history)."""
    ok, bad = verify_chain(stream, ledger_dir)
    if not ok:
        raise ValueError(f"stream '{stream}' chain broken at record {bad}; refusing to sign")
    private, pub_hex = load_or_create_keypair(attest_dir)
    sig_path = _attest_dir(attest_dir) / f"{stream}.sig.jsonl"
    with _attest_lock:
        signed_hashes = {s["record_hash"] for s in _read_jsonl(sig_path)}
        n_new = 0
        for rec in read_records(stream, ledger_dir):
            if rec["hash"] in signed_hashes:
                continue
            msg = _sig_message(stream, rec["day"], rec["hash"])
            _append_jsonl(sig_path, {
                "stream": stream,
                "day": rec["day"],
                "record_hash": rec["hash"],
                "message": msg.decode(),
                "sig": private.sign(msg).hex(),
                "public_key": pub_hex,
                "algo": ALGO,
                "signed_at": _now(),
            })
            n_new += 1
    if n_new:
        logger.info("attest: signed %d new record(s) on stream '%s'", n_new, stream)
    return {"stream": stream, "newly_signed": n_new,
            "total_signed": len(signed_hashes) + n_new}


def verify_stream(stream: str = DEFAULT_STREAM, ledger_dir: str | None = None,
                  attest_dir: str | None = None) -> dict:
    """Full independent verification of a stream: chain intact, every record
    hash recomputes, every record signed, every signature valid under the
    public key recorded beside it. Reports, never raises — a verification
    tool that crashes on bad input is useless to an auditor."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    problems: list[str] = []
    try:
        records = read_records(stream, ledger_dir)
        chain_ok, first_bad = verify_chain(stream, ledger_dir)
    except ValueError as exc:
        return {"stream": stream, "valid": False, "n_records": 0,
                "problems": [f"ledger unreadable: {exc}"]}
    if not chain_ok:
        problems.append(f"hash chain broken at record {first_bad}")
    for i, rec in enumerate(records):
        if rec.get("hash") != record_hash(rec):
            problems.append(f"record {i} (day {rec.get('day')}): stored hash does not recompute")

    sigs = {s["record_hash"]: s for s in read_signatures(stream, attest_dir)}
    current_pub = None
    pub_path = _attest_dir(attest_dir) / "operator_key.pub"
    if pub_path.exists():
        current_pub = pub_path.read_text().strip()
    n_sig_ok = 0
    for rec in records:
        s = sigs.get(rec["hash"])
        if s is None:
            problems.append(f"day {rec['day']}: record is not signed")
            continue
        try:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(s["public_key"]))
            pub.verify(bytes.fromhex(s["sig"]),
                       _sig_message(stream, rec["day"], rec["hash"]))
            n_sig_ok += 1
            if current_pub and s["public_key"] != current_pub:
                problems.append(f"day {rec['day']}: signed by a non-current key "
                                f"(rotation is fine, but check it was deliberate)")
        except (InvalidSignature, ValueError):
            problems.append(f"day {rec['day']}: signature INVALID")

    anchors = read_anchors(stream, attest_dir)
    anchored_days = {a["day"] for a in anchors if a["status"] in ("pending", "anchored")}
    n_bitcoin = sum(1 for a in anchors if a["status"] == "anchored")
    fatal = [p for p in problems if "rotation is fine" not in p]
    return {
        "stream": stream,
        "valid": not fatal,
        "n_records": len(records),
        "n_signed_valid": n_sig_ok,
        "n_days_anchored_or_pending": len(anchored_days),
        "n_anchors_bitcoin_confirmed": n_bitcoin,
        "problems": problems,
        "note": "chain + hashes verify with pure stdlib; signatures verify against the "
                "recorded public key; OTS proofs complete to Bitcoin with `ots verify` "
                "or `python -m seiche.attest upgrade`",
    }


# ---------------------------------------------------------------------------
# OpenTimestamps anchoring (real calendar submissions)
# ---------------------------------------------------------------------------
def read_anchors(stream: str = DEFAULT_STREAM, attest_dir: str | None = None) -> list[dict]:
    return _read_jsonl(_attest_dir(attest_dir) / f"{stream}.ots.jsonl")


def _read_varuint(buf: io.BytesIO) -> int:
    value, shift = 0, 0
    while True:
        b = buf.read(1)
        if not b:
            raise ValueError("truncated varuint")
        value |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return value
        shift += 7


def _read_varbytes(buf: io.BytesIO) -> bytes:
    n = _read_varuint(buf)
    data = buf.read(n)
    if len(data) != n:
        raise ValueError("truncated varbytes")
    return data


def parse_ots_fragment(digest: bytes, fragment: bytes) -> list[dict]:
    """Walk an OTS timestamp fragment from the submitted digest through its
    commitment operations and return the attestations found.

    Handles the linear fragments the calendars return (append/prepend/sha256
    chains ending in a pending or Bitcoin attestation). Returns entries like
    {"kind": "pending", "uri": ..., "commitment": hex} or
    {"kind": "bitcoin", "height": ..., "commitment": hex}. Raises ValueError
    on structures this parser does not support (rare; the exported proof is
    still standard and completes with the `ots` tooling)."""
    buf = io.BytesIO(fragment)
    out: list[dict] = []

    def walk(commitment: bytes) -> None:
        while True:
            tag = buf.read(1)
            if not tag:
                return
            t = tag[0]
            if t == _OTS_FORK:
                # one forked item follows; both branches share this commitment
                walk_one(commitment)
                continue
            consume(t, commitment)
            return

    def walk_one(commitment: bytes) -> None:
        tag = buf.read(1)
        if not tag:
            raise ValueError("truncated fork")
        consume(tag[0], commitment)

    def consume(t: int, commitment: bytes) -> None:
        if t == _OTS_ATTESTATION:
            tag8 = buf.read(8)
            payload = _read_varbytes(buf)
            if tag8 == _OTS_TAG_PENDING:
                uri = _read_varbytes(io.BytesIO(payload)).decode()
                out.append({"kind": "pending", "uri": uri, "commitment": commitment.hex()})
            elif tag8 == _OTS_TAG_BITCOIN:
                height = _read_varuint(io.BytesIO(payload))
                out.append({"kind": "bitcoin", "height": height, "commitment": commitment.hex()})
            else:
                out.append({"kind": "unknown", "tag": tag8.hex(), "commitment": commitment.hex()})
            return
        if t == _OTS_OP_APPEND:
            commitment = commitment + _read_varbytes(buf)
        elif t == _OTS_OP_PREPEND:
            commitment = _read_varbytes(buf) + commitment
        elif t == _OTS_OP_SHA256:
            commitment = hashlib.sha256(commitment).digest()
        elif t == _OTS_OP_SHA1:
            commitment = hashlib.sha1(commitment).digest()
        elif t == _OTS_OP_RIPEMD160:
            commitment = hashlib.new("ripemd160", commitment).digest()
        else:
            raise ValueError(f"unsupported OTS op 0x{t:02x}")
        walk(commitment)

    walk(digest)
    return out


def _default_client():
    import httpx
    return httpx.Client(timeout=10.0, headers={"User-Agent": "seiche-attest/1.0"})


def anchor_stream(stream: str = DEFAULT_STREAM, ledger_dir: str | None = None,
                  attest_dir: str | None = None, client=None,
                  calendars: tuple[str, ...] = CALENDARS) -> dict:
    """Submit every committed-but-unanchored day's record hash to the public
    OpenTimestamps calendars. The submitted digest IS the record hash (raw 32
    bytes), so a verifier can go straight from the ledger line to the Bitcoin
    proof with no intermediate encoding to trust. One successful calendar
    response is enough (they all aggregate into Bitcoin); failures are logged
    and retried on the next run. Requires network."""
    ots_path = _attest_dir(attest_dir) / f"{stream}.ots.jsonl"
    with _attest_lock:
        done = {a["day"] for a in _read_jsonl(ots_path)
                if a["status"] in ("pending", "anchored")}
        todo = [r for r in read_records(stream, ledger_dir) if r["day"] not in done]
        if not todo:
            return {"stream": stream, "submitted": 0, "already_anchored": len(done)}
        own_client = client is None
        if own_client:
            client = _default_client()
        submitted = 0
        try:
            for rec in todo:
                digest = bytes.fromhex(rec["hash"])
                for cal in calendars:
                    try:
                        resp = client.post(
                            f"{cal}/digest", content=digest,
                            headers={"Accept": "application/vnd.opentimestamps.v1",
                                     "Content-Type": "application/x-www-form-urlencoded"})
                        if resp.status_code != 200:
                            logger.warning("attest: calendar %s returned %s for day %s",
                                           cal, resp.status_code, rec["day"])
                            continue
                        fragment = resp.content
                        atts = []
                        try:
                            atts = parse_ots_fragment(digest, fragment)
                        except ValueError as exc:
                            logger.warning("attest: could not parse fragment from %s: %s", cal, exc)
                        _append_jsonl(ots_path, {
                            "day": rec["day"],
                            "record_hash": rec["hash"],
                            "digest": rec["hash"],
                            "calendar": cal,
                            "fragment_b64": base64.b64encode(fragment).decode(),
                            "attestations": atts,
                            "status": "pending",
                            "submitted_at": _now(),
                        })
                        submitted += 1
                        break  # one calendar per day is sufficient
                    except Exception as exc:  # network errors: log, try next calendar
                        logger.warning("attest: calendar %s failed for day %s: %s",
                                       cal, rec["day"], exc)
        finally:
            if own_client:
                client.close()
    return {"stream": stream, "submitted": submitted,
            "unreachable": len(todo) - submitted, "already_anchored": len(done)}


def upgrade_anchors(stream: str = DEFAULT_STREAM, attest_dir: str | None = None,
                    client=None) -> dict:
    """Complete pending OTS proofs: ask the calendar for the Bitcoin-committed
    continuation of each pending commitment (calendars aggregate roughly
    hourly, so run this a few hours after anchoring, or daily from cron).
    Appends an upgraded line per completed proof; originals are never
    rewritten (append-only, like everything here)."""
    ots_path = _attest_dir(attest_dir) / f"{stream}.ots.jsonl"
    with _attest_lock:
        lines = _read_jsonl(ots_path)
        upgraded_days = {a["day"] for a in lines if a["status"] == "anchored"}
        pending = [a for a in lines
                   if a["status"] == "pending" and a["day"] not in upgraded_days]
        if not pending:
            return {"stream": stream, "upgraded": 0, "still_pending": 0}
        own_client = client is None
        if own_client:
            client = _default_client()
        upgraded = 0
        try:
            for a in pending:
                targets = [att for att in a.get("attestations", []) if att["kind"] == "pending"]
                done = False
                for att in targets:
                    uri = att["uri"].rstrip("/")
                    try:
                        resp = client.get(f"{uri}/timestamp/{att['commitment']}")
                        if resp.status_code != 200:
                            continue
                        frag = resp.content
                        found = parse_ots_fragment(bytes.fromhex(att["commitment"]), frag)
                        btc = [f for f in found if f["kind"] == "bitcoin"]
                        if btc:
                            _append_jsonl(ots_path, {
                                "day": a["day"],
                                "record_hash": a["record_hash"],
                                "digest": a["digest"],
                                "calendar": uri,
                                "fragment_b64": base64.b64encode(frag).decode(),
                                "attestations": found,
                                "bitcoin_height": btc[0]["height"],
                                "status": "anchored",
                                "submitted_at": a["submitted_at"],
                                "upgraded_at": _now(),
                            })
                            upgraded += 1
                            done = True
                            break
                    except Exception as exc:
                        logger.warning("attest: upgrade via %s failed: %s", uri, exc)
                if not done:
                    logger.info("attest: day %s still pending Bitcoin aggregation", a["day"])
        finally:
            if own_client:
                client.close()
    return {"stream": stream, "upgraded": upgraded,
            "still_pending": len(pending) - upgraded}


# ---------------------------------------------------------------------------
# Signed run receipts (decision audit)
# ---------------------------------------------------------------------------
def attest_run(kind: str, manifest: dict, attest_dir: str | None = None) -> dict:
    """Sign a run manifest and persist it as an immutable receipt. The
    manifest should carry everything an independent reader needs to replay
    the decision: engine versions, input content hashes, frozen thresholds,
    and the outcome summary. Aggregate-level only — same public-data contract
    as the rest of the record."""
    private, pub_hex = load_or_create_keypair(attest_dir)
    manifest_hash = _canonical_hash(manifest)
    receipt = {
        "kind": kind,
        "manifest": manifest,
        "manifest_hash": manifest_hash,
        "message": _run_message(kind, manifest_hash).decode(),
        "sig": private.sign(_run_message(kind, manifest_hash)).hex(),
        "public_key": pub_hex,
        "algo": ALGO,
        "attested_at": _now(),
    }
    day = _today()
    runs_dir = _attest_dir(attest_dir) / "runs" / day
    runs_dir.mkdir(parents=True, exist_ok=True)
    receipt_id = f"{kind}-{manifest_hash[:16]}"
    path = runs_dir / f"{receipt_id}.json"
    if not path.exists():  # identical manifest re-run: keep the first receipt
        path.write_text(json.dumps(receipt, sort_keys=True, indent=1))
    return {"receipt_id": receipt_id, "day": day, "manifest_hash": manifest_hash,
            "public_key": pub_hex, "path": str(path)}


def verify_run_receipt(receipt: dict) -> dict:
    """Verify a run receipt independently: manifest hash recomputes and the
    signature is valid under the recorded public key."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    problems = []
    if _canonical_hash(receipt["manifest"]) != receipt["manifest_hash"]:
        problems.append("manifest hash does not recompute (manifest was modified)")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(receipt["public_key"]))
        pub.verify(bytes.fromhex(receipt["sig"]),
                   _run_message(receipt["kind"], receipt["manifest_hash"]))
    except (InvalidSignature, ValueError, KeyError):
        problems.append("signature INVALID")
    return {"valid": not problems, "problems": problems}


# ---------------------------------------------------------------------------
# The snapshot hook: attest the daily stress reading / regime call
# ---------------------------------------------------------------------------
def attest_stress_reading(day: str, record: dict,
                          stream: str = DEFAULT_STREAM,
                          ledger_dir: str | None = None,
                          attest_dir: str | None = None) -> dict:
    """The snapshot-hook entry point (called from assemble._record_pit when
    SEICHE_ATTEST=1): commit the day's aggregate reading to the
    'stress_readings' ledger stream, catch-up sign the stream, write a signed
    run receipt, and, when SEICHE_ATTEST_OTS=1, submit unanchored days to the
    OTS calendars. One committed record per day (the first published reading
    of the data-day; intraday revisions stay visible in the notary chain,
    which appends a link per distinct state). Never raises past its caller's
    try/except — attestation must not break a reading."""
    from seiche import notary

    forecasts = record.get("forecasts") or {}
    payload = {
        "regime": record.get("regime"),
        "value": record.get("value"),
        "coverage_pct": record.get("coverage_pct"),
        "subscores": record.get("subscores"),
        "forward_odds": {
            "p_ensemble": forecasts.get("p_ensemble"),
            "dispersion": forecasts.get("dispersion"),
        },
        # Data vintage: the full as-published pit record's digest (exactly the
        # digest the notary chains, so ledger and notary tie to one another)
        # plus the weight vector's own hash.
        "vintage": {
            "record_sha256": notary.canonical_digest(record),
            "weights_sha256": _canonical_hash(dict(record.get("weights") or {})),
        },
    }
    try:
        rec = append_record(day, payload, stream=stream, ledger_dir=ledger_dir)
        committed = {"committed": True, "hash": rec["hash"]}
    except ValueError as exc:
        # duplicate day = already committed this data-day; that is the contract
        committed = {"committed": False, "reason": str(exc)}
    signed = sign_stream(stream, ledger_dir, attest_dir)
    manifest = {
        "engine": "assemble.snapshot",
        "stream": stream,
        "day": day,
        "regime": record.get("regime"),
        "value": record.get("value"),
        "record_sha256": payload["vintage"]["record_sha256"],
        "ledger_commit": committed,
    }
    receipt = attest_run("stress_reading", manifest, attest_dir)
    anchored = None
    if os.getenv("SEICHE_ATTEST_OTS", "0") == "1":
        try:
            anchored = anchor_stream(stream, ledger_dir, attest_dir)
        except Exception as exc:
            logger.warning("attest: OTS anchoring failed (will retry next run): %s", exc)
            anchored = {"error": str(exc)}
    return {"attested": True, "ledger": committed, "signed": signed, "receipt": receipt,
            "anchoring": anchored if anchored is not None
            else "off (set SEICHE_ATTEST_OTS=1 or run `python -m seiche.attest anchor`)"}


# ---------------------------------------------------------------------------
# PROOF scoreboard proof
# ---------------------------------------------------------------------------
def _load_latest_scoreboard() -> tuple[str, dict]:
    """The PROOF backtest block from the most recent deep-layer blob — exactly
    what the proof_backtest MCP tool serves. Read-only against the store."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT key, payload FROM blobs WHERE key LIKE 'deep:%' "
            "ORDER BY fetched_at DESC"
        ).fetchall()
    finally:
        conn.close()
    for key, payload in rows:
        try:
            blob = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        bt = blob.get("backtest") if isinstance(blob, dict) else None
        if isinstance(bt, dict) and bt.get("ok"):
            return key, bt
    raise FileNotFoundError(
        "no deep-layer blob with a PROOF backtest found in the store; "
        "run `seiche pull` (or hit the API) first")


def prove_scoreboard(scoreboard: dict | None = None, source_key: str | None = None,
                     attest_dir: str | None = None, ledger_dir: str | None = None,
                     anchor: bool = False, client=None) -> dict:
    """Sign (and optionally anchor) the PROOF scoreboard: every top-level
    section of the backtest artifact hashed, a combined root committed to the
    'proof_scoreboard' ledger stream, the whole manifest signed. This proves
    the scoreboard existed in this exact form as of the anchor date — it
    cannot retroactively prove age, so the honest claim is "unchanged since
    first anchored", which compounds in value every month it stands."""
    if scoreboard is None:
        source_key, scoreboard = _load_latest_scoreboard()
    if not isinstance(scoreboard, dict) or not scoreboard:
        raise ValueError("scoreboard must be a non-empty dict")
    sections = {str(k): _canonical_hash(v if isinstance(v, dict) else {"value": v})
                for k, v in sorted(scoreboard.items())}
    root = _canonical_hash(sections)
    manifest = {"corpus": "proof_scoreboard", "source": source_key,
                "n_sections": len(sections), "sections": sections, "root": root,
                "scoreboard": scoreboard}
    receipt = attest_run("proof_scoreboard", manifest, attest_dir)

    day = _today()
    try:
        rec = append_record(day, {"scoreboard_root": root, "source": source_key,
                                  "n_sections": len(sections),
                                  "receipt_id": receipt["receipt_id"]},
                            stream=SCOREBOARD_STREAM, ledger_dir=ledger_dir)
        ledger_note = {"committed": True, "hash": rec["hash"]}
    except ValueError as exc:
        ledger_note = {"committed": False, "reason": str(exc)}
    sign_stream(SCOREBOARD_STREAM, ledger_dir, attest_dir)
    anchored = None
    if anchor:
        anchored = anchor_stream(SCOREBOARD_STREAM, ledger_dir, attest_dir, client=client)
    return {"root": root, "n_sections": len(sections), "source": source_key,
            "receipt": receipt, "ledger": ledger_note, "anchoring": anchored}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> None:
    ap = argparse.ArgumentParser(prog="python -m seiche.attest",
                                 description="Sign and Bitcoin-anchor the as-published record.")
    ap.add_argument("command", choices=["status", "sign", "anchor", "upgrade",
                                        "verify", "prove-scoreboard", "pubkey"])
    ap.add_argument("--stream", default=DEFAULT_STREAM)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "pubkey":
        print(public_key_hex())
        return
    if args.command == "sign":
        print(json.dumps(sign_stream(args.stream), indent=1))
        return
    if args.command == "anchor":
        print(json.dumps(anchor_stream(args.stream), indent=1))
        return
    if args.command == "upgrade":
        print(json.dumps(upgrade_anchors(args.stream), indent=1))
        return
    if args.command == "verify":
        print(json.dumps(verify_stream(args.stream), indent=1))
        return
    if args.command == "prove-scoreboard":
        print(json.dumps(prove_scoreboard(anchor=True), indent=1))
        return
    if args.command == "status":
        recs = read_records(args.stream)
        sigs = read_signatures(args.stream)
        anchors = read_anchors(args.stream)
        print(json.dumps({
            "stream": args.stream,
            "records": len(recs),
            "signed": len({s["record_hash"] for s in sigs}),
            "anchor_pending": len({a["day"] for a in anchors if a["status"] == "pending"}),
            "anchor_bitcoin": len({a["day"] for a in anchors if a["status"] == "anchored"}),
            "public_key": public_key_hex(),
        }, indent=1))


if __name__ == "__main__":
    _cli()
