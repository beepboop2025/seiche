"""The attest layer: signatures + OTS anchoring over the as-published record.

Contract under test: the PIT ledger is an append-only hash chain (one record
per day per stream) that refuses tampered history; signing is idempotent
catch-up over committed records and refuses broken chains; verification
detects payload tampering, missing and forged signatures, and key
substitution; OTS anchoring submits the raw record hash to a calendar, parses
the returned fragment to the pending commitment, and upgrades to a Bitcoin
attestation later; canonical Bitcoin confirmation requires a Core block header;
the snapshot hook is env-gated and never breaks a reading; the scoreboard proof
and public endpoints serve commitments, both proof fragments, and verdicts but
never payloads. All network is faked — the wire format in the fakes is
byte-exact OpenTimestamps serialization, so the parser is tested against the
real format, offline.
"""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from seiche import attest


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger"
    att = tmp_path / "attest"
    monkeypatch.setenv("SEICHE_PIT_LEDGER_DIR", str(ledger))
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(att))
    return str(ledger), str(att)


def _commit_days(ledger_dir, stream="s1", n=3):
    recs = []
    for i in range(n):
        recs.append(
            attest.append_record(
                f"2026-07-{10 + i:02d}",
                {"v": i, "note": "x"},
                stream=stream,
                ledger_dir=ledger_dir,
            )
        )
    return recs


def _signature_record(private_key, public_key, stream, record):
    message = attest._sig_message(stream, record["day"], record["hash"])
    return {
        "stream": stream,
        "day": record["day"],
        "record_hash": record["hash"],
        "message": message.decode(),
        "sig": private_key.sign(message).hex(),
        "public_key": public_key,
        "algo": attest.ALGO,
        "signed_at": "2026-07-12T00:00:00+00:00",
    }


@pytest.mark.parametrize("kind", ["custom", "traversal"])
def test_production_attest_directory_rejects_noncanonical_override(
    tmp_path, monkeypatch, kind
):
    data = tmp_path / "runtime-data"
    data.mkdir()
    canonical = data / "_attest"
    override = (
        tmp_path / "other-attest"
        if kind == "custom"
        else Path(f"{data}/nested/../_attest")
    )
    monkeypatch.setattr(attest, "DATA_DIR", data)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(override))

    with pytest.raises(ValueError, match="canonical runtime path"):
        attest._attest_dir_path()

    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(canonical))
    assert attest._attest_dir_path() == canonical


def test_production_attest_directory_rejects_canonical_symlink(tmp_path, monkeypatch):
    data = tmp_path / "runtime-data"
    data.mkdir()
    outside = tmp_path / "outside-attest"
    outside.mkdir()
    (data / "_attest").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(attest, "DATA_DIR", data)
    monkeypatch.setenv("SEICHE_ENV", "production")
    monkeypatch.delenv("SEICHE_ATTEST_DIR", raising=False)

    with pytest.raises(ValueError, match="canonical runtime path"):
        attest._attest_dir_path()


# ---------------------------------------------------------------------------
# The ledger chain
# ---------------------------------------------------------------------------
def test_ledger_chains_and_refuses_duplicate_day(dirs):
    ledger, _ = dirs
    recs = _commit_days(ledger, n=2)
    assert recs[0]["prev_hash"] == attest.GENESIS
    assert recs[1]["prev_hash"] == recs[0]["hash"]
    assert attest.verify_chain("s1", ledger) == (True, -1)
    with pytest.raises(ValueError, match="already has a committed record"):
        attest.append_record("2026-07-10", {"v": 9}, stream="s1", ledger_dir=ledger)


def test_verification_rejects_a_manually_chained_duplicate_day(dirs):
    ledger, att = dirs
    first = _commit_days(ledger, n=1)[0]
    attest.sign_stream("s1", ledger, att)
    duplicate = {
        "day": first["day"],
        "payload": {"v": 2, "note": "second publication"},
        "prev_hash": first["hash"],
    }
    duplicate["hash"] = attest.record_hash(duplicate)
    with open(os.path.join(ledger, "s1.jsonl"), "a") as stream:
        stream.write(
            json.dumps(duplicate, sort_keys=True, separators=(",", ":")) + "\n"
        )

    assert attest.verify_chain("s1", ledger) == (False, 1)
    with pytest.raises(ValueError, match="refusing to sign"):
        attest.sign_stream("s1", ledger, att)
    verdict = attest.verify_stream("s1", ledger, att)
    assert not verdict["valid"]
    assert any("duplicate ledger day" in problem for problem in verdict["problems"])


def test_ledger_detects_tamper_and_refuses_to_extend(dirs):
    ledger, _ = dirs
    _commit_days(ledger, n=2)
    path = os.path.join(ledger, "s1.jsonl")
    lines = open(path).read().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["v"] = 777
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    open(path, "w").write("\n".join(lines) + "\n")
    ok, bad = attest.verify_chain("s1", ledger)
    assert not ok and bad == 0
    with pytest.raises(ValueError, match="refusing to append"):
        attest.append_record("2026-07-20", {"v": 9}, stream="s1", ledger_dir=ledger)


@pytest.mark.parametrize(
    "stream", ["../evil", ".", "..", ".hidden", "bad/name", "bad\\name", "x" * 129]
)
def test_ledger_rejects_bad_stream_names(dirs, stream):
    ledger, _ = dirs
    with pytest.raises(ValueError, match="invalid stream name"):
        attest.append_record("2026-07-10", {}, stream=stream, ledger_dir=ledger)


def test_stream_sidecars_reject_symlink_escape(dirs, tmp_path):
    ledger, att = dirs
    outside = tmp_path / "outside.jsonl"
    outside.write_text("do not touch\n")
    os.makedirs(ledger, exist_ok=True)
    os.makedirs(att, exist_ok=True)
    os.symlink(outside, os.path.join(ledger, "escaped.jsonl"))
    os.symlink(outside, os.path.join(att, "escaped.sig.jsonl"))

    with pytest.raises(ValueError, match="escapes its storage root"):
        attest.read_records("escaped", ledger)
    with pytest.raises(ValueError, match="escapes its storage root"):
        attest.read_signatures("escaped", att)
    assert outside.read_text() == "do not touch\n"


