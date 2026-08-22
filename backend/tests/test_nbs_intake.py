from __future__ import annotations

import ast
import hashlib
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from seiche import nbs_intake as nbs

RAW_MARKER = "RAW-EVIDENCE-MUST-NOT-BE-PUBLIC"
CPI = "CN.NBS.CPI_INDEX"
INDUSTRIAL = "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY"

# Literal byte fixture for the reviewed v1 grammar.  The month token remains
# intentionally opaque because no owner-supplied browser export was retained.
LITERAL_CSV_GRAMMAR_FIXTURE = (
    b"\xef\xbb\xbfNBS browser export\r\n"
    b"Indicators\t,NBS month token 7\t\r\n"
    b"Consumer Price Index (The same month last year=100)\t,100.5\t\r\n"
    b"RAW-EVIDENCE-MUST-NOT-BE-PUBLIC\t,restricted\t\r\n"
    b"Data Sources: National Bureau of Statistics\t,\r\n"
)


def _canonical(record: object) -> bytes:
    return json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _raw(
    *,
    series_id: str = CPI,
    headers: tuple[str, ...] = ("July 2026",),
    values: tuple[str, ...] = ("100.5",),
    label: str | None = None,
    footer: str = "Data Sources: National Bureau of Statistics",
    duplicate_label: bool = False,
    note_rows: tuple[str, ...] = (),
    post_footer_row: str | None = None,
) -> bytes:
    source_label = label or nbs.NBS_SERIES_BINDINGS[series_id].label
    rows = [
        "NBS browser export",
        "\t,".join(("Indicators", *headers)) + "\t",
        "\t,".join((source_label, *values)) + "\t",
        "\t,".join((RAW_MARKER, *("restricted" for _ in headers))) + "\t",
    ]
    if duplicate_label:
        rows.append("\t,".join((source_label, *values)))
    rows.extend(note_rows)
    if footer:
        rows.append(footer + "\t,")
    if post_footer_row is not None:
        rows.append(post_footer_row)
    return b"\xef\xbb\xbf" + "\r\n".join(rows).encode("utf-8") + b"\r\n"


def _trust_dir(tmp_path: Path, public_key: str) -> Path:
    path = tmp_path / f"trust-{public_key[:8]}"
    path.mkdir()
    (path / "trusted_operator_keys").write_text(public_key + "\n")
    return path


