"""Bounded reader for the Board's own release XML packages.

The Board publishes each statistical release as a ZIP that holds one SDMX
compact data member (``<Series SERIES_NAME=...>`` elements with ``<Obs>``
rows inside ``<DataSet id=...>`` elements). These are the files linked as
"XML" on each release page, not the Data Download Program "Build Your
Package" tool that the Board retires the week of 2026-11-09.

The member is streamed once with expat. Document type and entity
declarations are refused. Every wanted series must appear exactly once, in
the expected data set, with exactly the pinned publisher attributes, and its
rows must be strictly date ordered. ``ND`` and ``NA`` rows are unavailable,
never zero.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from xml.parsers import expat

from seiche.sources.substitutes._common import (
    Acquisition,
    iso_day,
    parse_decimal,
    require_document,
    validate_zip_archive,
)

_CHUNK_BYTES = 1 << 20
_UNAVAILABLE = frozenset({"ND", "NA"})


def read_series(
    document: Acquisition,
    *,
    source_url: str,
    member: str,
    dataset: str,
    pinned: Mapping[str, Mapping[str, str]],
    label: str,
    max_archive_bytes: int,
    max_xml_bytes: int,
    max_members: int = 16,
) -> dict[str, list[dict[str, str]]]:
    """Return the ``<Obs>`` attributes of each pinned series."""

    require_document(
        document, source_url=source_url, max_bytes=max_archive_bytes, label=label
    )
    validate_zip_archive(
        document.payload, label=label, max_members=max_members,
        max_expanded_bytes=2 * max_xml_bytes,
    )
    found: dict[str, int] = {name: 0 for name in pinned}
    rows: dict[str, list[dict[str, str]]] = {name: [] for name in pinned}
    state: dict[str, str | None] = {"dataset": None, "series": None}

    def local(name: str) -> str:
        return name.rsplit(" ", 1)[-1]

    def start(name: str, attributes: dict[str, str]) -> None:
        tag = local(name)
        if tag == "DataSet":
            state["dataset"] = attributes.get("id")
        elif tag == "Series":
            series = attributes.get("SERIES_NAME")
            state["series"] = series if series in pinned else None
            if series in pinned:
                found[series] += 1
                if state["dataset"] != dataset:
                    raise ValueError(f"{label} series {series} left data set {dataset}")
                if dict(attributes) != dict(pinned[series]):
                    raise ValueError(f"{label} series {series} declarations changed")
        elif tag == "Obs" and state["series"] is not None:
            rows[state["series"]].append(dict(attributes))

    def end(name: str) -> None:
        tag = local(name)
        if tag == "Series":
            state["series"] = None
        elif tag == "DataSet":
            state["dataset"] = None

    def forbidden(*_args) -> None:
        raise ValueError(f"{label} XML declarations forbidden")

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    with zipfile.ZipFile(io.BytesIO(document.payload)) as archive:
        matches = [item for item in archive.infolist() if item.filename == member]
        if len(matches) != 1:
            raise ValueError(f"{label} XML member changed")
        selected = matches[0]
        if not 0 < selected.file_size <= max_xml_bytes:
            raise ValueError(f"{label} XML exceeds its byte bound")
        total = 0
        try:
            with archive.open(selected) as stream:
                while chunk := stream.read(_CHUNK_BYTES):
                    total += len(chunk)
                    if total > max_xml_bytes:
                        raise ValueError(f"{label} XML exceeds its byte bound")
                    parser.Parse(chunk, False)
                parser.Parse(b"", True)
        except expat.ExpatError as exc:
            raise ValueError(f"{label} XML is malformed") from exc
    for series, count in found.items():
        if count != 1:
            raise ValueError(f"{label} series {series} found {count} times")
    return rows


def available_values(
    rows: list[dict[str, str]], *, label: str
) -> list[tuple[date, Decimal, dict[str, str]]]:
    """Strictly ordered available observations; ND and NA rows are skipped."""

    previous: date | None = None
    values: list[tuple[date, Decimal, dict[str, str]]] = []
    for attributes in rows:
        day = iso_day(attributes.get("TIME_PERIOD", ""), label=label)
        if previous is not None and day <= previous:
            raise ValueError(f"{label} date duplicated or out of order: {day.isoformat()}")
        previous = day
        status = attributes.get("OBS_STATUS")
        if status in _UNAVAILABLE:
            continue
        if status != "A":
            raise ValueError(f"{label} observation status {status!r} unknown")
        values.append((day, parse_decimal(attributes.get("OBS_VALUE", ""), label=label), attributes))
    return values
