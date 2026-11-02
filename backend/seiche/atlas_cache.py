"""Generation-keyed delivery cache for the public money-market atlas.

The atlas was read, rebuilt and serialized on every request: about 2.2 MB and
7.5 to 10 seconds to first byte in production. This cache changes only whether
the server rebuilds; a request receives what a fresh build would give it.

* Observations. A built payload is reused only while the repository's
  fingerprint of the exact selected rows equals the one taken before that
  payload's read: row count, latest knowledge time and exact sums of knowledge
  and event instants over the same selection, cutoff and event window.
  Canonical rows are append-only, so a new row or vintage, or a row leaving
  the window, forces a rebuild. Never older than the current store.
* Rights. The key includes every pack's identity, each adapter's
  classification and redistribution status, each instrument's adapter and the
  public rights policy, so an entry is never served across a rights change.
* Freshness. While building, every publication-opportunity clock records the
  first instant its state can change (``freshness_transitions``). An entry
  expires at the earliest of those and at the next UTC midnight (country
  desks window events by UTC date). Before then every freshness state equals
  a fresh evaluation. (A stored row's publication time never follows its
  knowledge time, so no already-read row becomes eligible later.)
* Collector runs change every few minutes but drive only each market's
  ``adapters`` and ``faults``. They are re-read on every request and spliced
  into the cached bytes with the builder's own functions, so run status, run
  times and faults are always current.

``generated_at`` and ``observation_age_days`` keep describing the evaluation
instant of the build being reused; every state they qualify is unchanged. A
read with a repository fault, or a repository without a fingerprint, is not
cached and behaves exactly as before.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder

from seiche import public_rights
from seiche.markets.atlas import (
    collector_run_map,
    freshness_transitions,
    market_adapter_records,
)
from seiche.markets.base import MarketPack
from seiche.public_faults import sanitize_public_fault_payload

_SPLICE_PREFIX = "@@seiche-atlas-splice:"
_RUN_FIELDS = ("adapters", "faults")


def encode(payload: Any) -> bytes:
    """Byte-identical to FastAPI's default JSON response for the same content."""

    return json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")


def rights_identity(packs: Sequence[MarketPack]) -> str:
    """Everything that decides what the atlas may make public."""

    material: list[Any] = [public_rights.POLICY]
    for pack in packs:
        material.append((
            pack.market_id,
            id(pack),
            tuple(
                (adapter.adapter_id, adapter.classification.value,
                 adapter.redistribution_status.value)
                for adapter in pack.source_adapters
            ),
            tuple(
                (instrument.instrument_id, instrument.source_adapter_id)
                for instrument in pack.instruments
            ),
        ))
    return hashlib.sha256(repr(material).encode("utf-8")).hexdigest()


