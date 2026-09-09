"""Bounded, offline NY Fed archive import with honest clocks and stable retries.

The externally pinned inventory authenticates the retained source bytes. Its
precomputed canonical JSONL is never an input: the current native parser and
market pack re-project the 34 admitted instruments. Importing history does not
run collectors, write source status, or manufacture historical availability.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from seiche.domain.observation import Observation, QualityState, StalenessState
from seiche.markets.registry import default_registry
from seiche.repository import MarketRepository
from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy
from seiche.sources.official import parse_nyfed_rates, parse_nyfed_unsecured_rates

EXPECTED_ARCHIVE_OBSERVATIONS = 76_116
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_OBSERVATIONS = 100_000
INSTRUMENT_IDS = frozenset(
    [
        f"US.NYFED.{family}_{suffix}"
        for family in ("SOFR", "TGCR", "BGCR", "EFFR", "OBFR")
        for suffix in ("MEDIAN", "P01", "P25", "P75", "P99", "VOLUME")
    ]
    + [
        "US.NYFED.SOFR_AVERAGE_30D",
        "US.NYFED.SOFR_AVERAGE_90D",
        "US.NYFED.SOFR_AVERAGE_180D",
        "US.NYFED.SOFR_INDEX",
    ]
)
_FIELDS = {
    "percentRate": "MEDIAN",
    "percentPercentile1": "P01",
    "percentPercentile25": "P25",
    "percentPercentile75": "P75",
    "percentPercentile99": "P99",
    "volumeInBillions": "VOLUME",
}
_DOCUMENTS = {
    "documentation/reference-rate-methodology.html": "https://www.newyorkfed.org/markets/reference-rates/additional-information-about-reference-rates",
    "documentation/terms-of-use.html": "https://www.newyorkfed.org/privacy/termsofuse",
}
NOTICE = (
    "NY Fed reference rate data is subject to the Terms of Use posted at newyorkfed.org. "
    "The New York Fed is not responsible for publication of this data by Seiche, "
    "does not sanction or endorse any particular republication, and has no liability "
    "for your use. Seiche is not affiliated with the New York Fed. The New York Fed "
    "does not sanction, endorse, or recommend products or services offered by Seiche."
)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: Any) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _record_hash(observation: Observation) -> str:
    return _hash(
        json.dumps(
            observation.to_record(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )


def _clock(value: str, label: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return instant.astimezone(UTC)


def _safe_path(root: Path, relative: str) -> Path:
    label = PurePosixPath(relative)
    if (
        not relative
        or label.is_absolute()
        or str(label) != relative
        or any(part in {".", ".."} for part in label.parts)
    ):
        raise ValueError("archive path must be normalized and relative")
    result = root.joinpath(*label.parts)
    if any(
        parent.is_symlink()
        for parent in [result, *result.parents]
        if parent != root.parent
    ):
        raise ValueError("archive symlinks are not accepted")
    if not result.is_file() or not result.resolve().is_relative_to(root):
        raise ValueError("archive path is not a regular contained file")
    return result


@dataclass(frozen=True)
class ArchivePoint:
    point: ParsedPoint
    adapter_id: str
    capture_sha256: str
    captured_at: datetime


@dataclass(frozen=True)
class ValidatedArchive:
    inventory_sha256: str
    points: tuple[ArchivePoint, ...]
    captures: tuple[dict[str, Any], ...]
    report: dict[str, Any]


def validate_nyfed_archive(
    archive_root: Path, *, inventory_sha256: str
) -> ValidatedArchive:
    """Verify all inventory members, then reparse only annual native responses."""
    if not re.fullmatch(r"[0-9a-f]{64}", inventory_sha256):
        raise ValueError("inventory_sha256 must be an explicit lowercase SHA256")
    root = Path(archive_root).absolute()
    inventory_path = _safe_path(root, "artifact-inventory.json")
    if inventory_path.stat().st_size > 1024 * 1024:
        raise ValueError("archive inventory exceeds size bound")
    encoded = inventory_path.read_bytes()
    if _hash(encoded) != inventory_sha256:
        raise ValueError("archive inventory SHA256 mismatch")
    inventory = json.loads(encoded)
    if inventory.get("schema") != "seiche.private-artifact-inventory.v1":
        raise ValueError("unsupported archive inventory schema")
    entries = inventory.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("invalid archive entry count")
    files: dict[str, dict] = {}
    total = 0
    for entry in entries:
        relative = entry["path"]
        if relative in files:
            raise ValueError("duplicate inventory path")
        path = _safe_path(root, relative)
        size = path.stat().st_size
        total += size
        if size != entry["bytes"] or size > MAX_FILE_BYTES or total > MAX_ARCHIVE_BYTES:
            raise ValueError("archive byte count or size bound mismatch")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"archive member SHA256 mismatch: {relative}")
        files[relative] = entry
    if (
        inventory.get("files") != len(files)
        or inventory.get("bytes_excluding_inventory_and_checksum") != total
    ):
        raise ValueError("inventory totals do not match members")

    def read(relative: str) -> bytes:
        if relative not in files:
            raise ValueError(
                f"required source evidence missing from inventory: {relative}"
            )
        body = _safe_path(root, relative).read_bytes()
        if (
            len(body) != files[relative]["bytes"]
            or _hash(body) != files[relative]["sha256"]
        ):
            raise ValueError("archive changed during validation")
        return body

    private_root = PurePosixPath(inventory["private_root"])
    if not private_root.is_absolute() or ".." in private_root.parts:
        raise ValueError("invalid original archive root")

    def relative_path(original: str) -> str:
        original_path = PurePosixPath(original)
        if ".." in original_path.parts:
            raise ValueError("source evidence path traversal")
        try:
            return str(original_path.relative_to(private_root))
        except ValueError as exc:
            raise ValueError("source evidence path escapes original archive") from exc

    documents = json.loads(read("documentation/sources.json"))
    for relative, uri in _DOCUMENTS.items():
        matches = [item for item in documents if item.get("source_uri") == uri]
        if len(matches) != 1:
            raise ValueError("missing or ambiguous official methodology/rights capture")
        item = matches[0]
        body = read(relative)
        if (
            relative_path(item["path"]) != relative
            or item.get("status") != 200
            or item["sha256"] != _hash(body)
            or item["bytes"] != len(body)
        ):
            raise ValueError("official documentation capture identity mismatch")
        if _clock(item["captured_at"], "documentation capture") > datetime.now(UTC):
            raise ValueError("documentation capture is in the future")
    summary = json.loads(read("summary.json"))
    if summary.get(
        "canonical_observation_count"
    ) != EXPECTED_ARCHIVE_OBSERVATIONS or summary.get("canonical_instruments") != len(
        INSTRUMENT_IDS
    ):
        raise ValueError("archive is not the admitted 34-instrument history scope")
    results = json.loads(read("annual-results.json"))
    if not isinstance(results, list) or not 1 <= len(results) <= 64:
        raise ValueError("annual response count exceeds bounds")
    capture_receipts = tuple(
        json.loads(line) for line in read("requests.jsonl").splitlines() if line
    )
    pack = default_registry().get("US-USD")
    projection = hashlib.sha256()
    points: list[ArchivePoint] = []
    captures: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    absent: dict[str, list[str]] = {}
    seen: set[tuple[str, date]] = set()
    windows: set[tuple[str, str, str]] = set()
    excluded = 0
    now = datetime.now(UTC)
    for result in results:
        category = result.get("category")
        if category not in {"secured", "unsecured"} or result.get("label") != "annual":
            raise ValueError(
                "only annual secured/unsecured NY Fed captures are admitted"
            )
        source = "nyfed_rates" if category == "secured" else "nyfed_unsecured_rates"
        start, end = (
            date.fromisoformat(result["start"]),
            date.fromisoformat(result["end"]),
        )
        if (
            start > end
            or (end - start).days > 365
            or start < date(2000, 1, 1)
            or end > now.date()
        ):
            raise ValueError("annual request date window is invalid")
        window = category, start.isoformat(), end.isoformat()
        if window in windows:
            raise ValueError("duplicate annual request")
        windows.add(window)
        uri = urlsplit(result["url"])
        if (
            uri.scheme != "https"
            or uri.netloc != "markets.newyorkfed.org"
            or uri.path != f"/api/rates/{category}/all/search.json"
            or uri.fragment
            or parse_qs(uri.query)
            != {"startDate": [start.isoformat()], "endDate": [end.isoformat()]}
        ):
            raise ValueError(
                "source capture URI is outside the admitted NY Fed endpoint"
            )
        relative = relative_path(result["raw_path"])
        if not relative.startswith(f"raw/market=US-USD/source={source}/"):
            raise ValueError("raw source path does not match NY Fed adapter")
        raw = read(relative)
        captured_at = _clock(result["captured_at"], "raw capture")
        if captured_at > now or end > captured_at.date():
            raise ValueError("raw capture clock precedes request or is in the future")
        if (
            result["sha256"] != _hash(raw)
            or result["bytes"] != len(raw)
            or result["status_code"] != 200
        ):
            raise ValueError("raw capture receipt does not match source bytes")
        matching = [
            item
            for item in capture_receipts
            if item.get("request_key") == result["request_key"]
        ]
        identity_fields = (
            "captured_at",
            "sha256",
            "bytes",
            "url",
            "raw_path",
            "category",
            "start",
            "end",
            "status_code",
        )
        if len(matching) != 1 or any(
            matching[0].get(key) != result.get(key) for key in identity_fields
        ):
            raise ValueError("annual capture lacks a matching retained request receipt")
        native = json.loads(raw)
        rows = native.get("refRates")
        if (
            not isinstance(rows, list)
            or len(rows) != result["rows"]
            or len(rows) > 2000
        ):
            raise ValueError("native response row count mismatch")
        eligible = 0
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid source-native row")
            family = row.get("type")
            families = (
                {"SOFR", "TGCR", "BGCR", "SOFRAI"}
                if category == "secured"
                else {"EFFR", "OBFR"}
            )
            event = date.fromisoformat(row["effectiveDate"])
            if family not in families or not start <= event <= end:
                raise ValueError("native row outside declared family/date scope")
            if category == "unsecured" and event < date(2016, 3, 1):
                if family != "EFFR":
                    raise ValueError("OBFR native row predates its methodology")
                excluded += 1
                continue
            eligible += 1
            if family != "SOFRAI":
                for field, suffix in _FIELDS.items():
                    if row.get(field) in (None, "", "*", "n/a", "N/A", "NA"):
                        absent.setdefault(f"US.NYFED.{family}_{suffix}", []).append(
                            event.isoformat()
                        )
        parser = (
            parse_nyfed_rates if category == "secured" else parse_nyfed_unsecured_rates
        )
        parsed = (
            parser(
                FetchedDocument(result["url"], "application/json", raw),
                publication_time_policy=PublicationTimePolicy.UNKNOWN,
            )
            if eligible
            else ()
        )
        for point in parsed:
            identity = point.instrument_id, point.event_time
            if point.instrument_id not in INSTRUMENT_IDS or identity in seen:
                raise ValueError(
                    "undeclared or duplicated canonical native observation"
                )
            if (
                point.source_publication_time is not None
                or point.publication_time_policy is not PublicationTimePolicy.UNKNOWN
            ):
                raise ValueError("archive parser manufactured a publication clock")
            instrument = pack.instrument_map[point.instrument_id]
            if instrument.source_adapter_id != source:
                raise ValueError(
                    "native instrument does not belong to the source adapter"
                )
            value = instrument.normalize(point.raw_value)
            if not value.is_finite():
                raise ValueError("native value is not a finite canonical number")
            projection.update(
                _json(
                    {
                        "instrument_id": point.instrument_id,
                        "event_date": point.event_time.isoformat(),
                        "value": str(value),
                        "canonical_unit": instrument.canonical_unit.value,
                        "semantic_role": instrument.semantic_role.value,
                        "rate_compounding": instrument.rate_compounding.value
                        if instrument.rate_compounding
                        else None,
                        "day_count": instrument.day_count.value
                        if instrument.day_count
                        else None,
                        "source": source,
                        "capture_sha256": result["sha256"],
                        "revision_id": point.revision_id,
                        "row_evidence_sha256": _hash(point.row_evidence),
                    }
                )
            )
            seen.add(identity)
            counts[point.instrument_id] += 1
            points.append(ArchivePoint(point, source, result["sha256"], captured_at))
            if len(points) > MAX_OBSERVATIONS:
                raise ValueError("canonical observation count exceeds bounded import")
        captures.append(
            {
                "source": source,
                "source_uri": result["url"],
                "raw_path": relative,
                "sha256": result["sha256"],
                "bytes": len(raw),
                "captured_at": captured_at.isoformat(),
                "parsed_observations": len(parsed),
                "native_rows": len(rows),
            }
        )
    if len(points) != EXPECTED_ARCHIVE_OBSERVATIONS or set(counts) != INSTRUMENT_IDS:
        raise ValueError(
            "reparsed source history does not match admitted observation/instrument counts"
        )
    if excluded != summary.get("pre2016_effr_rows_retained_native_only"):
        raise ValueError("EFFR methodology exclusions differ from pinned archive audit")
    for instrument, count in counts.items():
        declared = summary["canonical_series"][instrument]
        if declared["count"] != count or declared[
            "absent_within_native_benchmark_dates"
        ] != len(absent.get(instrument, [])):
            raise ValueError(
                "reparsed native availability differs from pinned inventory audit"
            )
    report = {
        "schema": "seiche.nyfed-history-validation.v1",
        "status": "PASS",
        "inventory_sha256": inventory_sha256,
        "normalized_projection_sha256": projection.hexdigest(),
        "verified_files": len(files),
        "verified_bytes": total,
        "accepted_observations": len(points),
        "instruments": len(counts),
        "counts_by_instrument": dict(sorted(counts.items())),
        "missing_native_fields": {
            key: sorted(value) for key, value in sorted(absent.items())
        },
        "pre2016_effr_native_only": excluded,
        "publication_time_policy": "unknown",
        "knowledge_time_policy": "actual persisted first ingestion time; no retrospective availability",
        "rights_notice": NOTICE,
        "documentation_sha256": {path: files[path]["sha256"] for path in _DOCUMENTS},
        "canonical_jsonl_used": False,
        "source_freshness_updated": False,
    }
    return ValidatedArchive(inventory_sha256, tuple(points), tuple(captures), report)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".pending", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def import_nyfed_history(
    archive_root: Path,
    *,
    inventory_sha256: str,
    repository: MarketRepository | None = None,
    state_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import missing history only; retain state_path on the durable data volume.

    Every source/file/count is checked before writes. The fsynced intent fixes
    the actual first-ingestion clock before any row is inserted. Atomic missing-
    only repository batches serialize with live collector inserts, so neither a
    retry nor an older archived capture can replace a current source vintage.
    """
    archive = validate_nyfed_archive(archive_root, inventory_sha256=inventory_sha256)
    if dry_run:
        return {
            **archive.report,
            "dry_run": True,
            "captures": list(archive.captures),
            "inserted_observations": 0,
        }
    state_path = Path(state_path).absolute()
    if state_path.resolve().is_relative_to(Path(archive_root).resolve()):
        raise ValueError("import state must not modify the immutable source archive")
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        state_path.is_symlink()
        or state_path.with_suffix(state_path.suffix + ".lock").is_symlink()
    ):
        raise ValueError("import state/lock must not be a symlink")
    import_id = _hash(
        _json(
            {
                "schema": "seiche.nyfed-unknown-publication-import.v1",
                "inventory_sha256": inventory_sha256,
            }
        )
    )
    lock = os.open(
        state_path.with_suffix(state_path.suffix + ".lock"),
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if state_path.exists():
            state = json.loads(state_path.read_bytes())
            if (
                state.get("schema") != "seiche.nyfed-history-import-state.v1"
                or state.get("status") not in {"PREPARED", "COMPLETE"}
                or state.get("import_id") != import_id
                or state.get("inventory_sha256") != inventory_sha256
            ):
                raise ValueError("persisted import state belongs to another archive")
            knowledge = _clock(state["knowledge_time"], "persisted ingestion clock")
            if knowledge > datetime.now(UTC) or any(
                item.captured_at > knowledge for item in archive.points
            ):
                raise ValueError(
                    "persisted ingestion clock must follow capture and precede now"
                )
            if (
                state.get("normalized_projection_sha256")
                != archive.report["normalized_projection_sha256"]
            ):
                raise ValueError(
                    "persisted import projection changed; a reviewed new import is required"
                )
            if state.get("captures") != list(archive.captures):
                raise ValueError("persisted source capture provenance differs")
        else:
            knowledge = datetime.now(UTC).replace(microsecond=0)
            if any(item.captured_at > knowledge for item in archive.points):
                raise ValueError("ingestion clock precedes retained source capture")
            state = {
                "schema": "seiche.nyfed-history-import-state.v1",
                "status": "PREPARED",
                "import_id": import_id,
                "inventory_sha256": inventory_sha256,
                "normalized_projection_sha256": archive.report[
                    "normalized_projection_sha256"
                ],
                "knowledge_time": knowledge.isoformat(),
                "captures": list(archive.captures),
            }
            _atomic_json(state_path, state)
        pack = default_registry().get("US-USD")
        observations = []
        for archived in archive.points:
            point = archived.point
            instrument = pack.instrument_map[point.instrument_id]
            spec = pack.adapter_map[archived.adapter_id]
            observations.append(
                Observation(
                    market_id=pack.market_id,
                    monetary_area_id=pack.monetary_area_id,
                    jurisdiction_codes=pack.jurisdiction_codes,
                    currency=pack.currency,
                    instrument_id=point.instrument_id,
                    semantic_role=instrument.semantic_role,
                    value=instrument.normalize(point.raw_value),
                    canonical_unit=instrument.canonical_unit,
                    rate_compounding=instrument.rate_compounding,
                    day_count=instrument.day_count,
                    event_time=datetime.combine(
                        point.event_time, datetime.min.time(), tzinfo=UTC
                    ),
                    knowledge_time=knowledge,
                    source_publication_time=None,
                    revision_id=f"{point.revision_id}@archive-{import_id}:{archived.capture_sha256}",
                    source=archived.adapter_id,
                    evidence_hash=_hash(point.row_evidence),
                    connector_classification=spec.classification,
                    redistribution_status=spec.redistribution_status,
                    quality=QualityState.PROVISIONAL,
                    staleness=StalenessState.UNKNOWN,
                )
            )
        save_missing = getattr(repository, "save_missing_observations", None)
        if not callable(save_missing):
            raise ValueError(
                "repository does not support atomic missing-history imports"
            )
        revision_scope = {
            "instrument_ids": tuple(sorted(INSTRUMENT_IDS)),
            "event_time_from": min(item.event_time for item in observations),
            "event_time": max(item.event_time for item in observations),
        }
        prior = repository.load_observation_revisions(
            "US-USD", datetime.now(UTC), **revision_scope
        )
        expected = {_record_hash(item) for item in observations}
        if any(
            f"@archive-{import_id}:" in item.revision_id
            and _record_hash(item) not in expected
            for item in prior
        ):
            raise ValueError(
                "persisted ingestion identity differs from existing archive rows; restore the original import state"
            )
        inserted = 0
        for offset in range(0, len(observations), 1000):
            inserted += save_missing(observations[offset : offset + 1000])
        revisions = repository.load_observation_revisions(
            "US-USD",
            datetime.now(UTC),
            **revision_scope,
        )
        own = {
            _record_hash(item)
            for item in revisions
            if f"@archive-{import_id}:" in item.revision_id
        }
        expected = {_record_hash(item) for item in observations}
        if own - expected:
            raise ValueError(
                "stored archive import identity differs from the persisted projection"
            )
        covered = {
            (item.instrument_id, item.event_time, item.source) for item in revisions
        }
        if any(
            (item.instrument_id, item.event_time, item.source) not in covered
            for item in observations
        ):
            raise ValueError(
                "canonical storage readback did not account for every admitted source row"
            )
        receipt = {
            **archive.report,
            "schema": "seiche.nyfed-history-import.v1",
            "status": "PASS",
            "import_id": import_id,
            "knowledge_time": knowledge.isoformat(),
            "observed_at": datetime.now(UTC).isoformat(),
            "inserted_this_run": inserted,
            "imported_observations": len(own),
            "preserved_existing_observations": len(observations) - len(own),
            "imported_record_hashes_sha256": _hash(_json(sorted(own))),
            "captures": list(archive.captures),
            "dry_run": False,
        }
        _atomic_json(state_path, {**state, "status": "COMPLETE", "receipt": receipt})
        return receipt
    finally:
        os.close(lock)
