"""Offline, owner-operated intake for signed NBS browser exports.

The browser export is retained as opaque restricted evidence.  Seiche accepts
only a canonical manifest whose upstream identifiers, labels, bases, and
semantics exactly match the release-reviewed bindings below, then verifies a
domain-separated Ed25519 signature under the normal Seiche operator trust
policy.  The owner must generate each manifest's restricted commitment nonce
with a cryptographically secure random source.  Intake performs no network I/O
and never feeds the CN-CNY gauge or any scoring path.

Public output is deliberately metadata-only.  Numeric values remain in the
restricted signed manifest for the lifetime of this v1 schema.  Publishing
values requires a separately reviewed schema and migration; neither input nor
deployment configuration can relax that boundary.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

from seiche.nbs_trust import verify_trusted_ed25519_signature

NBS_EXPORT_SCHEMA = "seiche.nbs-owner-export.v1"
NBS_SIGNATURE_SCHEMA = "seiche.nbs-owner-export-signature.v1"
NBS_PUBLIC_SCHEMA = "seiche.nbs-macro-context.v1"
NBS_HEAD_SCHEMA = "seiche.nbs-head.v1"
NBS_DATASET = "CN.NBS.MACRO_CONTEXT"
NBS_SIGNATURE_DOMAIN = "seiche-nbs-owner-export-v1"
NBS_PUBLISHER = "National Bureau of Statistics of China"
NBS_TERMS_URL = "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html"
NBS_BROWSER_SOURCE_URL = (
    "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData"
)
NBS_RAW_FORMAT = "nbs-browser-tab-comma-wide-v1"
PRODUCTION_NBS_ROOT = Path("/var/lib/seiche-nbs")

# The production writer is a root-owned operator launcher.  These private
# process-capability variables are populated only for its one deployed child;
# they are intentionally consumed before any evidence bytes are read.
_PRODUCTION_GUARD_ROOT_FD_ENV = "SEICHE_NBS_INTAKE_GUARD_ROOT_FD"
_PRODUCTION_GUARD_TOKEN_FD_ENV = "SEICHE_NBS_INTAKE_GUARD_TOKEN_FD"
_PRODUCTION_GUARD_TOKEN_ENV = "SEICHE_NBS_INTAKE_GUARD_TOKEN"

MAX_MANIFEST_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 4 * 1024
MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_PUBLIC_BYTES = 1024 * 1024
MAX_HEAD_BYTES = 4 * 1024
MAX_RECORDS = 4096
MAX_CSV_ROWS = 10_000
MAX_INTAKE_FUTURE_SKEW_SECONDS = 5 * 60

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_EXPORT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MONTH_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?")
_RELEASE_URL_RE = re.compile(
    r"https://www\.stats\.gov\.cn/english/PressRelease/"
    r"([0-9]{6})/t([0-9]{8})_([0-9]+)\.html"
)
_STAGING_NAME_RE = re.compile(r"\.nbs-(publish|replace)-[0-9a-f]{32}\.tmp")
_LEGACY_TEMP_TOKEN_RE = re.compile(r"[a-z0-9_]{8}")
_STAGING_FILE_MODES = frozenset({0o600, 0o640})

_PUBLICATION_POLICY = MappingProxyType(
    {
        "public_distribution": "metadata_only",
        "rights_status": "redistribution_review_required",
        "terms_url": NBS_TERMS_URL,
        "context_only": True,
        "scoring_eligible": False,
        "cn_cny_gauge_eligible": False,
        "raw_evidence_access": "restricted",
    }
)
NBS_PUBLICATION_POLICY: Mapping[str, object] = _PUBLICATION_POLICY


class NBSIntakeError(ValueError):
    """Base class for malformed or unverifiable NBS intake evidence."""


class NBSIntegrityError(NBSIntakeError):
    """Evidence, signature, or committed storage failed verification."""


class NBSNotOnboardedError(NBSIntakeError):
    """The safely opened public revision store has never received a head."""


class NBSConflictError(NBSIntakeError):
    """An immutable identity, predecessor, or revision chain conflicts."""


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _production_root_selection(root: str | os.PathLike[str]) -> bool:
    """Return true only for the canonical production root.

    A custom store remains a supported development/test boundary.  An alias or
    descendant of the production tree is not a custom store: allowing either
    would bypass the mount proof while still writing production evidence.
    """

    selected = Path(os.path.abspath(os.fspath(root)))
    resolved = Path(os.path.realpath(selected))
    if selected == PRODUCTION_NBS_ROOT:
        return True
    try:
        aliases_production = selected.samefile(PRODUCTION_NBS_ROOT)
    except OSError:
        aliases_production = False
    if aliases_production:
        raise NBSIntegrityError(
            "production NBS intake must use the exact canonical root"
        )
    if _path_is_within(selected, PRODUCTION_NBS_ROOT) or _path_is_within(
        resolved, PRODUCTION_NBS_ROOT
    ):
        raise NBSIntegrityError(
            "production NBS intake must use the exact canonical root"
        )
    return False


def _guard_descriptor(value: str | None, *, kind: str) -> int:
    if (
        value is None
        or not value.isascii()
        or not value.isdecimal()
        or value != str(int(value))
        or int(value) < 3
    ):
        raise NBSIntegrityError(f"production NBS {kind} is unavailable")
    return int(value)


def _require_visible_guard_root(descriptor: int) -> None:
    try:
        opened = os.fstat(descriptor)
        visible = os.stat(PRODUCTION_NBS_ROOT, follow_symlinks=False)
    except OSError as exc:
        raise NBSIntegrityError(
            "production NBS root capability is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(visible.st_mode)
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
    ):
        raise NBSIntegrityError(
            "production NBS root capability does not match the visible root"
        )


@contextmanager
def _production_ingest_authorization(
    root: str | os.PathLike[str],
) -> Iterator[None]:
    """Consume and retain the operator launcher's one-use production grant."""

    if not _production_root_selection(root):
        yield
        return
    if os.geteuid() != 0:
        raise NBSIntegrityError("production NBS intake must run as root")

    root_value = os.environ.pop(_PRODUCTION_GUARD_ROOT_FD_ENV, None)
    token_fd_value = os.environ.pop(_PRODUCTION_GUARD_TOKEN_FD_ENV, None)
    token_value = os.environ.pop(_PRODUCTION_GUARD_TOKEN_ENV, None)
    root_source = _guard_descriptor(root_value, kind="root descriptor")
    token_source = _guard_descriptor(token_fd_value, kind="token descriptor")
    if root_source == token_source:
        raise NBSIntegrityError("production NBS guard descriptors are not distinct")
    if (
        token_value is None
        or len(token_value) != 64
        or re.fullmatch(r"[0-9a-f]{64}", token_value) is None
    ):
        raise NBSIntegrityError("production NBS guard token is malformed")

    root_descriptor = -1
    token_descriptor = -1
    try:
        root_descriptor = os.dup(root_source)
        token_descriptor = os.dup(token_source)
        token_metadata = os.fstat(token_descriptor)
        if not stat.S_ISFIFO(token_metadata.st_mode):
            raise NBSIntegrityError(
                "production NBS guard token descriptor is not a pipe"
            )
        import fcntl

        flags = fcntl.fcntl(token_descriptor, fcntl.F_GETFL)
        fcntl.fcntl(token_descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            token_bytes = os.read(token_descriptor, 33)
            trailing = os.read(token_descriptor, 1)
        except BlockingIOError as exc:
            raise NBSIntegrityError(
                "production NBS guard token pipe was not closed"
            ) from exc
        if trailing != b"" or not hmac.compare_digest(
            token_bytes, bytes.fromhex(token_value)
        ):
            raise NBSIntegrityError("production NBS guard token does not match")
        _require_visible_guard_root(root_descriptor)
        yield
        _require_visible_guard_root(root_descriptor)
    except OSError as exc:
        raise NBSIntegrityError("production NBS intake guard failed") from exc
    finally:
        if token_descriptor >= 0:
            os.close(token_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


@dataclass(frozen=True, slots=True)
class NBSSeriesBinding:
    """One release-reviewed identity binding for an NBS presentation table."""

    series_id: str
    catalogid: str
    row_id: str
    indicator_id: str
    export_key: str
    export_key_dimension: str
    dimension_code: str
    dimension_name: str | None
    catalog_label: str
    label: str
    base: str | None
    semantic_type: str
    unit: str
    threshold: str | None
    reference_release_url: str
    source_unit_label_exact: str | None
    source_unit_semantically_authoritative: bool
    minimum: Decimal
    maximum: Decimal
    private_transform: str | None = None

    def manifest_dict(self, *, release_url: str | None = None) -> dict[str, object]:
        return {
            "series_id": self.series_id,
            "catalogid": self.catalogid,
            "catalog_label": self.catalog_label,
            "row_id": self.row_id,
            "i": self.indicator_id,
            "ek": self.export_key,
            "ek_dp": self.export_key_dimension,
            "dp": self.dimension_code,
            "dp_name": self.dimension_name,
            "label": self.label,
            "reference_release_url": self.reference_release_url,
            "release_url": release_url or self.reference_release_url,
            "source_unit_label_exact": self.source_unit_label_exact,
            "source_unit_semantically_authoritative": (
                self.source_unit_semantically_authoritative
            ),
            "semantic_contract": {
                "value_kind": self.semantic_type,
                "canonical_unit": self.unit,
                "comparison_base": self.base,
                "transform": self.private_transform,
                "threshold": self.threshold,
            },
        }


_SERIES_BINDINGS = (
    NBSSeriesBinding(
        series_id="CN.NBS.CPI_INDEX",
        catalogid="5c7452825c7c4dcba391db5ca7f335c5",
        row_id="53180dfb9c14411ba4b762307c85920c",
        indicator_id="b50457fdeade41b0ac011456f7ab5e44",
        export_key="6021702000021|b50457fdeade41b0ac011456f7ab5e44",
        export_key_dimension=("6021702000021|b50457fdeade41b0ac011456f7ab5e44_1"),
        dimension_code="1",
        dimension_name="本期",
        catalog_label=(
            "Consumer Price Indices by Category (The same month last year=100) (2026-)"
        ),
        label="Consumer Price Index (The same month last year=100)",
        base="上年同月=100",
        semantic_type="index_level",
        unit="index_points",
        threshold=None,
        reference_release_url=(
            "https://www.stats.gov.cn/english/PressRelease/202608/"
            "t20260810_1965018.html"
        ),
        source_unit_label_exact="%",
        source_unit_semantically_authoritative=False,
        minimum=Decimal(0),
        maximum=Decimal(1000),
        private_transform="raw_minus_100",
    ),
    NBSSeriesBinding(
        series_id="CN.NBS.PPI_INDEX",
        catalogid="8bc27b5fd28e46df9b8fda8a5d336306",
        row_id="06bb16735fc4416ca91c5f0efa476eef",
        indicator_id="ab3a1ae25fdf45c1a15a30351a869800",
        export_key="6021702000021|ab3a1ae25fdf45c1a15a30351a869800",
        export_key_dimension=("6021702000021|ab3a1ae25fdf45c1a15a30351a869800_1"),
        dimension_code="1",
        dimension_name="本期",
        catalog_label=(
            "Producer Price Indices for Industrial Products by Sector "
            "(The same month last year=100) (2026 to present)"
        ),
        label=(
            "Producer Price Index for Industrial Products "
            "(The same month last year=100)"
        ),
        base="上年同月=100",
        semantic_type="index_level",
        unit="index_points",
        threshold=None,
        reference_release_url=(
            "https://www.stats.gov.cn/english/PressRelease/202608/"
            "t20260810_1965017.html"
        ),
        source_unit_label_exact="无",
        source_unit_semantically_authoritative=False,
        minimum=Decimal(0),
        maximum=Decimal(1000),
        private_transform="raw_minus_100",
    ),
    NBSSeriesBinding(
        series_id="CN.NBS.MANUFACTURING_PMI",
        catalogid="93ffbb1aa85740d3aa2618371508b606",
        row_id="a09aa989bdcf4cffa2021795722eb916",
        indicator_id="793a9f10f0494c20acf046aaecf76d2b",
        export_key="793a9f10f0494c20acf046aaecf76d2b",
        export_key_dimension="793a9f10f0494c20acf046aaecf76d2b_1",
        dimension_code="1",
        dimension_name="本期",
        catalog_label="Manufacturing Purchasing Managers' Index",
        label="Manufacturing Purchasing Managers' Index (%)",
        base=None,
        semantic_type="diffusion_index",
        unit="percentage_points",
        threshold="50",
        reference_release_url=(
            "https://www.stats.gov.cn/english/PressRelease/202608/"
            "t20260803_1964272.html"
        ),
        source_unit_label_exact="%",
        source_unit_semantically_authoritative=True,
        minimum=Decimal(0),
        maximum=Decimal(100),
    ),
    NBSSeriesBinding(
        series_id="CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
        catalogid="3f2e14f0542348ed9fe02476eca3450b",
        row_id="ef1b1765960d45a29b4d7c4ca91be916",
        indicator_id="2518cdacedbd41e790b455113dff9b27",
        export_key="2518cdacedbd41e790b455113dff9b27",
        export_key_dimension="2518cdacedbd41e790b455113dff9b27_11",
        dimension_code="11",
        dimension_name="同比增减%",
        catalog_label=(
            "Growth Rate of Value-added of Industrial Enterprises above Designated Size"
        ),
        label=(
            "Value-added of Industrial Enterprises above Designated Size, "
            "Growth Rate (The same period last year=100)(%)"
        ),
        base=None,
        semantic_type="real_yoy_change",
        unit="percent",
        threshold=None,
        reference_release_url=(
            "https://www.stats.gov.cn/english/PressRelease/202608/"
            "t20260818_1965071.html"
        ),
        source_unit_label_exact="%",
        source_unit_semantically_authoritative=True,
        minimum=Decimal(-100),
        maximum=Decimal(1000),
    ),
)

NBS_SERIES_BINDINGS: Mapping[str, NBSSeriesBinding] = MappingProxyType(
    {binding.series_id: binding for binding in _SERIES_BINDINGS}
)

# Compatibility-visible marker for callers that assert the release gate is
# closed.  V1 projection code intentionally does not consult it: changing a
# process-global set must never alter signed byte semantics or publish values.
NBS_PUBLIC_VALUE_RELEASE_APPROVALS: frozenset[str] = frozenset()

_INGEST_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class NBSMacroContext:
    """Verified public context projection for one immutable revision head."""

    revision_id: str
    record: Mapping[str, object]
    available: bool = True

    def to_dict(self) -> dict[str, object]:
        return _json_copy(self.record)


@dataclass(frozen=True, slots=True)
class NBSContextUnavailable:
    """Safe public result when no fully verified revision head is available."""

    reason_code: str = "signed_owner_export_unavailable"
    available: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": NBS_PUBLIC_SCHEMA,
            "available": False,
            "evidence_status": "unavailable",
            "dataset": NBS_DATASET,
            "context_only": True,
            "scoring_eligible": False,
            "cn_cny_gauge_eligible": False,
            "reason_code": self.reason_code,
        }


NBSPublicContext = NBSMacroContext | NBSContextUnavailable


def nbs_public_catalog() -> dict[str, object]:
    """Return the pure, code-owned metadata catalog for request-path use.

    This function performs no filesystem access, trust-policy lookup, network
    request, or raw-evidence read.  It is not a substitute for a signed export
    head: callers must preserve ``available=False`` until they separately
    receive a verified public projection.
    """

    series: list[dict[str, object]] = []
    for series_id in sorted(NBS_SERIES_BINDINGS):
        row = NBS_SERIES_BINDINGS[series_id].manifest_dict()
        row["value_publication"] = "withheld_pending_rights_review"
        series.append(row)
    return {
        "schema": NBS_PUBLIC_SCHEMA,
        "available": False,
        "evidence_status": "unavailable",
        "dataset": NBS_DATASET,
        "publisher": NBS_PUBLISHER,
        "source_url": NBS_BROWSER_SOURCE_URL,
        "publication_policy": dict(_PUBLICATION_POLICY),
        "values_published": False,
        "series": series,
        "reason_code": "signed_owner_export_required",
    }


@dataclass(frozen=True, slots=True)
class _ValidatedManifest:
    record: Mapping[str, object]
    canonical_bytes: bytes
    manifest_sha256: str
    export_id: str
    predecessor_export_id: str | None
    predecessor_manifest_sha256: str | None
    knowledge_time: datetime
    raw_sha256: str
    raw_size_bytes: int
    raw_filename: str
    raw_month_headers: tuple[Mapping[str, str], ...]
    records: tuple[Mapping[str, str], ...]
    max_period_by_series: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _ValidatedSignature:
    record: Mapping[str, object]
    canonical_bytes: bytes
    signed_at: datetime


def _json_copy(value: object):
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_copy(item) for item in value]
    if isinstance(value, list):
        return [_json_copy(item) for item in value]
    return value


