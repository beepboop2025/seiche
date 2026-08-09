"""Production primitives for official canonical market adapters.

The raw response and the parsed row deliberately have different evidence
identities.  A response bundle proves what the collector downloaded; a stable
row encoding identifies a particular value/vintage without making an
unrelated change elsewhere in the document revise every historical point.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from seiche.domain.observation import (
    Observation,
    QualityState,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.base import (
    CalendarUnavailableError,
    InstrumentSpec,
    MarketPack,
    PublicationClockPrecision,
)
from seiche.repository import MarketRepository, get_repository
from seiche.sources.base import ObservationBatch, RawCapture


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    source_uri: str
    media_type: str
    payload: bytes
    label: str = ""


@dataclass(frozen=True, slots=True)
class ParsedPoint:
    """A source-native row before pack-declared unit conversion."""

    instrument_id: str
    event_time: date | datetime
    raw_value: Decimal | int | float | str
    row_evidence: bytes
    source_publication_time: datetime | None = None
    revision_id: str | None = None
    quality: QualityState | None = None


class DocumentFetcher(Protocol):
    async def __call__(self, client: httpx.AsyncClient) -> Iterable[FetchedDocument]: ...


DocumentParser = Callable[[FetchedDocument], Iterable[ParsedPoint]]
Clock = Callable[[], datetime]


def _as_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _event_datetime(value: date | datetime, pack: MarketPack) -> datetime:
    """Return the canonical event key for an instant or a business-date label.

    A source-native ``date`` is not an instant at midnight in the market's
    timezone. It labels that market's business session. Store it at UTC
    midnight so equal session labels have one cross-market key. A source-native
    ``datetime`` remains an instant and is converted from its supplied (or
    pack-local, when naive) timezone.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=ZoneInfo(pack.local_timezone))
        return value.astimezone(UTC).replace(microsecond=0)
    return datetime.combine(value, time.min, tzinfo=UTC)


def _event_business_date(value: date | datetime, pack: MarketPack) -> date:
    """Resolve the pack-local date used by publication-calendar rules."""

    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        return value.date()
    return value.astimezone(ZoneInfo(pack.local_timezone)).date()


def _inferred_publication_time(event_day: date, pack: MarketPack, adapter_id: str) -> datetime:
    """Conservative per-row clock for an upstream-native but undated record."""

    adapter = pack.adapter_map[adapter_id]
    clock = adapter.publication_clock
    if clock.local_time is not None:
        return clock.resolve(event_day, pack.settlement_calendar)
    publication_day = pack.settlement_calendar.add_business_days(
        event_day,
        clock.business_day_lag,
    )
    # End of the declared publication day is conservative for replay: the row
    # never becomes knowable before the source could reasonably have posted it.
    local = datetime.combine(
        publication_day,
        time(23, 59, 59),
        tzinfo=ZoneInfo(clock.timezone_name),
    )
    return local.astimezone(UTC)


def _raw_capture(
    pack: MarketPack,
    adapter_id: str,
    captured_at: datetime,
    documents: tuple[FetchedDocument, ...],
) -> RawCapture:
    if len(documents) == 1:
        document = documents[0]
        payload = document.payload
        source_uri = document.source_uri
        media_type = document.media_type
    else:
        manifest = {
            "schema": "seiche.raw-bundle.v1",
            "documents": [
                {
                    "label": item.label,
                    "source_uri": item.source_uri,
                    "media_type": item.media_type,
                    "sha256": evidence_sha256(item.payload),
                    "payload_base64": base64.b64encode(item.payload).decode("ascii"),
                }
                for item in documents
            ],
        }
        payload = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        source_uri = "bundle:" + ",".join(item.source_uri for item in documents)
        media_type = "application/vnd.seiche.raw-bundle+json"
    return RawCapture(
        market_id=pack.market_id,
        adapter_id=adapter_id,
        captured_at=captured_at,
        source_uri=source_uri,
        media_type=media_type,
        payload=payload,
        evidence_hash=evidence_sha256(payload),
    )


