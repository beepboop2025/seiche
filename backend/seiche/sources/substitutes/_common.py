"""Shared contract for the free substitute source adapters.

A substitute is another publisher's free, republishable series that stands in
for a FRED series Seiche cannot republish. It is labelled, never merged: a
served row always names the FRED id it stands in for and whether it is a
"close substitute" (the same concept with a different method, cadence or
publisher) or a "proxy" (a related signal for the same analytical role).

Every adapter here follows the acquisition discipline of the direct Board
PRATES adapter (``seiche.sources.fed_prates``):

* one fixed HTTPS origin per document, no redirects, identity transport
  encoding, a declared and a streamed byte bound, and a total deadline;
* the SHA-256 of the raw bytes travels with every row;
* units, publisher series identities, duplicate dates and non-finite values
  are checked, and any change fails the run closed instead of rescaling;
* rows dated after the acquisition day are excluded, and knowledge time is
  the actual acquisition time, never an inferred publication clock.
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import io
import json
import math
import re
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

USER_AGENT = "LiquiLensResearch/1.0 (+https://liquilens.in/about/)"
SUBSTITUTE_CLASSES = frozenset({"close substitute", "proxy"})
REDISTRIBUTION_STATUSES = frozenset(
    {"allowed", "derived_only", "metadata_only", "prohibited"}
)
TOTAL_TIMEOUT_SECONDS = 120.0
# A substitute must never be a FRED copy under another name.
FORBIDDEN_HOSTS = frozenset(
    {"fred.stlouisfed.org", "api.stlouisfed.org", "alfred.stlouisfed.org"}
)

Clock = Callable[[], datetime]

_DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Acquisition:
    """Raw bytes exactly as received from one fixed publisher URL."""

    source_url: str
    media_type: str
    payload: bytes
    acquired_at: datetime
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "acquired_at", _aware_utc(self.acquired_at, "acquisition time")
        )
        object.__setattr__(self, "sha256", hashlib.sha256(self.payload).hexdigest())


@dataclass(frozen=True, slots=True)
class MeasureSpec:
    """One publisher measure an adapter emits, with its pinned unit."""

    label: str
    unit: str
    frequency: str
    description: str


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One validated publisher value, before substitute labelling.

    This is adapter output, not a served row. Only the substitutes registry
    turns it into a :class:`SubstituteRow`, which carries the FRED id it
    stands in for and its substitute class.
    """

    substitute_id: str
    measure: str
    event_date: date
    period: str
    value: Decimal
    unit: str
    frequency: str
    source: str
    publisher: str
    source_url: str
    terms_url: str
    knowledge_time: datetime
    raw_documents: tuple[tuple[str, str], ...]
    row_evidence: bytes
    row_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "knowledge_time", _aware_utc(self.knowledge_time, "knowledge time")
        )
        if not self.value.is_finite():
            raise ValueError(f"{self.substitute_id} {self.measure}: value nonfinite")
        if not self.raw_documents:
            raise ValueError(f"{self.substitute_id}: raw document hashes missing")
        for url, _digest in self.raw_documents:
            _reject_forbidden_host(url)
        _reject_forbidden_host(self.source_url)
        object.__setattr__(
            self, "row_sha256", hashlib.sha256(self.row_evidence).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class SubstituteRow:
    """A servable substitute value. It can never pass for the FRED original."""

    substitute_for: str
    substitute_class: str
    substitute_id: str
    measure: str
    label: str
    notice: str
    event_date: date
    period: str
    value: Decimal
    unit: str
    frequency: str
    source: str
    publisher: str
    source_url: str
    terms_url: str
    attribution: str
    redistribution_status: str
    knowledge_time: datetime
    raw_documents: tuple[tuple[str, str], ...]
    row_sha256: str

    def __post_init__(self) -> None:
        if self.substitute_class not in SUBSTITUTE_CLASSES:
            raise ValueError(f"unknown substitute class {self.substitute_class!r}")
        if not re.fullmatch(r"[A-Z0-9]{3,20}", self.substitute_for):
            raise ValueError(f"invalid FRED id {self.substitute_for!r}")
        if self.redistribution_status not in REDISTRIBUTION_STATUSES:
            raise ValueError(
                f"unknown redistribution status {self.redistribution_status!r}"
            )
        if self.substitute_for not in self.notice or self.substitute_class not in self.notice:
            raise ValueError("substitute notice must name the FRED id and class")
        if not self.attribution or not self.terms_url or not self.publisher:
            raise ValueError("substitute rows need publisher, terms and attribution")
        _reject_forbidden_host(self.source_url)

    def as_dict(self) -> dict[str, object]:
        """JSON-safe form. The value keeps its exact decimal text."""

        return {
            "substitute_for": self.substitute_for,
            "substitute_class": self.substitute_class,
            "substitute_id": self.substitute_id,
            "measure": self.measure,
            "label": self.label,
            "notice": self.notice,
            "event_date": self.event_date.isoformat(),
            "period": self.period,
            "value": str(self.value),
            "unit": self.unit,
            "frequency": self.frequency,
            "source": self.source,
            "publisher": self.publisher,
            "source_url": self.source_url,
            "terms_url": self.terms_url,
            "attribution": self.attribution,
            "redistribution_status": self.redistribution_status,
            "knowledge_time": self.knowledge_time.isoformat(),
            "raw_documents": [
                {"url": url, "sha256": digest} for url, digest in self.raw_documents
            ],
            "row_sha256": self.row_sha256,
        }


def _reject_forbidden_host(url: str) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    if host in FORBIDDEN_HOSTS:
        raise ValueError("a substitute row may not cite a FRED copy")


def validate_fixed_url(url: str) -> None:
    """Accept only a plain HTTPS URL on the default port."""

    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid source URL {url!r}") from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError(f"source URL is not a plain HTTPS origin: {url!r}")
    _reject_forbidden_host(url)


async def fetch_fixed(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    label: str,
    clock: Clock | None = None,
    accept: str = "*/*",
) -> Acquisition:
    """GET one fixed publisher URL with byte, time and redirect bounds.

    The acquisition time is read after the last byte arrives, so it is the
    moment Seiche actually held the document.
    """

    validate_fixed_url(url)
    headers = {
        "Accept": accept,
        "Accept-Encoding": "identity",
        "User-Agent": USER_AGENT,
    }
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with client.stream(
                "GET", url, headers=headers, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise ValueError(f"{label} HTTP {response.status_code}")
                if str(response.url) != url:
                    raise ValueError(f"{label} source URL changed")
                encoding = response.headers.get("content-encoding", "").strip().lower()
                if encoding not in {"", "identity"}:
                    raise ValueError(f"{label} transport encoding changed")
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_bytes = int(declared)
                    except ValueError as exc:
                        raise ValueError(f"{label} content length invalid") from exc
                    if not 0 <= declared_bytes <= max_bytes:
                        raise ValueError(f"{label} exceeds its {max_bytes} byte bound")
                payload = bytearray()
                async for chunk in response.aiter_raw():
                    payload.extend(chunk)
                    if len(payload) > max_bytes:
                        raise ValueError(f"{label} exceeds its {max_bytes} byte bound")
                media_type = (
                    response.headers.get("content-type", "application/octet-stream")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
    except TimeoutError as exc:
        raise ValueError(f"{label} exceeded its response deadline") from exc
    acquired_at = (clock or utc_now)()
    return Acquisition(url, media_type, bytes(payload), acquired_at)


def require_document(
    document: Acquisition, *, source_url: str, max_bytes: int, label: str
) -> None:
    """Refuse a document from any other URL, or one over its byte bound."""

    if document.source_url != source_url:
        raise ValueError(f"unrecognized {label} source")
    if len(document.payload) > max_bytes:
        raise ValueError(f"{label} exceeds its {max_bytes} byte bound")


def parse_decimal(text: object, *, label: str) -> Decimal:
    """Parse a plain finite decimal string. NaN, infinities and text fail."""

    raw = str(text).strip()
    if not _DECIMAL.fullmatch(raw):
        raise ValueError(f"{label}: {raw[:40]!r} is not a plain decimal")
    value = Decimal(raw)
    if not value.is_finite():
        raise ValueError(f"{label}: value nonfinite")
    return value


def decimal_from_cell(value: object, *, label: str) -> Decimal:
    """Convert a numeric spreadsheet cell without binary float drift."""

    if isinstance(value, bool):
        raise ValueError(f"{label}: boolean cell")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label}: value nonfinite")
        return Decimal(repr(value))
    raise ValueError(f"{label}: unexpected cell {value!r}")


def check_bounds(value: Decimal, low: str, high: str, *, label: str) -> None:
    """Fail closed when a value leaves its plausible range (a unit change)."""

    if not Decimal(low) <= value <= Decimal(high):
        raise ValueError(
            f"{label}: {value} outside the plausible {low} to {high} range; "
            "the unit or scale may have changed"
        )


def iso_day(text: str, *, label: str) -> date:
    """Parse a strict YYYY-MM-DD date."""

    raw = text.strip()
    if not _ISO_DAY.fullmatch(raw):
        raise ValueError(f"{label}: date {raw[:20]!r} is not YYYY-MM-DD")
    return date.fromisoformat(raw)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def acquisition_day(*documents: Acquisition) -> date:
    """The UTC day of the latest acquisition, the future-date boundary."""

    return max(document.acquired_at for document in documents).date()


def knowledge_time(*documents: Acquisition) -> datetime:
    """A row is known only once every document behind it was acquired."""

    return max(document.acquired_at for document in documents)


def row_evidence(**values: object) -> bytes:
    return json.dumps(
        values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def validate_zip_archive(
    payload: bytes,
    *,
    label: str,
    max_members: int,
    max_expanded_bytes: int,
) -> None:
    """Bound ZIP expansion (XLSX workbooks and release packages) before reading."""

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{label} is not a valid ZIP archive") from exc
    if not members or len(members) > max_members:
        raise ValueError(f"{label} exceeds its ZIP member limit")
    if len({member.filename for member in members}) != len(members):
        raise ValueError(f"{label} contains duplicate ZIP member names")
    expanded = 0
    for member in members:
        parts = member.filename.replace("\\", "/").split("/")
        if member.filename.startswith("/") or ".." in parts:
            raise ValueError(f"{label} contains an unsafe ZIP member name")
        if member.flag_bits & 0x1:
            raise ValueError(f"{label} contains an encrypted ZIP member")
        if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError(f"{label} uses an unsupported ZIP compression method")
        expanded += member.file_size
        if expanded > max_expanded_bytes:
            raise ValueError(f"{label} exceeds its ZIP expansion limit")


def make_observation(
    *,
    substitute_id: str,
    measure: str,
    spec: MeasureSpec,
    event_date: date,
    period: str,
    value: Decimal,
    source: str,
    publisher: str,
    source_url: str,
    terms_url: str,
    documents: tuple[Acquisition, ...],
    evidence: Mapping[str, object],
) -> SourceObservation:
    return SourceObservation(
        substitute_id=substitute_id,
        measure=measure,
        event_date=event_date,
        period=period,
        value=value,
        unit=spec.unit,
        frequency=spec.frequency,
        source=source,
        publisher=publisher,
        source_url=source_url,
        terms_url=terms_url,
        knowledge_time=knowledge_time(*documents),
        raw_documents=tuple((item.source_url, item.sha256) for item in documents),
        row_evidence=row_evidence(**evidence),
    )