def test_verify_stream_never_serializes_storage_exception_details(dirs, monkeypatch):
    ledger, att = dirs
    secret = "/Users/operator/private-ledger.jsonl?token=do-not-leak"

    def fail_closed(*_args, **_kwargs):
        raise ValueError(secret)

    monkeypatch.setattr(attest, "read_records", fail_closed)
    verdict = attest.verify_stream("s1", ledger, att)

    assert verdict == {
        "stream": "s1",
        "valid": False,
        "n_records": 0,
        "problems": ["ledger unreadable; inspect operator logs"],
    }
    assert secret not in json.dumps(verdict)


def _write_jsonl(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as stream:
        stream.write(json.dumps(value) + "\n")


def test_verify_stream_reports_malformed_valid_json_record(dirs):
    ledger, att = dirs
    _write_jsonl(
        os.path.join(ledger, "s1.jsonl"),
        {"day": "2026-07-10", "prev_hash": attest.GENESIS, "hash": "0" * 64},
    )

    verdict = attest.verify_stream("s1", ledger, att)

    assert not verdict["valid"]
    assert verdict["n_records"] == 1
    assert "malformed ledger record" in verdict["problems"][0]


def test_verify_stream_reports_malformed_valid_json_signature(dirs):
    ledger, att = dirs
    record = _commit_days(ledger, n=1)[0]
    _write_jsonl(
        os.path.join(att, "s1.sig.jsonl"),
        {"record_hash": record["hash"]},
    )

    verdict = attest.verify_stream("s1", ledger, att)

    assert not verdict["valid"]
    assert any(
        "malformed signature record" in problem for problem in verdict["problems"]
    )


def test_verify_stream_reports_malformed_valid_json_anchor(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    _write_jsonl(os.path.join(att, "s1.ots.jsonl"), {"status": "pending"})

    verdict = attest.verify_stream("s1", ledger, att)

    assert not verdict["valid"]
    assert any(
        "malformed or unbound anchor" in problem for problem in verdict["problems"]
    )


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def test_keypair_created_once_and_private(dirs):
    _, att = dirs
    _, pub1 = attest.load_or_create_keypair(att)
    _, pub2 = attest.load_or_create_keypair(att)
    assert pub1 == pub2 and len(pub1) == 64
    mode = os.stat(os.path.join(att, "operator_key.pem")).st_mode & 0o777
    assert mode == 0o600


def test_keypair_accepts_owner_read_only_legacy_mode(dirs):
    _, att = dirs
    _, expected = attest.load_or_create_keypair(att)
    private_path = os.path.join(att, "operator_key.pem")
    os.chmod(private_path, 0o400)

    _, observed = attest.load_or_create_keypair(att)

    assert observed == expected
    assert os.stat(private_path).st_mode & 0o777 == 0o400


def test_existing_keypair_loader_is_non_mutating(dirs):
    _, att = dirs
    _, expected = attest.load_or_create_keypair(att)
    public_path = Path(att) / "operator_key.pub"
    public_before = public_path.stat()

    _, observed = attest.load_existing_keypair(att)

    public_after = public_path.stat()
    assert observed == expected
    assert (
        public_after.st_ino,
        public_after.st_mtime_ns,
        public_after.st_ctime_ns,
    ) == (
        public_before.st_ino,
        public_before.st_mtime_ns,
        public_before.st_ctime_ns,
    )


def test_existing_keypair_loader_does_not_create_missing_state(dirs):
    _, att = dirs
    attest_root = Path(att)

    with pytest.raises((FileNotFoundError, ValueError)):
        attest.load_existing_keypair(att)

    assert not attest_root.exists()


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o640, id="group-readable"),
        pytest.param(0o620, id="group-writable"),
        pytest.param(0o604, id="world-readable"),
        pytest.param(0o602, id="world-writable"),
        pytest.param(0o700, id="owner-executable"),
    ],
)
def test_keypair_rejects_unsafe_existing_private_key_mode(dirs, mode):
    _, att = dirs
    attest.load_or_create_keypair(att)
    private_path = os.path.join(att, "operator_key.pem")
    os.chmod(private_path, mode)

    with pytest.raises(ValueError, match="owner-only regular file"):
        attest.load_or_create_keypair(att)


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "directory"])
def test_keypair_rejects_unsafe_existing_private_key_object(dirs, tmp_path, mutation):
    _, att = dirs
    attest.load_or_create_keypair(att)
    private_path = os.path.join(att, "operator_key.pem")
    if mutation == "symlink":
        outside = tmp_path / "outside-private-key.pem"
        outside.write_bytes(Path(private_path).read_bytes())
        os.chmod(outside, 0o600)
        os.unlink(private_path)
        os.symlink(outside, private_path)
    elif mutation == "hardlink":
        os.link(private_path, tmp_path / "operator-key-alias.pem")
    else:
        os.unlink(private_path)
        os.mkdir(private_path, mode=0o700)

    with pytest.raises(ValueError, match="private key"):
        attest.load_or_create_keypair(att)


def test_keypair_rejects_wrong_private_key_owner(dirs, monkeypatch):
    _, att = dirs
    attest.load_or_create_keypair(att)
    monkeypatch.setattr(
        attest,
        "_expected_private_key_uid",
        lambda: os.geteuid() + 1,
    )

    with pytest.raises(ValueError, match="owner-only regular file"):
        attest.load_or_create_keypair(att)


def test_keypair_creation_replaces_public_symlink_without_touching_target(
    dirs, tmp_path
):
    _, att = dirs
    os.makedirs(att, exist_ok=True)
    outside = tmp_path / "outside-public-key"
    outside.write_text("do not overwrite\n")
    os.symlink(outside, os.path.join(att, "operator_key.pub"))

    _, public_key = attest.load_or_create_keypair(att)

    assert outside.read_text() == "do not overwrite\n"
    public_path = Path(att) / "operator_key.pub"
    assert not public_path.is_symlink()
    assert public_path.read_text() == public_key + "\n"


# ---------------------------------------------------------------------------
# Signing + verification
# ---------------------------------------------------------------------------
def test_sign_stream_is_idempotent_catch_up(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=3)
    r1 = attest.sign_stream("s1", ledger, att)
    assert r1["newly_signed"] == 3
    r2 = attest.sign_stream("s1", ledger, att)
    assert r2["newly_signed"] == 0 and r2["total_signed"] == 3
    attest.append_record("2026-07-20", {"v": 9}, stream="s1", ledger_dir=ledger)
    assert attest.sign_stream("s1", ledger, att)["newly_signed"] == 1