class FunctionalCanonicalAdapter:
    """Fetch, parse, normalize and vintage one pack-declared source adapter."""

    def __init__(
        self,
        *,
        pack: MarketPack,
        adapter_id: str,
        source: str,
        fetcher: DocumentFetcher,
        parser: DocumentParser,
        repository: MarketRepository | None = None,
        clock: Clock | None = None,
        timeout_seconds: float = 60.0,
        historical_backfill: bool = False,
    ) -> None:
        if adapter_id not in pack.adapter_map:
            raise ValueError(f"{adapter_id!r} is not declared by {pack.market_id}")
        self.pack = pack
        self.market_id = pack.market_id
        self.adapter_id = adapter_id
        self.source = source
        self.fetcher = fetcher
        self.parser = parser
        self.repository = repository or get_repository()
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.timeout_seconds = timeout_seconds
        self.historical_backfill = historical_backfill

    async def collect(self) -> ObservationBatch:
        headers = {
            "User-Agent": "Seiche/0.9 (+https://seiche.info; research collector)",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.timeout_seconds,
            headers=headers,
        ) as client:
            documents = tuple(await self.fetcher(client))
        if not documents:
            raise ValueError("official adapter returned no source documents")
        captured_at = _as_utc(self.clock(), "capture clock")

        # Parse and validate the emitted scope before asking the repository for
        # prior vintages. Daily source files can contain years of rows, but a
        # recent collection must never scan unrelated instruments, adapters,
        # or history preceding the earliest row actually present in the fetch.
        prepared: list[tuple[ParsedPoint, InstrumentSpec, date, datetime]] = []
        for document in documents:
            for point in self.parser(document):
                try:
                    instrument = self.pack.instrument_map[point.instrument_id]
                except KeyError as exc:
                    raise ValueError(
                        f"parser emitted undeclared instrument {point.instrument_id!r}"
                    ) from exc
                if instrument.source_adapter_id != self.adapter_id:
                    raise ValueError(
                        f"{point.instrument_id!r} belongs to "
                        f"{instrument.source_adapter_id!r}, not {self.adapter_id!r}"
                    )
                event_day = _event_business_date(point.event_time, self.pack)
                event_time = _event_datetime(point.event_time, self.pack)
                prepared.append((point, instrument, event_day, event_time))

        prior = (
            self.repository.load_observations_as_of(
                self.pack.market_id,
                captured_at,
                event_time=captured_at,
                event_time_from=min(item[3] for item in prepared),
                instrument_ids=tuple(
                    sorted({item[1].instrument_id for item in prepared})
                ),
                sources=(self.source,),
            )
            if prepared
            else []
        )
        prior_by_event = {
            (item.instrument_id, item.event_time): item
            for item in prior
            if item.source == self.source
        }
        spec = self.pack.adapter_map[self.adapter_id]
        observations: list[Observation] = []
        for point, instrument, event_day, event_time in prepared:
            if event_time > captured_at:
                # A future session/instant is not part of this capture's
                # point-in-time state, regardless of an upstream clock.
                continue
            try:
                publication = (
                    _as_utc(point.source_publication_time, "source publication time")
                    if point.source_publication_time is not None
                    else _inferred_publication_time(
                        event_day,
                        self.pack,
                        self.adapter_id,
                    )
                )
            except CalendarUnavailableError:
                # A bounded calendar is also the bounded backfill contract.
                # Rows outside it are withheld rather than weekday-guessed.
                continue
            if publication > captured_at:
                # A same-day source can expose an effective date before the
                # declared publication clock. It is not knowable yet.
                continue
            row_hash = evidence_sha256(point.row_evidence)
            existing = prior_by_event.get((instrument.instrument_id, event_time))
            if existing is not None and existing.evidence_hash == row_hash:
                observations.append(existing)
                continue
            if existing is not None:
                # Preserve an upstream revision timestamp when it is explicit;
                # otherwise capture is the only defensible publication bound.
                if point.source_publication_time is None:
                    publication = captured_at
                knowledge = captured_at
                quality = QualityState.REVISED
            elif self.historical_backfill:
                # A current historical file is not a historical vintage.
                # Preserve the row's source publication clock, but make it
                # knowable only when this capture actually entered Seiche.
                # This imports useful history without leaking a final
                # vintage into a synthetic past backtest.
                knowledge = captured_at
                quality = QualityState.PROVISIONAL
            else:
                # Publication time says when the upstream row could have
                # existed. Knowledge time says when this Seiche record
                # actually observed it. Never backdate a newly seen row to
                # an inferred or reported upstream clock.
                knowledge = captured_at
                quality = point.quality or (
                    QualityState.VERIFIED
                    if (
                        point.source_publication_time is not None
                        or spec.publication_clock.precision
                        in {
                            PublicationClockPrecision.EXACT,
                            PublicationClockPrecision.SCHEDULED,
                        }
                    )
                    else QualityState.ESTIMATED
                )
            revision_id = point.revision_id or f"sha256:{row_hash[:20]}"
            observations.append(
                Observation(
                    market_id=self.pack.market_id,
                    monetary_area_id=self.pack.monetary_area_id,
                    jurisdiction_codes=self.pack.jurisdiction_codes,
                    currency=self.pack.currency,
                    instrument_id=instrument.instrument_id,
                    semantic_role=instrument.semantic_role,
                    value=instrument.normalize(point.raw_value),
                    canonical_unit=instrument.canonical_unit,
                    rate_compounding=instrument.rate_compounding,
                    day_count=instrument.day_count,
                    event_time=event_time,
                    knowledge_time=knowledge,
                    source_publication_time=publication,
                    revision_id=revision_id,
                    source=self.source,
                    evidence_hash=row_hash,
                    connector_classification=spec.classification,
                    redistribution_status=spec.redistribution_status,
                    quality=quality,
                    # Successful retrieval proves the adapter is live.
                    # Product materialization computes current age from the
                    # event/knowledge cutoff rather than aging old history.
                    staleness=StalenessState.FRESH,
                )
            )
        return ObservationBatch(
            market_id=self.pack.market_id,
            adapter_id=self.adapter_id,
            captured_at=captured_at,
            observations=tuple(
                sorted(
                    observations,
                    key=lambda item: (item.event_time, item.instrument_id),
                )
            ),
            raw_capture=_raw_capture(
                self.pack,
                self.adapter_id,
                captured_at,
                documents,
            ),
        )


async def get_documents(
    client: httpx.AsyncClient,
    requests: Iterable[tuple[str, str, dict[str, object] | None]],
) -> tuple[FetchedDocument, ...]:
    """Fetch independent GET documents without hiding a failed response."""

    documents: list[FetchedDocument] = []
    for label, uri, params in requests:
        response = await client.get(uri, params=params)
        response.raise_for_status()
        documents.append(
            FetchedDocument(
                source_uri=str(response.url),
                media_type=response.headers.get("content-type", "application/octet-stream")
                .split(";", 1)[0]
                .lower(),
                payload=response.content,
                label=label,
            )
        )
    return tuple(documents)