def _freeze_json(value: object):
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _json_copy(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise NBSIntegrityError("evidence is not canonical JSON data") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NBSIntegrityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_canonical_json(raw: bytes, *, kind: str) -> dict[str, object]:
    try:
        text = raw.decode("utf-8")

        def reject_constant(value: str) -> object:
            raise NBSIntegrityError(f"non-finite JSON number {value!r}")

        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (NBSIntegrityError, OSError):
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NBSIntegrityError(f"{kind} is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise NBSIntegrityError(f"{kind} JSON root must be an object")
    if not hmac.compare_digest(raw, _canonical_json_bytes(decoded)):
        raise NBSIntegrityError(f"{kind} must use canonical JSON bytes")
    return decoded


def _require_exact_fields(
    record: Mapping[str, object], required: set[str], *, kind: str
) -> None:
    actual = set(record)
    if actual != required:
        raise NBSIntegrityError(
            f"{kind} fields do not match schema; "
            f"missing={sorted(required - actual)}, unknown={sorted(actual - required)}"
        )


def _require_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _EXPORT_ID_RE.fullmatch(value) is None:
        raise NBSIntegrityError(f"invalid {field}")
    return value


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise NBSIntegrityError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise NBSIntegrityError(f"{field} is not a valid UTC timestamp") from exc
    timespec = "microseconds" if parsed.microsecond else "seconds"
    canonical = parsed.isoformat(timespec=timespec).replace("+00:00", "Z")
    if canonical != value:
        raise NBSIntegrityError(f"{field} is not in canonical UTC form")
    return parsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _reject_future_intake_timestamp(
    value: datetime,
    *,
    field: str,
    intake_time: datetime,
) -> None:
    if value > intake_time + timedelta(seconds=MAX_INTAKE_FUTURE_SKEW_SECONDS):
        raise NBSIntegrityError(f"{field} exceeds the intake future-skew bound")


def _validate_release_url(value: object, *, knowledge_time: datetime) -> str:
    if not isinstance(value, str):
        raise NBSIntegrityError("NBS release_url must be an exact official HTTPS URL")
    match = _RELEASE_URL_RE.fullmatch(value)
    if match is None or match.group(1) != match.group(2)[:6]:
        raise NBSIntegrityError("NBS release_url must be an exact official HTTPS URL")
    try:
        released_on = (
            datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=UTC).date()
        )
    except ValueError as exc:
        raise NBSIntegrityError(
            "NBS release_url contains an invalid publication date"
        ) from exc
    if released_on > knowledge_time.date():
        raise NBSIntegrityError("NBS release_url publication follows knowledge_time")
    return value


def _validate_source_binding(
    source: Mapping[str, object],
    binding: NBSSeriesBinding,
    *,
    knowledge_time: datetime,
) -> None:
    release_url = _validate_release_url(
        source.get("release_url"), knowledge_time=knowledge_time
    )
    if release_url != binding.reference_release_url:
        raise NBSIntegrityError(
            f"release_url is not code-reviewed for {binding.series_id}"
        )
    if source != binding.manifest_dict(release_url=release_url):
        raise NBSIntegrityError(
            f"manifest source metadata does not match {binding.series_id} binding"
        )


def _validate_period(value: object, *, captured_at: datetime) -> str:
    if not isinstance(value, str) or _MONTH_RE.fullmatch(value) is None:
        raise NBSIntegrityError("record period must use canonical YYYY-MM form")
    if value > captured_at.strftime("%Y-%m"):
        raise NBSIntegrityError("record period cannot follow captured_at")
    return value


def _validate_decimal(value: object, binding: NBSSeriesBinding) -> str:
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        raise NBSIntegrityError("record value must be a bounded decimal string")
    if value.startswith("-0") and Decimal(value) == 0:
        raise NBSIntegrityError("record value must not use negative zero")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:  # defensive; the lexical check is stricter
        raise NBSIntegrityError("record value is not a decimal") from exc
    if not parsed.is_finite() or not binding.minimum <= parsed <= binding.maximum:
        raise NBSIntegrityError(f"record value is outside {binding.series_id} bounds")
    return value


def _verify_raw_csv(manifest: _ValidatedManifest, raw: bytes) -> None:
    """Bind every signed record to its exact NBS wide-CSV label/month cell."""

    if not raw.startswith(b"\xef\xbb\xbf"):
        raise NBSIntegrityError("raw NBS CSV must carry its UTF-8 BOM")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NBSIntegrityError("raw NBS CSV is not valid UTF-8-sig") from exc
    if "\x00" in text:
        raise NBSIntegrityError("raw NBS CSV contains a NUL byte")
    lines = text.splitlines()
    if len(lines) > MAX_CSV_ROWS:
        raise NBSIntegrityError("raw NBS CSV exceeds the row bound")
    rows: list[list[str]] = []
    for line in lines:
        row = line.rstrip("\t").split("\t,")
        while row and row[-1] == "":
            row.pop()
        rows.append(row)

    header_indexes = [
        index for index, row in enumerate(rows) if row and row[0] == "Indicators"
    ]
    if len(header_indexes) != 1:
        raise NBSIntegrityError("raw NBS CSV requires exactly one Indicators header")
    header_index = header_indexes[0]
    header = rows[header_index]
    if len(header) < 2:
        raise NBSIntegrityError("raw NBS CSV contains no month columns")
    month_headers = header[1:]
    if len(month_headers) != len(set(month_headers)):
        raise NBSIntegrityError("raw NBS CSV contains duplicate month headers")
    declared_headers = [item["raw_header"] for item in manifest.raw_month_headers]
    if month_headers != declared_headers:
        raise NBSIntegrityError("raw NBS CSV month headers do not match the signed map")
    periods = [item["period"] for item in manifest.raw_month_headers]

    values_by_label: dict[str, dict[str, str]] = {}
    footer_seen = False
    in_notes = False
    for row in rows[header_index + 1 :]:
        if not row:
            continue
        first = row[0]
        if footer_seen:
            raise NBSIntegrityError("raw NBS CSV contains data after its footer")
        if first == "Data Sources: National Bureau of Statistics":
            if len(row) != 1:
                raise NBSIntegrityError("raw NBS CSV source footer is malformed")
            footer_seen = True
            continue
        if first.startswith("Note:"):
            in_notes = True
            continue
        if in_notes:
            continue
        if first in values_by_label:
            raise NBSIntegrityError("raw NBS CSV contains a duplicate indicator label")
        if not first or len(row) > len(header):
            raise NBSIntegrityError("raw NBS CSV contains a malformed indicator row")
        values_by_label[first] = {
            periods[index]: cell for index, cell in enumerate(row[1:]) if cell != ""
        }
    if not footer_seen:
        raise NBSIntegrityError(
            "raw NBS CSV lacks the exact National Bureau of Statistics footer"
        )
    for signed_record in manifest.records:
        binding = NBS_SERIES_BINDINGS[signed_record["series_id"]]
        cells = values_by_label.get(binding.label)
        if cells is None:
            raise NBSIntegrityError("signed series label is absent from raw NBS CSV")
        cell = cells.get(signed_record["period"])
        if cell is None or not hmac.compare_digest(cell, signed_record["value"]):
            raise NBSIntegrityError(
                "signed record value does not match its exact raw NBS CSV cell"
            )


def _validate_manifest(
    record: Mapping[str, object], *, canonical_bytes: bytes | None = None
) -> _ValidatedManifest:
    _require_exact_fields(
        record,
        {
            "schema",
            "dataset",
            "export_id",
            "predecessor_export_id",
            "predecessor_manifest_sha256",
            "commitment_nonce",
            "publisher",
            "knowledge_time",
            "source_url",
            "sources",
            "records",
            "raw_evidence",
            "publication_policy",
        },
        kind="manifest",
    )
    if record["schema"] != NBS_EXPORT_SCHEMA or record["dataset"] != NBS_DATASET:
        raise NBSIntegrityError("unsupported NBS manifest schema or dataset")
    if record["publisher"] != NBS_PUBLISHER:
        raise NBSIntegrityError("manifest publisher is not the pinned NBS identity")
    if record["source_url"] != NBS_BROWSER_SOURCE_URL:
        raise NBSIntegrityError("manifest source_url is not the pinned NBS browser")
    export_id = _require_identifier(record["export_id"], field="export_id")
    predecessor = record["predecessor_export_id"]
    predecessor_manifest_sha256 = record["predecessor_manifest_sha256"]
    if predecessor is not None:
        predecessor = _require_identifier(predecessor, field="predecessor_export_id")
        if predecessor == export_id:
            raise NBSIntegrityError("an export cannot name itself as predecessor")
        if (
            not isinstance(predecessor_manifest_sha256, str)
            or _SHA256_RE.fullmatch(predecessor_manifest_sha256) is None
        ):
            raise NBSIntegrityError(
                "predecessor_manifest_sha256 must bind the predecessor content"
            )
    elif predecessor_manifest_sha256 is not None:
        raise NBSIntegrityError(
            "a genesis export cannot name a predecessor manifest commitment"
        )
    knowledge_time = _parse_timestamp(record["knowledge_time"], field="knowledge_time")
    commitment_nonce = record["commitment_nonce"]
    if (
        not isinstance(commitment_nonce, str)
        or _SHA256_RE.fullmatch(commitment_nonce) is None
    ):
        raise NBSIntegrityError(
            "commitment_nonce must be a random 32-byte lowercase-hex value"
        )

    sources = record["sources"]
    if (
        not isinstance(sources, list)
        or not sources
        or len(sources) > len(NBS_SERIES_BINDINGS)
    ):
        raise NBSIntegrityError("manifest sources must be a bounded non-empty array")
    source_ids: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            raise NBSIntegrityError("manifest source entries must be objects")
        series_id = source.get("series_id")
        if not isinstance(series_id, str) or series_id not in NBS_SERIES_BINDINGS:
            raise NBSIntegrityError("manifest source is not release-pinned")
        _validate_source_binding(
            source,
            NBS_SERIES_BINDINGS[series_id],
            knowledge_time=knowledge_time,
        )
        source_ids.append(series_id)
    if len(source_ids) != len(set(source_ids)) or source_ids != sorted(source_ids):
        raise NBSIntegrityError("manifest sources must be unique and series-sorted")

    records = record["records"]
    if not isinstance(records, list) or not 0 < len(records) <= MAX_RECORDS:
        raise NBSIntegrityError("manifest records must be a bounded non-empty array")
    validated_records: list[Mapping[str, str]] = []
    identities: set[tuple[str, str]] = set()
    max_period_by_series: dict[str, str] = {}
    for item in records:
        if not isinstance(item, dict):
            raise NBSIntegrityError("manifest record entries must be objects")
        _require_exact_fields(
            item, {"series_id", "period", "value"}, kind="manifest record"
        )
        series_id = item["series_id"]
        if not isinstance(series_id, str) or series_id not in source_ids:
            raise NBSIntegrityError("manifest record has no exact pinned source")
        period = _validate_period(item["period"], captured_at=knowledge_time)
        value = _validate_decimal(item["value"], NBS_SERIES_BINDINGS[series_id])
        identity = (series_id, period)
        if identity in identities:
            raise NBSIntegrityError("manifest contains a duplicate series period")
        identities.add(identity)
        validated_records.append(
            MappingProxyType({"series_id": series_id, "period": period, "value": value})
        )
        max_period_by_series[series_id] = max(
            period, max_period_by_series.get(series_id, period)
        )
    if [(item["series_id"], item["period"]) for item in records] != sorted(identities):
        raise NBSIntegrityError("manifest records must be series-and-period sorted")
    if set(source_ids) != set(max_period_by_series):
        raise NBSIntegrityError("each pinned source requires at least one record")

    raw = record["raw_evidence"]
    if not isinstance(raw, dict):
        raise NBSIntegrityError("raw_evidence must be an object")
    _require_exact_fields(
        raw,
        {
            "filename",
            "format",
            "media_type",
            "month_headers",
            "sha256",
            "size_bytes",
        },
        kind="raw_evidence",
    )
    filename = raw["filename"]
    if (
        not isinstance(filename, str)
        or not filename
        or filename != filename.strip()
        or len(filename.encode("utf-8")) > 255
        or Path(filename).name != filename
        or not filename.lower().endswith(".csv")
    ):
        raise NBSIntegrityError("raw evidence filename must be a safe CSV basename")
    if raw["media_type"] != "text/csv":
        raise NBSIntegrityError("raw evidence media_type must be text/csv")
    if raw["format"] != NBS_RAW_FORMAT:
        raise NBSIntegrityError("raw evidence format is not the pinned browser CSV v1")
    month_headers = raw["month_headers"]
    if not isinstance(month_headers, list) or not 0 < len(month_headers) <= 240:
        raise NBSIntegrityError("raw evidence month_headers must be a bounded array")
    validated_headers: list[Mapping[str, str]] = []
    seen_periods: set[str] = set()
    seen_raw_headers: set[str] = set()
    for item in month_headers:
        if not isinstance(item, dict):
            raise NBSIntegrityError("raw evidence month header entries must be objects")
        _require_exact_fields(
            item, {"period", "raw_header"}, kind="raw evidence month header"
        )
        period = _validate_period(item["period"], captured_at=knowledge_time)
        raw_header = item["raw_header"]
        if (
            not isinstance(raw_header, str)
            or not raw_header
            or raw_header != raw_header.strip()
            or len(raw_header.encode("utf-8")) > 128
            or "\t," in raw_header
            or "\n" in raw_header
            or "\r" in raw_header
            or raw_header == "Indicators"
        ):
            raise NBSIntegrityError("raw evidence month header is malformed")
        if period in seen_periods or raw_header in seen_raw_headers:
            raise NBSIntegrityError("raw evidence month header map is not unique")
        seen_periods.add(period)
        seen_raw_headers.add(raw_header)
        validated_headers.append(
            MappingProxyType({"period": period, "raw_header": raw_header})
        )
    raw_sha256 = raw["sha256"]
    if not isinstance(raw_sha256, str) or _SHA256_RE.fullmatch(raw_sha256) is None:
        raise NBSIntegrityError("raw evidence SHA-256 is malformed")
    raw_size = raw["size_bytes"]
    if (
        isinstance(raw_size, bool)
        or not isinstance(raw_size, int)
        or not 0 < raw_size <= MAX_RAW_BYTES
    ):
        raise NBSIntegrityError("raw evidence size is outside the intake bound")
    if not set(max_period_by_series.values()).issubset(seen_periods):
        raise NBSIntegrityError("record period has no exact raw month header binding")
    if any(item["period"] not in seen_periods for item in validated_records):
        raise NBSIntegrityError("record period has no exact raw month header binding")

    policy = record["publication_policy"]
    if not isinstance(policy, dict) or policy != dict(_PUBLICATION_POLICY):
        raise NBSIntegrityError("manifest publication policy is not release-pinned")

    manifest_bytes = canonical_bytes or _canonical_json_bytes(record)
    return _ValidatedManifest(
        record=MappingProxyType(_json_copy(record)),
        canonical_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        export_id=export_id,
        predecessor_export_id=predecessor,
        predecessor_manifest_sha256=predecessor_manifest_sha256,
        knowledge_time=knowledge_time,
        raw_sha256=raw_sha256,
        raw_size_bytes=raw_size,
        raw_filename=filename,
        raw_month_headers=tuple(validated_headers),
        records=tuple(validated_records),
        max_period_by_series=MappingProxyType(max_period_by_series),
    )


def _public_payload(manifest: _ValidatedManifest) -> dict[str, object]:
    source_rows: list[dict[str, object]] = []
    for source in manifest.record["sources"]:
        row = _json_copy(source)
        row["value_publication"] = "withheld_pending_rights_review"
        source_rows.append(row)
    return {
        "schema": NBS_PUBLIC_SCHEMA,
        "available": True,
        "evidence_status": "restricted",
        "dataset": NBS_DATASET,
        "revision_id": manifest.export_id,
        "predecessor_revision_id": manifest.predecessor_export_id,
        "predecessor_manifest_sha256": manifest.predecessor_manifest_sha256,
        "knowledge_time": manifest.record["knowledge_time"],
        "publisher": NBS_PUBLISHER,
        "source_url": NBS_BROWSER_SOURCE_URL,
        "publication_policy": dict(_PUBLICATION_POLICY),
        "values_published": False,
        "series": source_rows,
        "provenance": {
            "manifest_sha256": manifest.manifest_sha256,
            "owner_attestation": "ed25519",
        },
        "caveats": [
            "Owner-attested browser export; not an NBS digital signature.",
            "Metadata-only macro context; excluded from scoring and CN-CNY gauge roles.",
            "Raw evidence and observation values remain restricted; public revision commitments are retained.",
        ],
    }


def build_signature_claim(
    manifest_record: Mapping[str, object],
    *,
    signed_at: str,
    signer_key_id: str,
) -> dict[str, object]:
    """Build the exact domain-separated claim an owner must sign offline."""

    manifest = _validate_manifest(manifest_record)
    _parse_timestamp(signed_at, field="signed_at")
    if (
        not isinstance(signer_key_id, str)
        or _SHA256_RE.fullmatch(signer_key_id) is None
    ):
        raise NBSIntegrityError("signer_key_id must identify an Ed25519 public key")
    projection_sha256 = hashlib.sha256(
        _canonical_json_bytes(_public_payload(manifest))
    ).hexdigest()
    return {
        "schema": NBS_SIGNATURE_SCHEMA,
        "algorithm": "ed25519",
        "domain": NBS_SIGNATURE_DOMAIN,
        "export_id": manifest.export_id,
        "signer_key_id": signer_key_id,
        "signed_at": signed_at,
        "manifest_sha256": manifest.manifest_sha256,
        "public_projection_sha256": projection_sha256,
    }


def encode_signature_claim(claim: Mapping[str, object]) -> bytes:
    """Canonical bytes for the detached Ed25519 signature operation."""

    _require_exact_fields(
        claim,
        {
            "schema",
            "algorithm",
            "domain",
            "export_id",
            "signer_key_id",
            "signed_at",
            "manifest_sha256",
            "public_projection_sha256",
        },
        kind="signature claim",
    )
    if claim["schema"] != NBS_SIGNATURE_SCHEMA:
        raise NBSIntegrityError("unsupported signature claim schema")
    if claim["algorithm"] != "ed25519" or claim["domain"] != NBS_SIGNATURE_DOMAIN:
        raise NBSIntegrityError("signature claim algorithm or domain is invalid")
    return _canonical_json_bytes(claim)


def build_signature_claim_from_manifest_file(
    manifest_path: str | os.PathLike[str],
    *,
    signed_at: str,
    signer_key_id: str,
) -> dict[str, object]:
    """Build a claim from one bounded, canonical, safely opened manifest.

    Offline signing tools should use this entry point instead of a permissive
    ``json.load`` round trip.  It guarantees that the bytes used to derive the
    manifest commitment are the same canonical bytes intake will later accept.
    """

    manifest_bytes = _stable_read(
        manifest_path,
        maximum_bytes=MAX_MANIFEST_BYTES,
        kind="input manifest",
    )
    manifest_record = _decode_canonical_json(
        manifest_bytes,
        kind="input manifest",
    )
    return build_signature_claim(
        manifest_record,
        signed_at=signed_at,
        signer_key_id=signer_key_id,
    )


def _validate_signature(
    record: Mapping[str, object],
    manifest: _ValidatedManifest,
    *,
    canonical_bytes: bytes | None = None,
    attest_dir: str | None = None,
) -> _ValidatedSignature:
    _require_exact_fields(
        record,
        {
            "schema",
            "algorithm",
            "domain",
            "export_id",
            "signer_key_id",
            "signed_at",
            "manifest_sha256",
            "public_projection_sha256",
            "signature",
        },
        kind="signature sidecar",
    )
    signature_hex = record["signature"]
    if (
        not isinstance(signature_hex, str)
        or _SIGNATURE_RE.fullmatch(signature_hex) is None
    ):
        raise NBSIntegrityError("detached Ed25519 signature is malformed")
    signer = record["signer_key_id"]
    if not isinstance(signer, str):
        raise NBSIntegrityError("signature signer key is malformed")
    signed_at = _parse_timestamp(record["signed_at"], field="signed_at")
    if signed_at < manifest.knowledge_time:
        raise NBSIntegrityError("signed_at cannot precede knowledge_time")
    expected_claim = build_signature_claim(
        manifest.record,
        signed_at=record["signed_at"],
        signer_key_id=signer,
    )
    actual_claim = {key: value for key, value in record.items() if key != "signature"}
    if actual_claim != expected_claim:
        raise NBSIntegrityError("signature sidecar does not bind the exact export")
    try:
        verify_trusted_ed25519_signature(
            encode_signature_claim(expected_claim),
            signature_hex,
            signer,
            attest_dir=attest_dir,
        )
    except ValueError as exc:
        raise NBSIntegrityError(
            "owner export signature is not trusted and valid"
        ) from exc
    sidecar_bytes = canonical_bytes or _canonical_json_bytes(record)
    return _ValidatedSignature(
        record=MappingProxyType(_json_copy(record)),
        canonical_bytes=sidecar_bytes,
        signed_at=signed_at,
    )


def _validate_safe_directory_metadata(metadata: os.stat_result, *, kind: str) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise NBSIntegrityError(f"{kind} is not a directory")
    mode = stat.S_IMODE(metadata.st_mode)
    sticky_root_directory = bool(mode & stat.S_ISVTX) and metadata.st_uid == 0
    if mode & 0o022 and not sticky_root_directory:
        raise NBSIntegrityError(f"{kind} is writable by an unsafe principal")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise NBSIntegrityError(f"{kind} has an unsafe owner")


def _open_safe_directory(path: str | os.PathLike[str], *, kind: str) -> int:
    """Open a directory by no-follow components with safe ancestor ownership."""

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise NBSIntegrityError(f"{kind} requires no-follow directory opens")
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(absolute.anchor, flags)
    except OSError as exc:
        raise NBSIntegrityError(f"{kind} root cannot be opened safely") from exc
    try:
        _validate_safe_directory_metadata(os.fstat(descriptor), kind=f"{kind} root")
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise NBSIntegrityError(
                    f"{kind} ancestor cannot be opened safely"
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
            _validate_safe_directory_metadata(
                os.fstat(descriptor), kind=f"{kind} ancestor"
            )
    except NBSIntegrityError:
        os.close(descriptor)
        raise
    return descriptor


def _directory_metadata(
    path: str | os.PathLike[str],
    *,
    kind: str,
    required_mode: int | None = None,
    required_uid: int | None = None,
) -> os.stat_result:
    descriptor = _open_safe_directory(path, kind=kind)
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if required_mode is not None and stat.S_IMODE(metadata.st_mode) != required_mode:
        raise NBSIntegrityError(f"{kind} has an unsafe mode")
    if required_uid is not None and metadata.st_uid != required_uid:
        raise NBSIntegrityError(f"{kind} has an unexpected owner")
    return metadata


def _stable_read(
    path: str | os.PathLike[str],
    *,
    maximum_bytes: int,
    kind: str,
    required_mode: int | None = None,
    required_uid: int | None = None,
    required_gid: int | None = None,
) -> bytes:
    """Read a bounded file through no-follow, ownership-checked path components."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise NBSIntegrityError(f"{kind} requires no-follow file opens")
    selected = Path(os.path.abspath(os.fspath(path)))
    parent_descriptor = _open_safe_directory(selected.parent, kind=f"{kind} parent")
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(selected.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        os.close(parent_descriptor)
        raise NBSIntegrityError(f"{kind} cannot be opened safely") from exc
    os.close(parent_descriptor)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise NBSIntegrityError(f"{kind} must be a single-link regular file")
        if required_mode is not None and stat.S_IMODE(before.st_mode) != required_mode:
            raise NBSIntegrityError(f"{kind} has an unsafe file mode")
        if required_mode is None and stat.S_IMODE(before.st_mode) & 0o022:
            raise NBSIntegrityError(f"{kind} is writable by an unsafe principal")
        if before.st_uid not in {0, os.geteuid()}:
            raise NBSIntegrityError(f"{kind} has an unsafe owner")
        if required_uid is not None and before.st_uid != required_uid:
            raise NBSIntegrityError(f"{kind} has an unexpected owner")
        if required_gid is not None and before.st_gid != required_gid:
            raise NBSIntegrityError(f"{kind} has an unexpected group")
        if not 0 < before.st_size <= maximum_bytes:
            raise NBSIntegrityError(f"{kind} size is outside its bound")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise NBSIntegrityError(f"{kind} exceeds its size bound")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    def identity(value):
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_uid,
            value.st_gid,
        )

    if total != before.st_size or identity(before) != identity(after):
        raise NBSIntegrityError(f"{kind} changed while being read")
    return b"".join(chunks)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    except OSError:
        # Directory fsync is unavailable on a few otherwise supported filesystems.
        pass
    finally:
        os.close(descriptor)


def _ensure_directory(
    path: Path,
    mode: int,
    *,
    required_uid: int,
) -> os.stat_result:
    selected = Path(os.path.abspath(path))
    parent_descriptor = _open_safe_directory(selected.parent, kind="storage parent")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    created = False
    try:
        try:
            descriptor = os.open(selected.name, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            try:
                os.mkdir(selected.name, mode=mode, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
            else:
                created = True
            descriptor = os.open(selected.name, flags, dir_fd=parent_descriptor)
        try:
            if created:
                os.fchmod(descriptor, mode)
                os.fsync(parent_descriptor)
            metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise NBSIntegrityError(
            f"storage directory cannot be opened safely: {selected}"
        ) from exc
    finally:
        os.close(parent_descriptor)
    _validate_safe_directory_metadata(metadata, kind="storage directory")
    if stat.S_IMODE(metadata.st_mode) != mode:
        raise NBSIntegrityError(f"storage directory has unsafe mode: {selected}")
    if metadata.st_uid != required_uid:
        raise NBSIntegrityError(f"storage directory has unexpected owner: {selected}")
    return metadata


def _rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int,
    destination_dir_fd: int,
) -> None:
    """Atomically rename one file only when the destination is absent."""

    if os.name == "nt":
        # Windows rename is already no-replace; unlike POSIX rename, it fails
        # when the destination exists.
        os.rename(source, destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source_name = os.fsencode(source.name)
    destination_name = os.fsencode(destination.name)
    if sys.platform == "darwin":
        try:
            renameatx_np = libc.renameatx_np
        except AttributeError as exc:  # pragma: no cover - obsolete Darwin
            raise NBSIntegrityError(
                "atomic no-replace rename is unavailable on this platform"
            ) from exc
        renameatx_np.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_dir_fd,
            source_name,
            destination_dir_fd,
            destination_name,
            0x00000004,  # RENAME_EXCL
        )
    elif sys.platform.startswith("linux"):
        try:
            renameat2 = libc.renameat2
        except AttributeError as exc:  # pragma: no cover - obsolete libc
            raise NBSIntegrityError(
                "atomic no-replace rename is unavailable on this platform"
            ) from exc
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_dir_fd,
            source_name,
            destination_dir_fd,
            destination_name,
            1,  # RENAME_NOREPLACE
        )
    else:  # pragma: no cover - supported production platforms are above
        raise NBSIntegrityError(
            "atomic no-replace rename is unavailable on this platform"
        )
    if result == 0:
        return
    error = ctypes.get_errno() or errno.EIO
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), os.fspath(destination))
    raise OSError(error, os.strerror(error), os.fspath(destination))


def _stage_payload(
    staging_dir: Path,
    destination: Path,
    payload: bytes,
    *,
    mode: int,
    gid: int | None = None,
    operation: str,
) -> None:
    """Write, sync, and rename one staged file while the intake lock is held."""

    if operation not in {"publish", "replace"}:
        raise NBSIntegrityError("atomic staging operation is invalid")
    staging_descriptor = -1
    destination_descriptor = -1
    descriptor = -1
    temporary_name = ""
    renamed = False
    try:
        staging_descriptor = _open_safe_directory(
            staging_dir, kind="NBS atomic staging directory"
        )
        destination_descriptor = _open_safe_directory(
            destination.parent, kind="NBS atomic destination directory"
        )
        staging_metadata = os.fstat(staging_descriptor)
        destination_metadata = os.fstat(destination_descriptor)
        if (
            stat.S_IMODE(staging_metadata.st_mode) != 0o700
            or staging_metadata.st_uid != os.geteuid()
        ):
            raise NBSIntegrityError("NBS atomic staging directory is unsafe")
        if staging_metadata.st_dev != destination_metadata.st_dev:
            raise NBSIntegrityError(
                "NBS atomic staging directory is not on the destination filesystem"
            )
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        for _attempt in range(32):
            temporary_name = f".nbs-{operation}-{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=staging_descriptor,
                )
            except FileExistsError:
                continue
            break
        else:
            raise NBSIntegrityError("could not allocate a unique NBS staging file")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            staged_before = os.fstat(handle.fileno())
            if gid is not None and staged_before.st_gid != gid:
                if not hasattr(os, "fchown"):
                    raise NBSIntegrityError(
                        "NBS public group assignment is unavailable"
                    )
                os.fchown(handle.fileno(), -1, gid)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
            staged = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(staged.st_mode)
                or staged.st_nlink != 1
                or staged.st_uid != os.geteuid()
                or stat.S_IMODE(staged.st_mode) != mode
                or (gid is not None and staged.st_gid != gid)
            ):
                raise NBSIntegrityError("NBS staged publication metadata is unsafe")
        # All legitimate writers hold the same flock. A single rename avoids
        # the two-link crash window created by the former link+unlink sequence.
        if operation == "publish":
            _rename_noreplace(
                staging_dir / temporary_name,
                destination,
                source_dir_fd=staging_descriptor,
                destination_dir_fd=destination_descriptor,
            )
        else:
            os.replace(
                temporary_name,
                destination.name,
                src_dir_fd=staging_descriptor,
                dst_dir_fd=destination_descriptor,
            )
        renamed = True
        _fsync_directory(destination.parent)
        _fsync_directory(staging_dir)
    except FileExistsError:
        raise
    except OSError as exc:
        raise NBSIntegrityError("NBS atomic publication failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name and not renamed and staging_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=staging_descriptor)
            except FileNotFoundError:
                pass
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if staging_descriptor >= 0:
            os.close(staging_descriptor)


def _atomic_publish(
    path: Path,
    payload: bytes,
    *,
    mode: int,
    kind: str,
    staging_dir: Path,
    gid: int | None = None,
) -> None:
    if path.exists() or path.is_symlink():
        try:
            existing = _stable_read(
                path,
                maximum_bytes=max(len(payload), 1),
                kind=f"committed {kind}",
                required_mode=mode,
                required_gid=gid,
            )
        except NBSIntegrityError as exc:
            raise NBSConflictError(f"committed {kind} is unsafe") from exc
        if not hmac.compare_digest(existing, payload):
            raise NBSConflictError(f"committed {kind} conflicts with exact retry")
        return
    try:
        _stage_payload(
            staging_dir,
            path,
            payload,
            mode=mode,
            gid=gid,
            operation="publish",
        )
    except FileExistsError as exc:
        try:
            existing = _stable_read(
                path,
                maximum_bytes=max(len(payload), 1),
                kind=f"raced committed {kind}",
                required_mode=mode,
                required_gid=gid,
            )
        except NBSIntegrityError as read_exc:
            raise NBSConflictError(
                f"committed {kind} appeared unsafely during publication"
            ) from read_exc
        if hmac.compare_digest(existing, payload):
            return
        raise NBSConflictError(
            f"committed {kind} appeared with conflicting content"
        ) from exc


def _atomic_replace(
    path: Path,
    payload: bytes,
    *,
    mode: int,
    kind: str,
    staging_dir: Path,
    gid: int | None = None,
) -> None:
    """Crash-safely replace one operational pointer, never evidence content."""

    if path.exists() or path.is_symlink():
        existing = _stable_read(
            path,
            maximum_bytes=MAX_HEAD_BYTES,
            kind=f"existing {kind}",
            required_mode=mode,
            required_gid=gid,
        )
        if hmac.compare_digest(existing, payload):
            return
    _stage_payload(
        staging_dir,
        path,
        payload,
        mode=mode,
        gid=gid,
        operation="replace",
    )


def _head_receipt(
    *,
    sequence: int,
    revision_id: str,
    manifest_sha256: str,
    public_projection_sha256: str,
    signature_sha256: str,
) -> dict[str, object]:
    return {
        "schema": NBS_HEAD_SCHEMA,
        "sequence": sequence,
        "revision_id": revision_id,
        "manifest_sha256": manifest_sha256,
        "public_projection_sha256": public_projection_sha256,
        "signature_sha256": signature_sha256,
    }


def _validate_head_receipt(record: Mapping[str, object]) -> dict[str, object]:
    _require_exact_fields(
        record,
        {
            "schema",
            "sequence",
            "revision_id",
            "manifest_sha256",
            "public_projection_sha256",
            "signature_sha256",
        },
        kind="head receipt",
    )
    sequence = record["sequence"]
    if (
        record["schema"] != NBS_HEAD_SCHEMA
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= 0
        or sequence > 1_000_000_000
    ):
        raise NBSIntegrityError("head receipt schema or sequence is invalid")
    _require_identifier(record["revision_id"], field="head revision_id")
    for field in (
        "manifest_sha256",
        "public_projection_sha256",
        "signature_sha256",
    ):
        value = record[field]
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise NBSIntegrityError(f"head receipt {field} is malformed")
    return _json_copy(record)


def _read_head_receipt(
    path: Path,
    *,
    mode: int,
    kind: str,
    required_uid: int | None = None,
    required_gid: int | None = None,
) -> dict[str, object] | None:
    if not path.exists() and not path.is_symlink():
        return None
    record, _raw = _validated_json_file(
        path,
        maximum_bytes=MAX_HEAD_BYTES,
        mode=mode,
        kind=kind,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    return _validate_head_receipt(record)


def _require_expected_head(
    actual: Mapping[str, object] | None,
    expected: Mapping[str, object] | None,
    *,
    kind: str,
) -> None:
    if actual != expected:
        raise NBSIntegrityError(f"{kind} does not match the unique revision head")


def _validated_json_file(
    path: Path,
    *,
    maximum_bytes: int,
    mode: int,
    kind: str,
    required_uid: int | None = None,
    required_gid: int | None = None,
) -> tuple[dict[str, object], bytes]:
    raw = _stable_read(
        path,
        maximum_bytes=maximum_bytes,
        kind=kind,
        required_mode=mode,
        required_uid=required_uid,
        required_gid=required_gid,
    )
    return _decode_canonical_json(raw, kind=kind), raw


def _public_record(
    manifest: _ValidatedManifest, signature: _ValidatedSignature
) -> dict[str, object]:
    record = _public_payload(manifest)
    record["attestation"] = _json_copy(signature.record)
    return record


def _context_from_public_record(record: Mapping[str, object]) -> NBSMacroContext:
    return NBSMacroContext(
        revision_id=str(record["revision_id"]),
        record=_freeze_json(record),
    )


class NBSIntakeStore:
    """Append-only restricted evidence and signed metadata projection store."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        attest_dir: str | None = None,
    ) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))
        if self.root == PRODUCTION_NBS_ROOT and attest_dir is not None:
            raise NBSIntegrityError("production NBS trust policy is release-pinned")
        self.owner_uid = os.geteuid()
        self.attest_dir = attest_dir
        self.restricted = self.root / "restricted"
        self.objects = self.restricted / "objects" / "sha256"
        self.exports = self.restricted / "exports"
        self.restricted_head = self.exports / ".head.json"
        self.public = self.root / "public"
        self.revisions = self.public / "revisions"
        self.public_head = self.revisions / ".head.json"
        self.staging = self.root / ".staging"
        self.lock_path = self.root / ".nbs-intake.lock"
        self.public_gid: int | None = None

    def _ensure_layout(self) -> None:
        _ensure_directory(self.root, 0o750, required_uid=self.owner_uid)
        _ensure_directory(self.restricted, 0o700, required_uid=self.owner_uid)
        _ensure_directory(
            self.restricted / "objects", 0o700, required_uid=self.owner_uid
        )
        _ensure_directory(self.objects, 0o700, required_uid=self.owner_uid)
        _ensure_directory(self.exports, 0o700, required_uid=self.owner_uid)
        public_metadata = _ensure_directory(
            self.public, 0o750, required_uid=self.owner_uid
        )
        # Production provisions this as root:seiche.  Setgid makes root-run
        # atomic publications inherit the API-readable seiche group.
        revisions_metadata = _ensure_directory(
            self.revisions, 0o2750, required_uid=self.owner_uid
        )
        expected_public_gid = revisions_metadata.st_gid
        if self.root == PRODUCTION_NBS_ROOT:
            try:
                import grp

                expected_public_gid = grp.getgrnam("seiche").gr_gid
            except (ImportError, KeyError) as exc:
                raise NBSIntegrityError(
                    "production NBS public reader group is unavailable"
                ) from exc
        if (
            public_metadata.st_gid != expected_public_gid
            or revisions_metadata.st_gid != expected_public_gid
        ):
            raise NBSIntegrityError(
                "NBS public directories do not share the reviewed reader group"
            )
        self.public_gid = expected_public_gid
        # This root-owned directory shares the store filesystem but is outside
        # both evidence scanners, so a pre-rename crash cannot poison reads.
        _ensure_directory(self.staging, 0o700, required_uid=self.owner_uid)

    def _validate_atomic_orphan(
        self,
        path: Path,
        *,
        maximum_bytes: int,
        expected_mode: int | None = None,
        destination: Path | None = None,
        kind: str,
    ) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise NBSIntegrityError(f"{kind} cannot be inspected safely") from exc
        allowed_modes = _STAGING_FILE_MODES
        if expected_mode is not None:
            allowed_modes = frozenset({0o600, expected_mode})
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self.owner_uid
            or stat.S_IMODE(metadata.st_mode) not in allowed_modes
            or not 0 <= metadata.st_size <= maximum_bytes
            or metadata.st_nlink not in {1, 2}
        ):
            raise NBSIntegrityError(f"{kind} has unsafe metadata")
        if destination is None:
            if metadata.st_nlink != 1:
                raise NBSIntegrityError(f"{kind} has an unexpected hard link")
            return
        if metadata.st_nlink == 1:
            return
        try:
            committed = destination.lstat()
        except OSError as exc:
            raise NBSIntegrityError(
                f"{kind} has no recoverable committed link"
            ) from exc
        if (
            not stat.S_ISREG(committed.st_mode)
            or committed.st_dev != metadata.st_dev
            or committed.st_ino != metadata.st_ino
            or committed.st_nlink != 2
            or committed.st_uid != self.owner_uid
            or stat.S_IMODE(committed.st_mode) != expected_mode
        ):
            raise NBSIntegrityError(f"{kind} hard-link state is not recoverable")

    def _remove_atomic_orphans(
        self,
        orphans: list[tuple[Path, int, int | None, Path | None, str]],
    ) -> None:
        for path, maximum_bytes, expected_mode, destination, kind in orphans:
            self._validate_atomic_orphan(
                path,
                maximum_bytes=maximum_bytes,
                expected_mode=expected_mode,
                destination=destination,
                kind=kind,
            )
        synced: set[Path] = set()
        for path, _maximum, _mode, _destination, _kind in orphans:
            path.unlink()
            synced.add(path.parent)
        for directory in synced:
            _fsync_directory(directory)

    @staticmethod
    def _legacy_temp_destination(
        entry: Path,
        allowed: Mapping[str, tuple[int, int]],
    ) -> tuple[Path, int, int] | None:
        for target_name, (maximum_bytes, mode) in allowed.items():
            prefix = f".{target_name}."
            if not entry.name.startswith(prefix) or not entry.name.endswith(".tmp"):
                continue
            token = entry.name[len(prefix) : -4]
            if _LEGACY_TEMP_TOKEN_RE.fullmatch(token) is not None:
                return entry.parent / target_name, maximum_bytes, mode
        return None

    def _reconcile_atomic_orphans(self) -> None:
        """Remove only controller-shaped crash remnants while holding the flock."""

        staging_entries = sorted(self.staging.iterdir(), key=lambda item: item.name)
        staging_orphans: list[tuple[Path, int, int | None, Path | None, str]] = []
        for entry in staging_entries:
            if _STAGING_NAME_RE.fullmatch(entry.name) is None:
                raise NBSIntegrityError("NBS atomic staging contains an unknown entry")
            staging_orphans.append(
                (entry, MAX_RAW_BYTES, None, None, "NBS atomic staging orphan")
            )
        self._remove_atomic_orphans(staging_orphans)

        legacy: list[tuple[Path, int, int | None, Path | None, str]] = []

        for entry in self.exports.iterdir():
            target = self._legacy_temp_destination(
                entry, {".head.json": (MAX_HEAD_BYTES, 0o600)}
            )
            if target is not None:
                destination, maximum, mode = target
                legacy.append(
                    (entry, maximum, mode, destination, "legacy restricted-head temp")
                )
                continue
            if (
                entry.is_symlink()
                or not entry.is_dir()
                or _EXPORT_ID_RE.fullmatch(entry.name) is None
            ):
                continue
            metadata = entry.stat()
            if (
                metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                continue
            for child in entry.iterdir():
                target = self._legacy_temp_destination(
                    child,
                    {
                        "manifest.json": (MAX_MANIFEST_BYTES, 0o600),
                        "signature.json": (MAX_SIGNATURE_BYTES, 0o600),
                    },
                )
                if target is not None:
                    destination, maximum, mode = target
                    legacy.append(
                        (child, maximum, mode, destination, "legacy export temp")
                    )

        for entry in self.revisions.iterdir():
            allowed = {".head.json": (MAX_HEAD_BYTES, 0o640)}
            if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                body = entry.name[1:-4]
                target_name, separator, token = body.rpartition(".")
                if (
                    separator
                    and _LEGACY_TEMP_TOKEN_RE.fullmatch(token) is not None
                    and target_name.endswith(".json")
                    and _EXPORT_ID_RE.fullmatch(target_name[:-5]) is not None
                ):
                    allowed[target_name] = (MAX_PUBLIC_BYTES, 0o640)
            target = self._legacy_temp_destination(entry, allowed)
            if target is not None:
                destination, maximum, mode = target
                legacy.append((entry, maximum, mode, destination, "legacy public temp"))

        for bucket in self.objects.iterdir():
            if (
                bucket.is_symlink()
                or not bucket.is_dir()
                or re.fullmatch(r"[0-9a-f]{2}", bucket.name) is None
            ):
                continue
            metadata = bucket.stat()
            if (
                metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                continue
            for entry in bucket.iterdir():
                if not entry.name.startswith(".") or not entry.name.endswith(".tmp"):
                    continue
                body = entry.name[1:-4]
                target_name, separator, token = body.rpartition(".")
                if (
                    not separator
                    or _SHA256_RE.fullmatch(target_name) is None
                    or _LEGACY_TEMP_TOKEN_RE.fullmatch(token) is None
                ):
                    continue
                legacy.append(
                    (
                        entry,
                        MAX_RAW_BYTES,
                        0o600,
                        bucket / target_name,
                        "legacy raw-object temp",
                    )
                )
        self._remove_atomic_orphans(legacy)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_layout()
        if not hasattr(os, "O_NOFOLLOW"):
            raise NBSIntegrityError("NBS intake locking requires no-follow opens")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != self.owner_uid
            ):
                raise NBSIntegrityError("NBS intake lock file is unsafe")
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._reconcile_atomic_orphans()
            yield
        finally:
            os.close(descriptor)

    def _raw_path(self, sha256: str) -> Path:
        return self.objects / sha256[:2] / sha256

    def _export_path(self, export_id: str) -> Path:
        _require_identifier(export_id, field="export_id")
        return self.exports / export_id

    def _projection_path(self, export_id: str) -> Path:
        _require_identifier(export_id, field="export_id")
        return self.revisions / f"{export_id}.json"

    def _check_raw_object(self, manifest: _ValidatedManifest) -> None:
        path = self._raw_path(manifest.raw_sha256)
        payload = _stable_read(
            path,
            maximum_bytes=MAX_RAW_BYTES,
            kind="restricted raw object",
            required_mode=0o600,
        )
        if len(payload) != manifest.raw_size_bytes or not hmac.compare_digest(
            hashlib.sha256(payload).hexdigest(), manifest.raw_sha256
        ):
            raise NBSIntegrityError("restricted raw object commitment is invalid")

    def _load_restricted_exports(
        self,
        *,
        candidate: _ValidatedManifest | None = None,
        candidate_signature: _ValidatedSignature | None = None,
        intake_time: datetime | None = None,
    ) -> tuple[dict[str, tuple[_ValidatedManifest, _ValidatedSignature]], bool]:
        observed_at = intake_time or _utc_now()
        loaded: dict[str, tuple[_ValidatedManifest, _ValidatedSignature]] = {}
        candidate_committed = False
        for directory in sorted(self.exports.iterdir(), key=lambda item: item.name):
            if directory.name == self.restricted_head.name:
                continue
            if directory.is_symlink() or not directory.is_dir():
                raise NBSIntegrityError("restricted exports contain an unsafe entry")
            export_id = _require_identifier(directory.name, field="stored export_id")
            if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise NBSIntegrityError("restricted export directory has unsafe mode")
            allowed_names = {"manifest.json", "signature.json"}
            unknown = {item.name for item in directory.iterdir()} - allowed_names
            if unknown:
                raise NBSIntegrityError("restricted export contains unknown files")
            manifest_path = directory / "manifest.json"
            signature_path = directory / "signature.json"
            if not manifest_path.exists():
                if candidate is not None and export_id == candidate.export_id:
                    if signature_path.exists() and candidate_signature is not None:
                        existing = _stable_read(
                            signature_path,
                            maximum_bytes=MAX_SIGNATURE_BYTES,
                            kind="candidate signature",
                            required_mode=0o600,
                        )
                        if not hmac.compare_digest(
                            existing, candidate_signature.canonical_bytes
                        ):
                            raise NBSConflictError("candidate signature conflicts")
                    continue
                raise NBSIntegrityError("restricted export is incomplete")
            manifest_record, manifest_bytes = _validated_json_file(
                manifest_path,
                maximum_bytes=MAX_MANIFEST_BYTES,
                mode=0o600,
                kind="restricted manifest",
            )
            manifest = _validate_manifest(
                manifest_record, canonical_bytes=manifest_bytes
            )
            _reject_future_intake_timestamp(
                manifest.knowledge_time,
                field="stored knowledge_time",
                intake_time=observed_at,
            )
            if manifest.export_id != export_id:
                raise NBSIntegrityError("restricted manifest path identity is invalid")
            if candidate is not None and export_id == candidate.export_id:
                if not hmac.compare_digest(
                    manifest.canonical_bytes, candidate.canonical_bytes
                ):
                    raise NBSConflictError("export_id is committed with other content")
                if candidate_signature is None:
                    raise NBSIntegrityError("candidate signature is unavailable")
                if signature_path.exists():
                    existing = _stable_read(
                        signature_path,
                        maximum_bytes=MAX_SIGNATURE_BYTES,
                        kind="candidate signature",
                        required_mode=0o600,
                    )
                    if not hmac.compare_digest(
                        existing, candidate_signature.canonical_bytes
                    ):
                        raise NBSConflictError("candidate signature conflicts")
                loaded[export_id] = (candidate, candidate_signature)
                candidate_committed = True
                continue
            if not signature_path.exists():
                raise NBSIntegrityError("restricted export signature is missing")
            signature_record, signature_bytes = _validated_json_file(
                signature_path,
                maximum_bytes=MAX_SIGNATURE_BYTES,
                mode=0o600,
                kind="restricted signature",
            )
            signature = _validate_signature(
                signature_record,
                manifest,
                canonical_bytes=signature_bytes,
                attest_dir=self.attest_dir,
            )
            _reject_future_intake_timestamp(
                signature.signed_at,
                field="stored signed_at",
                intake_time=observed_at,
            )
            self._check_raw_object(manifest)
            loaded[export_id] = (manifest, signature)
        return loaded, candidate_committed

    @staticmethod
    def _validate_chain(
        exports: Mapping[str, tuple[_ValidatedManifest, _ValidatedSignature]],
    ) -> str | None:
        if not exports:
            return None
        children: dict[str, list[str]] = {export_id: [] for export_id in exports}
        genesis: list[str] = []
        for export_id, (manifest, _signature) in exports.items():
            predecessor = manifest.predecessor_export_id
            if predecessor is None:
                genesis.append(export_id)
                continue
            if predecessor not in exports:
                raise NBSIntegrityError("revision predecessor is missing")
            children[predecessor].append(export_id)
        if len(genesis) != 1 or any(
            len(next_ids) > 1 for next_ids in children.values()
        ):
            raise NBSIntegrityError("revision history contains a fork")
        visited: list[str] = []
        current = genesis[0]
        previous: tuple[_ValidatedManifest, _ValidatedSignature] | None = None
        maximum_period_by_series: dict[str, str] = {}
        while True:
            if current in visited:
                raise NBSIntegrityError("revision history contains a cycle")
            visited.append(current)
            pair = exports[current]
            if previous is not None:
                prior_manifest, prior_signature = previous
                manifest, signature = pair
                if (
                    manifest.predecessor_manifest_sha256
                    != prior_manifest.manifest_sha256
                ):
                    raise NBSIntegrityError(
                        "revision predecessor content commitment does not match"
                    )
                if manifest.knowledge_time <= prior_manifest.knowledge_time:
                    raise NBSIntegrityError(
                        "knowledge_time does not advance revision history"
                    )
                if signature.signed_at <= prior_signature.signed_at:
                    raise NBSIntegrityError(
                        "signed_at does not advance revision history"
                    )
            manifest = pair[0]
            for series_id, period in manifest.max_period_by_series.items():
                if period < maximum_period_by_series.get(series_id, period):
                    raise NBSIntegrityError("revision history rolls a series backward")
                maximum_period_by_series[series_id] = max(
                    period, maximum_period_by_series.get(series_id, period)
                )
            next_ids = children[current]
            if not next_ids:
                break
            previous = pair
            current = next_ids[0]
        if len(visited) != len(exports):
            raise NBSIntegrityError("revision history is disconnected")
        return current

    @staticmethod
    def _restricted_head_receipt(
        exports: Mapping[str, tuple[_ValidatedManifest, _ValidatedSignature]],
        head: str | None,
    ) -> dict[str, object] | None:
        if head is None:
            return None
        manifest, signature = exports[head]
        projection_sha256 = signature.record["public_projection_sha256"]
        if not isinstance(projection_sha256, str):
            raise NBSIntegrityError("head projection commitment is malformed")
        return _head_receipt(
            sequence=len(exports),
            revision_id=head,
            manifest_sha256=manifest.manifest_sha256,
            public_projection_sha256=projection_sha256,
            signature_sha256=hashlib.sha256(signature.canonical_bytes).hexdigest(),
        )

    @staticmethod
    def _public_head_receipt(
        contexts: Mapping[str, tuple[NBSMacroContext, datetime]],
        head: str,
    ) -> dict[str, object]:
        record = contexts[head][0].record
        provenance = record["provenance"]
        attestation = record["attestation"]
        if not isinstance(provenance, Mapping) or not isinstance(attestation, Mapping):
            raise NBSIntegrityError("public head commitments are malformed")
        return _head_receipt(
            sequence=len(contexts),
            revision_id=head,
            manifest_sha256=str(provenance["manifest_sha256"]),
            public_projection_sha256=str(attestation["public_projection_sha256"]),
            signature_sha256=hashlib.sha256(
                _canonical_json_bytes(attestation)
            ).hexdigest(),
        )

    def _require_public_matches_restricted_head(
        self,
        expected_head: str | None,
    ) -> None:
        if expected_head is None:
            if tuple(self.revisions.iterdir()):
                raise NBSIntegrityError(
                    "public revisions exist without restricted evidence"
                )
            return
        context = self.load_public_context_strict()
        if context.revision_id != expected_head:
            raise NBSIntegrityError("public and restricted revision heads do not match")

    @staticmethod
    def _audit_entries(
        path: Path, *, kind: str
    ) -> dict[str, tuple[Path, os.stat_result]]:
        """Inspect one directory without following any member symlink."""

        entries: dict[str, tuple[Path, os.stat_result]] = {}
        try:
            selected = sorted(path.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise NBSIntegrityError(f"{kind} cannot be enumerated safely") from exc
        for entry in selected:
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise NBSIntegrityError(
                    f"{kind} member cannot be inspected safely"
                ) from exc
            entries[entry.name] = (entry, metadata)
        return entries

    def _audit_directory(
        self,
        path: Path,
        *,
        kind: str,
        mode: int,
        gid: int | None = None,
    ) -> tuple[os.stat_result, dict[str, tuple[Path, os.stat_result]]]:
        metadata = _directory_metadata(
            path,
            kind=kind,
            required_mode=mode,
            required_uid=self.owner_uid,
        )
        if gid is not None and metadata.st_gid != gid:
            raise NBSIntegrityError(f"{kind} has an unexpected group")
        return metadata, self._audit_entries(path, kind=kind)

    @staticmethod
    def _require_audit_members(
        entries: Mapping[str, tuple[Path, os.stat_result]],
        expected: set[str],
        *,
        kind: str,
    ) -> None:
        if set(entries) != expected:
            raise NBSIntegrityError(f"{kind} members are not exact")

    def audit_store_strict(self) -> str:
        """Read-only audit of the complete restricted and public evidence store.

        This recovery primitive deliberately never takes the writer lock or
        repairs storage.  A safely provisioned but never-onboarded tree returns
        ``not_onboarded``; a complete, mutually committed history returns
        ``verified_head``.  Every other state fails closed.
        """

        _root_metadata, root_entries = self._audit_directory(
            self.root,
            kind="NBS evidence root",
            mode=0o750,
        )
        minimal_root = {"restricted", "public"}
        initialized_root = {
            ".nbs-intake.lock",
            ".staging",
            "restricted",
            "public",
        }
        root_names = set(root_entries)
        if root_names == minimal_root:
            initialized = False
        elif root_names == initialized_root:
            initialized = True
        else:
            raise NBSIntegrityError("NBS evidence root members are not exact")

        _restricted_metadata, restricted_entries = self._audit_directory(
            self.restricted,
            kind="NBS restricted store",
            mode=0o700,
        )
        public_metadata, public_entries = self._audit_directory(
            self.public,
            kind="NBS public store",
            mode=0o750,
        )
        self._require_audit_members(
            public_entries,
            {"revisions"},
            kind="NBS public store",
        )
        _revisions_metadata, revision_entries = self._audit_directory(
            self.revisions,
            kind="NBS public revision store",
            mode=0o2750,
            gid=public_metadata.st_gid,
        )

        if not initialized:
            self._require_audit_members(
                restricted_entries,
                set(),
                kind="unonboarded NBS restricted store",
            )
            self._require_audit_members(
                revision_entries,
                set(),
                kind="unonboarded NBS public revision store",
            )
            try:
                self.load_public_context_strict()
            except NBSNotOnboardedError:
                return "not_onboarded"
            raise NBSIntegrityError("unonboarded NBS store unexpectedly has a head")

        _staging_metadata, staging_entries = self._audit_directory(
            self.staging,
            kind="NBS atomic staging store",
            mode=0o700,
        )
        self._require_audit_members(
            staging_entries,
            set(),
            kind="NBS atomic staging store",
        )
        lock_path, lock_metadata = root_entries[".nbs-intake.lock"]
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != self.owner_uid
            or lock_metadata.st_nlink != 1
            or lock_metadata.st_size != 0
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise NBSIntegrityError("NBS intake lock file is unsafe")
        try:
            visible_lock = lock_path.lstat()
        except OSError as exc:
            raise NBSIntegrityError("NBS intake lock file changed") from exc
        if (
            visible_lock.st_dev,
            visible_lock.st_ino,
            visible_lock.st_mode,
            visible_lock.st_nlink,
            visible_lock.st_size,
            visible_lock.st_uid,
        ) != (
            lock_metadata.st_dev,
            lock_metadata.st_ino,
            lock_metadata.st_mode,
            lock_metadata.st_nlink,
            lock_metadata.st_size,
            lock_metadata.st_uid,
        ):
            raise NBSIntegrityError("NBS intake lock file changed")

        self._require_audit_members(
            restricted_entries,
            {"objects", "exports"},
            kind="NBS restricted store",
        )
        _objects_metadata, object_entries = self._audit_directory(
            self.restricted / "objects",
            kind="NBS restricted object store",
            mode=0o700,
        )
        self._require_audit_members(
            object_entries,
            {"sha256"},
            kind="NBS restricted object store",
        )
        _sha_metadata, bucket_entries = self._audit_directory(
            self.objects,
            kind="NBS restricted SHA-256 store",
            mode=0o700,
        )
        _exports_metadata, export_entries = self._audit_directory(
            self.exports,
            kind="NBS restricted export store",
            mode=0o700,
        )
        for name, (path, metadata) in export_entries.items():
            if name == self.restricted_head.name:
                continue
            _require_identifier(name, field="stored export_id")
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise NBSIntegrityError(
                    "restricted export directory has unsafe metadata"
                )
            export_members = self._audit_entries(
                path,
                kind="restricted export",
            )
            self._require_audit_members(
                export_members,
                {"manifest.json", "signature.json"},
                kind="restricted export",
            )

        exports, candidate_committed = self._load_restricted_exports()
        if candidate_committed:
            raise NBSIntegrityError("read-only NBS audit observed a candidate")
        restricted_head = self._validate_chain(exports)
        expected_head_receipt = self._restricted_head_receipt(
            exports,
            restricted_head,
        )
        stored_restricted_head = _read_head_receipt(
            self.restricted_head,
            mode=0o600,
            kind="restricted head receipt",
            required_uid=self.owner_uid,
        )
        _require_expected_head(
            stored_restricted_head,
            expected_head_receipt,
            kind="restricted head receipt",
        )

        referenced_objects = {
            manifest.raw_sha256 for manifest, _signature in exports.values()
        }
        expected_buckets = {sha256[:2] for sha256 in referenced_objects}
        if set(bucket_entries) != expected_buckets:
            raise NBSIntegrityError(
                "restricted raw object bucket set does not match manifests"
            )
        observed_objects: set[str] = set()
        for bucket, (path, metadata) in bucket_entries.items():
            if (
                re.fullmatch(r"[0-9a-f]{2}", bucket) is None
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise NBSIntegrityError(
                    "restricted raw object bucket has unsafe metadata"
                )
            members = self._audit_entries(path, kind="restricted raw object bucket")
            expected_members = {
                sha256 for sha256 in referenced_objects if sha256.startswith(bucket)
            }
            if set(members) != expected_members:
                raise NBSIntegrityError(
                    "restricted raw object set does not match manifests"
                )
            for sha256, (_object_path, object_metadata) in members.items():
                if (
                    _SHA256_RE.fullmatch(sha256) is None
                    or not stat.S_ISREG(object_metadata.st_mode)
                    or object_metadata.st_uid != self.owner_uid
                    or object_metadata.st_nlink != 1
                    or stat.S_IMODE(object_metadata.st_mode) != 0o600
                ):
                    raise NBSIntegrityError("restricted raw object has unsafe metadata")
                observed_objects.add(sha256)
        if observed_objects != referenced_objects:
            raise NBSIntegrityError(
                "restricted raw object set does not match manifests"
            )

        expected_revision_names = {f"{export_id}.json" for export_id in exports}
        if expected_head_receipt is not None:
            expected_revision_names.add(self.public_head.name)
        self._require_audit_members(
            revision_entries,
            expected_revision_names,
            kind="NBS public revision store",
        )
        for export_id, (manifest, signature) in exports.items():
            expected_projection = _canonical_json_bytes(
                _public_record(manifest, signature)
            )
            observed_projection = _stable_read(
                self._projection_path(export_id),
                maximum_bytes=MAX_PUBLIC_BYTES,
                kind="public projection",
                required_mode=0o640,
                required_uid=self.owner_uid,
                required_gid=public_metadata.st_gid,
            )
            if not hmac.compare_digest(observed_projection, expected_projection):
                raise NBSIntegrityError(
                    "public projection does not match restricted evidence"
                )

        stored_public_head = _read_head_receipt(
            self.public_head,
            mode=0o640,
            kind="public head receipt",
            required_uid=self.owner_uid,
            required_gid=public_metadata.st_gid,
        )
        _require_expected_head(
            stored_public_head,
            expected_head_receipt,
            kind="public head receipt",
        )
        if not exports:
            try:
                self.load_public_context_strict()
            except NBSNotOnboardedError:
                return "not_onboarded"
            raise NBSIntegrityError("empty NBS store unexpectedly has a public head")

        public_context = self.load_public_context_strict()
        if public_context.revision_id != restricted_head:
            raise NBSIntegrityError("public and restricted revision heads do not match")
        return "verified_head"

    def ingest(
        self,
        manifest_path: str | os.PathLike[str],
        signature_path: str | os.PathLike[str],
        raw_path: str | os.PathLike[str],
    ) -> NBSMacroContext:
        """Authorize production, then verify and append one signed export."""

        with _production_ingest_authorization(self.root):
            return self._ingest_authorized(
                manifest_path,
                signature_path,
                raw_path,
            )

    def _ingest_authorized(
        self,
        manifest_path: str | os.PathLike[str],
        signature_path: str | os.PathLike[str],
        raw_path: str | os.PathLike[str],
    ) -> NBSMacroContext:
        """Verify and atomically append one signed export or exact retry."""

        intake_time = _utc_now()
        manifest_bytes = _stable_read(
            manifest_path,
            maximum_bytes=MAX_MANIFEST_BYTES,
            kind="input manifest",
        )
        manifest_record = _decode_canonical_json(manifest_bytes, kind="manifest")
        manifest = _validate_manifest(manifest_record, canonical_bytes=manifest_bytes)
        _reject_future_intake_timestamp(
            manifest.knowledge_time,
            field="knowledge_time",
            intake_time=intake_time,
        )
        signature_bytes = _stable_read(
            signature_path,
            maximum_bytes=MAX_SIGNATURE_BYTES,
            kind="input signature",
        )
        signature_record = _decode_canonical_json(
            signature_bytes, kind="signature sidecar"
        )
        signature = _validate_signature(
            signature_record,
            manifest,
            canonical_bytes=signature_bytes,
            attest_dir=self.attest_dir,
        )
        _reject_future_intake_timestamp(
            signature.signed_at,
            field="signed_at",
            intake_time=intake_time,
        )
        raw_bytes = _stable_read(
            raw_path, maximum_bytes=MAX_RAW_BYTES, kind="input raw evidence"
        )
        if Path(raw_path).name != manifest.raw_filename:
            raise NBSIntegrityError("raw evidence filename does not match manifest")
        if len(raw_bytes) != manifest.raw_size_bytes or not hmac.compare_digest(
            hashlib.sha256(raw_bytes).hexdigest(), manifest.raw_sha256
        ):
            raise NBSIntegrityError(
                "raw evidence does not match its manifest commitment"
            )
        _verify_raw_csv(manifest, raw_bytes)

        public_record = _public_record(manifest, signature)
        public_bytes = _canonical_json_bytes(public_record)
        if len(public_bytes) > MAX_PUBLIC_BYTES:
            raise NBSIntegrityError("public projection exceeds its size bound")

        with _INGEST_LOCK, self._locked():
            if self.public_gid is None:
                raise NBSIntegrityError("NBS public reader group is unavailable")
            exports, committed = self._load_restricted_exports(
                candidate=manifest,
                candidate_signature=signature,
                intake_time=intake_time,
            )
            head = self._validate_chain(exports)
            expected_restricted_head = self._restricted_head_receipt(exports, head)
            actual_restricted_head = _read_head_receipt(
                self.restricted_head,
                mode=0o600,
                kind="restricted head receipt",
            )
            if committed:
                if head != manifest.export_id:
                    raise NBSConflictError(
                        "only the current head permits an exact retry"
                    )
                prior_exports = dict(exports)
                prior_exports.pop(manifest.export_id)
                prior_head = self._validate_chain(prior_exports)
                expected_prior_head = self._restricted_head_receipt(
                    prior_exports, prior_head
                )
                if actual_restricted_head not in (
                    None,
                    expected_prior_head,
                    expected_restricted_head,
                ):
                    raise NBSIntegrityError(
                        "restricted head receipt is not a recoverable retry state"
                    )
            elif manifest.predecessor_export_id != head:
                raise NBSConflictError("manifest predecessor is not the current head")
            else:
                _require_expected_head(
                    actual_restricted_head,
                    expected_restricted_head,
                    kind="restricted head receipt",
                )
                self._require_public_matches_restricted_head(head)
            if not committed:
                proposed = dict(exports)
                proposed[manifest.export_id] = (manifest, signature)
                if self._validate_chain(proposed) != manifest.export_id:
                    raise NBSConflictError(
                        "manifest does not create one advancing head"
                    )

            raw_parent = self._raw_path(manifest.raw_sha256).parent
            _ensure_directory(raw_parent, 0o700, required_uid=self.owner_uid)
            _atomic_publish(
                self._raw_path(manifest.raw_sha256),
                raw_bytes,
                mode=0o600,
                kind="restricted raw object",
                staging_dir=self.staging,
            )
            export_dir = self._export_path(manifest.export_id)
            _ensure_directory(export_dir, 0o700, required_uid=self.owner_uid)
            _atomic_publish(
                export_dir / "manifest.json",
                manifest.canonical_bytes,
                mode=0o600,
                kind="restricted manifest",
                staging_dir=self.staging,
            )
            _atomic_publish(
                export_dir / "signature.json",
                signature.canonical_bytes,
                mode=0o600,
                kind="restricted signature",
                staging_dir=self.staging,
            )
            _atomic_publish(
                self._projection_path(manifest.export_id),
                public_bytes,
                mode=0o640,
                kind="public projection",
                staging_dir=self.staging,
                gid=self.public_gid,
            )
            complete, _ = self._load_restricted_exports(intake_time=intake_time)
            complete_head = self._validate_chain(complete)
            if complete_head != manifest.export_id:
                raise NBSIntegrityError(
                    "committed revision did not become the unique head"
                )
            complete_receipt = self._restricted_head_receipt(complete, complete_head)
            if complete_receipt is None:
                raise NBSIntegrityError("committed revision has no head receipt")
            receipt_bytes = _canonical_json_bytes(complete_receipt)
            _atomic_replace(
                self.restricted_head,
                receipt_bytes,
                mode=0o600,
                kind="restricted head receipt",
                staging_dir=self.staging,
            )
            _atomic_replace(
                self.public_head,
                receipt_bytes,
                mode=0o640,
                kind="public head receipt",
                staging_dir=self.staging,
                gid=self.public_gid,
            )
            loaded = self.load_public_context_strict()
            if loaded.revision_id != manifest.export_id:
                raise NBSIntegrityError("published revision did not become public head")
        return _context_from_public_record(public_record)

    def _validate_public_record(
        self, record: Mapping[str, object]
    ) -> tuple[NBSMacroContext, datetime]:
        observed_at = _utc_now()
        _require_exact_fields(
            record,
            {
                "schema",
                "available",
                "evidence_status",
                "dataset",
                "revision_id",
                "predecessor_revision_id",
                "predecessor_manifest_sha256",
                "knowledge_time",
                "publisher",
                "source_url",
                "publication_policy",
                "values_published",
                "series",
                "provenance",
                "caveats",
                "attestation",
            },
            kind="public projection",
        )
        if (
            record["schema"] != NBS_PUBLIC_SCHEMA
            or record["available"] is not True
            or record["evidence_status"] != "restricted"
            or record["dataset"] != NBS_DATASET
            or record["publisher"] != NBS_PUBLISHER
            or record["source_url"] != NBS_BROWSER_SOURCE_URL
            or record["publication_policy"] != dict(_PUBLICATION_POLICY)
        ):
            raise NBSIntegrityError("public projection policy is invalid")
        revision_id = _require_identifier(record["revision_id"], field="revision_id")
        predecessor = record["predecessor_revision_id"]
        predecessor_manifest_sha256 = record["predecessor_manifest_sha256"]
        if predecessor is not None:
            predecessor = _require_identifier(
                predecessor, field="predecessor_revision_id"
            )
            if (
                not isinstance(predecessor_manifest_sha256, str)
                or _SHA256_RE.fullmatch(predecessor_manifest_sha256) is None
            ):
                raise NBSIntegrityError(
                    "public predecessor manifest commitment is malformed"
                )
        elif predecessor_manifest_sha256 is not None:
            raise NBSIntegrityError(
                "public genesis cannot carry a predecessor manifest commitment"
            )
        knowledge_time = _parse_timestamp(
            record["knowledge_time"], field="knowledge_time"
        )
        _reject_future_intake_timestamp(
            knowledge_time,
            field="public knowledge_time",
            intake_time=observed_at,
        )
        series = record["series"]
        if not isinstance(series, list) or not series:
            raise NBSIntegrityError("public series metadata is unavailable")
        observed_ids: list[str] = []
        for source in series:
            if not isinstance(source, dict):
                raise NBSIntegrityError("public series metadata is malformed")
            series_id = source.get("series_id")
            if not isinstance(series_id, str) or series_id not in NBS_SERIES_BINDINGS:
                raise NBSIntegrityError("public series metadata is not release-pinned")
            source_binding = {
                key: value
                for key, value in source.items()
                if key != "value_publication"
            }
            _validate_source_binding(
                source_binding,
                NBS_SERIES_BINDINGS[series_id],
                knowledge_time=knowledge_time,
            )
            expected = _json_copy(source_binding)
            expected["value_publication"] = "withheld_pending_rights_review"
            if source != expected:
                raise NBSIntegrityError("metadata-only public source binding drifted")
            observed_ids.append(series_id)
        if observed_ids != sorted(set(observed_ids)):
            raise NBSIntegrityError("public series metadata is not unique and sorted")
        if record["values_published"] is not False:
            raise NBSIntegrityError("public value publication gate is inconsistent")
        if (
            not isinstance(record["caveats"], list)
            or record["caveats"] != _public_payload_from_public(record)["caveats"]
        ):
            raise NBSIntegrityError("public caveats are not code-owned")
        provenance = record["provenance"]
        if not isinstance(provenance, dict):
            raise NBSIntegrityError("public provenance is malformed")
        _require_exact_fields(
            provenance,
            {"manifest_sha256", "owner_attestation"},
            kind="public provenance",
        )
        if (
            not isinstance(provenance["manifest_sha256"], str)
            or _SHA256_RE.fullmatch(provenance["manifest_sha256"]) is None
            or provenance["owner_attestation"] != "ed25519"
        ):
            raise NBSIntegrityError("public provenance commitments are invalid")
        attestation = record["attestation"]
        if not isinstance(attestation, dict):
            raise NBSIntegrityError("public attestation is malformed")
        _require_exact_fields(
            attestation,
            {
                "schema",
                "algorithm",
                "domain",
                "export_id",
                "signer_key_id",
                "signed_at",
                "manifest_sha256",
                "public_projection_sha256",
                "signature",
            },
            kind="public attestation",
        )
        unsigned = {
            key: _json_copy(value)
            for key, value in record.items()
            if key != "attestation"
        }
        projection_hash = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
        if (
            attestation["schema"] != NBS_SIGNATURE_SCHEMA
            or attestation["algorithm"] != "ed25519"
            or attestation["domain"] != NBS_SIGNATURE_DOMAIN
            or attestation["export_id"] != revision_id
            or attestation["manifest_sha256"] != provenance["manifest_sha256"]
            or attestation["public_projection_sha256"] != projection_hash
        ):
            raise NBSIntegrityError("public attestation commitments do not match")
        signature_hex = attestation["signature"]
        signer = attestation["signer_key_id"]
        claim = {key: value for key, value in attestation.items() if key != "signature"}
        if not isinstance(signature_hex, str) or not isinstance(signer, str):
            raise NBSIntegrityError("public Ed25519 attestation is malformed")
        try:
            verify_trusted_ed25519_signature(
                encode_signature_claim(claim),
                signature_hex,
                signer,
                attest_dir=self.attest_dir,
            )
        except ValueError as exc:
            raise NBSIntegrityError(
                "public attestation is not trusted and valid"
            ) from exc
        signed_at = _parse_timestamp(attestation["signed_at"], field="signed_at")
        if signed_at < knowledge_time:
            raise NBSIntegrityError("public signed_at precedes knowledge_time")
        _reject_future_intake_timestamp(
            signed_at,
            field="public signed_at",
            intake_time=observed_at,
        )
        return _context_from_public_record(record), signed_at

    def load_public_context_strict(self) -> NBSMacroContext:
        """Return the unique verified public head, raising on any store drift."""

        public_metadata = _directory_metadata(
            self.public,
            kind="public NBS store",
            required_mode=0o750,
        )
        revisions_metadata = _directory_metadata(
            self.revisions,
            kind="public revision store",
            required_mode=0o2750,
            required_uid=public_metadata.st_uid,
        )
        stored_head = _read_head_receipt(
            self.public_head,
            mode=0o640,
            kind="public head receipt",
            required_uid=revisions_metadata.st_uid,
            required_gid=revisions_metadata.st_gid,
        )
        contexts: dict[str, tuple[NBSMacroContext, datetime]] = {}
        for path in sorted(self.revisions.iterdir(), key=lambda item: item.name):
            if path.name == self.public_head.name:
                continue
            if path.suffix != ".json" or path.name.startswith("."):
                raise NBSIntegrityError(
                    "public revision store contains an unknown entry"
                )
            record, _raw = _validated_json_file(
                path,
                maximum_bytes=MAX_PUBLIC_BYTES,
                mode=0o640,
                kind="public projection",
                required_uid=revisions_metadata.st_uid,
                required_gid=revisions_metadata.st_gid,
            )
            context, signed_at = self._validate_public_record(record)
            if path.name != f"{context.revision_id}.json":
                raise NBSIntegrityError("public projection path identity is invalid")
            if context.revision_id in contexts:
                raise NBSIntegrityError("public revision identity is duplicated")
            contexts[context.revision_id] = (context, signed_at)
        if not contexts:
            if stored_head is None:
                raise NBSNotOnboardedError(
                    "public revision store has not been onboarded"
                )
            raise NBSIntegrityError("public head exists without any revisions")
        children: dict[str, list[str]] = {revision_id: [] for revision_id in contexts}
        genesis: list[str] = []
        for revision_id, (context, _signed_at) in contexts.items():
            predecessor = context.record["predecessor_revision_id"]
            if predecessor is None:
                genesis.append(revision_id)
            elif predecessor not in contexts:
                raise NBSIntegrityError("public predecessor is missing")
            else:
                predecessor_hash = context.record["predecessor_manifest_sha256"]
                actual_predecessor_hash = contexts[predecessor][0].record["provenance"][
                    "manifest_sha256"
                ]
                if predecessor_hash != actual_predecessor_hash:
                    raise NBSIntegrityError(
                        "public predecessor content commitment does not match"
                    )
                children[predecessor].append(revision_id)
        if len(genesis) != 1 or any(len(items) > 1 for items in children.values()):
            raise NBSIntegrityError("public revision history contains a fork")
        visited: list[str] = []
        current = genesis[0]
        previous_signed_at: datetime | None = None
        previous_knowledge_time: datetime | None = None
        while True:
            visited.append(current)
            signed_at = contexts[current][1]
            knowledge_time = _parse_timestamp(
                contexts[current][0].record["knowledge_time"],
                field="knowledge_time",
            )
            if previous_signed_at is not None and signed_at <= previous_signed_at:
                raise NBSIntegrityError("public signature chronology does not advance")
            if (
                previous_knowledge_time is not None
                and knowledge_time <= previous_knowledge_time
            ):
                raise NBSIntegrityError("public knowledge chronology does not advance")
            next_ids = children[current]
            if not next_ids:
                break
            previous_signed_at = signed_at
            previous_knowledge_time = knowledge_time
            current = next_ids[0]
        if len(visited) != len(contexts):
            raise NBSIntegrityError("public revision history is disconnected")
        expected_head = NBSIntakeStore._public_head_receipt(contexts, current)
        _require_expected_head(
            stored_head,
            expected_head,
            kind="public head receipt",
        )
        return contexts[current][0]

    def load_public_context(self) -> NBSPublicContext:
        """Fail closed to a typed, non-diagnostic unavailable projection."""

        try:
            return self.load_public_context_strict()
        except (NBSIntakeError, OSError, TypeError, ValueError):
            return NBSContextUnavailable()


def _public_payload_from_public(record: Mapping[str, object]) -> dict[str, object]:
    """Return code-owned fields used to validate a stored public envelope."""

    return {
        "caveats": [
            "Owner-attested browser export; not an NBS digital signature.",
            "Metadata-only macro context; excluded from scoring and CN-CNY gauge roles.",
            "Raw evidence and observation values remain restricted; public revision commitments are retained.",
        ]
    }


def ingest_signed_export(
    manifest_path: str | os.PathLike[str],
    signature_path: str | os.PathLike[str],
    raw_path: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str],
    attest_dir: str | None = None,
) -> NBSMacroContext:
    """Convenience wrapper for one offline signed-export intake."""

    return NBSIntakeStore(root, attest_dir=attest_dir).ingest(
        manifest_path, signature_path, raw_path
    )


class _NBSPublicContextReader:
    """Read-only view that constructs only the operator-published directory."""

    def __init__(
        self,
        public_dir: str | os.PathLike[str],
        *,
        attest_dir: str | None = None,
    ) -> None:
        self.public = Path(os.path.abspath(os.fspath(public_dir)))
        self.revisions = self.public / "revisions"
        self.public_head = self.revisions / ".head.json"
        self.attest_dir = attest_dir

    _validate_public_record = NBSIntakeStore._validate_public_record
    load_public_context_strict = NBSIntakeStore.load_public_context_strict
    load_public_context = NBSIntakeStore.load_public_context


def load_public_context_from_directory(
    public_dir: str | os.PathLike[str], *, attest_dir: str | None = None
) -> NBSPublicContext:
    """Load only ``<public_dir>/revisions``; never construct a restricted path."""

    return _NBSPublicContextReader(
        public_dir, attest_dir=attest_dir
    ).load_public_context()


def load_public_context_from_public_dir(
    public_dir: str | os.PathLike[str], *, attest_dir: str | None = None
) -> NBSPublicContext:
    """Explicit spelling for API/MCP callers with only a public-dir grant."""

    return load_public_context_from_directory(public_dir, attest_dir=attest_dir)


def load_public_context_strict_from_public_dir(
    public_dir: str | os.PathLike[str], *, attest_dir: str | None = None
) -> NBSMacroContext:
    """Strictly load only a public revision store, preserving typed failures."""

    return _NBSPublicContextReader(
        public_dir, attest_dir=attest_dir
    ).load_public_context_strict()


def resolve_public_context(
    public_dir: str | os.PathLike[str] | None,
    *,
    attest_dir: str | None = None,
) -> dict[str, object]:
    """Resolve a verified signed head or the pure structural catalog fallback.

    The caller owns configuration lookup and passes the directory explicitly;
    this function never reads environment variables or restricted paths.
    """

    if public_dir is None:
        return nbs_public_catalog()
    context = load_public_context_from_public_dir(public_dir, attest_dir=attest_dir)
    if isinstance(context, NBSMacroContext) and context.available:
        return context.to_dict()
    return nbs_public_catalog()


def load_public_context(
    root: str | os.PathLike[str], *, attest_dir: str | None = None
) -> NBSPublicContext:
    """Load the verified public head or a safe typed unavailable projection."""

    return NBSIntakeStore(root, attest_dir=attest_dir).load_public_context()
