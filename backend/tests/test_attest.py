"""The attest layer: signatures + OTS anchoring over the as-published record.

Contract under test: the PIT ledger is an append-only hash chain (one record
per day per stream) that refuses tampered history; signing is idempotent
catch-up over committed records and refuses broken chains; verification
detects payload tampering, missing and forged signatures, and key
substitution; OTS anchoring submits the raw record hash to a calendar, parses
the returned fragment to the pending commitment, and upgrades to a Bitcoin
attestation later; the snapshot hook is env-gated and never breaks a reading;
the scoreboard proof and the public endpoints serve commitments and verdicts
but never payloads. All network is faked — the wire format in the fakes is
byte-exact OpenTimestamps serialization, so the parser is tested against the
real format, offline.
"""

import hashlib
import json
import os

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
        recs.append(attest.append_record(f"2026-07-{10 + i:02d}", {"v": i, "note": "x"},
                                         stream=stream, ledger_dir=ledger_dir))
    return recs


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


def test_ledger_rejects_bad_stream_names(dirs):
    ledger, _ = dirs
    with pytest.raises(ValueError, match="invalid stream name"):
        attest.append_record("2026-07-10", {}, stream="../evil", ledger_dir=ledger)


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
    attest.append_record("2026-07-10", {"v": 0, "note": "x"}, stream="s1", ledger_dir=ledger)
    attest.append_record("2026-07-11", {"v": 1, "note": "REWRITTEN"}, stream="s1",
                         ledger_dir=ledger)
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


def test_verify_flags_non_current_key_but_stays_valid(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)
    attest.sign_stream("s1", ledger, att)
    # rotate: new keypair in place, old signature remains
    os.remove(os.path.join(att, "operator_key.pem"))
    os.remove(os.path.join(att, "operator_key.pub"))
    attest.load_or_create_keypair(att)
    v = attest.verify_stream("s1", ledger, att)
    assert v["valid"]  # rotation is a warning, not a failure
    assert any("non-current key" in p for p in v["problems"])


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
    return (bytes([attest._OTS_OP_APPEND]) + _varbytes(nonce)
            + bytes([attest._OTS_OP_SHA256])
            + bytes([attest._OTS_ATTESTATION]) + attest._OTS_TAG_PENDING
            + _varbytes(_varbytes(uri.encode())))


def _bitcoin_fragment(commitment: bytes, height: int) -> bytes:
    """prepend(x) -> sha256 -> BitcoinBlockHeader(height), a merkle-path shape."""
    return (bytes([attest._OTS_OP_PREPEND]) + _varbytes(b"\x11\x22")
            + bytes([attest._OTS_OP_SHA256])
            + bytes([attest._OTS_ATTESTATION]) + attest._OTS_TAG_BITCOIN
            + _varbytes(_varuint(height)))


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

    def post(self, url, content=b"", headers=None):
        self.posted.append((url, content))
        cal = url.rsplit("/digest", 1)[0]
        return _FakeResponse(200, _pending_fragment(content, self.nonce, cal))

    def get(self, url):
        commitment = bytes.fromhex(url.rsplit("/", 1)[1])
        return _FakeResponse(200, _bitcoin_fragment(commitment, 903000))

    def close(self):
        pass


def test_anchor_and_upgrade_flow(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=2)
    attest.sign_stream("s1", ledger, att)
    client = _FakeCalendarClient()
    r = attest.anchor_stream("s1", ledger, att, client=client,
                             calendars=("https://cal.example",))
    assert r["submitted"] == 2
    # submitted digest is the raw record hash
    recs = attest.read_records("s1", ledger)
    assert client.posted[0][1] == bytes.fromhex(recs[0]["hash"])
    # idempotent
    r2 = attest.anchor_stream("s1", ledger, att, client=client,
                              calendars=("https://cal.example",))
    assert r2["submitted"] == 0 and r2["already_anchored"] == 2
    # upgrade completes to a Bitcoin attestation
    up = attest.upgrade_anchors("s1", att, client=client)
    assert up["upgraded"] == 2 and up["still_pending"] == 0
    anchored = [a for a in attest.read_anchors("s1", att) if a["status"] == "anchored"]
    assert len(anchored) == 2 and anchored[0]["bitcoin_height"] == 903000
    v = attest.verify_stream("s1", ledger, att)
    assert v["valid"] and v["n_anchors_bitcoin_confirmed"] == 2


