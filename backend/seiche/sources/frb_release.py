"""Bounded reader for the Federal Reserve Board's own release packages.

The Board publishes each statistical release as a ZIP holding one SDMX-ML
compact data member (``<Series SERIES_NAME=...>`` elements with ``<Obs>``
rows). This reader opens only the named member, streams it once, refuses XML
declarations, and keeps the declarations and rows of the requested series. It
interprets nothing: callers pin units, dates and values themselves.
"""
from __future__ import annotations

import io
import zipfile
from collections.abc import Collection
from xml.parsers import expat

from seiche.sources.canonical import FetchedDocument

_CHUNK_BYTES = 1 << 20

Declarations = dict[str, list[dict[str, str]]]
Rows = dict[str, list[dict[str, str]]]


def _scan(stream, wanted: Collection[str], *, label: str, max_xml_bytes: int) -> tuple[Declarations, Rows]:
    found: Declarations = {name: [] for name in wanted}
    rows: Rows = {name: [] for name in wanted}
    inside: str | None = None

    def local(name: str) -> str:
        return name.rsplit(" ", 1)[-1]

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal inside
        tag = local(name)
        if tag == "Series":
            series = attributes.get("SERIES_NAME")
            inside = series if series in found else None
            if inside is not None:
                found[inside].append(dict(attributes))
        elif inside is not None and tag == "Obs":
            rows[inside].append(dict(attributes))

    def end(name: str) -> None:
        nonlocal inside
        if local(name) == "Series":
            inside = None

    def forbidden(*_args) -> None:
        raise ValueError(f"{label} XML declarations forbidden")

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    total = 0
    try:
        while chunk := stream.read(_CHUNK_BYTES):
            total += len(chunk)
            if total > max_xml_bytes:
                raise ValueError(f"{label} XML exceeds bound")
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as exc:
        raise ValueError(f"{label} XML is malformed") from exc
    return found, rows


def read_release_series(
    document: FetchedDocument,
    *,
    source_url: str,
    member: str,
    wanted: Collection[str],
    label: str,
    max_archive_bytes: int,
    max_xml_bytes: int,
    max_members: int,
) -> tuple[Declarations, Rows]:
    """Return the declarations and rows of ``wanted`` series from one package."""

    if document.source_uri != source_url or len(document.payload) > max_archive_bytes:
        raise ValueError(f"unrecognized or oversized {label} source")
    with zipfile.ZipFile(io.BytesIO(document.payload)) as archive:
        members = archive.infolist()
        matches = [item for item in members if item.filename == member]
        if len(members) > max_members or len(matches) != 1:
            raise ValueError(f"{label} XML member changed")
        selected = matches[0]
        if selected.flag_bits & 1 or not 0 < selected.file_size <= max_xml_bytes:
            raise ValueError(f"{label} XML size or encryption changed")
        with archive.open(selected) as stream:
            return _scan(stream, wanted, label=label, max_xml_bytes=max_xml_bytes)
