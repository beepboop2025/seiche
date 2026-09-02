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
    including us — can backdate a record: the stored pending and continuation
    fragments are commitment-linked, and an optional Bitcoin Core RPC check
    proves the final commitment is the Merkle root of the canonical block at
    the attested height. Submission is a real network call to the public
    calendars (this is not simulated); until the calendar's aggregation lands
    in a block (typically a few hours) the stored proof is honestly marked
    "pending" and `upgrade` completes it later.

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
    python -m seiche.attest verify [--stream S] [--bitcoin-node URL]
                                                    # structural verification;
                                                    # Core confirms Bitcoin
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
from seiche.nbs_trust import PRODUCTION_TRUSTED_OPERATOR_KEYS

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
_STREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ED25519_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")
_MAX_TRUSTED_OPERATOR_KEYS = 32
_MAX_OTS_FRAGMENT_BYTES = 1024 * 1024
_MAX_OTS_CALENDAR_RESPONSE_BYTES = 10_000
_MAX_OTS_ATTESTATION_PAYLOAD_BYTES = 8192
_MAX_OTS_OP_BYTES = 4096
_MAX_OTS_PENDING_URI_BYTES = 1000
_MAX_OTS_RECURSION_DEPTH = 256
_MAX_OTS_TIMESTAMP_NODES = 4096
_OTS_PENDING_URI_RE = re.compile(rb"^[A-Za-z0-9._/:\-]{0,1000}$")


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
    with _open_stream_text(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# The PIT ledger: append-only hash-chained daily record, one JSONL per stream.
# ---------------------------------------------------------------------------
def _ledger_dir(ledger_dir: str | None = None) -> Path:
    p = Path(
        ledger_dir or os.getenv("SEICHE_PIT_LEDGER_DIR") or (DATA_DIR / "_pit_ledger")
    )
    p.mkdir(parents=True, exist_ok=True)
    return p


def _validate_stream_name(stream: str) -> None:
    if not isinstance(stream, str) or _STREAM_RE.fullmatch(stream) is None:
        raise ValueError(f"invalid stream name: {stream!r}")


def _safe_stream_file(root: Path, stream: str, suffix: str) -> Path:
    """Return a stream sidecar that is provably contained by ``root``.

    Stream names are part of the signed message, so silently rewriting them
    would weaken the audit trail.  Validate the exact name instead, then
    resolve the destination and reject symlink or traversal escapes before any
    read or write.  Keeping every stream file directly beneath its fixed root
    also makes the storage contract straightforward to audit and back up.
    """
    _validate_stream_name(stream)
    resolved_root = root.resolve()
    candidate = (resolved_root / f"{stream}{suffix}").resolve()
    if candidate.parent != resolved_root:
        raise ValueError(f"stream path escapes its storage root: {stream!r}")
    return candidate


def _existing_stream_file(root: Path, stream: str, suffix: str) -> Path | None:
    """Select an existing sidecar by enumerating its trusted storage root.

    Public stream names are compared with entry names, never joined into a
    filesystem path. This keeps request data out of read path expressions and
    still rejects symlinks or non-regular entries with a matching name.
    """
    _validate_stream_name(stream)
    resolved_root = root.resolve(strict=True)
    expected_name = f"{stream}{suffix}"
    try:
        entries = tuple(resolved_root.iterdir())
    except OSError as exc:
        raise ValueError("stream storage is unavailable") from exc
    for candidate in entries:
        if candidate.name != expected_name:
            continue
        if candidate.is_symlink():
            raise ValueError(f"stream path escapes its storage root: {stream!r}")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("stream sidecar is unavailable") from exc
        if resolved.parent != resolved_root or not resolved.is_file():
            raise ValueError(f"stream path escapes its storage root: {stream!r}")
        return resolved
    return None


def _open_stream_text(path: Path):
    """Open one enumerated regular sidecar without following a swapped link."""
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("stream sidecar is not a regular file")
        stream = os.fdopen(descriptor, encoding="utf-8")
        descriptor = -1
        return stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _stream_path(stream: str, ledger_dir: str | None = None) -> Path:
    return _safe_stream_file(_ledger_dir(ledger_dir), stream, ".jsonl")


def canonical(day: str, payload: dict, prev_hash: str) -> str:
    """Deterministic JSON for hashing — sorted keys, no whitespace drift."""
    return json.dumps(
        {"day": day, "payload": payload, "prev_hash": prev_hash},
        sort_keys=True,
        separators=(",", ":"),
    )


def record_hash(record: dict) -> str:
    """SHA-256 over the canonical (day, payload, prev_hash) — the chain commits
    to content AND order."""
    body = canonical(record["day"], record["payload"], record["prev_hash"])
    return hashlib.sha256(body.encode()).hexdigest()


def read_records(
    stream: str = DEFAULT_STREAM, ledger_dir: str | None = None
) -> list[dict]:
    """All records of a stream, in append order. Missing stream -> []."""
    path = _existing_stream_file(_ledger_dir(ledger_dir), stream, ".jsonl")
    if path is None:
        return []
    records = []
    with _open_stream_text(path) as fh:
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


def verify_chain(
    stream: str = DEFAULT_STREAM, ledger_dir: str | None = None
) -> tuple[bool, int]:
    """Walk the stream's chain from genesis. Returns (ok, first_bad_index);
    first_bad_index is -1 when the chain is intact."""
    prev = GENESIS
    seen_days: set[str] = set()
    for i, rec in enumerate(read_records(stream, ledger_dir)):
        if (
            not _valid_record_shape(rec)
            or rec["day"] in seen_days
            or rec["prev_hash"] != prev
            or rec["hash"] != record_hash(rec)
        ):
            return False, i
        seen_days.add(rec["day"])
        prev = rec["hash"]
    return True, -1


def append_record(
    day: str, payload: dict, stream: str = DEFAULT_STREAM, ledger_dir: str | None = None
) -> dict:
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
            raise ValueError(
                f"stream '{stream}' chain broken at record {bad}; refusing to append"
            )
        if any(r["day"] == day for r in records):
            raise ValueError(
                f"stream '{stream}' already has a committed record for day {day}"
            )
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
        logger.info(
            "PIT ledger '%s': committed day %s (%d records)",
            stream,
            day,
            len(records) + 1,
        )
        return rec


# ---------------------------------------------------------------------------
# Paths and keys
# ---------------------------------------------------------------------------
def _attest_dir(attest_dir: str | None = None) -> Path:
    p = Path(attest_dir or os.getenv("SEICHE_ATTEST_DIR") or (DATA_DIR / "_attest"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_or_create_keypair(attest_dir: str | None = None):
    """The operator's Ed25519 keypair. Generated once, private key written
    0600; the public key is published (hex) for independent verification. A
    custom installation also bootstraps a separate trust file exactly once.
    Key rotation is fail-closed until the new key is explicitly added to the
    installation's authenticated trust policy."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    d = _attest_dir(attest_dir)
    priv_path, pub_path = d / "operator_key.pem", d / "operator_key.pub"
    if priv_path.exists():
        private = serialization.load_pem_private_key(
            priv_path.read_bytes(), password=None
        )
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
    trust_path = d / "trusted_operator_keys"
    if not trust_path.exists():
        try:
            descriptor = os.open(
                trust_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
            )
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as trust_file:
                trust_file.write(pub_hex + "\n")
                trust_file.flush()
                os.fsync(trust_file.fileno())
    return private, pub_hex


def public_key_hex(attest_dir: str | None = None) -> str:
    _, pub = load_or_create_keypair(attest_dir)
    return pub


def _read_public_keys(path: Path) -> frozenset[str]:
    """Read a bounded, no-follow trust file containing one Ed25519 key per line."""
    with _open_stream_text(path) as trust_file:
        body = trust_file.read((_MAX_TRUSTED_OPERATOR_KEYS * 65) + 1)
    if len(body) > _MAX_TRUSTED_OPERATOR_KEYS * 65:
        raise ValueError("operator trust policy is too large")
    keys = [line.strip() for line in body.splitlines() if line.strip()]
    if not keys or len(keys) > _MAX_TRUSTED_OPERATOR_KEYS:
        raise ValueError("operator trust policy has no bounded key set")
    if len(keys) != len(set(keys)) or any(
        _SHA256_RE.fullmatch(key) is None for key in keys
    ):
        raise ValueError("operator trust policy contains malformed keys")
    return frozenset(keys)


def _trusted_operator_keys(attest_dir: str | None = None) -> frozenset[str]:
    """Return the authenticated keys accepted for stream and run signatures.

    The hosted Seiche service uses a key allowlist pinned in the signed source
    release. Custom storage roots use the once-bootstrapped trust file; callers
    may instead supply a comma-separated deployment policy through the
    environment. The mutable ``operator_key.pub`` file is never itself treated
    as signature authority.
    """
    configured = os.getenv("SEICHE_ATTEST_TRUSTED_PUBLIC_KEYS", "").strip()
    if configured:
        keys = [key.strip() for key in configured.split(",") if key.strip()]
        if not keys or len(keys) > _MAX_TRUSTED_OPERATOR_KEYS:
            raise ValueError("configured operator trust policy is invalid")
        if len(keys) != len(set(keys)) or any(
            _SHA256_RE.fullmatch(key) is None for key in keys
        ):
            raise ValueError("configured operator trust policy is malformed")
        return frozenset(keys)
    if attest_dir is None and os.getenv("SEICHE_ATTEST_DIR") is None:
        return PRODUCTION_TRUSTED_OPERATOR_KEYS
    trust_path = _attest_dir(attest_dir) / "trusted_operator_keys"
    if trust_path.is_symlink() or not trust_path.exists():
        # Older hosted deployments can legitimately predate the separate
        # trust file. Only the release-pinned production identity may use this
        # migration path; an arbitrary mutable operator_key.pub never can.
        current_key = _current_operator_public_key(attest_dir)
        if current_key in PRODUCTION_TRUSTED_OPERATOR_KEYS:
            return PRODUCTION_TRUSTED_OPERATOR_KEYS
        raise ValueError("operator trust policy is unavailable")
    return _read_public_keys(trust_path)


def _current_operator_public_key(attest_dir: str | None = None) -> str:
    pub_path = _attest_dir(attest_dir) / "operator_key.pub"
    if pub_path.is_symlink() or not pub_path.exists():
        raise ValueError("operator public key is unavailable")
    with _open_stream_text(pub_path) as public_key_file:
        body = public_key_file.read(66)
    public_key = body.strip()
    if len(body) > 65 or _SHA256_RE.fullmatch(public_key) is None:
        raise ValueError("operator public key is malformed")
    return public_key


def verify_trusted_ed25519_signature(
    message: bytes,
    signature_hex: str,
    signer_public_key_hex: str,
    *,
    attest_dir: str | None = None,
) -> None:
    """Verify one detached signature under Seiche's authenticated trust policy.

    The key carried beside a detached signature is only an identifier.  It is
    accepted solely when it is already present in the release-pinned hosted
    allowlist or the installation's explicit trust policy; mutable sidecar
    material never bootstraps authority.  Successful verification returns
    ``None`` and every malformed, untrusted, or invalid input fails closed.
    """

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(message, bytes) or not message:
        raise ValueError("signature message must be non-empty bytes")
    if (
        not isinstance(signature_hex, str)
        or _ED25519_SIGNATURE_RE.fullmatch(signature_hex) is None
    ):
        raise ValueError("Ed25519 signature is malformed")
    if (
        not isinstance(signer_public_key_hex, str)
        or _SHA256_RE.fullmatch(signer_public_key_hex) is None
    ):
        raise ValueError("Ed25519 signer key is malformed")
    # Request-facing verifiers must not let ambient process configuration
    # replace the release-pinned hosted identity.  Tests and non-hosted
    # installations can opt into a separate authenticated policy only by
    # passing its directory explicitly.
    trusted_keys = (
        PRODUCTION_TRUSTED_OPERATOR_KEYS
        if attest_dir is None
        else _trusted_operator_keys(attest_dir)
    )
    if signer_public_key_hex not in trusted_keys:
        raise ValueError("Ed25519 signer key is not trusted")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(signer_public_key_hex)
        )
        public_key.verify(bytes.fromhex(signature_hex), message)
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise ValueError("Ed25519 signature is invalid") from exc


def _sig_message(stream: str, day: str, record_hash_hex: str) -> bytes:
    return f"{DOMAIN}:{stream}:{day}:{record_hash_hex}".encode()


def _run_message(kind: str, manifest_hash: str) -> bytes:
    return f"{DOMAIN}:run:{kind}:{manifest_hash}".encode()


# ---------------------------------------------------------------------------
# Signing the ledger
# ---------------------------------------------------------------------------
def read_signatures(
    stream: str = DEFAULT_STREAM, attest_dir: str | None = None
) -> list[dict]:
    path = _existing_stream_file(_attest_dir(attest_dir), stream, ".sig.jsonl")
    return [] if path is None else _read_jsonl(path)


def sign_stream(
    stream: str = DEFAULT_STREAM,
    ledger_dir: str | None = None,
    attest_dir: str | None = None,
) -> dict:
    """Catch-up signer: sign every committed ledger record that does not yet
    have a signature. Idempotent — safe from cron, from the snapshot hook, or
    by hand. Refuses to sign on top of a broken chain (a signature must never
    launder a corrupt history)."""
    ok, bad = verify_chain(stream, ledger_dir)
    if not ok:
        raise ValueError(
            f"stream '{stream}' chain broken at record {bad}; refusing to sign"
        )
    private, pub_hex = load_or_create_keypair(attest_dir)
    sig_path = _safe_stream_file(_attest_dir(attest_dir), stream, ".sig.jsonl")
    with _attest_lock:
        signed_hashes = {s["record_hash"] for s in _read_jsonl(sig_path)}
        n_new = 0
        for rec in read_records(stream, ledger_dir):
            if rec["hash"] in signed_hashes:
                continue
            msg = _sig_message(stream, rec["day"], rec["hash"])
            _append_jsonl(
                sig_path,
                {
                    "stream": stream,
                    "day": rec["day"],
                    "record_hash": rec["hash"],
                    "message": msg.decode(),
                    "sig": private.sign(msg).hex(),
                    "public_key": pub_hex,
                    "algo": ALGO,
                    "signed_at": _now(),
                },
            )
            n_new += 1
    if n_new:
        logger.info("attest: signed %d new record(s) on stream '%s'", n_new, stream)
    return {
        "stream": stream,
        "newly_signed": n_new,
        "total_signed": len(signed_hashes) + n_new,
    }


def _valid_record_shape(record) -> bool:
    return (
        isinstance(record, dict)
        and set(record) == {"day", "payload", "prev_hash", "hash"}
        and isinstance(record["day"], str)
        and bool(record["day"])
        and isinstance(record["payload"], dict)
        and isinstance(record["prev_hash"], str)
        and _SHA256_RE.fullmatch(record["prev_hash"]) is not None
        and isinstance(record["hash"], str)
        and _SHA256_RE.fullmatch(record["hash"]) is not None
    )


def _valid_signature_shape(signature, stream: str) -> bool:
    if not isinstance(signature, dict) or set(signature) != {
        "stream",
        "day",
        "record_hash",
        "message",
        "sig",
        "public_key",
        "algo",
        "signed_at",
    }:
        return False
    if not (
        signature["stream"] == stream
        and isinstance(signature["day"], str)
        and bool(signature["day"])
        and isinstance(signature["record_hash"], str)
        and _SHA256_RE.fullmatch(signature["record_hash"]) is not None
        and isinstance(signature["public_key"], str)
        and _SHA256_RE.fullmatch(signature["public_key"]) is not None
        and isinstance(signature["sig"], str)
        and _ED25519_SIGNATURE_RE.fullmatch(signature["sig"]) is not None
        and signature["algo"] == ALGO
        and isinstance(signature["signed_at"], str)
        and bool(signature["signed_at"])
    ):
        return False
    return (
        signature["message"]
        == _sig_message(
            stream,
            signature["day"],
            signature["record_hash"],
        ).decode()
    )


def _valid_anchor_shape(anchor, record_identities: set[tuple[str, str]]) -> bool:
    if not isinstance(anchor, dict) or anchor.get("status") not in {
        "pending",
        "anchored",
    }:
        return False
    common_fields = {
        "day",
        "record_hash",
        "digest",
        "calendar",
        "fragment_b64",
        "attestations",
        "status",
        "submitted_at",
    }
    expected_fields = (
        common_fields
        if anchor["status"] == "pending"
        else common_fields | {"bitcoin_height", "upgraded_at"}
    )
    if set(anchor) != expected_fields:
        return False
    day = anchor.get("day")
    record_hash_hex = anchor.get("record_hash")
    if not (
        isinstance(day, str)
        and bool(day)
        and isinstance(record_hash_hex, str)
        and _SHA256_RE.fullmatch(record_hash_hex) is not None
        and anchor.get("digest") == record_hash_hex
        and (day, record_hash_hex) in record_identities
        and isinstance(anchor.get("calendar"), str)
        and anchor["calendar"].startswith("https://")
        and isinstance(anchor.get("fragment_b64"), str)
        and bool(anchor["fragment_b64"])
        and isinstance(anchor.get("attestations"), list)
        and isinstance(anchor.get("submitted_at"), str)
        and bool(anchor["submitted_at"])
    ):
        return False
    try:
        _decode_anchor_fragment(anchor)
    except (ValueError, TypeError):
        return False
    if anchor["status"] == "anchored":
        return (
            type(anchor.get("bitcoin_height")) is int
            and anchor["bitcoin_height"] >= 0
            and isinstance(anchor.get("upgraded_at"), str)
            and bool(anchor["upgraded_at"])
        )
    return True


def _decode_anchor_fragment(anchor: dict) -> bytes:
    encoded = anchor["fragment_b64"]
    if len(encoded) > ((_MAX_OTS_FRAGMENT_BYTES + 2) // 3) * 4:
        raise ValueError("OTS fragment exceeds the verification bound")
    fragment = base64.b64decode(encoded, validate=True)
    if (
        not fragment
        or len(fragment) > _MAX_OTS_FRAGMENT_BYTES
        or base64.b64encode(fragment).decode() != encoded
    ):
        raise ValueError("OTS fragment is empty, oversized, or non-canonical")
    return fragment


def _verify_stream_report(
    stream: str = DEFAULT_STREAM,
    ledger_dir: str | None = None,
    attest_dir: str | None = None,
    bitcoin_rpc=None,
) -> dict:
    """Build a schema-checked verification report for ``verify_stream``."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    problems: list[str] = []
    try:
        records = read_records(stream, ledger_dir)
    except Exception:
        logger.exception("attest: ledger is unreadable; verification stopped")
        return {
            "stream": stream,
            "valid": False,
            "n_records": 0,
            "problems": ["ledger unreadable; inspect operator logs"],
        }

    valid_records: list[dict] = []
    records_by_hash: dict[str, dict] = {}
    seen_days: set[str] = set()
    previous_hash = GENESIS
    for i, rec in enumerate(records):
        if not _valid_record_shape(rec):
            problems.append(f"record {i}: malformed ledger record")
            continue
        if rec["day"] in seen_days:
            problems.append(f"record {i}: duplicate ledger day {rec['day']}")
        seen_days.add(rec["day"])
        if rec["prev_hash"] != previous_hash:
            problems.append(f"hash chain broken at record {i}")
        if rec["hash"] != record_hash(rec):
            problems.append(
                f"record {i} (day {rec['day']}): stored hash does not recompute"
            )
        previous_hash = rec["hash"]
        if rec["hash"] in records_by_hash:
            problems.append(f"record {i}: duplicate ledger hash")
        else:
            records_by_hash[rec["hash"]] = rec
        valid_records.append(rec)

    try:
        raw_signatures = read_signatures(stream, attest_dir)
    except Exception:
        logger.exception("attest: signature ledger is unreadable")
        raw_signatures = []
        problems.append("signature ledger unreadable; inspect operator logs")
    sigs: dict[str, dict] = {}
    for i, signature in enumerate(raw_signatures):
        if not _valid_signature_shape(signature, stream):
            problems.append(f"signature {i}: malformed signature record")
            continue
        record_hash_hex = signature["record_hash"]
        if record_hash_hex in sigs:
            problems.append(f"signature {i}: duplicate record signature")
            continue
        sigs[record_hash_hex] = signature

    trusted_keys: frozenset[str] = frozenset()
    try:
        trusted_keys = _trusted_operator_keys(attest_dir)
    except Exception:
        logger.exception("attest: operator trust policy is unreadable")
        problems.append("operator trust policy unreadable; inspect operator logs")
    try:
        current_pub = _current_operator_public_key(attest_dir)
        if current_pub not in trusted_keys:
            problems.append("current operator public key is not trusted")
    except Exception:
        logger.exception("attest: operator public key is unreadable")
        problems.append("operator public key unreadable; inspect operator logs")

    n_sig_ok = 0
    for record_hash_hex, signature in sigs.items():
        rec = records_by_hash.get(record_hash_hex)
        identity_matches = rec is not None and signature["day"] == rec["day"]
        if rec is None:
            problems.append(
                f"signature for day {signature['day']}: no matching ledger record"
            )
        elif not identity_matches:
            problems.append(f"day {rec['day']}: signature identity mismatch")
        key_is_trusted = signature["public_key"] in trusted_keys
        if not key_is_trusted:
            problems.append(
                f"signature for day {signature['day']}: signer key is not trusted"
            )
        try:
            pub = Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(signature["public_key"])
            )
            pub.verify(
                bytes.fromhex(signature["sig"]),
                _sig_message(stream, signature["day"], record_hash_hex),
            )
            if identity_matches and key_is_trusted:
                n_sig_ok += 1
        except (InvalidSignature, ValueError, TypeError):
            problems.append(f"signature for day {signature['day']}: signature INVALID")
    for rec in valid_records:
        if rec["hash"] not in sigs:
            problems.append(f"day {rec['day']}: record is not signed")

    try:
        raw_anchors = read_anchors(stream, attest_dir)
    except Exception:
        logger.exception("attest: anchor ledger is unreadable")
        raw_anchors = []
        problems.append("anchor ledger unreadable; inspect operator logs")
    record_identities = {(rec["day"], rec["hash"]) for rec in valid_records}
    anchored_identities, bitcoin_evidence = _verify_anchor_evidence(
        raw_anchors,
        record_identities,
        problems,
    )
    bitcoin_confirmed = _verify_bitcoin_evidence(
        bitcoin_evidence, bitcoin_rpc, problems
    )
    return {
        "stream": stream,
        "valid": not problems,
        "n_records": len(records),
        "n_signed_valid": n_sig_ok,
        "n_days_anchored_or_pending": len(anchored_identities),
        "n_anchors_bitcoin_attested": len(bitcoin_evidence),
        "n_anchors_bitcoin_confirmed": len(bitcoin_confirmed),
        "bitcoin_confirmation_check": (
            "bitcoin_core_rpc" if bitcoin_rpc is not None else "not_requested"
        ),
        "problems": problems,
        "note": "chain + hashes verify with pure stdlib; signatures verify against the "
        "release-pinned or installation-approved key set; stored OTS fragments are "
        "parsed and commitment-linked; Bitcoin confirmation requires a configured "
        "Bitcoin Core RPC and is never inferred from an attestation tag alone",
    }


def verify_stream(
    stream: str = DEFAULT_STREAM,
    ledger_dir: str | None = None,
    attest_dir: str | None = None,
    bitcoin_rpc=None,
) -> dict:
    """Full independent verification of a stream: chain and unique-day policy
    intact, exact record/signature identity sets, every signature valid under
    an authenticated operator key, and every stored OTS state semantically
    linked to its record. Reports, never raises — a verification tool that
    crashes on bad input is useless to an auditor."""
    try:
        return _verify_stream_report(stream, ledger_dir, attest_dir, bitcoin_rpc)
    except Exception:
        logger.exception("attest: unexpected verification failure")
        reported_stream = stream if isinstance(stream, str) else "<invalid>"
        return {
            "stream": reported_stream,
            "valid": False,
            "n_records": 0,
            "problems": ["attestation evidence is malformed; inspect operator logs"],
        }


# ---------------------------------------------------------------------------
# OpenTimestamps anchoring (real calendar submissions)
# ---------------------------------------------------------------------------
def read_anchors(
    stream: str = DEFAULT_STREAM, attest_dir: str | None = None
) -> list[dict]:
    path = _existing_stream_file(_attest_dir(attest_dir), stream, ".ots.jsonl")
    return [] if path is None else _read_jsonl(path)


def _read_varuint(buf: io.BytesIO) -> int:
    value, shift = 0, 0
    for index in range(10):
        b = buf.read(1)
        if not b:
            raise ValueError("truncated varuint")
        value |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            if index and b[0] == 0:
                raise ValueError("non-canonical varuint")
            return value
        shift += 7
    raise ValueError("varuint exceeds the verification bound")


def _read_varbytes(buf: io.BytesIO, *, max_len: int, min_len: int = 0) -> bytes:
    n = _read_varuint(buf)
    if n > max_len:
        raise ValueError("varbytes exceeds the protocol bound")
    if n < min_len:
        raise ValueError("varbytes is shorter than the protocol bound")
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
    on structures this bounded parser does not support."""
    # Timestamp messages are arbitrary bounded byte strings.  Our initial
    # record submission is a 32-byte SHA-256 digest, but a calendar continuation
    # starts at the exact PendingAttestation commitment and that intermediate
    # message may be longer.  The final Bitcoin attestation remains restricted
    # to a 32-byte commitment in consume().
    if not digest or len(digest) > _MAX_OTS_OP_BYTES:
        raise ValueError("OTS starting commitment exceeds the protocol bound")
    if not fragment or len(fragment) > _MAX_OTS_FRAGMENT_BYTES:
        raise ValueError("OTS fragment is empty or exceeds the verification bound")
    buf = io.BytesIO(fragment)
    out: list[dict] = []
    nodes_seen = 0

    def account_node() -> None:
        nonlocal nodes_seen
        nodes_seen += 1
        if nodes_seen > _MAX_OTS_TIMESTAMP_NODES:
            raise ValueError("OTS timestamp exceeds the node bound")

    def walk(commitment: bytes, depth: int = 0) -> None:
        if depth >= _MAX_OTS_RECURSION_DEPTH:
            raise ValueError("OTS timestamp exceeds the recursion bound")
        while True:
            tag = buf.read(1)
            if not tag:
                raise ValueError("truncated OTS timestamp child")
            account_node()
            t = tag[0]
            if t == _OTS_FORK:
                # one forked item follows; both branches share this commitment
                walk_one(commitment, depth)
                continue
            consume(t, commitment, depth)
            return

    def walk_one(commitment: bytes, depth: int) -> None:
        tag = buf.read(1)
        if not tag:
            raise ValueError("truncated fork")
        account_node()
        if tag[0] == _OTS_FORK:
            raise ValueError("fork child cannot be another fork marker")
        consume(tag[0], commitment, depth)

    def consume(t: int, commitment: bytes, depth: int) -> None:
        if t == _OTS_ATTESTATION:
            tag8 = buf.read(8)
            if len(tag8) != 8:
                raise ValueError("truncated OTS attestation tag")
            payload = _read_varbytes(buf, max_len=_MAX_OTS_ATTESTATION_PAYLOAD_BYTES)
            if tag8 == _OTS_TAG_PENDING:
                # A remote calendar may return a PendingAttestation at an
                # intermediate operation result.  Unlike a Bitcoin block
                # header attestation, that commitment is not required to be
                # a 32-byte digest.  The calendar hashes raw commitments when
                # it later aggregates them into its Merkle tree.  Keep the
                # generic operation-result bound here and reserve the strict
                # 32-byte rule for final Bitcoin attestations below.
                if not commitment or len(commitment) > _MAX_OTS_OP_BYTES:
                    raise ValueError(
                        "pending attestation commitment exceeds the protocol bound"
                    )
                payload_buffer = io.BytesIO(payload)
                uri_bytes = _read_varbytes(
                    payload_buffer, max_len=_MAX_OTS_PENDING_URI_BYTES
                )
                if _OTS_PENDING_URI_RE.fullmatch(uri_bytes) is None:
                    raise ValueError("pending attestation URI is invalid")
                uri = uri_bytes.decode("ascii")
                if payload_buffer.read(1):
                    raise ValueError("pending attestation has trailing payload")
                out.append(
                    {"kind": "pending", "uri": uri, "commitment": commitment.hex()}
                )
            elif tag8 == _OTS_TAG_BITCOIN:
                if len(commitment) != 32:
                    raise ValueError(
                        "Bitcoin attestation commitment must be exactly 32 bytes"
                    )
                payload_buffer = io.BytesIO(payload)
                height = _read_varuint(payload_buffer)
                if payload_buffer.read(1):
                    raise ValueError("Bitcoin attestation has trailing payload")
                out.append(
                    {
                        "kind": "bitcoin",
                        "height": height,
                        "commitment": commitment.hex(),
                    }
                )
            else:
                out.append(
                    {
                        "kind": "unknown",
                        "tag": tag8.hex(),
                        "commitment": commitment.hex(),
                    }
                )
            return
        if depth >= _MAX_OTS_RECURSION_DEPTH:
            raise ValueError("OTS timestamp exceeds the recursion bound")
        if t == _OTS_OP_APPEND:
            commitment = commitment + _read_varbytes(
                buf, max_len=_MAX_OTS_OP_BYTES, min_len=1
            )
        elif t == _OTS_OP_PREPEND:
            commitment = (
                _read_varbytes(buf, max_len=_MAX_OTS_OP_BYTES, min_len=1) + commitment
            )
        elif t == _OTS_OP_SHA256:
            commitment = hashlib.sha256(commitment).digest()
        elif t == _OTS_OP_SHA1:
            commitment = hashlib.sha1(commitment).digest()
        elif t == _OTS_OP_RIPEMD160:
            commitment = hashlib.new("ripemd160", commitment).digest()
        else:
            raise ValueError(f"unsupported OTS op 0x{t:02x}")
        if not commitment or len(commitment) > _MAX_OTS_OP_BYTES:
            raise ValueError("OTS operation result exceeds the protocol bound")
        walk(commitment, depth + 1)

    walk(digest)
    if buf.read(1):
        raise ValueError("OTS fragment has trailing bytes")
    if not out:
        raise ValueError("OTS fragment contains no attestations")
    return out


def _matching_pending_attestations(anchor: dict, parsed: list[dict]) -> list[dict]:
    """Return only pending leaves bound to the anchor's selected calendar."""
    expected_calendar = anchor["calendar"].rstrip("/")
    return [
        attestation
        for attestation in parsed
        if attestation.get("kind") == "pending"
        and isinstance(attestation.get("uri"), str)
        and attestation["uri"].rstrip("/") == expected_calendar
        and isinstance(attestation.get("commitment"), str)
        and 2 <= len(attestation["commitment"]) <= _MAX_OTS_OP_BYTES * 2
        and len(attestation["commitment"]) % 2 == 0
        and re.fullmatch(r"[0-9a-f]+", attestation["commitment"]) is not None
    ]


def _verify_anchor_evidence(
    raw_anchors: list[dict],
    record_identities: set[tuple[str, str]],
    problems: list[str],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], dict]]:
    """Parse and link every pending/Bitcoin anchor state in append order."""
    pending_by_identity: dict[tuple[str, str], tuple[dict, list[dict]]] = {}
    anchored_identities: set[tuple[str, str]] = set()
    bitcoin_evidence: dict[tuple[str, str], dict] = {}
    seen_anchored: set[tuple[str, str]] = set()

    for index, anchor in enumerate(raw_anchors):
        if not _valid_anchor_shape(anchor, record_identities):
            problems.append(f"anchor {index}: malformed or unbound anchor record")
            continue
        identity = (anchor["day"], anchor["record_hash"])
        fragment = _decode_anchor_fragment(anchor)

        if anchor["status"] == "pending":
            if identity in pending_by_identity:
                problems.append(f"anchor {index}: duplicate pending anchor state")
                continue
            try:
                parsed = parse_ots_fragment(
                    bytes.fromhex(anchor["record_hash"]), fragment
                )
            except (UnicodeDecodeError, ValueError, TypeError):
                problems.append(f"anchor {index}: pending OTS fragment is invalid")
                continue
            pending_attestations = _matching_pending_attestations(anchor, parsed)
            if parsed != anchor["attestations"] or not pending_attestations:
                problems.append(
                    f"anchor {index}: pending OTS attestations do not match the fragment"
                )
                continue
            pending_by_identity[identity] = (anchor, pending_attestations)
            anchored_identities.add(identity)
            continue

        if identity in seen_anchored:
            problems.append(f"anchor {index}: duplicate Bitcoin anchor state")
            continue
        seen_anchored.add(identity)
        parent = pending_by_identity.get(identity)
        if parent is None:
            problems.append(
                f"anchor {index}: Bitcoin anchor has no validated pending parent"
            )
            continue
        parent_anchor, pending_attestations = parent
        if anchor["submitted_at"] != parent_anchor["submitted_at"] or anchor[
            "calendar"
        ].rstrip("/") != parent_anchor["calendar"].rstrip("/"):
            problems.append(f"anchor {index}: Bitcoin anchor parent identity mismatch")
            continue

        continuation_valid = None
        for pending_attestation in pending_attestations:
            try:
                parsed = parse_ots_fragment(
                    bytes.fromhex(pending_attestation["commitment"]), fragment
                )
            except (UnicodeDecodeError, ValueError, TypeError):
                continue
            bitcoin_attestations = [
                attestation
                for attestation in parsed
                if attestation.get("kind") == "bitcoin"
                and type(attestation.get("height")) is int
                and attestation["height"] == anchor["bitcoin_height"]
            ]
            if parsed == anchor["attestations"] and bitcoin_attestations:
                continuation_valid = bitcoin_attestations[0]
                break
        if not continuation_valid:
            problems.append(
                f"anchor {index}: Bitcoin continuation or reported height is invalid"
            )
            continue
        anchored_identities.add(identity)
        bitcoin_evidence[identity] = {
            "height": continuation_valid["height"],
            "commitment": continuation_valid["commitment"],
        }

    return anchored_identities, bitcoin_evidence