def _manifest(
    *,
    export_id: str,
    predecessor: str | None,
    predecessor_manifest_sha256: str | None,
    raw: bytes,
    raw_filename: str,
    series_id: str = CPI,
    records: list[dict[str, str]] | None = None,
    knowledge_time: str = "2026-08-22T10:00:00Z",
    month_headers: list[dict[str, str]] | None = None,
    release_url: str | None = None,
    commitment_nonce: str | None = None,
) -> dict[str, object]:
    selected_records = records or [
        {"series_id": series_id, "period": "2026-07", "value": "100.5"}
    ]
    selected_headers = month_headers or [
        {"period": "2026-07", "raw_header": "July 2026"}
    ]
    source = nbs.NBS_SERIES_BINDINGS[series_id].manifest_dict()
    if release_url is not None:
        source["release_url"] = release_url
    return {
        "schema": nbs.NBS_EXPORT_SCHEMA,
        "dataset": nbs.NBS_DATASET,
        "export_id": export_id,
        "predecessor_export_id": predecessor,
        "predecessor_manifest_sha256": predecessor_manifest_sha256,
        "commitment_nonce": (
            secrets.token_hex(32) if commitment_nonce is None else commitment_nonce
        ),
        "publisher": nbs.NBS_PUBLISHER,
        "knowledge_time": knowledge_time,
        "source_url": nbs.NBS_BROWSER_SOURCE_URL,
        "sources": [source],
        "records": selected_records,
        "raw_evidence": {
            "filename": raw_filename,
            "format": nbs.NBS_RAW_FORMAT,
            "media_type": "text/csv",
            "month_headers": selected_headers,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
        "publication_policy": dict(nbs.NBS_PUBLICATION_POLICY),
    }


def _bundle(
    tmp_path: Path,
    private_key: Ed25519PrivateKey,
    *,
    tag: str,
    export_id: str = "nbs-2026-07-r1",
    predecessor: str | None = None,
    predecessor_manifest_sha256: str | None = None,
    series_id: str = CPI,
    raw: bytes | None = None,
    records: list[dict[str, str]] | None = None,
    knowledge_time: str = "2026-08-22T10:00:00Z",
    signed_at: str = "2026-08-22T10:05:00Z",
    month_headers: list[dict[str, str]] | None = None,
    release_url: str | None = None,
    commitment_nonce: str | None = None,
) -> tuple[Path, Path, Path, dict[str, object]]:
    directory = tmp_path / f"input-{tag}"
    directory.mkdir()
    raw_filename = f"{tag}.csv"
    raw_bytes = raw or _raw(series_id=series_id)
    manifest = _manifest(
        export_id=export_id,
        predecessor=predecessor,
        predecessor_manifest_sha256=predecessor_manifest_sha256,
        raw=raw_bytes,
        raw_filename=raw_filename,
        series_id=series_id,
        records=records,
        knowledge_time=knowledge_time,
        month_headers=month_headers,
        release_url=release_url,
        commitment_nonce=commitment_nonce,
    )
    public_key = private_key.public_key().public_bytes_raw().hex()
    claim = nbs.build_signature_claim(
        manifest,
        signed_at=signed_at,
        signer_key_id=public_key,
    )
    signature = {
        **claim,
        "signature": private_key.sign(nbs.encode_signature_claim(claim)).hex(),
    }
    manifest_path = directory / "manifest.json"
    signature_path = directory / "signature.json"
    raw_path = directory / raw_filename
    manifest_path.write_bytes(_canonical(manifest))
    signature_path.write_bytes(_canonical(signature))
    raw_path.write_bytes(raw_bytes)
    return manifest_path, signature_path, raw_path, manifest


def _manifest_sha256(
    bundle: tuple[Path, Path, Path, dict[str, object]],
) -> str:
    return hashlib.sha256(_canonical(bundle[3])).hexdigest()


@pytest.fixture
def signer(tmp_path: Path) -> tuple[Ed25519PrivateKey, str, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw().hex()
    return private_key, public_key, _trust_dir(tmp_path, public_key)


@pytest.fixture(autouse=True)
def fixed_intake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep signed revision fixtures deterministic as wall-clock time advances."""

    monkeypatch.setattr(
        nbs,
        "_utc_now",
        lambda: datetime(2026, 12, 31, tzinfo=UTC),
    )


def _ingest(
    store: nbs.NBSIntakeStore,
    bundle: tuple[Path, Path, Path, dict[str, object]],
) -> nbs.NBSMacroContext:
    manifest_path, signature_path, raw_path, _manifest_record = bundle
    return store.ingest(manifest_path, signature_path, raw_path)


def test_manifest_file_claim_helper_preserves_intake_canonicality(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, public_key, _trust = signer
    bundle = _bundle(tmp_path, private_key, tag="claim-helper")

    claim = nbs.build_signature_claim_from_manifest_file(
        bundle[0],
        signed_at="2026-08-22T10:05:00Z",
        signer_key_id=public_key,
    )

    assert claim == nbs.build_signature_claim(
        bundle[3],
        signed_at="2026-08-22T10:05:00Z",
        signer_key_id=public_key,
    )
    assert nbs.encode_signature_claim(claim) == _canonical(claim)


def test_manifest_file_claim_helper_rejects_noncanonical_bytes(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, public_key, _trust = signer
    bundle = _bundle(tmp_path, private_key, tag="claim-noncanonical")
    bundle[0].write_text(json.dumps(bundle[3], indent=2), encoding="utf-8")

    with pytest.raises(nbs.NBSIntegrityError, match="must use canonical JSON bytes"):
        nbs.build_signature_claim_from_manifest_file(
            bundle[0],
            signed_at="2026-08-22T10:05:00Z",
            signer_key_id=public_key,
        )


def test_ingest_persists_restricted_evidence_and_metadata_only_projection(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(tmp_path, private_key, tag="first")
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))

    context = _ingest(store, bundle)

    assert context.available is True
    public = context.to_dict()
    assert public["evidence_status"] == "restricted"
    assert public["values_published"] is False
    assert public["publication_policy"] == dict(nbs.NBS_PUBLICATION_POLICY)
    assert public["series"][0]["semantic_contract"] == {
        "value_kind": "index_level",
        "canonical_unit": "index_points",
        "comparison_base": "上年同月=100",
        "transform": "raw_minus_100",
        "threshold": None,
    }
    assert public["series"][0]["source_unit_label_exact"] == "%"
    assert public["series"][0]["source_unit_semantically_authoritative"] is False
    assert public["series"][0]["dp_name"] == "本期"
    assert public["series"][0]["value_publication"] == (
        "withheld_pending_rights_review"
    )
    with pytest.raises(TypeError):
        context.record["values_published"] = True
    with pytest.raises(TypeError):
        context.record["series"][0]["label"] = "drifted"
    public_path = root / "public" / "revisions" / "nbs-2026-07-r1.json"
    public_bytes = public_path.read_bytes()
    assert RAW_MARKER.encode() not in public_bytes
    assert b"100.5" not in public_bytes
    assert b"first.csv" not in public_bytes
    assert b"latest_value" not in public_bytes
    assert public["provenance"] == {
        "manifest_sha256": public["attestation"]["manifest_sha256"],
        "owner_attestation": "ed25519",
    }
    assert "raw_sha256" not in public["attestation"]
    assert "raw_size_bytes" not in public["provenance"]
    assert bundle[3]["commitment_nonce"].encode() not in public_bytes

    raw_digest = bundle[3]["raw_evidence"]["sha256"]
    assert raw_digest.encode() not in public_bytes
    assert "raw_sha256" not in json.loads(bundle[1].read_text())
    raw_path = root / "restricted" / "objects" / "sha256" / raw_digest[:2] / raw_digest
    assert raw_path.read_bytes() == bundle[2].read_bytes()
    assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600
    export_dir = root / "restricted" / "exports" / "nbs-2026-07-r1"
    assert stat.S_IMODE(export_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((export_dir / "manifest.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((export_dir / "signature.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(public_path.stat().st_mode) == 0o640
    restricted_head = root / "restricted" / "exports" / ".head.json"
    public_head = root / "public" / "revisions" / ".head.json"
    assert stat.S_IMODE(restricted_head.stat().st_mode) == 0o600
    assert stat.S_IMODE(public_head.stat().st_mode) == 0o640
    assert public_head.stat().st_gid == (root / "public" / "revisions").stat().st_gid
    assert json.loads(public_head.read_text()) == json.loads(
        restricted_head.read_text()
    )
    assert json.loads(public_head.read_text())["sequence"] == 1
    assert stat.S_IMODE((root / "public" / "revisions").stat().st_mode) == 0o2750
    assert public_path.stat().st_gid == (root / "public" / "revisions").stat().st_gid
    assert public_path.stat().st_nlink == 1
    assert raw_path.stat().st_nlink == 1
    loaded = store.load_public_context()
    assert isinstance(loaded, nbs.NBSMacroContext)
    assert loaded.to_dict() == public


def test_public_catalog_is_pure_metadata_and_hard_disables_values() -> None:
    catalog = nbs.nbs_public_catalog()

    assert catalog["available"] is False
    assert catalog["evidence_status"] == "unavailable"
    assert catalog["values_published"] is False
    assert catalog["reason_code"] == "signed_owner_export_required"
    assert nbs.NBS_PUBLIC_VALUE_RELEASE_APPROVALS == frozenset()
    assert {row["series_id"] for row in catalog["series"]} == set(
        nbs.NBS_SERIES_BINDINGS
    )
    assert all(
        row["value_publication"] == "withheld_pending_rights_review"
        for row in catalog["series"]
    )
    assert all("latest_value" not in row for row in catalog["series"])
    by_id = {row["series_id"]: row for row in catalog["series"]}
    assert by_id["CN.NBS.PPI_INDEX"]["source_unit_label_exact"] == "无"
    assert by_id["CN.NBS.PPI_INDEX"]["source_unit_semantically_authoritative"] is False
    assert all(
        by_id[series_id]["dp_name"] == "本期"
        for series_id in (CPI, "CN.NBS.PPI_INDEX", "CN.NBS.MANUFACTURING_PMI")
    )
    assert {key: row["catalog_label"] for key, row in by_id.items()} == {
        CPI: (
            "Consumer Price Indices by Category (The same month last year=100) (2026-)"
        ),
        "CN.NBS.PPI_INDEX": (
            "Producer Price Indices for Industrial Products by Sector "
            "(The same month last year=100) (2026 to present)"
        ),
        "CN.NBS.MANUFACTURING_PMI": "Manufacturing Purchasing Managers' Index",
        INDUSTRIAL: (
            "Growth Rate of Value-added of Industrial Enterprises above Designated Size"
        ),
    }
    assert nbs.MAX_RAW_BYTES == 32 * 1024 * 1024
    assert nbs.MAX_MANIFEST_BYTES == 256 * 1024
    assert nbs.MAX_SIGNATURE_BYTES == 4 * 1024


def test_manifest_nonce_is_unique_lowercase_hex_and_stays_restricted(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, _trust = signer
    first = _bundle(tmp_path, private_key, tag="nonce-first")
    second = _bundle(tmp_path, private_key, tag="nonce-second")
    nonces = (first[3]["commitment_nonce"], second[3]["commitment_nonce"])

    assert nonces[0] != nonces[1]
    for nonce in nonces:
        assert isinstance(nonce, str)
        assert len(nonce) == 64
        assert set(nonce) <= set("0123456789abcdef")
    assert "raw_sha256" not in nbs.build_signature_claim(
        first[3],
        signed_at="2026-08-22T10:05:00Z",
        signer_key_id=private_key.public_key().public_bytes_raw().hex(),
    )


@pytest.mark.parametrize("nonce", ["a" * 63, "A" * 64, "g" * 64])
def test_manifest_rejects_non_32_byte_lowercase_hex_nonce(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    nonce: str,
) -> None:
    private_key, _public_key, _trust = signer

    with pytest.raises(nbs.NBSIntegrityError, match="commitment_nonce"):
        _bundle(
            tmp_path,
            private_key,
            tag=hashlib.sha256(nonce.encode()).hexdigest()[:8],
            commitment_nonce=nonce,
        )


def test_manifest_requires_commitment_nonce(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(tmp_path, private_key, tag="missing-nonce")
    manifest = json.loads(bundle[0].read_text())
    manifest.pop("commitment_nonce")
    bundle[0].write_bytes(_canonical(manifest))

    with pytest.raises(nbs.NBSIntegrityError, match="missing=.*commitment_nonce"):
        _ingest(nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)), bundle)


def test_v1_public_value_gate_cannot_be_relaxed_by_process_state(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, _public_key, trust = signer
    monkeypatch.setattr(nbs, "NBS_PUBLIC_VALUE_RELEASE_APPROVALS", frozenset({CPI}))
    catalog = nbs.nbs_public_catalog()
    assert catalog["values_published"] is False
    assert "latest_value" not in catalog["series"][0]

    context = _ingest(
        nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)),
        _bundle(tmp_path, private_key, tag="hard-false"),
    ).to_dict()
    assert context["values_published"] is False
    assert "latest_value" not in context["series"][0]


def test_exact_retry_is_a_noop_but_same_id_different_content_conflicts(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    first = _bundle(tmp_path, private_key, tag="retry-a")
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))

    original = _ingest(store, first).to_dict()
    retried = _ingest(store, first).to_dict()
    assert retried == original
    assert (
        len(
            tuple(
                path
                for path in (tmp_path / "store/public/revisions").glob("*.json")
                if not path.name.startswith(".")
            )
        )
        == 1
    )

    conflicting_raw = _raw(values=("100.6",))
    conflict = _bundle(
        tmp_path,
        private_key,
        tag="retry-b",
        raw=conflicting_raw,
        records=[{"series_id": CPI, "period": "2026-07", "value": "100.6"}],
        knowledge_time="2026-08-22T11:00:00Z",
        signed_at="2026-08-22T11:05:00Z",
    )
    with pytest.raises(nbs.NBSConflictError, match="other content"):
        _ingest(store, conflict)


def test_revision_chain_accepts_one_head_and_rejects_fork_and_rollback(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    first = _bundle(tmp_path, private_key, tag="chain-1")
    second = _bundle(
        tmp_path,
        private_key,
        tag="chain-2",
        export_id="nbs-2026-08-r2",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(first),
        raw=_raw(headers=("August 2026",), values=("100.7",)),
        records=[{"series_id": CPI, "period": "2026-08", "value": "100.7"}],
        month_headers=[{"period": "2026-08", "raw_header": "August 2026"}],
        knowledge_time="2026-09-01T10:00:00Z",
        signed_at="2026-09-01T10:05:00Z",
    )
    _ingest(store, first)
    assert _ingest(store, second).revision_id == "nbs-2026-08-r2"
    assert store.load_public_context_strict().revision_id == "nbs-2026-08-r2"

    fork = _bundle(
        tmp_path,
        private_key,
        tag="chain-fork",
        export_id="nbs-fork",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(first),
        knowledge_time="2026-09-02T10:00:00Z",
        signed_at="2026-09-02T10:05:00Z",
    )
    with pytest.raises(nbs.NBSConflictError, match="current head"):
        _ingest(store, fork)

    rollback = _bundle(
        tmp_path,
        private_key,
        tag="chain-rollback",
        export_id="nbs-rollback",
        predecessor="nbs-2026-08-r2",
        predecessor_manifest_sha256=_manifest_sha256(second),
        raw=_raw(headers=("June 2026",), values=("100.1",)),
        records=[{"series_id": CPI, "period": "2026-06", "value": "100.1"}],
        month_headers=[{"period": "2026-06", "raw_header": "June 2026"}],
        knowledge_time="2026-09-03T10:00:00Z",
        signed_at="2026-09-03T10:05:00Z",
    )
    with pytest.raises(nbs.NBSIntegrityError, match="rolls a series backward"):
        _ingest(store, rollback)
    assert not (tmp_path / "store/restricted/exports/nbs-rollback").exists()
    assert not (tmp_path / "store/public/revisions/nbs-rollback.json").exists()


def test_revision_chain_rejects_nonadjacent_series_rollback(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    first = _bundle(tmp_path, private_key, tag="nonadjacent-1")
    middle = _bundle(
        tmp_path,
        private_key,
        tag="nonadjacent-2",
        export_id="nbs-pmi-2026-08-r2",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(first),
        series_id="CN.NBS.MANUFACTURING_PMI",
        raw=_raw(
            series_id="CN.NBS.MANUFACTURING_PMI",
            headers=("August 2026",),
            values=("49.2",),
        ),
        records=[
            {
                "series_id": "CN.NBS.MANUFACTURING_PMI",
                "period": "2026-08",
                "value": "49.2",
            }
        ],
        month_headers=[{"period": "2026-08", "raw_header": "August 2026"}],
        knowledge_time="2026-09-01T10:00:00Z",
        signed_at="2026-09-01T10:05:00Z",
    )
    rollback = _bundle(
        tmp_path,
        private_key,
        tag="nonadjacent-3",
        export_id="nbs-cpi-2026-06-r3",
        predecessor="nbs-pmi-2026-08-r2",
        predecessor_manifest_sha256=_manifest_sha256(middle),
        raw=_raw(headers=("June 2026",), values=("100.1",)),
        records=[{"series_id": CPI, "period": "2026-06", "value": "100.1"}],
        month_headers=[{"period": "2026-06", "raw_header": "June 2026"}],
        knowledge_time="2026-09-02T10:00:00Z",
        signed_at="2026-09-02T10:05:00Z",
    )

    _ingest(store, first)
    _ingest(store, middle)
    with pytest.raises(nbs.NBSIntegrityError, match="rolls a series backward"):
        _ingest(store, rollback)


def test_predecessor_manifest_hash_prevents_cross_store_history_splice(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    genesis_a = _bundle(tmp_path, private_key, tag="splice-a")
    genesis_b = _bundle(
        tmp_path,
        private_key,
        tag="splice-b",
        raw=_raw(values=("100.6",)),
        records=[{"series_id": CPI, "period": "2026-07", "value": "100.6"}],
    )
    child = _bundle(
        tmp_path,
        private_key,
        tag="splice-child",
        export_id="nbs-2026-08-r2",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(genesis_a),
        raw=_raw(headers=("August 2026",), values=("100.7",)),
        records=[{"series_id": CPI, "period": "2026-08", "value": "100.7"}],
        month_headers=[{"period": "2026-08", "raw_header": "August 2026"}],
        knowledge_time="2026-09-01T10:00:00Z",
        signed_at="2026-09-01T10:05:00Z",
    )
    store_a = nbs.NBSIntakeStore(tmp_path / "store-a", attest_dir=str(trust))
    store_b = nbs.NBSIntakeStore(tmp_path / "store-b", attest_dir=str(trust))

    _ingest(store_a, genesis_a)
    assert _ingest(store_a, child).revision_id == "nbs-2026-08-r2"
    _ingest(store_b, genesis_b)
    with pytest.raises(nbs.NBSIntegrityError, match="predecessor content commitment"):
        _ingest(store_b, child)


def test_public_head_receipt_rejects_suffix_deletion_and_pointer_removal(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    first = _bundle(tmp_path, private_key, tag="suffix-1")
    second = _bundle(
        tmp_path,
        private_key,
        tag="suffix-2",
        export_id="nbs-2026-08-r2",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(first),
        raw=_raw(headers=("August 2026",), values=("100.7",)),
        records=[{"series_id": CPI, "period": "2026-08", "value": "100.7"}],
        month_headers=[{"period": "2026-08", "raw_header": "August 2026"}],
        knowledge_time="2026-09-01T10:00:00Z",
        signed_at="2026-09-01T10:05:00Z",
    )
    _ingest(store, first)
    _ingest(store, second)

    latest = root / "public" / "revisions" / "nbs-2026-08-r2.json"
    latest.unlink()
    assert isinstance(store.load_public_context(), nbs.NBSContextUnavailable)

    assert _ingest(store, second).revision_id == "nbs-2026-08-r2"
    public_head = root / "public" / "revisions" / ".head.json"
    public_head.unlink()
    assert isinstance(store.load_public_context(), nbs.NBSContextUnavailable)


def test_restricted_head_receipt_blocks_append_after_suffix_deletion(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    first = _bundle(tmp_path, private_key, tag="restricted-suffix-1")
    second = _bundle(
        tmp_path,
        private_key,
        tag="restricted-suffix-2",
        export_id="nbs-2026-08-r2",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(first),
        raw=_raw(headers=("August 2026",), values=("100.7",)),
        records=[{"series_id": CPI, "period": "2026-08", "value": "100.7"}],
        month_headers=[{"period": "2026-08", "raw_header": "August 2026"}],
        knowledge_time="2026-09-01T10:00:00Z",
        signed_at="2026-09-01T10:05:00Z",
    )
    _ingest(store, first)
    _ingest(store, second)
    latest_dir = root / "restricted" / "exports" / "nbs-2026-08-r2"
    (latest_dir / "manifest.json").unlink()
    (latest_dir / "signature.json").unlink()
    latest_dir.rmdir()

    replacement = _bundle(
        tmp_path,
        private_key,
        tag="restricted-suffix-replacement",
        export_id="nbs-2026-09-r3",
        predecessor="nbs-2026-07-r1",
        predecessor_manifest_sha256=_manifest_sha256(first),
        raw=_raw(headers=("September 2026",), values=("100.8",)),
        records=[{"series_id": CPI, "period": "2026-09", "value": "100.8"}],
        month_headers=[{"period": "2026-09", "raw_header": "September 2026"}],
        knowledge_time="2026-10-01T10:00:00Z",
        signed_at="2026-10-01T10:05:00Z",
    )
    with pytest.raises(nbs.NBSIntegrityError, match="restricted head receipt"):
        _ingest(store, replacement)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("input_index", [0, 1, 2])
def test_input_files_reject_symlinks_and_hardlinks(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    kind: str,
    input_index: int,
) -> None:
    private_key, _public_key, trust = signer
    bundle = list(_bundle(tmp_path, private_key, tag=f"unsafe-{kind}-{input_index}"))
    original = bundle[input_index]
    replacement = original.with_name(f"replacement-{original.name}")
    if kind == "symlink":
        replacement.symlink_to(original)
    else:
        os.link(original, replacement)
    bundle[input_index] = replacement
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))

    with pytest.raises(nbs.NBSIntegrityError, match="safely|single-link"):
        _ingest(store, tuple(bundle))


def test_input_and_storage_paths_reject_unsafe_ancestors(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(tmp_path, private_key, tag="unsafe-ancestor")

    input_alias = tmp_path / "input-alias"
    input_alias.symlink_to(bundle[0].parent, target_is_directory=True)
    aliased_bundle = (
        input_alias / bundle[0].name,
        input_alias / bundle[1].name,
        input_alias / bundle[2].name,
        bundle[3],
    )
    with pytest.raises(nbs.NBSIntegrityError, match="ancestor"):
        _ingest(
            nbs.NBSIntakeStore(tmp_path / "input-store", attest_dir=str(trust)),
            aliased_bundle,
        )

    storage_parent = tmp_path / "real-storage-parent"
    storage_parent.mkdir()
    storage_alias = tmp_path / "storage-alias"
    storage_alias.symlink_to(storage_parent, target_is_directory=True)
    with pytest.raises(nbs.NBSIntegrityError, match="ancestor"):
        _ingest(
            nbs.NBSIntakeStore(storage_alias / "store", attest_dir=str(trust)),
            bundle,
        )

    bundle[0].parent.chmod(0o775)
    with pytest.raises(nbs.NBSIntegrityError, match="unsafe principal"):
        _ingest(
            nbs.NBSIntakeStore(tmp_path / "writable-store", attest_dir=str(trust)),
            bundle,
        )


def test_untrusted_key_and_signature_tamper_fail_before_persistence(
    tmp_path: Path,
) -> None:
    signer = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    trust = _trust_dir(tmp_path, other_key)
    bundle = _bundle(tmp_path, signer, tag="untrusted")
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    with pytest.raises(nbs.NBSIntegrityError, match="not trusted and valid"):
        _ingest(store, bundle)
    assert not (tmp_path / "store").exists()

    own_key = signer.public_key().public_bytes_raw().hex()
    own_trust = _trust_dir(tmp_path, own_key)
    signature = json.loads(bundle[1].read_text())
    signature["signature"] = (
        "0" if signature["signature"][0] != "0" else "1"
    ) + signature["signature"][1:]
    bundle[1].write_bytes(_canonical(signature))
    tampered_store = nbs.NBSIntakeStore(
        tmp_path / "tampered-store", attest_dir=str(own_trust)
    )
    with pytest.raises(nbs.NBSIntegrityError, match="not trusted and valid"):
        _ingest(tampered_store, bundle)


def test_default_hosted_trust_ignores_ambient_key_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw().hex()
    ambient_trust = _trust_dir(tmp_path, public_key)
    monkeypatch.setenv("SEICHE_ATTEST_TRUSTED_PUBLIC_KEYS", public_key)
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(ambient_trust))
    bundle = _bundle(tmp_path, private_key, tag="ambient-trust")

    with pytest.raises(nbs.NBSIntegrityError, match="not trusted and valid"):
        _ingest(nbs.NBSIntakeStore(tmp_path / "store"), bundle)
    assert not (tmp_path / "store").exists()


def test_exact_retry_recovers_after_restricted_commit_before_publication(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, _public_key, trust = signer
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    bundle = _bundle(tmp_path, private_key, tag="crash-retry")
    original_publish = nbs._atomic_publish
    failed = False

    def crash_before_public(path, payload, *, mode, kind, staging_dir):
        nonlocal failed
        if kind == "public projection" and not failed:
            failed = True
            raise OSError("simulated process loss before public publication")
        return original_publish(
            path,
            payload,
            mode=mode,
            kind=kind,
            staging_dir=staging_dir,
        )

    monkeypatch.setattr(nbs, "_atomic_publish", crash_before_public)
    with pytest.raises(OSError, match="simulated process loss"):
        _ingest(store, bundle)
    assert (
        tmp_path
        / "store"
        / "restricted"
        / "exports"
        / "nbs-2026-07-r1"
        / "signature.json"
    ).exists()
    assert not (tmp_path / "store/public/revisions/.head.json").exists()

    monkeypatch.setattr(nbs, "_atomic_publish", original_publish)
    assert _ingest(store, bundle).revision_id == "nbs-2026-07-r1"
    assert store.load_public_context_strict().revision_id == "nbs-2026-07-r1"


def test_safe_staging_orphan_is_reconciled_before_publish_and_retry(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    store._ensure_layout()
    orphan = store.staging / f".nbs-publish-{'a' * 32}.tmp"
    orphan.write_bytes(b"partial pre-rename payload")
    orphan.chmod(0o600)
    bundle = _bundle(tmp_path, private_key, tag="staged-orphan")

    first = _ingest(store, bundle).to_dict()

    assert not orphan.exists()
    assert tuple(store.staging.iterdir()) == ()
    assert not tuple(store.restricted.rglob("*.tmp"))
    assert not tuple(store.public.rglob("*.tmp"))
    projection = store.revisions / "nbs-2026-07-r1.json"
    committed = projection.read_bytes()
    assert _ingest(store, bundle).to_dict() == first
    assert projection.read_bytes() == committed
    assert projection.stat().st_nlink == 1


@pytest.mark.parametrize(
    ("unsafe_kind", "match"),
    [
        ("unknown", "unknown entry"),
        ("unsafe-mode", "unsafe metadata"),
        ("hardlink", "unexpected hard link"),
    ],
)
def test_unsafe_staging_entries_fail_closed_without_publication(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    unsafe_kind: str,
    match: str,
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / f"store-{unsafe_kind}"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    store._ensure_layout()
    if unsafe_kind == "unknown":
        entry = store.staging / "unrecognized.tmp"
        entry.write_bytes(b"unknown")
        entry.chmod(0o600)
    else:
        entry = store.staging / f".nbs-publish-{'b' * 32}.tmp"
        if unsafe_kind == "unsafe-mode":
            entry.write_bytes(b"unsafe mode")
            entry.chmod(0o666)
        else:
            outside = tmp_path / "outside-hardlink-target"
            outside.write_bytes(b"linked")
            outside.chmod(0o600)
            os.link(outside, entry)

    with pytest.raises(nbs.NBSIntegrityError, match=match):
        _ingest(
            store,
            _bundle(tmp_path, private_key, tag=f"unsafe-{unsafe_kind}"),
        )

    assert entry.exists()
    assert tuple(store.exports.iterdir()) == ()
    assert tuple(store.revisions.iterdir()) == ()


def test_append_only_publish_race_never_overwrites_existing_bytes(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    bundle = _bundle(tmp_path, private_key, tag="publish-race")
    raw_sha256 = str(bundle[3]["raw_evidence"]["sha256"])
    destination = store.objects / raw_sha256[:2] / raw_sha256
    raced_bytes = b"same-uid race must survive"
    raced_inode: int | None = None
    original_rename_noreplace = nbs._rename_noreplace

    def introduce_destination(source, target, **kwargs):
        nonlocal raced_inode
        if target == destination and raced_inode is None:
            target.write_bytes(raced_bytes)
            target.chmod(0o600)
            raced_inode = target.stat().st_ino
        return original_rename_noreplace(source, target, **kwargs)

    monkeypatch.setattr(nbs, "_rename_noreplace", introduce_destination)

    with pytest.raises(nbs.NBSConflictError, match="conflicting content"):
        _ingest(store, bundle)

    assert raced_inode is not None
    assert destination.read_bytes() == raced_bytes
    assert destination.stat().st_ino == raced_inode
    assert destination.stat().st_nlink == 1
    assert tuple(store.staging.iterdir()) == ()
    assert tuple(store.exports.iterdir()) == ()
    assert tuple(store.revisions.iterdir()) == ()


def test_exact_retry_recovers_legacy_two_link_publish_crashes(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    bundle = _bundle(tmp_path, private_key, tag="legacy-two-link")
    original = _ingest(store, bundle).to_dict()
    raw_sha256 = str(bundle[3]["raw_evidence"]["sha256"])
    raw = store.objects / raw_sha256[:2] / raw_sha256
    export = store.exports / "nbs-2026-07-r1"
    projection = store.revisions / "nbs-2026-07-r1.json"
    finals_and_temps = (
        (raw, raw.parent / f".{raw.name}.abcdefgh.tmp"),
        (export / "manifest.json", export / ".manifest.json.ijklmnop.tmp"),
        (export / "signature.json", export / ".signature.json.qrstuvwx.tmp"),
        (projection, projection.parent / f".{projection.name}.1234abcd.tmp"),
    )
    committed = {final: final.read_bytes() for final, _temp in finals_and_temps}
    for final, temp in finals_and_temps:
        os.link(final, temp)
        assert final.stat().st_nlink == 2

    with pytest.raises(nbs.NBSIntegrityError):
        store.load_public_context_strict()

    assert _ingest(store, bundle).to_dict() == original
    for final, temp in finals_and_temps:
        assert not temp.exists()
        assert final.stat().st_nlink == 1
        assert final.read_bytes() == committed[final]
    assert tuple(store.staging.iterdir()) == ()
    assert store.load_public_context_strict().to_dict() == original


def test_duplicate_keys_noncanonical_json_and_raw_tamper_fail_closed(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer

    duplicate = _bundle(tmp_path, private_key, tag="duplicate-json")
    duplicate[0].write_bytes(b'{"schema":"shadow",' + duplicate[0].read_bytes()[1:])
    with pytest.raises(nbs.NBSIntegrityError, match="duplicate JSON key"):
        _ingest(
            nbs.NBSIntakeStore(tmp_path / "duplicate-store", attest_dir=str(trust)),
            duplicate,
        )

    noncanonical = _bundle(tmp_path, private_key, tag="noncanonical-json")
    noncanonical[1].write_bytes(noncanonical[1].read_bytes() + b"\n")
    with pytest.raises(nbs.NBSIntegrityError, match="canonical JSON bytes"):
        _ingest(
            nbs.NBSIntakeStore(tmp_path / "noncanonical-store", attest_dir=str(trust)),
            noncanonical,
        )

    raw_tamper = _bundle(tmp_path, private_key, tag="raw-tamper")
    raw_tamper[2].write_bytes(raw_tamper[2].read_bytes() + b"tamper")
    with pytest.raises(nbs.NBSIntegrityError, match="manifest commitment"):
        _ingest(
            nbs.NBSIntakeStore(tmp_path / "raw-tamper-store", attest_dir=str(trust)),
            raw_tamper,
        )


@pytest.mark.parametrize(
    ("period", "value", "match"),
    [
        ("2026-7", "100.5", "period"),
        ("2026-07", "NaN", "decimal string"),
        ("2026-07", "-0.0", "negative zero"),
        ("2026-07", "1001", "outside"),
    ],
)
def test_manifest_rejects_malformed_periods_and_values(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    period: str,
    value: str,
    match: str,
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(tmp_path, private_key, tag=f"bad-{match}")
    manifest = json.loads(bundle[0].read_text())
    manifest["records"][0]["period"] = period
    manifest["records"][0]["value"] = value
    bundle[0].write_bytes(_canonical(manifest))
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))

    with pytest.raises(nbs.NBSIntegrityError, match=match):
        _ingest(store, bundle)


@pytest.mark.parametrize(
    ("knowledge_time", "signed_at", "field"),
    [
        (
            "2026-08-22T10:05:01Z",
            "2026-08-22T10:05:02Z",
            "knowledge_time",
        ),
        (
            "2026-08-22T10:00:00Z",
            "2026-08-22T10:05:01Z",
            "signed_at",
        ),
    ],
)
def test_intake_rejects_trusted_timestamps_beyond_explicit_future_skew(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    knowledge_time: str,
    signed_at: str,
    field: str,
) -> None:
    private_key, _public_key, trust = signer
    monkeypatch.setattr(
        nbs,
        "_utc_now",
        lambda: datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
    )
    bundle = _bundle(
        tmp_path,
        private_key,
        tag=f"future-{field}",
        knowledge_time=knowledge_time,
        signed_at=signed_at,
    )
    store_root = tmp_path / f"store-{field}"

    with pytest.raises(nbs.NBSIntegrityError, match=f"{field} exceeds"):
        _ingest(nbs.NBSIntakeStore(store_root, attest_dir=str(trust)), bundle)

    assert not store_root.exists()


def test_intake_accepts_timestamps_at_future_skew_boundary(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, _public_key, trust = signer
    monkeypatch.setattr(
        nbs,
        "_utc_now",
        lambda: datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
    )
    bundle = _bundle(tmp_path, private_key, tag="future-boundary")

    assert _ingest(
        nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)), bundle
    ).available


def test_source_identity_label_unit_and_base_are_exactly_pinned(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    for index, mutation in enumerate(
        (
            ("label", "Consumer Price Index (%)"),
            ("source_unit_semantically_authoritative", True),
            ("ek_dp", "same_1"),
        )
    ):
        bundle = _bundle(tmp_path, private_key, tag=f"binding-{index}")
        manifest = json.loads(bundle[0].read_text())
        key, value = mutation
        manifest["sources"][0][key] = value
        bundle[0].write_bytes(_canonical(manifest))
        with pytest.raises(nbs.NBSIntegrityError, match="source metadata"):
            _ingest(store, bundle)
    bundle = _bundle(tmp_path, private_key, tag="binding-base")
    manifest = json.loads(bundle[0].read_text())
    manifest["sources"][0]["semantic_contract"]["comparison_base"] = None
    bundle[0].write_bytes(_canonical(manifest))
    with pytest.raises(nbs.NBSIntegrityError, match="source metadata"):
        _ingest(store, bundle)


@pytest.mark.parametrize(
    "release_url",
    [
        "http://www.stats.gov.cn/english/PressRelease/202608/t20260810_1.html",
        "https://evil.example/english/PressRelease/202608/t20260810_1.html",
        "https://user@www.stats.gov.cn/english/PressRelease/202608/t20260810_1.html",
        "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1.html?q=1",
        "https://www.stats.gov.cn/english/PressRelease/202607/t20260810_1.html",
    ],
)
def test_release_url_rejects_nonofficial_or_inconsistent_identity(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    release_url: str,
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(
        tmp_path, private_key, tag=hashlib.sha256(release_url.encode()).hexdigest()[:8]
    )
    manifest = json.loads(bundle[0].read_text())
    manifest["sources"][0]["release_url"] = release_url
    bundle[0].write_bytes(_canonical(manifest))

    with pytest.raises(nbs.NBSIntegrityError, match="official HTTPS URL"):
        _ingest(
            nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)),
            bundle,
        )


def test_official_looking_release_url_requires_code_reviewed_allowlist_entry(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    release_url = (
        "https://www.stats.gov.cn/english/PressRelease/202609/t20260901_2000000.html"
    )
    bundle = _bundle(tmp_path, private_key, tag="unreviewed-release")
    manifest = json.loads(bundle[0].read_text())
    manifest["knowledge_time"] = "2026-09-02T10:00:00Z"
    manifest["sources"][0]["release_url"] = release_url
    bundle[0].write_bytes(_canonical(manifest))
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    with pytest.raises(nbs.NBSIntegrityError, match="not code-reviewed"):
        _ingest(store, bundle)


@pytest.mark.parametrize(
    ("raw_bytes", "match"),
    [
        (_raw(values=("100.6",)), "exact raw NBS CSV cell"),
        (_raw(footer=""), "lacks the exact"),
        (_raw(label="Consumer Price Index (%)"), "label is absent"),
        (_raw(duplicate_label=True), "duplicate indicator label"),
        (
            _raw(headers=("July 2026", "July 2026"), values=("100.5", "100.5")),
            "duplicate month headers",
        ),
        (_raw(post_footer_row="unexpected"), "data after its footer"),
    ],
)
def test_raw_csv_must_match_signed_label_month_value_and_footer(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    raw_bytes: bytes,
    match: str,
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(
        tmp_path,
        private_key,
        tag=f"raw-{hashlib.sha256(raw_bytes).hexdigest()[:8]}",
        raw=raw_bytes,
    )
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))

    with pytest.raises(nbs.NBSIntegrityError, match=match):
        _ingest(store, bundle)


@pytest.mark.parametrize("separator", ["\r", "\u2028"])
def test_raw_csv_row_bound_uses_the_parser_splitlines_representation(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    separator: str,
) -> None:
    private_key, _public_key, trust = signer
    raw = ("\ufeff" + separator.join(["row"] * (nbs.MAX_CSV_ROWS + 1))).encode()
    bundle = _bundle(
        tmp_path, private_key, tag=f"row-bound-{ord(separator):x}", raw=raw
    )

    with pytest.raises(nbs.NBSIntegrityError, match="row bound"):
        _ingest(nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)), bundle)


def test_raw_csv_allows_note_continuations_before_exact_footer(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    raw = _raw(
        note_rows=(
            "Note: jointly reported months may remain blank",
            "Continuation line retained verbatim",
        )
    )
    bundle = _bundle(tmp_path, private_key, tag="note-continuation", raw=raw)
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    assert _ingest(store, bundle).available is True


def test_raw_header_map_binds_unverified_lexeme_without_guessing(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    raw = _raw(headers=("NBS month token 7",))
    bundle = _bundle(
        tmp_path,
        private_key,
        tag="opaque-header",
        raw=raw,
        month_headers=[{"period": "2026-07", "raw_header": "NBS month token 7"}],
    )
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    assert _ingest(store, bundle).available is True

    mismatch = _bundle(
        tmp_path,
        private_key,
        tag="opaque-header-mismatch",
        export_id="other-export",
        raw=raw,
        month_headers=[{"period": "2026-07", "raw_header": "July 2026"}],
    )
    with pytest.raises(nbs.NBSIntegrityError, match="signed map"):
        _ingest(nbs.NBSIntakeStore(tmp_path / "other", attest_dir=str(trust)), mismatch)


def test_literal_csv_grammar_fixture_is_accepted_without_inferred_month_text(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    bundle = _bundle(
        tmp_path,
        private_key,
        tag="literal-grammar",
        raw=LITERAL_CSV_GRAMMAR_FIXTURE,
        month_headers=[{"period": "2026-07", "raw_header": "NBS month token 7"}],
    )
    context = _ingest(
        nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)), bundle
    )
    assert context.available is True


def test_industrial_blank_month_stays_missing_not_zero(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    raw = _raw(
        series_id=INDUSTRIAL,
        headers=("February 2026", "July 2026"),
        values=("", "4.5"),
    )
    bundle = _bundle(
        tmp_path,
        private_key,
        tag="industrial-blank",
        series_id=INDUSTRIAL,
        raw=raw,
        records=[{"series_id": INDUSTRIAL, "period": "2026-07", "value": "4.5"}],
        month_headers=[
            {"period": "2026-02", "raw_header": "February 2026"},
            {"period": "2026-07", "raw_header": "July 2026"},
        ],
    )
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    public = _ingest(store, bundle).to_dict()
    assert public["values_published"] is False
    assert "latest_value" not in public["series"][0]


def test_public_tamper_and_hardlink_fail_closed_to_typed_unavailable(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
) -> None:
    private_key, _public_key, trust = signer
    store = nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust))
    _ingest(store, _bundle(tmp_path, private_key, tag="public-tamper"))
    public_path = tmp_path / "store/public/revisions/nbs-2026-07-r1.json"
    record = json.loads(public_path.read_text())
    record["values_published"] = True
    public_path.write_bytes(_canonical(record))
    unavailable = store.load_public_context()
    assert isinstance(unavailable, nbs.NBSContextUnavailable)
    assert unavailable.to_dict()["reason_code"] == "signed_owner_export_unavailable"
    assert unavailable.to_dict()["evidence_status"] == "unavailable"

    # A second store gives an independently valid projection for the hard-link check.
    other_store = nbs.NBSIntakeStore(tmp_path / "other-store", attest_dir=str(trust))
    _ingest(
        other_store,
        _bundle(tmp_path, private_key, tag="public-link", export_id="link-export"),
    )
    other_public = tmp_path / "other-store/public/revisions/link-export.json"
    os.link(other_public, tmp_path / "public-hardlink")
    assert isinstance(other_store.load_public_context(), nbs.NBSContextUnavailable)


def test_public_dir_loader_never_constructs_or_reads_restricted_paths(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, _public_key, trust = signer
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root, attest_dir=str(trust))
    expected = _ingest(
        store, _bundle(tmp_path, private_key, tag="public-only")
    ).to_dict()
    original_stable_read = nbs._stable_read
    opened: list[Path] = []

    def public_only_read(path, **kwargs):
        selected = Path(path)
        opened.append(selected)
        assert "restricted" not in selected.parts
        return original_stable_read(path, **kwargs)

    def forbidden_store_init(*_args, **_kwargs):
        raise AssertionError("public loader instantiated the restricted intake store")

    monkeypatch.setattr(nbs, "_stable_read", public_only_read)
    monkeypatch.setattr(nbs.NBSIntakeStore, "__init__", forbidden_store_init)
    loaded = nbs.load_public_context_from_public_dir(
        root / "public", attest_dir=str(trust)
    )
    strict_loaded = nbs.load_public_context_strict_from_public_dir(
        root / "public", attest_dir=str(trust)
    )

    assert isinstance(loaded, nbs.NBSMacroContext)
    assert loaded.to_dict() == expected
    assert strict_loaded.to_dict() == expected
    assert opened
    assert all(path.is_relative_to(root / "public") for path in opened)
    assert (
        nbs.resolve_public_context(root / "public", attest_dir=str(trust)) == expected
    )

    public_alias = tmp_path / "public-alias"
    public_alias.symlink_to(root / "public", target_is_directory=True)
    assert isinstance(
        nbs.load_public_context_from_public_dir(public_alias, attest_dir=str(trust)),
        nbs.NBSContextUnavailable,
    )


def test_missing_public_dir_returns_typed_unavailable(tmp_path: Path) -> None:
    result = nbs.load_public_context_from_public_dir(tmp_path / "missing")

    assert isinstance(result, nbs.NBSContextUnavailable)
    assert result.to_dict()["evidence_status"] == "unavailable"
    assert nbs.resolve_public_context(None) == nbs.nbs_public_catalog()
    assert nbs.resolve_public_context(tmp_path / "missing") == nbs.nbs_public_catalog()
    with pytest.raises(nbs.NBSIntegrityError):
        nbs.load_public_context_strict_from_public_dir(tmp_path / "missing")


def test_strict_public_loader_types_only_a_safely_empty_store_as_not_onboarded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    store = nbs.NBSIntakeStore(root)
    store._ensure_layout()

    with pytest.raises(nbs.NBSNotOnboardedError):
        nbs.load_public_context_strict_from_public_dir(root / "public")
    assert isinstance(
        nbs.load_public_context_from_public_dir(root / "public"),
        nbs.NBSContextUnavailable,
    )

    head = {
        "schema": nbs.NBS_HEAD_SCHEMA,
        "sequence": 1,
        "revision_id": "missing-revision",
        "manifest_sha256": "0" * 64,
        "public_projection_sha256": "1" * 64,
        "signature_sha256": "2" * 64,
    }
    store.public_head.write_bytes(_canonical(head))
    store.public_head.chmod(0o640)

    with pytest.raises(nbs.NBSIntegrityError) as error:
        nbs.load_public_context_strict_from_public_dir(root / "public")
    assert not isinstance(error.value, nbs.NBSNotOnboardedError)


def test_ingest_does_not_open_network_or_spawn_processes(
    tmp_path: Path,
    signer: tuple[Ed25519PrivateKey, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    import subprocess

    private_key, _public_key, trust = signer

    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline intake crossed a process or network boundary")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    context = _ingest(
        nbs.NBSIntakeStore(tmp_path / "store", attest_dir=str(trust)),
        _bundle(tmp_path, private_key, tag="offline-runtime"),
    )
    assert context.available is True


def test_module_has_no_network_imports() -> None:
    source_path = Path(nbs.__file__)
    tree = ast.parse(source_path.read_text())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(
        {"httpx", "requests", "socket", "urllib", "aiohttp"}
    )
