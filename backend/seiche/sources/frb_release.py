"""Bounded reader for the Federal Reserve Board's own release packages.

The Board publishes each statistical release as a ZIP holding one SDMX-ML
compact data member (``<Series SERIES_NAME=...>`` elements with ``<Obs>``
rows, grouped in ``<DataSet id=...>`` elements). This reader opens only the
named member, streams it once, refuses XML declarations, and hands each series
declaration, with the id of the data set that holds it, to a caller that
decides whether to receive its rows. It interprets nothing: callers pin units,
dates and values themselves.
"""
from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Collection
from xml.parsers import expat

from seiche.sources.canonical import FetchedDocument

_CHUNK_BYTES = 1 << 20

Declarations = dict[str, list[dict[str, str]]]
Rows = dict[str, list[dict[str, str]]]
RowHandler = Callable[[dict[str, str]], None]
# Called with (data set id or None, series attributes) for every series in the
# member. It returns the handler for that series' rows, or None to skip them.
SeriesHandler = Callable[[str | None, dict[str, str]], RowHandler | None]


def _stream(stream, on_series: SeriesHandler, *, label: str, max_xml_bytes: int) -> None:
    on_row: RowHandler | None = None
    dataset: str | None = None

    def local(name: str) -> str:
        return name.rsplit(" ", 1)[-1]

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal on_row, dataset
        tag = local(name)
        if tag == "DataSet":
            dataset = attributes.get("id")
        elif tag == "Series":
            on_row = on_series(dataset, dict(attributes))
        elif on_row is not None and tag == "Obs":
            on_row(dict(attributes))

    def end(name: str) -> None:
        nonlocal on_row, dataset
        tag = local(name)
        if tag == "Series":
            on_row = None
        elif tag == "DataSet":
            dataset = None

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


def stream_release(
    document: FetchedDocument,
    on_series: SeriesHandler,
    *,
    source_url: str,
    member: str,
    label: str,
    max_archive_bytes: int,
    max_xml_bytes: int,
    max_members: int,
) -> None:
    """Stream one package member once, handing each series to ``on_series``.

    The archive must come from ``source_url``, stay within its byte bound, hold
    at most ``max_members`` members and exactly one named ``member``, unencrypted
    and within ``max_xml_bytes`` both as declared and as actually read.
    """

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
            _stream(stream, on_series, label=label, max_xml_bytes=max_xml_bytes)


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

    found: Declarations = {name: [] for name in wanted}
    rows: Rows = {name: [] for name in wanted}

    def on_series(_dataset: str | None, attributes: dict[str, str]) -> RowHandler | None:
        series = attributes.get("SERIES_NAME")
        if series not in found:
            return None
        found[series].append(attributes)
        return rows[series].append

    stream_release(
        document, on_series, source_url=source_url, member=member, label=label,
        max_archive_bytes=max_archive_bytes, max_xml_bytes=max_xml_bytes,
        max_members=max_members,
    )
    return found, rows