def _verify_bitcoin_evidence(
    bitcoin_evidence: dict[tuple[str, str], dict],
    bitcoin_rpc,
    problems: list[str],
) -> set[tuple[str, str]]:
    """Confirm OTS commitments against canonical headers from Bitcoin Core.

    ``bitcoin_rpc`` is a callable taking ``(method, params)``. Without one, the
    offline verifier reports structurally valid Bitcoin attestations but never
    calls them confirmed. A configured node is the consensus trust boundary.
    """
    if bitcoin_rpc is None:
        return set()
    confirmed: set[tuple[str, str]] = set()
    for identity, evidence in sorted(bitcoin_evidence.items()):
        try:
            height = evidence["height"]
            block_hash = bitcoin_rpc("getblockhash", [height])
            header_hex = bitcoin_rpc("getblockheader", [block_hash, False])
            if not (
                isinstance(block_hash, str)
                and _SHA256_RE.fullmatch(block_hash) is not None
                and isinstance(header_hex, str)
                and re.fullmatch(r"[0-9a-f]{160}", header_hex) is not None
            ):
                raise ValueError("Bitcoin Core returned malformed header data")
            header = bytes.fromhex(header_hex)
            computed_hash = hashlib.sha256(hashlib.sha256(header).digest()).digest()
            if computed_hash[::-1].hex() != block_hash:
                raise ValueError("Bitcoin block hash does not match its header")
            if header[36:68].hex() != evidence["commitment"]:
                raise ValueError("OTS commitment is not the block Merkle root")
        except Exception:
            logger.exception("attest: Bitcoin header verification failed")
            problems.append(
                f"day {identity[0]}: Bitcoin attestation does not verify against "
                "the configured node"
            )
            continue
        confirmed.add(identity)
    return confirmed