def test_anchor_survives_dead_calendar(dirs):
    ledger, att = dirs
    _commit_days(ledger, n=1)

    class _DeadThenLive(_FakeCalendarClient):
        def post(self, url, content=b"", headers=None):
            if "dead" in url:
                raise ConnectionError("boom")
            return super().post(url, content, headers)

    r = attest.anchor_stream("s1", ledger, att, client=_DeadThenLive(),
                             calendars=("https://dead.example", "https://cal.example"))
    assert r["submitted"] == 1


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
    out = attest.attest_stress_reading("2026-07-12", _PIT_RECORD,
                                       ledger_dir=ledger, attest_dir=att)
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
    out2 = attest.attest_stress_reading("2026-07-12", _PIT_RECORD,
                                        ledger_dir=ledger, attest_dir=att)
    assert not out2["ledger"]["committed"]
    assert out2["signed"]["newly_signed"] == 0
    assert len(attest.read_records("stress_readings", ledger)) == 1


def test_attest_stress_reading_handles_numpy_values(dirs):
    ledger, att = dirs
    np = pytest.importorskip("numpy")
    record = {**_PIT_RECORD, "value": np.float64(41.0),
              "subscores": {"tails": np.float64(55.0)}}
    out = attest.attest_stress_reading("2026-07-12", record,
                                       ledger_dir=ledger, attest_dir=att)
    assert out["ledger"]["committed"]
    assert attest.verify_stream("stress_readings", ledger, att)["valid"]


def test_record_pit_hook_is_gated_and_never_breaks_the_reading(dirs, tmp_path, monkeypatch):
    """assemble._record_pit: no attestation unless SEICHE_ATTEST=1; with it,
    the day lands in the ledger signed; an attest fault never raises."""
    from seiche import assemble, notary, store

    ledger, att = dirs
    monkeypatch.setattr(notary, "DB_PATH", tmp_path / "notary.sqlite")
    monkeypatch.setattr(store, "save_blob", lambda key, payload: None)
    engines = {"composite": {"ok": True, "value": 41.0, "regime": "EROSION",
                             "coverage_pct": 96, "subscores": {"tails": 55.0}}}
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
    assemble._record_pit(engines, deep)   # must not raise


def test_prove_scoreboard(dirs):
    ledger, att = dirs
    scoreboard = {
        "ok": True,
        "sample": {"start": "2018-01-01", "end": "2026-07-01", "n_events": 14},
        "event_capture": {"recall": 0.79, "precision_runs": 0.61},
        "episodes": [{"date": "2019-09-17", "episode": "repo spike"}],
        "caveats": ["small event count; CIs are wide"],
    }
    out = attest.prove_scoreboard(scoreboard, source_key="deep:test:2026-07-12",
                                  attest_dir=att, ledger_dir=ledger)
    assert out["n_sections"] == 5 and out["ledger"]["committed"]
    # same content -> same root; ledger refuses a same-day duplicate, honestly
    out2 = attest.prove_scoreboard(scoreboard, source_key="deep:test:2026-07-12",
                                   attest_dir=att, ledger_dir=ledger)
    assert out2["root"] == out["root"] and not out2["ledger"]["committed"]
    v = attest.verify_stream("proof_scoreboard", ledger, att)
    assert v["valid"] and v["n_records"] == 1
    # changed scoreboard -> different root
    out3 = attest.prove_scoreboard({**scoreboard, "caveats": ["polished"]},
                                   attest_dir=att, ledger_dir=ledger)
    assert out3["root"] != out["root"]


def test_prove_scoreboard_anchor_flow(dirs):
    ledger, att = dirs
    client = _FakeCalendarClient()
    out = attest.prove_scoreboard({"ok": True, "sample": {"n_events": 14}},
                                  attest_dir=att, ledger_dir=ledger,
                                  anchor=True, client=client)
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
    res = client.get("/api/attest/pubkey")
    assert res.status_code == 200
    body = res.json()
    assert len(body["public_key"]) == 64 and body["algo"] == "ed25519"
    assert body["domain"] == "seiche-pit-v1"


def test_endpoint_stream_serves_commitments_without_payloads(client, dirs):
    ledger, att = dirs
    _commit_days(ledger, stream="s1", n=2)
    attest.sign_stream("s1", ledger, att)
    res = client.get("/api/attest/stream/s1")
    assert res.status_code == 200
    body = res.json()
    assert body["verification"]["valid"] and len(body["days"]) == 2
    assert body["days"][0]["signature"]["sig"]
    assert "payload" not in json.dumps(body)  # commitments only, never content


def test_endpoint_unknown_stream_404(client, dirs):
    assert client.get("/api/attest/stream/nope").status_code == 404
    assert client.get("/api/attest/verify/nope").status_code == 404
    assert client.get("/api/attest/stream/..evil%2F").status_code in (404, 422)


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