def etag_matches(header: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison of If-None-Match against a strong tag."""

    if not header:
        return False
    for candidate in header.split(","):
        tag = candidate.strip()
        if tag == "*" or tag.removeprefix("W/") == etag:
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Build:
    key: tuple[Any, ...]
    token: str
    built_at: datetime
    valid_until: datetime
    market_ids: tuple[str, ...]
    chunks: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class _Rendered:
    token: str
    runs_digest: str
    body: bytes
    gzip: bytes
    etag: str


@dataclass(frozen=True, slots=True)
class Delivery:
    body: bytes
    gzip: bytes
    etag: str
    state: str  # "hit" reused a build, "miss" rebuilt it, "bypass" was not cacheable


class AtlasCache:
    """One current build plus its latest run-spliced rendering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._build: _Build | None = None
        self._rendered: _Rendered | None = None

    def clear(self) -> None:
        with self._lock:
            self._build = None
            self._rendered = None

    def _current(self, key: tuple[Any, ...], cutoff: datetime) -> _Build | None:
        build = self._build
        if build is not None and build.key == key and build.built_at <= cutoff < build.valid_until:
            return build
        return None

    def deliver(
        self,
        *,
        key: tuple[Any, ...],
        cutoff: datetime,
        packs: Sequence[MarketPack],
        collector_runs: Iterable[Mapping[str, Any]],
        build_without_runs: Callable[[], dict[str, Any] | None],
    ) -> Delivery | None:
        """Serve from the current build, rebuilding it once when it is invalid.

        ``build_without_runs`` returns the sanitized payload built with no
        collector runs, or None when the read must not be cached (a repository
        read fault). None is returned to the caller in that case.
        """

        runs = tuple(collector_runs)
        state = "hit"
        build = self._current(key, cutoff)
        if build is None:
            # One rebuild at a time; requests that waited reuse its result.
            with self._lock:
                build = self._current(key, cutoff)
                if build is None:
                    state = "miss"
                    with freshness_transitions() as transitions:
                        payload = build_without_runs()
                    if payload is None:
                        return None
                    build = self._split(key, cutoff, payload, transitions)
                    if build.valid_until > cutoff:
                        self._build = build
        return self._render(build, runs, cutoff, packs, state)

    @staticmethod
    def _split(
        key: tuple[Any, ...],
        cutoff: datetime,
        payload: dict[str, Any],
        transitions: Iterable[datetime],
    ) -> _Build:
        token = uuid.uuid4().hex
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise ValueError("atlas payload has no markets list")
        tokens: list[bytes] = []
        market_ids: list[str] = []
        for index, market in enumerate(markets):
            if not isinstance(market, dict) or any(field not in market for field in _RUN_FIELDS):
                raise ValueError("atlas market lacks its run-derived fields")
            market_ids.append(str(market["market_id"]))
            for field in _RUN_FIELDS:
                sentinel = f"{_SPLICE_PREFIX}{token}:{index}:{field}"
                market[field] = sentinel
                tokens.append(json.dumps(sentinel).encode("utf-8"))
        rest = encode(payload)
        chunks: list[bytes] = []
        for marker in tokens:
            before, found, after = rest.partition(marker)
            if not found or marker in after:
                raise ValueError("atlas splice marker is missing or repeated")
            chunks.append(before)
            rest = after
        chunks.append(rest)
        next_midnight = datetime.combine(cutoff.astimezone(UTC).date() + timedelta(days=1), time.min, tzinfo=UTC)
        limits = [next_midnight, *(item for item in transitions if item is not None)]
        return _Build(
            key=key,
            token=token,
            built_at=cutoff,
            valid_until=min(limits),
            market_ids=tuple(market_ids),
            chunks=tuple(chunks),
        )

    def _render(
        self,
        build: _Build,
        runs: tuple[Mapping[str, Any], ...],
        cutoff: datetime,
        packs: Sequence[MarketPack],
        state: str,
    ) -> Delivery:
        packs_by_id = {pack.market_id: pack for pack in packs}
        run_map = collector_run_map(runs, cutoff)
        fragments: list[bytes] = []
        for market_id in build.market_ids:
            adapters, faults = market_adapter_records(packs_by_id[market_id], run_map)
            # The same sanitizer the whole payload passes through, on the
            # same field names, so the spliced bytes equal a full build.
            public = sanitize_public_fault_payload({"adapters": adapters, "faults": faults})
            fragments.extend(encode(public[field]) for field in _RUN_FIELDS)
        digest = hashlib.sha256(b"\x00".join(fragments)).hexdigest()
        rendered = self._rendered
        if rendered is None or rendered.token != build.token or rendered.runs_digest != digest:
            parts = [build.chunks[0]]
            for fragment, chunk in zip(fragments, build.chunks[1:], strict=True):
                parts.extend((fragment, chunk))
            body = b"".join(parts)
            rendered = _Rendered(
                token=build.token,
                runs_digest=digest,
                body=body,
                gzip=gzip.compress(body, 6),
                etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"',
            )
            self._rendered = rendered
        return Delivery(rendered.body, rendered.gzip, rendered.etag, state)