def test_verify_stream_happy_path(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=3)
    attest.sign_stream("s1", ledger, att)
    v = attest.verify_stream("s1", ledger, att)
    assert v["valid"] and v["n_records"] == 3 and v["n_signed_valid"] == 3
    assert v["problems"] == []


def test_verify_detects_payload_tamper(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=3)
    attest.sign_stream("s1", ledger, att)
    path = os.path.join(ledger, "s1.jsonl")
    lines = open(path).read().splitlines()
    rec = json.loads(lines[1])
    rec["payload"]["v"] = 999  # rewrite history
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    open(path, "w").write("\n".join(lines) + "\n")
    v = attest.verify_stream("s1", ledger, att)
    assert not v["valid"]
    assert any("does not recompute" in p or "chain broken" in p for p in v["problems"])


def test_verify_detects_full_chain_rewrite_via_signatures(dirs):
    """The attack the bare ledger cannot catch: rewrite the whole file from
    genesis with recomputed hashes. The chain then verifies — only the
    signatures give it away."""
    ledger, att = dirs
    _commit_days(ledger, n=2)
    attest.sign_stream("s1", ledger, att)
    os.remove(os.path.join(ledger, "s1.jsonl"))
    attest.append_record(
        "2026-07-10", {"v": 0, "note": "x"}, stream="s1", ledger_dir=ledger
    )
    attest.append_record(
        "2026-07-11", {"v": 1, "note": "REWRITTEN"}, stream="s1", ledger_dir=ledger
    )
    v = attest.verify_stream("s1", ledger, att)
    assert not v["valid"]
    assert any("not signed" in p for p in v["problems"])