def _default_client():
    import httpx

    return httpx.Client(timeout=10.0, headers={"User-Agent": "seiche-attest/1.0"})


def _bounded_calendar_response(response) -> tuple[int, bytes]:
    """Read a successful calendar response without exceeding the wire bound."""
    status_code = response.status_code
    if status_code != 200:
        return status_code, b""

    headers = getattr(response, "headers", {})
    raw_length = headers.get("content-length") if hasattr(headers, "get") else None
    if raw_length is not None:
        normalized_length = str(raw_length).strip()
        if not normalized_length.isdigit():
            raise ValueError("calendar returned an invalid Content-Length")
        if int(normalized_length) > _MAX_OTS_CALENDAR_RESPONSE_BYTES:
            raise ValueError("calendar response exceeds the wire bound")

    iter_bytes = getattr(response, "iter_bytes", None)
    chunks = iter_bytes() if callable(iter_bytes) else (response.content,)
    body = bytearray()
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise ValueError("calendar returned a non-bytes response chunk")
        if len(body) + len(chunk) > _MAX_OTS_CALENDAR_RESPONSE_BYTES:
            raise ValueError("calendar response exceeds the wire bound")
        body.extend(chunk)
    return status_code, bytes(body)


def _calendar_request(client, method: str, url: str, **kwargs) -> tuple[int, bytes]:
    """Use streaming HTTP in production while retaining small test clients."""
    stream = getattr(client, "stream", None)
    if callable(stream):
        with stream(method.upper(), url, **kwargs) as response:
            return _bounded_calendar_response(response)
    response = getattr(client, method.lower())(url, **kwargs)
    return _bounded_calendar_response(response)


class _BitcoinCoreRPC:
    """Small credential-sanitizing JSON-RPC adapter for owner-run verification."""

    def __init__(self, endpoint: str):
        from urllib.parse import unquote, urlsplit, urlunsplit

        import httpx

        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Bitcoin Core RPC endpoint must be HTTP(S)")
        if parsed.query or parsed.fragment:
            raise ValueError(
                "Bitcoin Core RPC endpoint must not have query or fragment"
            )
        auth = None
        if parsed.username is not None:
            auth = (unquote(parsed.username), unquote(parsed.password or ""))
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        safe_endpoint = urlunsplit(
            (parsed.scheme, host, parsed.path.rstrip("/") or "/", "", "")
        )
        self._client = httpx.Client(
            base_url=safe_endpoint,
            auth=auth,
            timeout=10.0,
            headers={"User-Agent": "seiche-bitcoin-verifier/1"},
        )
        self._request_id = 0

    def __call__(self, method: str, params: list):
        self._request_id += 1
        response = self._client.post(
            "",
            json={
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            },
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("error") is not None:
            raise ValueError("Bitcoin Core RPC returned an error")
        return body.get("result")

    def close(self) -> None:
        self._client.close()


def anchor_stream(
    stream: str = DEFAULT_STREAM,
    ledger_dir: str | None = None,
    attest_dir: str | None = None,
    client=None,
    calendars: tuple[str, ...] = CALENDARS,
) -> dict:
    """Submit every committed-but-unanchored day's record hash to the public
    OpenTimestamps calendars. The submitted digest IS the record hash (raw 32
    bytes), so a verifier can go straight from the ledger line to the Bitcoin
    proof with no intermediate encoding to trust. One successful calendar
    response is enough (they all aggregate into Bitcoin); failures are logged
    and retried on the next run. Requires network."""
    ots_path = _safe_stream_file(_attest_dir(attest_dir), stream, ".ots.jsonl")
    with _attest_lock:
        records = read_records(stream, ledger_dir)
        identities = {(record["day"], record["hash"]) for record in records}
        existing_problems: list[str] = []
        done, _ = _verify_anchor_evidence(
            _read_jsonl(ots_path), identities, existing_problems
        )
        if existing_problems:
            raise ValueError("existing OTS evidence is invalid; refusing to append")
        todo = [
            record for record in records if (record["day"], record["hash"]) not in done
        ]
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
                        status_code, fragment = _calendar_request(
                            client,
                            "POST",
                            f"{cal}/digest",
                            content=digest,
                            headers={
                                "Accept": "application/vnd.opentimestamps.v1",
                                "Content-Type": "application/x-www-form-urlencoded",
                            },
                        )
                        if status_code != 200:
                            logger.warning(
                                "attest: calendar %s returned %s for day %s",
                                cal,
                                status_code,
                                rec["day"],
                            )
                            continue
                        try:
                            atts = parse_ots_fragment(digest, fragment)
                        except (UnicodeDecodeError, ValueError) as exc:
                            logger.warning(
                                "attest: could not parse fragment from %s: %s", cal, exc
                            )
                            continue
                        if not any(
                            attestation.get("kind") == "pending"
                            and attestation.get("uri", "").rstrip("/")
                            == cal.rstrip("/")
                            for attestation in atts
                        ):
                            logger.warning(
                                "attest: calendar %s returned no bound pending proof",
                                cal,
                            )
                            continue
                        _append_jsonl(
                            ots_path,
                            {
                                "day": rec["day"],
                                "record_hash": rec["hash"],
                                "digest": rec["hash"],
                                "calendar": cal,
                                "fragment_b64": base64.b64encode(fragment).decode(),
                                "attestations": atts,
                                "status": "pending",
                                "submitted_at": _now(),
                            },
                        )
                        submitted += 1
                        break  # one calendar per day is sufficient
                    except Exception as exc:  # network errors: log, try next calendar
                        logger.warning(
                            "attest: calendar %s failed for day %s: %s",
                            cal,
                            rec["day"],
                            exc,
                        )
        finally:
            if own_client:
                client.close()
    return {
        "stream": stream,
        "submitted": submitted,
        "unreachable": len(todo) - submitted,
        "already_anchored": len(done),
    }


def upgrade_anchors(
    stream: str = DEFAULT_STREAM,
    attest_dir: str | None = None,
    client=None,
    ledger_dir: str | None = None,
) -> dict:
    """Complete pending OTS proofs: ask the calendar for the Bitcoin-committed
    continuation of each pending commitment (calendars aggregate roughly
    hourly, so run this a few hours after anchoring, or daily from cron).
    Appends an upgraded line per completed proof; originals are never
    rewritten (append-only, like everything here)."""
    ots_path = _safe_stream_file(_attest_dir(attest_dir), stream, ".ots.jsonl")
    with _attest_lock:
        lines = _read_jsonl(ots_path)
        records = read_records(stream, ledger_dir)
        identities = {(record["day"], record["hash"]) for record in records}
        existing_problems: list[str] = []
        valid_identities, upgraded_identities = _verify_anchor_evidence(
            lines, identities, existing_problems
        )
        if existing_problems:
            raise ValueError("existing OTS evidence is invalid; refusing to upgrade")
        pending = [
            a
            for a in lines
            if a["status"] == "pending"
            and (a["day"], a["record_hash"]) in valid_identities
            and (a["day"], a["record_hash"]) not in upgraded_identities
        ]
        if not pending:
            return {"stream": stream, "upgraded": 0, "still_pending": 0}
        own_client = client is None
        if own_client:
            client = _default_client()
        upgraded = 0
        try:
            for a in pending:
                parsed_pending = parse_ots_fragment(
                    bytes.fromhex(a["record_hash"]), _decode_anchor_fragment(a)
                )
                targets = _matching_pending_attestations(a, parsed_pending)
                done = False
                for att in targets:
                    uri = att["uri"].rstrip("/")
                    try:
                        status_code, frag = _calendar_request(
                            client,
                            "GET",
                            f"{uri}/timestamp/{att['commitment']}",
                        )
                        if status_code != 200:
                            continue
                        found = parse_ots_fragment(
                            bytes.fromhex(att["commitment"]), frag
                        )
                        btc = [f for f in found if f["kind"] == "bitcoin"]
                        if btc:
                            _append_jsonl(
                                ots_path,
                                {
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
                                },
                            )
                            upgraded += 1
                            done = True
                            break
                    except Exception as exc:
                        logger.warning("attest: upgrade via %s failed: %s", uri, exc)
                if not done:
                    logger.info(
                        "attest: day %s still pending Bitcoin aggregation", a["day"]
                    )
        finally:
            if own_client:
                client.close()
    return {
        "stream": stream,
        "upgraded": upgraded,
        "still_pending": len(pending) - upgraded,
    }


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
    return {
        "receipt_id": receipt_id,
        "day": day,
        "manifest_hash": manifest_hash,
        "public_key": pub_hex,
        "path": str(path),
    }


def verify_run_receipt(receipt: dict, attest_dir: str | None = None) -> dict:
    """Verify a run receipt under the same authenticated operator-key policy."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    problems: list[str] = []
    expected_fields = {
        "kind",
        "manifest",
        "manifest_hash",
        "message",
        "sig",
        "public_key",
        "algo",
        "attested_at",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        return {"valid": False, "problems": ["malformed run receipt"]}
    if not (
        isinstance(receipt["kind"], str)
        and bool(receipt["kind"])
        and isinstance(receipt["manifest"], dict)
        and isinstance(receipt["manifest_hash"], str)
        and _SHA256_RE.fullmatch(receipt["manifest_hash"]) is not None
        and isinstance(receipt["public_key"], str)
        and _SHA256_RE.fullmatch(receipt["public_key"]) is not None
        and isinstance(receipt["sig"], str)
        and _ED25519_SIGNATURE_RE.fullmatch(receipt["sig"]) is not None
        and receipt["algo"] == ALGO
        and isinstance(receipt["attested_at"], str)
        and bool(receipt["attested_at"])
    ):
        return {"valid": False, "problems": ["malformed run receipt"]}
    expected_message = _run_message(receipt["kind"], receipt["manifest_hash"])
    if receipt["message"] != expected_message.decode():
        problems.append("signed message does not match the run identity")
    if _canonical_hash(receipt["manifest"]) != receipt["manifest_hash"]:
        problems.append("manifest hash does not recompute (manifest was modified)")
    try:
        trusted_keys = _trusted_operator_keys(attest_dir)
    except Exception:
        trusted_keys = frozenset()
        problems.append("operator trust policy is unavailable")
    if receipt["public_key"] not in trusted_keys:
        problems.append("signer key is not trusted")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(receipt["public_key"]))
        pub.verify(
            bytes.fromhex(receipt["sig"]),
            expected_message,
        )
    except (InvalidSignature, ValueError, TypeError):
        problems.append("signature INVALID")
    return {"valid": not problems, "problems": problems}


# ---------------------------------------------------------------------------
# The snapshot hook: attest the daily stress reading / regime call
# ---------------------------------------------------------------------------
def attest_stress_reading(
    day: str,
    record: dict,
    stream: str = DEFAULT_STREAM,
    ledger_dir: str | None = None,
    attest_dir: str | None = None,
) -> dict:
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
            logger.warning(
                "attest: OTS anchoring failed (will retry next run): %s", exc
            )
            anchored = {"error": str(exc)}
    return {
        "attested": True,
        "ledger": committed,
        "signed": signed,
        "receipt": receipt,
        "anchoring": anchored
        if anchored is not None
        else "off (set SEICHE_ATTEST_OTS=1 or run `python -m seiche.attest anchor`)",
    }


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
        "run `seiche pull` (or hit the API) first"
    )


def prove_scoreboard(
    scoreboard: dict | None = None,
    source_key: str | None = None,
    attest_dir: str | None = None,
    ledger_dir: str | None = None,
    anchor: bool = False,
    client=None,
) -> dict:
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
    sections = {
        str(k): _canonical_hash(v if isinstance(v, dict) else {"value": v})
        for k, v in sorted(scoreboard.items())
    }
    root = _canonical_hash(sections)
    manifest = {
        "corpus": "proof_scoreboard",
        "source": source_key,
        "n_sections": len(sections),
        "sections": sections,
        "root": root,
        "scoreboard": scoreboard,
    }
    receipt = attest_run("proof_scoreboard", manifest, attest_dir)

    day = _today()
    try:
        rec = append_record(
            day,
            {
                "scoreboard_root": root,
                "source": source_key,
                "n_sections": len(sections),
                "receipt_id": receipt["receipt_id"],
            },
            stream=SCOREBOARD_STREAM,
            ledger_dir=ledger_dir,
        )
        ledger_note = {"committed": True, "hash": rec["hash"]}
    except ValueError as exc:
        ledger_note = {"committed": False, "reason": str(exc)}
    sign_stream(SCOREBOARD_STREAM, ledger_dir, attest_dir)
    anchored = None
    if anchor:
        anchored = anchor_stream(
            SCOREBOARD_STREAM, ledger_dir, attest_dir, client=client
        )
    return {
        "root": root,
        "n_sections": len(sections),
        "source": source_key,
        "receipt": receipt,
        "ledger": ledger_note,
        "anchoring": anchored,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m seiche.attest",
        description="Sign and Bitcoin-anchor the as-published record.",
    )
    ap.add_argument(
        "command",
        choices=[
            "status",
            "sign",
            "anchor",
            "upgrade",
            "verify",
            "prove-scoreboard",
            "pubkey",
        ],
    )
    ap.add_argument("--stream", default=DEFAULT_STREAM)
    ap.add_argument(
        "--bitcoin-node",
        default=os.getenv("SEICHE_BITCOIN_RPC_URL"),
        help=(
            "trusted Bitcoin Core JSON-RPC endpoint for confirmation checks; "
            "may also be set with SEICHE_BITCOIN_RPC_URL"
        ),
    )
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
        bitcoin_rpc = _BitcoinCoreRPC(args.bitcoin_node) if args.bitcoin_node else None
        try:
            print(
                json.dumps(
                    verify_stream(args.stream, bitcoin_rpc=bitcoin_rpc), indent=1
                )
            )
        finally:
            if bitcoin_rpc is not None:
                bitcoin_rpc.close()
        return
    if args.command == "prove-scoreboard":
        print(json.dumps(prove_scoreboard(anchor=True), indent=1))
        return
    if args.command == "status":
        recs = read_records(args.stream)
        sigs = read_signatures(args.stream)
        anchors = read_anchors(args.stream)
        print(
            json.dumps(
                {
                    "stream": args.stream,
                    "records": len(recs),
                    "signed": len({s["record_hash"] for s in sigs}),
                    "anchor_pending": len(
                        {a["day"] for a in anchors if a["status"] == "pending"}
                    ),
                    "anchor_bitcoin": len(
                        {a["day"] for a in anchors if a["status"] == "anchored"}
                    ),
                    "public_key": public_key_hex(),
                },
                indent=1,
            )
        )


if __name__ == "__main__":
    _cli()