def test_verify_detects_forged_signature(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    sig_path = os.path.join(att, "s1.sig.jsonl")
    s = json.loads(open(sig_path).read().strip())
    s["sig"] = "ab" * 64
    open(sig_path, "w").write(json.dumps(s) + "\n")
    v = attest.verify_stream("s1", ledger, att)
    assert not v["valid"]
    assert any("INVALID" in p for p in v["problems"])


def test_verify_rejects_an_unapproved_key_rotation(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    # rotate: new keypair in place, old signature remains
    os.remove(os.path.join(att, "operator_key.pem"))
    os.remove(os.path.join(att, "operator_key.pub"))
    attest.load_or_create_keypair(att)
    v = attest.verify_stream("s1", ledger, att)
    assert not v["valid"]
    assert any("current operator public key is not trusted" in p for p in v["problems"])


def test_verify_rejects_a_full_rewrite_signed_by_an_attacker_key(dirs):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ledger, att = dirs
    _commit_days(ledger, n=2)
    attest.sign_stream("s1", ledger, att)

    rewritten = []
    previous_hash = attest.GENESIS
    for index, day in enumerate(("2026-07-10", "2026-07-11")):
        record = {
            "day": day,
            "payload": {"v": 900 + index, "note": "attacker rewrite"},
            "prev_hash": previous_hash,
        }
        record["hash"] = attest.record_hash(record)
        rewritten.append(record)
        previous_hash = record["hash"]
    with open(os.path.join(ledger, "s1.jsonl"), "w") as stream:
        for record in rewritten:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )

    attacker = Ed25519PrivateKey.generate()
    attacker_public = attacker.public_key().public_bytes_raw().hex()
    with open(os.path.join(att, "s1.sig.jsonl"), "w") as stream:
        for record in rewritten:
            stream.write(
                json.dumps(
                    _signature_record(attacker, attacker_public, "s1", record),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )

    verdict = attest.verify_stream("s1", ledger, att)
    assert not verdict["valid"]
    assert verdict["n_signed_valid"] == 0
    assert any("signer key is not trusted" in p for p in verdict["problems"])


def test_verify_rejects_a_signature_orphaned_by_ledger_truncation(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=2)
    attest.sign_stream("s1", ledger, att)
    ledger_path = os.path.join(ledger, "s1.jsonl")
    first_line = open(ledger_path).read().splitlines()[0]
    open(ledger_path, "w").write(first_line + "\n")

    verdict = attest.verify_stream("s1", ledger, att)
    assert not verdict["valid"]
    assert verdict["n_signed_valid"] == 1
    assert any("no matching ledger record" in p for p in verdict["problems"])


def test_sign_refuses_broken_chain(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=2)
    path = os.path.join(ledger, "s1.jsonl")
    lines = open(path).read().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["v"] = 777
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    open(path, "w").write("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="refusing to sign"):
        attest.sign_stream("s1", ledger, att)


# ---------------------------------------------------------------------------
# OTS wire format helpers (byte-exact fakes)
# ---------------------------------------------------------------------------
def _varuint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _varbytes(b: bytes) -> bytes:
    return _varuint(len(b)) + b


def _pending_fragment(digest: bytes, nonce: bytes, uri: str) -> bytes:
    """append(nonce) -> sha256 -> PendingAttestation(uri), as a calendar returns."""
    return (
        bytes([attest._OTS_OP_APPEND])
        + _varbytes(nonce)
        + bytes([attest._OTS_OP_SHA256])
        + bytes([attest._OTS_ATTESTATION])
        + attest._OTS_TAG_PENDING
        + _varbytes(_varbytes(uri.encode()))
    )


def _pending_attestation(uri: str) -> bytes:
    return (
        bytes([attest._OTS_ATTESTATION])
        + attest._OTS_TAG_PENDING
        + _varbytes(_varbytes(uri.encode()))
    )


def _bitcoin_fragment(commitment: bytes, height: int) -> bytes:
    """prepend(x) -> sha256 -> BitcoinBlockHeader(height), a merkle-path shape."""
    return (
        bytes([attest._OTS_OP_PREPEND])
        + _varbytes(b"\x11\x22")
        + bytes([attest._OTS_OP_SHA256])
        + bytes([attest._OTS_ATTESTATION])
        + attest._OTS_TAG_BITCOIN
        + _varbytes(_varuint(height))
    )


def test_parse_ots_fragment_pending_commitment_math():
    digest = hashlib.sha256(b"record").digest()
    nonce = b"\x01\x02\x03\x04"
    frag = _pending_fragment(digest, nonce, "https://cal.example")
    atts = attest.parse_ots_fragment(digest, frag)
    assert len(atts) == 1 and atts[0]["kind"] == "pending"
    assert atts[0]["uri"] == "https://cal.example"
    assert atts[0]["commitment"] == hashlib.sha256(digest + nonce).hexdigest()


def test_parse_ots_fragment_bitcoin():
    c = hashlib.sha256(b"commitment").digest()
    atts = attest.parse_ots_fragment(c, _bitcoin_fragment(c, 903211))
    assert atts[0]["kind"] == "bitcoin" and atts[0]["height"] == 903211


def test_parse_ots_fragment_accepts_bounded_pending_continuation_start():
    pending_commitment = hashlib.sha256(b"commitment").digest() + b"calendar-tip"
    atts = attest.parse_ots_fragment(
        pending_commitment, _bitcoin_fragment(pending_commitment, 903211)
    )
    assert atts[0]["kind"] == "bitcoin" and atts[0]["height"] == 903211


@pytest.mark.parametrize("commitment", (b"", b"x" * (attest._MAX_OTS_OP_BYTES + 1)))
def test_parse_ots_fragment_bounds_starting_commitment(commitment):
    with pytest.raises(ValueError, match="starting commitment exceeds"):
        attest.parse_ots_fragment(
            commitment, _pending_attestation("https://cal.example")
        )


def test_parse_ots_fragment_rejects_ignored_trailing_bytes():
    commitment = hashlib.sha256(b"commitment").digest()
    fragment = _bitcoin_fragment(commitment, 903211) + b"\x00"
    with pytest.raises(ValueError, match="trailing"):
        attest.parse_ots_fragment(commitment, fragment)


def test_parse_ots_fragment_rejects_an_operation_without_a_child():
    commitment = hashlib.sha256(b"commitment").digest()
    with pytest.raises(ValueError, match="truncated OTS timestamp child"):
        attest.parse_ots_fragment(commitment, bytes([attest._OTS_OP_SHA256]))


@pytest.mark.parametrize("tag", (attest._OTS_OP_APPEND, attest._OTS_OP_PREPEND))
def test_parse_ots_fragment_enforces_binary_operation_bounds(tag):
    commitment = hashlib.sha256(b"commitment").digest()
    bitcoin = (
        bytes([attest._OTS_ATTESTATION])
        + attest._OTS_TAG_BITCOIN
        + _varbytes(_varuint(903211))
    )
    with pytest.raises(ValueError, match="shorter than the protocol bound"):
        attest.parse_ots_fragment(commitment, bytes([tag]) + _varbytes(b"") + bitcoin)
    with pytest.raises(ValueError, match="operation result exceeds"):
        attest.parse_ots_fragment(
            commitment,
            bytes([tag]) + _varbytes(b"x" * 4065) + bitcoin,
        )
    with pytest.raises(ValueError, match="varbytes exceeds"):
        attest.parse_ots_fragment(
            commitment,
            bytes([tag]) + _varbytes(b"x" * 4097) + bitcoin,
        )


def test_parse_ots_fragment_rejects_non_sha256_bitcoin_commitment():
    commitment = hashlib.sha256(b"commitment").digest()
    fragment = (
        bytes([attest._OTS_OP_SHA1])
        + bytes([attest._OTS_ATTESTATION])
        + attest._OTS_TAG_BITCOIN
        + _varbytes(_varuint(903211))
    )
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        attest.parse_ots_fragment(commitment, fragment)


def test_parse_ots_fragment_accepts_bounded_intermediate_pending_commitment():
    commitment = hashlib.sha256(b"commitment").digest()
    fragment = (
        bytes([attest._OTS_OP_APPEND])
        + _varbytes(b"calendar-branch")
        + _pending_attestation("https://cal.example")
    )
    parsed = attest.parse_ots_fragment(commitment, fragment)
    assert parsed == [
        {
            "kind": "pending",
            "uri": "https://cal.example",
            "commitment": (commitment + b"calendar-branch").hex(),
        }
    ]


@pytest.mark.parametrize(
    ("record_hash", "fragment_b64", "expected_commitment"),
    (
        (
            "7c149b92ca634211ff84f8c166b36d4fc33bc59c83dce33f018480be0a92c165",
            "8AgIpMZ0MMlIbwjwEGfq2GDHcQfhmrD9zW7NOzkI8SC9zOZYFSWwAq9igF/WahkY8ZGu2debsZBfU5sKgOU1kQjwICAhxpSLEbrqiCBDIyIK82YBLxvjjaRFLmrVtvhOiq5tCPEEalQ+dvAIV2j/mEdYB4wAg9/jDS75DI4uLWh0dHBzOi8vYWxpY2UuYnRjLmNhbGVuZGFyLm9wZW50aW1lc3RhbXBzLm9yZw==",
            "6a543e767d80cb2c9e7fde166cdab9509c37b3a654e97ba0f06de6e3ad38c42917f1bbe65768ff984758078c",
        ),
        (
            "849589eef603d6046fec8bf1b70b68dc85ba696c0a9b3ed686ab39eaf16d665b",
            "8AgDDTex4pqHbgjwIJr5YkusX2gnvzOC80NCvMlDfmYzvAb4pnwYm9W2jKAPCPAggtbsjgUZhIs3MSTB89xaBcMaNazLkojjvPr6lMAc8foI8BD46oqyotg4mVagyoxDc+W8CPEg55HqCs5K7PHeSrF+MO2pY00CI5ACJteujFhFN+5TJDII8SCqu7NTOgbGgImV88Dp3J25r1GSlEyQkOM/Dr1F5MRewAjxBGpUPbDwCNPAIyavJOmHAIPf4w0u+QyOLi1odHRwczovL2FsaWNlLmJ0Yy5jYWxlbmRhci5vcGVudGltZXN0YW1wcy5vcmc=",
            "6a543db0a9f06f68b57a494deeca4e1af82e0706fc6d92f27c2413bb2756d670eb3b480fd3c02326af24e987",
        ),
    ),
)
def test_parse_real_calendar_pending_fragments_with_long_commitments(
    record_hash, fragment_b64, expected_commitment
):
    """Regression fixtures copied byte-for-byte from the public OTS ledger."""
    parsed = attest.parse_ots_fragment(
        bytes.fromhex(record_hash), base64.b64decode(fragment_b64, validate=True)
    )
    assert parsed == [
        {
            "kind": "pending",
            "uri": "https://alice.btc.calendar.opentimestamps.org",
            "commitment": expected_commitment,
        }
    ]


def test_parse_real_calendar_bitcoin_continuation_from_long_commitment():
    """The public ledger's first completed continuation is a forked OTS tree."""
    pending_commitment = bytes.fromhex(
        "6a543e767d80cb2c9e7fde166cdab9509c37b3a654e97ba0f06de6e3ad38c429"
        "17f1bbe65768ff984758078c"
    )
    fragment = base64.b64decode(
        "CPEg/xjKBwz915KYATztbpJcl/Uk2sxAfi9DZnSW7hOgeKcI8CBS8vKClAMxdzzG"
        "cVOiCQM1lYgYJr1iiWoVAGLtu+PJlgjxIJJEHQMHN79DQTEULUuKc77Y5BeAmNJE"
        "cPWX6xzQmhK+CPAgH2vs0d7YDbuPL3zAhn6SQ+H4tOtwMc9h4EUQTXtnlokI8SD7"
        "FgxELciJImWureTH7WeOxCs4z/fg1UO7SYr2/DvU0AjxIIYazCqLsO5mJ4YQwq+1"
        "nvkx3zGD09aQVH+UejQeUBBKCPAgGoDnIbbSOFZxwi4DzQQk5lairf6xYwTnVj9S"
        "uDJy04QI8CDyMPrsUP6V3JpxfWHJKWO1pqBDaHbu9ka3s0p8MEmeVQjxIH1vTqpS"
        "qmftn8Voj4A8uLe6M+pSI8gKvlliVsPhKJM1CPAgEDEHNeFzFwP+6P8HJkBzkA6R"
        "rRkjwmQULUWpxXg5yqkI8SCxTKd86jqOV7EdQ4otUGZgywYts9cSaeMFVg8BhiuF"
        "qwjxWQEAAAABi4Q5r5VdeAr+wTAiYhG1hjlOtGWuUhUWis0UIGTndRUAAAAAAP7/"
        "//8CkFIAAAAAAAAWABTFbpfqGjlv/fKMYoIn3QUAQKDpwgAAAAAAAAAAImog8ARY"
        "nQ4ACAjwIMgi3NbyCfryKc7G4LzXZt6s7FKih+d5ShqQqZdedKZpCAjxIBSERPq/"
        "iZ9bopHzit/DwuhN7v+HolnAWF4hDeslfzLlCAjwIKAhEWVNF+UTiDPWZFdkjvK0"
        "KWVqAJAorpWWD/gi+Oj5CAjxIPnk8tON3+a7gV7qM3X+5vbD4/WyIDk9SvZN+YRk"
        "jnJpCAjwIIBvZ8MZwz0RFwfsz37ANx3ftO5xp5iZ4OjEE5RuqM4eCAjxIMLflunH"
        "ARIB36AEM24nXuR3s7p90axPKNW1jHLm5F1OCAjxIJg9Nb7dhxn7BnUuBvaDV+xX"
        "tPyQ6eNEsAQsm42hjl9CCAjwIMx9MGUnDvfAMO7s93mnLqVu9W64aIs2hWqFT0fX"
        "n2PpCAjxIP2MAJl4GSOhwb4SbTwC3txymet5t6k/4NGv8WyRDEA0CAjxIKeWgIan"
        "m4ZXT0XBIOKzmZxoHSnFi+XG2MKh5x3TvaJdCAjwIFwkIdMqqSrYgdNiniUPNZa2"
        "zEZz0rY7nvbDppyTZPpmCAjxIPcrRYGGFtZdbBvdFJnB1qYqf3gVlAAuL8J0RBwI"
        "DLgOCAjwIM+VMgf0RDEN/ivCGIWyoRW56lEB7PJFIyeZ8ahKGFA4CAgABYiWDXPX"
        "GQED2bo6",
        validate=True,
    )
    assert attest.parse_ots_fragment(pending_commitment, fragment) == [
        {
            "kind": "bitcoin",
            "height": 957785,
            "commitment": (
                "c55c5ef567b2ca8589d763c3e867f447bec1bfaea07909923056d5956415f9bd"
            ),
        }
    ]


def test_parse_ots_fragment_enforces_recursion_and_node_bounds(monkeypatch):
    commitment = hashlib.sha256(b"commitment").digest()
    bitcoin = (
        bytes([attest._OTS_ATTESTATION])
        + attest._OTS_TAG_BITCOIN
        + _varbytes(_varuint(903211))
    )
    accepted = (
        bytes([attest._OTS_OP_SHA256]) * (attest._MAX_OTS_RECURSION_DEPTH - 1) + bitcoin
    )
    assert attest.parse_ots_fragment(commitment, accepted)[0]["kind"] == "bitcoin"
    too_deep = (
        bytes([attest._OTS_OP_SHA256]) * attest._MAX_OTS_RECURSION_DEPTH + bitcoin
    )
    with pytest.raises(ValueError, match="recursion bound"):
        attest.parse_ots_fragment(commitment, too_deep)

    monkeypatch.setattr(attest, "_MAX_OTS_TIMESTAMP_NODES", 2)
    forked = (
        bytes([attest._OTS_FORK])
        + _pending_attestation("https://one.example")
        + _pending_attestation("https://two.example")
    )
    with pytest.raises(ValueError, match="node bound"):
        attest.parse_ots_fragment(commitment, forked)


def test_parse_ots_fragment_enforces_attestation_and_pending_uri_bounds():
    commitment = hashlib.sha256(b"commitment").digest()
    unknown = (
        bytes([attest._OTS_ATTESTATION])
        + b"unknown!"
        + _varbytes(b"x" * (attest._MAX_OTS_ATTESTATION_PAYLOAD_BYTES + 1))
    )
    with pytest.raises(ValueError, match="varbytes exceeds"):
        attest.parse_ots_fragment(commitment, unknown)

    invalid_uri = _pending_attestation("https://cal.example?redirect=other")
    with pytest.raises(ValueError, match="URI is invalid"):
        attest.parse_ots_fragment(commitment, invalid_uri)


class _FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


class _FakeCalendarClient:
    """Byte-exact fake of an OTS calendar: POST /digest returns a pending
    fragment; GET /timestamp/<commitment> returns a Bitcoin continuation."""

    def __init__(self):
        self.nonce = b"\xaa\xbb\xcc\xdd"
        self.posted = []
        self.heights = {}

    def post(self, url, content=b"", headers=None):
        self.posted.append((url, content))
        cal = url.rsplit("/digest", 1)[0]
        return _FakeResponse(200, _pending_fragment(content, self.nonce, cal))

    def get(self, url):
        commitment = bytes.fromhex(url.rsplit("/", 1)[1])
        height = self.heights.setdefault(commitment.hex(), 903000 + len(self.heights))
        return _FakeResponse(200, _bitcoin_fragment(commitment, height))

    def close(self):
        pass


def test_anchor_and_upgrade_flow(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=2)
    attest.sign_stream("s1", ledger, att)
    client = _FakeCalendarClient()
    r = attest.anchor_stream(
        "s1", ledger, att, client=client, calendars=("https://cal.example",)
    )
    assert r["submitted"] == 2
    # submitted digest is the raw record hash
    recs = attest.read_records("s1", ledger)
    assert client.posted[0][1] == bytes.fromhex(recs[0]["hash"])
    # idempotent
    r2 = attest.anchor_stream(
        "s1", ledger, att, client=client, calendars=("https://cal.example",)
    )
    assert r2["submitted"] == 0 and r2["already_anchored"] == 2
    # upgrade completes to a Bitcoin attestation
    up = attest.upgrade_anchors("s1", att, client=client)
    assert up["upgraded"] == 2 and up["still_pending"] == 0
    anchored = [a for a in attest.read_anchors("s1", att) if a["status"] == "anchored"]
    assert len(anchored) == 2 and anchored[0]["bitcoin_height"] == 903000
    v = attest.verify_stream("s1", ledger, att)
    assert v["valid"]
    assert v["n_anchors_bitcoin_attested"] == 2
    assert v["n_anchors_bitcoin_confirmed"] == 0
    assert v["bitcoin_confirmation_check"] == "not_requested"

    headers = {}
    for anchor in anchored:
        commitment = anchor["attestations"][0]["commitment"]
        header = b"\x00" * 36 + bytes.fromhex(commitment) + b"\x00" * 12
        block_hash = (
            hashlib.sha256(hashlib.sha256(header).digest()).digest()[::-1].hex()
        )
        headers[anchor["bitcoin_height"]] = (block_hash, header.hex())

    def bitcoin_rpc(method, params):
        if method == "getblockhash":
            return headers[params[0]][0]
        if method == "getblockheader":
            block_hash = params[0]
            return next(
                header
                for expected, header in headers.values()
                if expected == block_hash
            )
        raise AssertionError(method)

    checked = attest.verify_stream("s1", ledger, att, bitcoin_rpc=bitcoin_rpc)
    assert checked["valid"]
    assert checked["n_anchors_bitcoin_confirmed"] == 2
    assert checked["bitcoin_confirmation_check"] == "bitcoin_core_rpc"


def test_upgrade_only_dereferences_the_validated_calendar_branch(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)

    class _ForkedCalendar(_FakeCalendarClient):
        def __init__(self):
            super().__init__()
            self.requested = []

        def post(self, url, content=b"", headers=None):
            cal = url.rsplit("/digest", 1)[0]
            fragment = (
                bytes([attest._OTS_FORK])
                + _pending_attestation("https://foreign.example")
                + _pending_attestation(cal)
            )
            return _FakeResponse(200, fragment)

        def get(self, url):
            self.requested.append(url)
            assert url.startswith("https://cal.example/timestamp/")
            return super().get(url)

    calendar = _ForkedCalendar()
    anchored = attest.anchor_stream(
        "s1", ledger, att, client=calendar, calendars=("https://cal.example",)
    )
    assert anchored["submitted"] == 1
    upgraded = attest.upgrade_anchors("s1", att, client=calendar)
    assert upgraded["upgraded"] == 1
    assert len(calendar.requested) == 1
    assert attest.verify_stream("s1", ledger, att)["valid"]


def test_verify_rejects_a_forged_bitcoin_anchor_fragment(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    client = _FakeCalendarClient()
    attest.anchor_stream(
        "s1", ledger, att, client=client, calendars=("https://cal.example",)
    )
    pending = attest.read_anchors("s1", att)[0]
    forged = {
        **pending,
        "fragment_b64": base64.b64encode(b"\x00").decode(),
        "attestations": [],
        "bitcoin_height": 903000,
        "status": "anchored",
        "upgraded_at": "2026-07-12T00:00:00+00:00",
    }
    with open(os.path.join(att, "s1.ots.jsonl"), "a") as stream:
        stream.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")

    verdict = attest.verify_stream("s1", ledger, att)
    assert not verdict["valid"]
    assert verdict["n_anchors_bitcoin_attested"] == 0
    assert verdict["n_anchors_bitcoin_confirmed"] == 0
    assert any("Bitcoin continuation" in p for p in verdict["problems"])


def test_verify_rejects_a_reported_height_not_in_the_ots_fragment(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    client = _FakeCalendarClient()
    attest.anchor_stream(
        "s1", ledger, att, client=client, calendars=("https://cal.example",)
    )
    attest.upgrade_anchors("s1", att, client=client)
    anchor_path = os.path.join(att, "s1.ots.jsonl")
    lines = open(anchor_path).read().splitlines()
    anchored = json.loads(lines[-1])
    anchored["bitcoin_height"] += 1
    lines[-1] = json.dumps(anchored, sort_keys=True, separators=(",", ":"))
    open(anchor_path, "w").write("\n".join(lines) + "\n")

    verdict = attest.verify_stream("s1", ledger, att)
    assert not verdict["valid"]
    assert verdict["n_anchors_bitcoin_attested"] == 0
    assert any("reported height" in p for p in verdict["problems"])


def test_verify_rejects_boolean_bitcoin_height(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    client = _FakeCalendarClient()
    attest.anchor_stream(
        "s1", ledger, att, client=client, calendars=("https://cal.example",)
    )
    attest.upgrade_anchors("s1", att, client=client)
    anchor_path = os.path.join(att, "s1.ots.jsonl")
    lines = open(anchor_path).read().splitlines()
    anchored = json.loads(lines[-1])
    anchored["bitcoin_height"] = True
    lines[-1] = json.dumps(anchored, sort_keys=True, separators=(",", ":"))
    open(anchor_path, "w").write("\n".join(lines) + "\n")

    verdict = attest.verify_stream("s1", ledger, att)
    assert not verdict["valid"]
    assert verdict["n_anchors_bitcoin_attested"] == 0
    assert any("malformed or unbound anchor" in p for p in verdict["problems"])


def test_anchor_survives_dead_calendar(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)

    class _DeadThenLive(_FakeCalendarClient):
        def post(self, url, content=b"", headers=None):
            if "dead" in url:
                raise ConnectionError("boom")
            return super().post(url, content, headers)

    r = attest.anchor_stream(
        "s1",
        ledger,
        att,
        client=_DeadThenLive(),
        calendars=("https://dead.example", "https://cal.example"),
    )
    assert r["submitted"] == 1


def test_anchor_streams_and_caps_calendar_responses(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)

    class _StreamingResponse:
        status_code = 200
        headers = {}

        def __init__(self):
            self.chunks_read = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @property
        def content(self):
            raise AssertionError("streaming path must not materialize response.content")

        def iter_bytes(self):
            for chunk in (b"x" * 6000, b"y" * 5000, b"unreachable"):
                self.chunks_read += 1
                yield chunk

    class _StreamingClient:
        def __init__(self):
            self.response = _StreamingResponse()

        def stream(self, method, url, **kwargs):
            assert method == "POST"
            assert url == "https://cal.example/digest"
            assert len(kwargs["content"]) == 32
            return self.response

    client = _StreamingClient()
    result = attest.anchor_stream(
        "s1", ledger, att, client=client, calendars=("https://cal.example",)
    )
    assert result["submitted"] == 0 and result["unreachable"] == 1
    assert client.response.chunks_read == 2


# ---------------------------------------------------------------------------
# Run receipts + snapshot hook + scoreboard proof
# ---------------------------------------------------------------------------
def test_run_receipt_round_trip_and_tamper(dirs):
    _, att = dirs
    out = attest.attest_run("unit_test", {"engine": "m", "score": 1.5}, att)
    receipt = json.loads(open(out["path"]).read())
    assert attest.verify_run_receipt(receipt)["valid"]
    receipt["manifest"]["score"] = 9.9
    bad = attest.verify_run_receipt(receipt)
    assert not bad["valid"] and any("modified" in p for p in bad["problems"])


def test_run_receipt_rejects_a_valid_signature_from_an_untrusted_key(dirs):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _, att = dirs
    out = attest.attest_run("unit_test", {"score": 1.5}, att)
    receipt = json.loads(open(out["path"]).read())
    attacker = Ed25519PrivateKey.generate()
    receipt["public_key"] = attacker.public_key().public_bytes_raw().hex()
    receipt["sig"] = attacker.sign(
        attest._run_message(receipt["kind"], receipt["manifest_hash"])
    ).hex()

    verdict = attest.verify_run_receipt(receipt, att)
    assert not verdict["valid"]
    assert "signer key is not trusted" in verdict["problems"]


_PIT_RECORD = {
    "date": "2026-07-12",
    "value": 41.0,
    "regime": "EROSION",
    "coverage_pct": 96,
    "subscores": {"tails": 55.0, "kink": 30.0},
    "weights": {"tails": 0.17, "kink": 0.13},
    "tell": 12.0,
    "forecasts": {"p_ensemble": 0.11, "dispersion": 0.04, "views": {"ml": 0.1}},
    "book": None,
}


def test_attest_stress_reading_commits_signs_and_receipts(dirs):
    ledger, att = dirs
    out = attest.attest_stress_reading(
        "2026-07-12", _PIT_RECORD, ledger_dir=ledger, attest_dir=att
    )
    assert out["attested"] and out["ledger"]["committed"]
    assert out["signed"]["total_signed"] == 1
    assert out["receipt"]["manifest_hash"]
    recs = attest.read_records("stress_readings", ledger)
    assert len(recs) == 1
    p = recs[0]["payload"]
    assert p["regime"] == "EROSION" and p["value"] == 41.0
    assert p["forward_odds"]["p_ensemble"] == 0.11
    assert len(p["vintage"]["record_sha256"]) == 64
    assert attest.verify_stream("stress_readings", ledger, att)["valid"]
    # the same data-day re-run: ledger honestly refuses, signing stays idempotent
    out2 = attest.attest_stress_reading(
        "2026-07-12", _PIT_RECORD, ledger_dir=ledger, attest_dir=att
    )
    assert not out2["ledger"]["committed"]
    assert out2["signed"]["newly_signed"] == 0
    assert len(attest.read_records("stress_readings", ledger)) == 1


def test_attest_stress_reading_handles_numpy_values(dirs):
    ledger, att = dirs
    np = pytest.importorskip("numpy")
    record = {
        **_PIT_RECORD,
        "value": np.float64(41.0),
        "subscores": {"tails": np.float64(55.0)},
    }
    out = attest.attest_stress_reading(
        "2026-07-12", record, ledger_dir=ledger, attest_dir=att
    )
    assert out["ledger"]["committed"]
    assert attest.verify_stream("stress_readings", ledger, att)["valid"]


def test_record_pit_hook_is_gated_and_never_breaks_the_reading(
    dirs, tmp_path, monkeypatch
):
    """assemble._record_pit: no attestation unless SEICHE_ATTEST=1; with it,
    the day lands in the ledger signed; an attest fault never raises."""
    from seiche import assemble, notary, store

    ledger, att = dirs
    monkeypatch.setattr(notary, "DB_PATH", tmp_path / "notary.sqlite")
    monkeypatch.setattr(store, "save_blob", lambda key, payload: None)
    engines = {
        "composite": {
            "ok": True,
            "value": 41.0,
            "regime": "EROSION",
            "coverage_pct": 96,
            "subscores": {"tails": 55.0},
        }
    }
    deep = {"tell": {"tell": 12.0}, "stacker": {"ok": False}, "book": {}}

    # gate off (default): nothing written
    monkeypatch.delenv("SEICHE_ATTEST", raising=False)
    assemble._record_pit(engines, deep)
    assert attest.read_records("stress_readings", ledger) == []

    # gate on: committed and signed
    monkeypatch.setenv("SEICHE_ATTEST", "1")
    assemble._record_pit(engines, deep)
    recs = attest.read_records("stress_readings", ledger)
    assert len(recs) == 1 and recs[0]["payload"]["regime"] == "EROSION"
    assert attest.verify_stream("stress_readings", ledger, att)["valid"]

    # an attest fault is swallowed and logged, never raised
    def _boom(*a, **k):
        raise RuntimeError("attest exploded")

    monkeypatch.setattr(attest, "attest_stress_reading", _boom)
    assemble._record_pit(engines, deep)  # must not raise


def test_prove_scoreboard(dirs):
    ledger, att = dirs
    scoreboard = {
        "ok": True,
        "sample": {"start": "2018-01-01", "end": "2026-07-01", "n_events": 14},
        "event_capture": {"recall": 0.79, "precision_runs": 0.61},
        "episodes": [{"date": "2019-09-17", "episode": "repo spike"}],
        "caveats": ["small event count; CIs are wide"],
    }
    out = attest.prove_scoreboard(
        scoreboard, source_key="deep:test:2026-07-12", attest_dir=att, ledger_dir=ledger
    )
    assert out["n_sections"] == 5 and out["ledger"]["committed"]
    # same content -> same root; ledger refuses a same-day duplicate, honestly
    out2 = attest.prove_scoreboard(
        scoreboard, source_key="deep:test:2026-07-12", attest_dir=att, ledger_dir=ledger
    )
    assert out2["root"] == out["root"] and not out2["ledger"]["committed"]
    v = attest.verify_stream("proof_scoreboard", ledger, att)
    assert v["valid"] and v["n_records"] == 1
    # changed scoreboard -> different root
    out3 = attest.prove_scoreboard(
        {**scoreboard, "caveats": ["polished"]}, attest_dir=att, ledger_dir=ledger
    )
    assert out3["root"] != out["root"]


def test_prove_scoreboard_anchor_flow(dirs):
    ledger, att = dirs
    client = _FakeCalendarClient()
    out = attest.prove_scoreboard(
        {"ok": True, "sample": {"n_events": 14}},
        attest_dir=att,
        ledger_dir=ledger,
        anchor=True,
        client=client,
    )
    assert out["anchoring"]["submitted"] == 1
    up = attest.upgrade_anchors("proof_scoreboard", att, client=client)
    assert up["upgraded"] == 1


# ---------------------------------------------------------------------------
# Public endpoints: commitments only, never payloads
# ---------------------------------------------------------------------------
@pytest.fixture
def client(dirs):
    from fastapi.testclient import TestClient

    from seiche import api

    return TestClient(api.app)


def test_endpoint_pubkey(client, dirs):
    _, att = dirs
    attest.load_or_create_keypair(att)
    res = client.get("/api/attest/pubkey")
    assert res.status_code == 200
    body = res.json()
    assert len(body["public_key"]) == 64 and body["algo"] == "ed25519"
    assert body["domain"] == "seiche-pit-v1"
    assert body["operator_key_trusted"]
    assert body["public_key"] in body["trusted_public_keys"]


def test_endpoint_stream_serves_commitments_without_payloads(client, dirs):
    ledger, att = dirs
    _commit_days(ledger, stream="s1", n=2)
    attest.sign_stream("s1", ledger, att)
    res = client.get("/api/attest/stream/s1")
    assert res.status_code == 200
    body = res.json()
    assert body["verification"]["valid"] and len(body["days"]) == 2
    assert body["days"][0]["signature"]["sig"]
    assert body["days"][0]["anchor_evidence"] == []
    assert "payload" not in json.dumps(body)  # commitments only, never content


def test_endpoint_stream_serves_both_validated_anchor_fragments(client, dirs):
    ledger, att = dirs
    _commit_days(ledger, stream="s1", n=1)
    attest.sign_stream("s1", ledger, att)
    calendar = _FakeCalendarClient()
    attest.anchor_stream(
        "s1", ledger, att, client=calendar, calendars=("https://cal.example",)
    )
    attest.upgrade_anchors("s1", att, client=calendar)

    response = client.get("/api/attest/stream/s1")
    assert response.status_code == 200
    body = response.json()
    evidence = body["days"][0]["anchor_evidence"]
    assert [item["status"] for item in evidence] == ["pending", "anchored"]
    assert all(item["fragment_b64"] and item["attestations"] for item in evidence)
    assert evidence[0]["record_hash"] == body["days"][0]["record_hash"]
    assert evidence[1]["bitcoin_height"] == 903000
    assert body["days"][0]["anchor"]["status"] == "anchored"
    assert "payload" not in json.dumps(body)


def test_endpoint_stream_refuses_to_serve_malformed_anchor_evidence(client, dirs):
    ledger, att = dirs
    _commit_days(ledger, stream="s1", n=1)
    attest.sign_stream("s1", ledger, att)
    _write_jsonl(os.path.join(att, "s1.ots.jsonl"), {"status": "pending"})

    response = client.get("/api/attest/stream/s1")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "attestation record is temporarily unavailable"
    }


def test_endpoint_unknown_stream_404(client, dirs):
    assert client.get("/api/attest/stream/nope").status_code == 404
    assert client.get("/api/attest/verify/nope").status_code == 404
    assert client.get("/api/attest/stream/..evil%2F").status_code in (404, 422)


def test_endpoint_rejects_unbounded_history_request(client, dirs):
    assert client.get("/api/attest/stream/s1?n=0").status_code == 422
    assert client.get("/api/attest/stream/s1?n=1001").status_code == 422


@pytest.mark.parametrize(
    "endpoint",
    ("/api/attest/stream/s1", "/api/attest/verify/s1"),
)
def test_endpoint_sanitizes_attestation_storage_failures(client, monkeypatch, endpoint):
    secret = "/Users/operator/private-ledger.jsonl?token=do-not-leak"

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(attest, "read_records", fail_closed)
    response = client.get(endpoint)
    assert response.status_code == 503
    assert response.json() == {
        "detail": "attestation record is temporarily unavailable"
    }
    assert secret not in response.text


def test_endpoint_verify_reports_tamper(client, dirs):
    ledger, att = dirs
    _commit_days(ledger, stream="s1", n=2)
    attest.sign_stream("s1", ledger, att)
    path = os.path.join(ledger, "s1.jsonl")
    lines = open(path).read().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["v"] = 42
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    open(path, "w").write("\n".join(lines) + "\n")
    res = client.get("/api/attest/verify/s1")
    assert res.status_code == 200 and res.json()["valid"] is False
