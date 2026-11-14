"""One presenter for LiquiLens, Seiche and Undertow readings on every surface.

This module implements the evidence presentation contract (liquilens-site
``docs/design/EVIDENCE-PRESENTATION-CONTRACT.md``, version 1) and the surface
rules in ``docs/design/PORTABLE-SURFACES.md``, as fixed at liquilens-site
commit 59302ce8. It turns a verified evidence carrier, or a product's native
value, into the contract's presentation slots and one of ten display states,
and renders them as plain text (two or three lines per reading) or as an HTML
table for notebooks.

It is deliberately one file. It imports only the Python standard library, runs
on Python 3.10 and later, and embeds the design tokens it needs, so a
stdlib-only project can copy it byte for byte. It does not verify carriers:
callers pass the disposition and reason codes that ``verify_evidence_carrier``
produced at the reader's clock. It never writes into a carrier, fills a missing
value, grants authority, or draws a gap as a calm state.

Entry points: ``state_for`` (section 4 product rows), ``check_label`` (check
words, which are not readings), ``resolution_notice`` (name lookups, which are
not readings either), ``derive_display_state`` (section 3), the ``reading_*``
constructors, ``render_text`` and ``render_reading`` (section 5),
``ReadingTable`` and ``render_html`` (notebooks), ``add_display_columns``
(data frames) and ``terminal_style`` (the only function that reads the
environment).
"""

from __future__ import annotations

import html
import math
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, TypeAlias

__all__ = [
    "COLOUR_MODES",
    "DEFAULT_BOUNDARY",
    "DERIVED",
    "DISPLAY_COLUMNS",
    "DISPLAY_STATES",
    "DISPOSITIONS",
    "FULL_LIST_LINE",
    "GAP_STATES",
    "MARK_MODES",
    "NEVER_HIDDEN_CHECKS",
    "NOT_COVERED",
    "NOT_STATED",
    "OBSERVED",
    "PARTIAL",
    "PROVISIONAL",
    "REASON_CODES",
    "RESEARCH",
    "RESTRICTED",
    "STALE",
    "STATE_ORDER",
    "STRUCTURAL",
    "TOKENS_COMMIT",
    "TOKENS_REPOSITORY",
    "UNAVAILABLE",
    "VALUE_STATES",
    "DisplayState",
    "ProductState",
    "Reading",
    "ReadingTable",
    "SourceRef",
    "StateDerivation",
    "add_display_columns",
    "check_label",
    "derive_display_state",
    "format_instant",
    "format_period",
    "parse_clock",
    "product_name",
    "reading_access_refused",
    "reading_from_carrier",
    "reading_from_native",
    "reading_not_covered",
    "reading_number",
    "render_html",
    "render_reading",
    "render_text",
    "resolution_notice",
    "state_for",
    "terminal_style",
    "verification_outcome",
]

ClockInput: TypeAlias = str | datetime | date
ValueInput: TypeAlias = str | int | float | Decimal

TOKENS_REPOSITORY = "beepboop2025/liquilens-site"
TOKENS_COMMIT = "59302ce87ef186ed01fb8ce15f665cf29ad4cfb6"

NOT_STATED = "Not stated"
# Closes a capped view (render_text with max_bytes) when any reading was cut.
FULL_LIST_LINE = "Full list in the JSON block."
DEFAULT_BOUNDARY = "No execution, recommendation or credit-rating authority."

OBSERVED = "observed"
DERIVED = "derived"
STRUCTURAL = "structural"
RESEARCH = "research"
PROVISIONAL = "provisional"
PARTIAL = "partial"
STALE = "stale"
RESTRICTED = "restricted"
NOT_COVERED = "not-covered"
UNAVAILABLE = "unavailable"

STATE_ORDER: tuple[str, ...] = (
    OBSERVED,
    DERIVED,
    STRUCTURAL,
    RESEARCH,
    PROVISIONAL,
    PARTIAL,
    STALE,
    RESTRICTED,
    NOT_COVERED,
    UNAVAILABLE,
)
# Contract section 3: these five never take the calm treatment and never show
# a value. The other five are drawn with a value.
GAP_STATES = frozenset({PARTIAL, STALE, RESTRICTED, NOT_COVERED, UNAVAILABLE})
VALUE_STATES = frozenset({OBSERVED, DERIVED, STRUCTURAL, RESEARCH, PROVISIONAL})

MARK_MODES: tuple[str, ...] = ("unicode", "ascii")
COLOUR_MODES: tuple[str, ...] = ("none", "truecolor", "ansi256", "ansi16")
DISPOSITIONS: tuple[str, ...] = ("full", "metadata_only", "reject")

# Contract section 1, in the order the contract lists them.
REASON_CODES: tuple[str, ...] = (
    "evidence_not_yet_known",
    "evidence_as_of_in_future",
    "rights_restricted",
    "rights_unknown",
    "rights_blocked",
    "rights_metadata_only",
    "redistribution_not_permitted",
    "rights_license_missing",
    "rights_attribution_missing",
    "claim_restricted",
    "claim_unavailable",
    "evidence_expired",
)
DISPLAY_COLUMNS: tuple[str, ...] = (
    "display_state",
    "value",
    "unit",
    "source_publisher",
)

# The words exactly as the contract's section 3 table prints them.
_STATE_WORDS = {
    OBSERVED: "Observed",
    DERIVED: "Derived",
    STRUCTURAL: "Structural",
    RESEARCH: "Research",
    PROVISIONAL: "Provisional",
    PARTIAL: "Partial",
    STALE: "Stale",
    RESTRICTED: "Restricted",
    NOT_COVERED: "Not covered",
    UNAVAILABLE: "Unavailable",
}

# Exact content of the vendored design/ansi.json at TOKENS_COMMIT, held as
# Python data so this file needs nothing beside it. tests/test_present.py
# fails when this differs from the vendored copy.
_ANSI_EXPORT: dict[str, Any] = {
    "_comment": (
        "Generated by scripts/build_design_tokens.py from "
        "design/family-tokens.json. Do not edit by hand."
    ),
    "rules": [
        "Never set a background colour.",
        (
            "Body text uses the terminal default foreground (SGR 39); only "
            "marks, state words and links take colour."
        ),
        (
            "Every state prints its word. Colour and marks are additions, "
            "never the only channel."
        ),
        (
            "Honour NO_COLOR and non-terminal output: print plain words and "
            "ASCII marks only."
        ),
        (
            "Use truecolor when COLORTERM is truecolor or 24bit, else "
            "ansi256, else ansi16."
        ),
    ],
    "reset": "\x1b[0m",
    "bold": "1",
    "roles": {
        "accent": {
            "hex": "#abc7ff",
            "truecolor": "38;2;171;199;255",
            "ansi256": "38;5;153",
            "ansi16": "94",
        },
        "muted": {
            "hex": "#9da2ab",
            "truecolor": "38;2;157;162;171",
            "ansi256": "38;5;248",
            "ansi16": "90",
        },
        "good": {
            "hex": "#92d3bd",
            "truecolor": "38;2;146;211;189",
            "ansi256": "38;5;115",
            "ansi16": "32",
        },
        "warn": {
            "hex": "#ebc58d",
            "truecolor": "38;2;235;197;141",
            "ansi256": "38;5;186",
            "ansi16": "33",
        },
        "bad": {
            "hex": "#f1a3a3",
            "truecolor": "38;2;241;163;163",
            "ansi256": "38;5;217",
            "ansi16": "31",
        },
        "text": {
            "hex": "#f2f1ec",
            "sgr": "39",
            "note": (
                "Body text keeps the terminal's default foreground, so light "
                "themes stay legible."
            ),
        },
    },
    "states": {
        "observed": {
            "role": "good",
            "mark": "\N{BLACK CIRCLE}",
            "mark_ascii": "*",
            "calm": True,
        },
        "derived": {
            "role": "good",
            "mark": "\N{BLACK DIAMOND}",
            "mark_ascii": "+",
            "calm": True,
        },
        "structural": {
            "role": "muted",
            "mark": "\N{BLACK SQUARE}",
            "mark_ascii": "#",
            "calm": False,
        },
        "research": {
            "role": "muted",
            "mark": "\N{WHITE DIAMOND}",
            "mark_ascii": "^",
            "calm": False,
        },
        "provisional": {
            "role": "warn",
            "mark": "\N{DOTTED CIRCLE}",
            "mark_ascii": "~",
            "calm": False,
        },
        "partial": {
            "role": "warn",
            "mark": "\N{CIRCLE WITH LEFT HALF BLACK}",
            "mark_ascii": "%",
            "calm": False,
        },
        "stale": {
            "role": "warn",
            "mark": "\N{WHITE CIRCLE}",
            "mark_ascii": "o",
            "calm": False,
        },
        "restricted": {
            "role": "warn",
            "mark": "\N{CIRCLED DIVISION SLASH}",
            "mark_ascii": "/",
            "calm": False,
        },
        "not-covered": {
            "role": "warn",
            "mark": "\N{DOTTED SQUARE}",
            "mark_ascii": ":",
            "calm": False,
        },
        "unavailable": {
            "role": "bad",
            "mark": "\N{SQUARE WITH UPPER RIGHT TO LOWER LEFT FILL}",
            "mark_ascii": "x",
            "calm": False,
        },
    },
}

# The values this file uses from the vendored design/family-tokens.json, keyed
# by token path. Colours are hex strings, or (hex, alpha) for soft tints; font
# stacks are tuples; dimensions are px numbers. tests/test_present.py checks
# every entry against the vendored copy.
_FAMILY_TOKENS: dict[str, Any] = {
    "color.surface": "#0b0d10",
    "color.line": "#23272e",
    "color.text": "#f2f1ec",
    "color.muted": "#9da2ab",
    "color.accent": "#abc7ff",
    "color.good": "#92d3bd",
    "color.warn": "#ebc58d",
    "color.bad": "#f1a3a3",
    "color.warn-soft": ("#ebc58d", 0.08),
    "color.bad-soft": ("#f1a3a3", 0.08),
    "font.family.text": (
        "-apple-system",
        "BlinkMacSystemFont",
        "Segoe UI Variable Text",
        "Segoe UI",
        "Helvetica Neue",
        "Helvetica",
        "Arial",
        "sans-serif",
    ),
    "font.family.code": (
        "ui-monospace",
        "SF Mono",
        "Cascadia Mono",
        "Roboto Mono",
        "Menlo",
        "Consolas",
        "monospace",
    ),
    "font.size.label": 12,
    "font.size.small": 13,
    "font.weight.medium": 500,
    "font.leading.tight": 1.25,
    "space.1": 4,
    "space.2": 8,
    "space.3": 12,
    "space.4": 16,
    "radius.panel": 6,
}

_PRODUCT_NAMES = {
    "liquilens": "LiquiLens",
    "seiche": "Seiche",
    "undertow": "Undertow",
    "palimpsest": "Palimpsest",
}
_AUTHORITY_BOUNDARY = {
    "financial_authority": "none",
    "can_execute": False,
    "can_recommend": False,
    "is_credit_rating": False,
}
_SEVERITIES = frozenset({"good", "warn", "bad"})
_KNOWN_BASES = frozenset({"capture", "publication", "proxy"})
# datetime.UTC needs Python 3.11; this file also runs on 3.10.
_UTC = timezone(timedelta(0))


@dataclass(frozen=True)
class DisplayState:
    """One of the contract's ten display states: a word, two marks and a role."""

    key: str
    word: str
    mark: str
    mark_ascii: str
    role: str
    calm: bool

    @property
    def gap(self) -> bool:
        """True for the five states that never show a value."""

        return self.key in GAP_STATES


def _build_display_states() -> Mapping[str, DisplayState]:
    states: dict[str, DisplayState] = {}
    for key in STATE_ORDER:
        spec = _ANSI_EXPORT["states"][key]
        states[key] = DisplayState(
            key=key,
            word=_STATE_WORDS[key],
            mark=spec["mark"],
            mark_ascii=spec["mark_ascii"],
            role=spec["role"],
            calm=spec["calm"],
        )
    return MappingProxyType(states)


DISPLAY_STATES: Mapping[str, DisplayState] = _build_display_states()


# ---------------------------------------------------------------------------
# Clocks: parsed here, so Python 3.10 never meets a trailing Z in fromisoformat.

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_INSTANT_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})"
    r"(?::(\d{2})(?:[.,](\d{1,9}))?)?"
    r"([Zz]|[+-]\d{2}:?\d{2})"
)


def parse_clock(value: ClockInput) -> datetime | date:
    """Parse a period date or a zoned instant.

    A ``YYYY-MM-DD`` string or a ``date`` stays a date. An instant must carry
    ``Z`` or an explicit offset (a naive one is refused, never guessed) and is
    returned in UTC.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("a datetime clock must be timezone-aware")
        return value.astimezone(_UTC)
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError("a clock must be an ISO 8601 string, a date or a datetime")
    match = _DATE_RE.fullmatch(value)
    if match is not None:
        year, month, day = (int(part) for part in match.groups())
        return date(year, month, day)
    match = _INSTANT_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"not an ISO 8601 date or zoned instant: {value!r}")
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    microsecond = int(((match.group(7) or "") + "000000")[:6])
    zone = match.group(8)
    offset = timedelta(0)
    if zone not in ("Z", "z"):
        digits = zone[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            raise ValueError(f"invalid UTC offset in {value!r}")
        offset = timedelta(hours=hours, minutes=minutes)
        if zone[0] == "-":
            offset = -offset
    instant = datetime(
        year, month, day, hour, minute, second, microsecond, tzinfo=timezone(offset)
    )
    return instant.astimezone(_UTC)


def _instant_text(instant: datetime) -> str:
    return (
        f"{instant.year:04d}-{instant.month:02d}-{instant.day:02d} "
        f"{instant.hour:02d}:{instant.minute:02d} UTC"
    )


def format_period(value: ClockInput) -> str:
    """Print a period slot: ``YYYY-MM-DD`` for a date or a midnight UTC instant.

    Any other instant prints in full as ``YYYY-MM-DD HH:MM UTC``, so a time is
    never dropped. Nothing is converted to local time.
    """

    parsed = parse_clock(value)
    if not isinstance(parsed, datetime):
        return parsed.isoformat()
    if (parsed.hour, parsed.minute, parsed.second, parsed.microsecond) == (0, 0, 0, 0):
        return parsed.date().isoformat()
    return _instant_text(parsed)


def format_instant(value: ClockInput) -> str:
    """Print an instant as ``YYYY-MM-DD HH:MM UTC``; a bare date stays a date."""

    parsed = parse_clock(value)
    if isinstance(parsed, datetime):
        return _instant_text(parsed)
    return parsed.isoformat()


# ---------------------------------------------------------------------------
# Producer text and values.

_LINE_CONTROLS = frozenset(
    "\t\n\v\f\r\x1c\x1d\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"
)
_FORMAT_CONTROLS = frozenset(
    (
        "\N{ARABIC LETTER MARK}",
        "\N{LEFT-TO-RIGHT MARK}",
        "\N{RIGHT-TO-LEFT MARK}",
        "\N{LEFT-TO-RIGHT EMBEDDING}",
        "\N{RIGHT-TO-LEFT EMBEDDING}",
        "\N{POP DIRECTIONAL FORMATTING}",
        "\N{LEFT-TO-RIGHT OVERRIDE}",
        "\N{RIGHT-TO-LEFT OVERRIDE}",
        "\N{LEFT-TO-RIGHT ISOLATE}",
        "\N{RIGHT-TO-LEFT ISOLATE}",
        "\N{FIRST STRONG ISOLATE}",
        "\N{POP DIRECTIONAL ISOLATE}",
    )
)
# Strings that stand in for a missing figure. A value state never shows one:
# the reading becomes a gap that says so.
_PLACEHOLDERS = frozenset(
    {
        "",
        "-",
        "--",
        "---",
        "\N{FIGURE DASH}",
        "\N{EN DASH}",
        "\N{EM DASH}",
        "\N{HORIZONTAL BAR}",
        "\N{MINUS SIGN}",
        "?",
        "n/a",
        "n.a.",
        "na",
        "nan",
        "nil",
        "none",
        "null",
    }
)
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


# Typographic normalisation: the family's text never carries an en or em
# dash, so those two characters in producer text print as a hyphen-minus.
_DASHES = frozenset("\N{EN DASH}\N{EM DASH}")


def _clean(text: str) -> str:
    """Producer text verbatim, except that it can never start a new line,
    recolour a terminal or reorder a line: line and tab controls become
    spaces; other control and bidirectional formatting characters print as
    visible backslash-u escapes. As typographic normalisation, an en or em
    dash prints as a hyphen-minus."""

    out: list[str] = []
    for char in text:
        code = ord(char)
        if char in _LINE_CONTROLS:
            out.append(" ")
        elif char in _DASHES:
            out.append("-")
        elif code < 0x20 or 0x7F <= code <= 0x9F or char in _FORMAT_CONTROLS:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    return "".join(out)


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    return value if value.strip() else None


def _optional_clock(value: object, name: str) -> ClockInput | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, (str, datetime, date)):
        raise TypeError(f"{name} must be an ISO 8601 string, a date or a datetime")
    parse_clock(value)
    return value


def _shown_value(value: object) -> ValueInput | None:
    """Return a value as served, or None when it is absent or a placeholder."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("a boolean is not a reading value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, str):
        return None if value.strip().casefold() in _PLACEHOLDERS else value
    raise TypeError("a reading value must be a number or a string")


def _value_text(value: ValueInput) -> str:
    """Digits exactly as served: strings untouched, never rounded or regrouped."""

    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return repr(value)
    return str(value)


def product_name(product: str | None) -> str:
    """The product's name in words; an unknown producer prints verbatim."""

    if product is None:
        return NOT_STATED
    return _PRODUCT_NAMES.get(product, product)


@dataclass(frozen=True)
class SourceRef:
    """A source slot: publisher first, then title and link, then retrieval."""

    publisher: str
    title: str | None = None
    url: str | None = None
    retrieved_at: ClockInput | None = None
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.publisher, str):
            raise TypeError("a source's publisher must be a string")
        if not self.publisher.strip():
            raise ValueError("a source needs its publisher's name")
        for name in ("title", "url", "content_sha256"):
            text = _optional_text(getattr(self, name), f"source {name}")
            object.__setattr__(self, name, text)
        retrieved = _optional_clock(self.retrieved_at, "source retrieved_at")
        object.__setattr__(self, "retrieved_at", retrieved)


def _coerce_sources(sources: object) -> tuple[SourceRef, ...]:
    if sources is None:
        return ()
    if isinstance(sources, (str, SourceRef, Mapping)):
        sources = (sources,)
    if not isinstance(sources, Iterable):
        raise TypeError("sources must be SourceRef values, mappings or names")
    coerced: list[SourceRef] = []
    for item in sources:
        if isinstance(item, SourceRef):
            coerced.append(item)
        elif isinstance(item, str):
            coerced.append(SourceRef(publisher=item))
        elif isinstance(item, Mapping):
            publisher = item.get("publisher")
            if not isinstance(publisher, str):
                raise TypeError("a source needs its publisher's name as a string")
            coerced.append(
                SourceRef(
                    publisher=publisher,
                    title=item.get("title"),
                    url=item.get("url"),
                    retrieved_at=item.get("retrieved_at"),
                    content_sha256=item.get("content_sha256"),
                )
            )
        else:
            raise TypeError("sources must be SourceRef values, mappings or names")
    return tuple(coerced)


def _coerce_codes(codes: object) -> tuple[str, ...]:
    if codes is None:
        return ()
    if isinstance(codes, str) or not isinstance(codes, Iterable):
        raise TypeError("reason_codes must be a sequence of code strings")
    out: list[str] = []
    for code in codes:
        if not isinstance(code, str):
            raise TypeError("each reason code must be a string")
        if not code.strip():
            raise ValueError("a reason code must not be blank")
        out.append(code)
    return tuple(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Readings.


@dataclass(frozen=True)
class Reading:
    """The contract's presentation slots for one reading.

    A gap state (Partial, Stale, Restricted, Not covered, Unavailable) holds no
    value. A value state (Observed, Derived, Structural, Research, Provisional)
    must hold one; with nothing to show, the reading is a gap instead. Text
    slots keep the producer's words; ``None`` prints as "Not stated".
    """

    state: str
    label: str | None = None
    subject: str | None = None
    value: ValueInput | None = None
    unit: str | None = None
    numeric: float | None = None
    product: str | None = None
    period: ClockInput | None = None
    known_at: ClockInput | None = None
    known_basis: str | None = None
    as_of: ClockInput | None = None
    expires_at: ClockInput | None = None
    checked_at: ClockInput | None = None
    sources: tuple[SourceRef, ...] = ()
    rights_status: str | None = None
    attribution: str | None = None
    rights_license: str | None = None
    state_label: str | None = None
    severity: str | None = None
    reason: str | None = None
    reason_codes: tuple[str, ...] = ()
    limits: str | None = None
    boundary: str | None = None
    claim_kind: str | None = None
    subject_identifiers: tuple[tuple[str, str], ...] = ()
    carrier_id: str | None = None
    record_hash: str | None = None
    disposition: str | None = None

    def __post_init__(self) -> None:
        if self.state not in DISPLAY_STATES:
            raise ValueError(f"unknown display state: {self.state!r}")
        for name in (
            "label",
            "subject",
            "unit",
            "product",
            "known_basis",
            "rights_status",
            "attribution",
            "rights_license",
            "state_label",
            "severity",
            "reason",
            "limits",
            "boundary",
            "claim_kind",
            "carrier_id",
            "record_hash",
            "disposition",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        for name in ("period", "known_at", "as_of", "expires_at", "checked_at"):
            object.__setattr__(self, name, _optional_clock(getattr(self, name), name))
        object.__setattr__(self, "sources", _coerce_sources(self.sources))
        object.__setattr__(self, "reason_codes", _coerce_codes(self.reason_codes))
        raw_identifiers: Any = self.subject_identifiers
        if isinstance(raw_identifiers, Mapping):
            raw_identifiers = raw_identifiers.items()
        identifiers = tuple((str(key), str(item)) for key, item in raw_identifiers)
        object.__setattr__(self, "subject_identifiers", identifiers)
        self._check_slots()

    def _check_slots(self) -> None:
        if self.known_basis is not None and self.known_basis not in _KNOWN_BASES:
            raise ValueError("known_basis must be capture, publication or proxy")
        if self.disposition is not None and self.disposition not in DISPOSITIONS:
            raise ValueError("disposition must be full, metadata_only or reject")
        if self.severity is not None and self.severity not in _SEVERITIES:
            raise ValueError("severity must be good, warn or bad")
        if self.severity is not None and self.state in GAP_STATES:
            raise ValueError("a gap state carries no severity")
        if self.value is not None and _shown_value(self.value) is None:
            raise ValueError(
                "value is blank, a placeholder or not finite: leave it None "
                "and let the state say why"
            )
        if self.state in GAP_STATES and self.value is not None:
            raise ValueError(f"the {self.state} state never shows a value")
        if self.state in VALUE_STATES and self.value is None:
            raise ValueError(
                f"the {self.state} state needs a value; without one the "
                "reading is a gap"
            )
        if self.disposition not in (None, "full") and self.value is not None:
            raise ValueError("only a full disposition may show a value")
        if self.numeric is None:
            return
        if self.value is None:
            raise ValueError("numeric needs a shown value")
        if isinstance(self.numeric, bool) or not isinstance(
            self.numeric, (int, float, Decimal)
        ):
            raise TypeError("numeric must be a real number")
        number = float(self.numeric)
        if not math.isfinite(number):
            raise ValueError("numeric must be finite")
        object.__setattr__(self, "numeric", number)

    @property
    def display(self) -> DisplayState:
        return DISPLAY_STATES[self.state]

    @property
    def shows_value(self) -> bool:
        return self.state in VALUE_STATES and self.value is not None


# ---------------------------------------------------------------------------
# Section 3: deriving the display state.

_RULE_2_CODES = frozenset({"evidence_not_yet_known", "evidence_as_of_in_future"})
_RULE_3_CODES = frozenset({"claim_unavailable"})
_RULE_4_CODES = frozenset(
    {
        "rights_restricted",
        "rights_unknown",
        "rights_blocked",
        "claim_restricted",
        "rights_metadata_only",
        "redistribution_not_permitted",
        "rights_license_missing",
        "rights_attribution_missing",
    }
)
_RULE_6_CODES = frozenset({"evidence_expired"})
# verify_evidence_carrier gives these only with a reject disposition.
_REJECT_ONLY_CODES = frozenset(
    {
        "evidence_not_yet_known",
        "evidence_as_of_in_future",
        "rights_restricted",
        "rights_unknown",
        "rights_blocked",
    }
)
_KNOWN_CODES = frozenset(REASON_CODES)
_CLAIM_STATES = {
    "observed": OBSERVED,
    "derived": DERIVED,
    "structural": STRUCTURAL,
    "research": RESEARCH,
    "restricted": RESTRICTED,
    "unavailable": UNAVAILABLE,
}


@dataclass(frozen=True)
class StateDerivation:
    """The winning state and rule, plus everything else that applied."""

    state: str
    rule: int
    matched_rules: tuple[int, ...]
    reason_codes: tuple[str, ...]
    qualifiers: tuple[str, ...]

    @property
    def display(self) -> DisplayState:
        return DISPLAY_STATES[self.state]


def _disposition_text(value: object) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise TypeError("disposition must be full, metadata_only or reject")
    if raw not in DISPOSITIONS:
        raise ValueError(f"unknown disposition: {raw!r}")
    return raw


def _checked_outcome(
    disposition: object, reason_codes: object
) -> tuple[str | None, tuple[str, ...]]:
    """Refuse a disposition and reason codes that no verification produces."""

    text = _disposition_text(disposition)
    codes = _coerce_codes(reason_codes)
    if text is None and codes:
        raise ValueError("reason codes need the disposition they came with")
    if text == "full" and codes:
        raise ValueError("a full disposition carries no reason codes")
    if text in ("metadata_only", "reject") and not codes:
        raise ValueError(f"a {text} disposition always carries a reason code")
    if text != "reject" and _REJECT_ONLY_CODES.intersection(codes):
        raise ValueError(
            "evidence_not_yet_known, evidence_as_of_in_future, rights_restricted, "
            "rights_unknown and rights_blocked come only with a reject disposition"
        )
    return text, codes


def verification_outcome(verification: object) -> tuple[str, tuple[str, ...]]:
    """Read the disposition and reason codes from a verification result.

    Accepts the ``VerifiedEvidenceCarrier`` that ``verify_evidence_carrier``
    returns, or a mapping such as the MCP ``verify_carrier`` result
    (``export_disposition`` or ``disposition`` plus ``reason_codes``).
    """

    if isinstance(verification, Mapping):
        both = "disposition" in verification and "export_disposition" in verification
        if both and verification["disposition"] != verification["export_disposition"]:
            raise ValueError("disposition and export_disposition disagree")
        disposition = verification.get(
            "disposition", verification.get("export_disposition")
        )
        codes = verification.get("reason_codes", ())
    else:
        disposition = getattr(verification, "disposition", None)
        codes = getattr(verification, "reason_codes", None)
    text, checked = _checked_outcome(disposition, codes)
    if text is None:
        raise ValueError("the verification result has no disposition")
    return text, checked


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be True or False")
    return value


def _rule_eight(claim_status: str | None, native_state: str | None) -> str:
    native = native_state if native_state in VALUE_STATES else None
    if claim_status is not None and claim_status not in _CLAIM_STATES:
        return UNAVAILABLE
    claimed = None if claim_status is None else _CLAIM_STATES[claim_status]
    if claimed is None:
        return UNAVAILABLE if native is None else native
    if native is None:
        return claimed
    # Never calmer than either the carrier's claim or the product's own row.
    if DISPLAY_STATES[claimed].calm and not DISPLAY_STATES[native].calm:
        return native
    return claimed


def derive_display_state(
    disposition: object = None,
    reason_codes: Iterable[str] = (),
    *,
    claim_status: str | None = None,
    not_covered: bool = False,
    insufficient_history: bool = False,
    stale: bool = False,
    provisional: bool = False,
    access_refused: bool = False,
    native_state: str | None = None,
) -> StateDerivation:
    """Apply section 3 of the contract: the first matching rule wins.

    ``disposition`` and ``reason_codes`` come from verifying a carrier at the
    reader's clock; pass ``None`` and no codes when there is no carrier. The
    producer inputs are the qualifiers the contract names: not covered,
    insufficient history, the product's own stale rule and provisional. Two
    inputs extend it, each at the rule for its state: ``access_refused`` (an
    access gate withheld the reading, rule 4) and ``native_state`` (the state
    ``state_for`` gave a product's native value). Every reason code is
    returned, not only the winning one.
    """

    text, codes = _checked_outcome(disposition, reason_codes)
    flags = {
        "not_covered": _flag(not_covered, "not_covered"),
        "insufficient_history": _flag(insufficient_history, "insufficient_history"),
        "stale": _flag(stale, "stale"),
        "provisional": _flag(provisional, "provisional"),
        "access_refused": _flag(access_refused, "access_refused"),
    }
    if native_state is not None and native_state not in DISPLAY_STATES:
        raise ValueError(f"unknown native display state: {native_state!r}")
    if claim_status is not None and not isinstance(claim_status, str):
        raise TypeError("claim_status must be a string or None")

    matched: dict[int, str] = {}
    qualifiers: list[str] = []

    def hit(rule: int, state: str, qualifier: str | None = None) -> None:
        matched.setdefault(rule, state)
        if qualifier is not None:
            qualifiers.append(qualifier)

    code_set = frozenset(codes)
    if flags["not_covered"]:
        hit(1, NOT_COVERED, "not_covered")
    if native_state == NOT_COVERED:
        hit(1, NOT_COVERED, "native_state")
    if text == "reject" and code_set & _RULE_2_CODES:
        hit(2, UNAVAILABLE)
    if code_set & _RULE_3_CODES:
        hit(3, UNAVAILABLE)
    if native_state == UNAVAILABLE:
        hit(3, UNAVAILABLE, "native_state")
    # An unknown reason code still qualified the disclosure: never calm.
    if code_set & _RULE_4_CODES or code_set - _KNOWN_CODES:
        hit(4, RESTRICTED)
    if flags["access_refused"]:
        hit(4, RESTRICTED, "access_refused")
    if native_state == RESTRICTED:
        hit(4, RESTRICTED, "native_state")
    if flags["insufficient_history"]:
        hit(5, PARTIAL, "insufficient_history")
    if native_state == PARTIAL:
        hit(5, PARTIAL, "native_state")
    if code_set & _RULE_6_CODES:
        hit(6, STALE)
    if flags["stale"]:
        hit(6, STALE, "stale")
    if native_state == STALE:
        hit(6, STALE, "native_state")
    if flags["provisional"]:
        hit(7, PROVISIONAL, "provisional")
    if native_state == PROVISIONAL:
        hit(7, PROVISIONAL, "native_state")
    if not matched:
        hit(8, _rule_eight(claim_status, native_state))

    rule = min(matched)
    return StateDerivation(
        state=matched[rule],
        rule=rule,
        matched_rules=tuple(sorted(matched)),
        reason_codes=codes,
        qualifiers=tuple(dict.fromkeys(qualifiers)),
    )


# ---------------------------------------------------------------------------
# Section 4: each product's native values.


@dataclass(frozen=True)
class ProductState:
    """A product's native value as its section 4 row presents it: the display
    state, the literal label the reader sees, and a severity (good, warn or
    bad) only for a scored Undertow tier."""

    state: str
    label: str
    severity: str | None
    product: str
    native: str


_STATE_PRODUCTS = ("liquilens", "seiche", "undertow")
_RESOLUTION_STATUSES = frozenset({"resolved", "ambiguous", "not_found"})
# Checks are not readings: they print as words through check_label only.
_CHECK_WORDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "liquilens": (
            "reconciled",
            "within_rounding",
            "inconsistent",
            # Historical replay verdicts, exactly as the evidence tools serve them.
            "clean",
            "clean_pass",
            "flagged",
            "warned",
            "false_alarm",
            "missed",
            "missed_fraud_masked",
            "insufficient_data",
        ),
        # verify() results, then the sealed-record outcomes exactly as
        # undertow_mm.engine.sealed_calls serves them. VOID and PENDING are
        # not decided and raise.
        "undertow": ("match", "mismatch", "indeterminate", "HIT", "MISS"),
    }
)
# Check results a surface must always print, never cut to fit or leave out.
NEVER_HIDDEN_CHECKS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "liquilens": frozenset({"inconsistent", "missed", "missed_fraud_masked"}),
        "undertow": frozenset({"mismatch", "MISS"}),
    }
)
# Case-folded, so state_for refuses a check word in any case.
_ALL_CHECK_WORDS = frozenset(
    word.casefold() for words in _CHECK_WORDS.values() for word in words
)
_LIQUILENS_ROWS = {
    "observed": (OBSERVED, "Accepted filing evidence"),
    "stale": (STALE, "Stale evidence"),
    "historical": (STALE, "Historical evidence"),
    "unavailable": (UNAVAILABLE, "Evidence unavailable"),
    "not_disclosed": (UNAVAILABLE, "Not disclosed"),
    "provisional": (PROVISIONAL, "Provisional business update"),
    "withheld": (RESTRICTED, "Withheld"),
    "not_covered": (NOT_COVERED, "Not covered"),
    "lender_not_listed": (NOT_COVERED, "Not covered"),
    "not_in_slim": (NOT_COVERED, "Not covered"),
    "not_applicable": (NOT_COVERED, "Not applicable"),
    "not_assessed": (NOT_COVERED, "Not assessed"),
    "PARTIAL": (PARTIAL, "PARTIAL"),
    "identity": (STRUCTURAL, "Identity record"),
}
_LIQUILENS_BUSINESS_UPDATE = {
    "provisional": (PROVISIONAL, "Provisional business update"),
    "stale": (STALE, "Stale provisional business update"),
}
# LiquiLens's own stale rule: annual evidence over 400 days, other over 183.
_LIQUILENS_STALE_DAYS = {True: 400, False: 183}
# LiquiLens board and regime words: a severity beside the reading's own state,
# the same rule as a scored Undertow tier.
_LIQUILENS_SEVERITIES = {"CALM": "good", "WATCH": "warn", "ALARM": "bad"}
_SEICHE_ROWS = {
    "observed": OBSERVED,
    "derived": DERIVED,
    "structural": STRUCTURAL,
    "restricted": RESTRICTED,
    "unavailable": UNAVAILABLE,
    # Seiche's own stale rule: a series is stale once its latest observation is
    # past the aging limit for its expected cadence (StalenessState). The
    # market atlas serves the same word upper-cased.
    "stale": STALE,
    "STALE": STALE,
    # The market atlas's availability words for a metric with no publishable
    # value: RESTRICTED when its source is not an open, redistributable one,
    # UNAVAILABLE when there is nothing to publish.
    "RESTRICTED": RESTRICTED,
    "UNAVAILABLE": UNAVAILABLE,
}
_UNDERTOW_TIERS = {
    "SURPLUS": "good",
    "NORMAL": "good",
    "STRAINED": "warn",
    "DEFICIENT": "bad",
}
_UNDERTOW_GAP_WORDS = ("ACCRUING", "PARTIAL")
_UNDERTOW_NO_MEASURES = "UNAVAILABLE"
_UNDERTOW_BOARD_WORDS = ("behind", "stale", "unreachable")
_UNDERTOW_RIGHTS = "restricted"
# Per-reading evidence words: Seiche's vocabulary as Undertow serves it (for
# example undertow.gold-india.v1 evidence_state) and the Exit Atlas words
# (undertow.exit-atlas.v1 evidence_states). Lower case, so never a tier word.
_UNDERTOW_EVIDENCE = {
    "observed": (OBSERVED, "Observed"),
    "derived": (DERIVED, "Derived"),
    "structural": (STRUCTURAL, "Structural"),
    "unavailable": (UNAVAILABLE, "Unavailable"),
    "issuer_disclosed": (OBSERVED, "Issuer disclosed"),
    # A sibling lab surface's own reading: display only, never scored, so it
    # never takes the calm colour.
    "context": (RESEARCH, "Context (display only)"),
    "accruing": (PARTIAL, "Accruing"),
    "partial": (PARTIAL, "Partial"),
}
# crypto_workbench.exit_check venue, rung and overall rows, namespaced so they
# cannot collide with tier or board words.
_UNDERTOW_EXIT_CHECK = {
    "exit_check.available": (DERIVED, "Available"),
    "exit_check.unavailable": (UNAVAILABLE, "Unavailable"),
    "exit_check.partial": (PARTIAL, "Partial"),
}


def _qualifiers(
    product: str,
    native: str,
    given: Mapping[str, object],
    allowed: Mapping[str, tuple[type, ...]],
) -> dict[str, object]:
    unknown = sorted(set(given) - set(allowed))
    if unknown:
        raise TypeError(
            f"{product} {native!r} takes no qualifier named {', '.join(unknown)}"
        )
    values: dict[str, object] = {}
    for name, value in given.items():
        if value is None:
            continue
        kinds = allowed[name]
        if isinstance(value, bool) and bool not in kinds:
            raise TypeError(f"qualifier {name} must not be a boolean")
        if not isinstance(value, kinds):
            raise TypeError(f"qualifier {name} has the wrong type")
        values[name] = value
    return values


def _liquilens_state(native: str, given: Mapping[str, object]) -> ProductState:
    if native == "failure_radar":
        values = _qualifiers("liquilens", native, given, {"tier_publication": (str,)})
        # LiquiLens publishes tiers only while tier_publication.state reads
        # "published"; anything else is paused. The contract has no row for
        # published tiers, so that fails closed.
        if values.get("tier_publication") == "published":
            raise ValueError(
                "the contract has no row for published Failure Radar tiers"
            )
        return ProductState(RESEARCH, "Research only", None, "liquilens", native)
    if native in _LIQUILENS_SEVERITIES:
        # The served reading is Observed and the word is its severity. A
        # reading that is itself a gap takes its own gap row instead, and a
        # severity row whose reading has no value becomes Unavailable without
        # the word (reading_from_native).
        _qualifiers("liquilens", native, given, {})
        severity = _LIQUILENS_SEVERITIES[native]
        return ProductState(OBSERVED, native, severity, "liquilens", native)
    if given.get("provisional_business_update") is True:
        _qualifiers(
            "liquilens", native, given, {"provisional_business_update": (bool,)}
        )
        row = _LIQUILENS_BUSINESS_UPDATE.get(native)
        if row is None:
            raise ValueError(
                f"{native!r} is not a provisional business update status"
            )
        return ProductState(row[0], row[1], None, "liquilens", native)
    allowed: dict[str, tuple[type, ...]] = {"provisional_business_update": (bool,)}
    if native == "observed":
        allowed.update({"age_days": (int, float, Decimal), "annual": (bool,)})
    values = _qualifiers("liquilens", native, given, allowed)
    row = _LIQUILENS_ROWS.get(native)
    if row is None:
        raise ValueError(f"unknown LiquiLens value: {native!r}")
    if "annual" in values and "age_days" not in values:
        raise TypeError("annual needs age_days")
    age = values.get("age_days")
    if isinstance(age, (int, float, Decimal)):
        days = float(age)
        if not math.isfinite(days) or days < 0:
            raise ValueError("age_days must be a finite, non-negative number")
        # An unstated cadence takes the shorter limit, so it is never calmer.
        limit = _LIQUILENS_STALE_DAYS[values.get("annual") is True]
        if days > limit:
            return ProductState(STALE, "Stale evidence", None, "liquilens", native)
    return ProductState(row[0], row[1], None, "liquilens", native)


def _seiche_state(native: str, given: Mapping[str, object]) -> ProductState:
    _qualifiers("seiche", native, given, {})
    state = _SEICHE_ROWS.get(native)
    if state is None:
        raise ValueError(f"unknown Seiche status: {native!r}")
    return ProductState(state, native, None, "seiche", native)


def _undertow_state(native: str, given: Mapping[str, object]) -> ProductState:
    if native in _UNDERTOW_EXIT_CHECK:
        _qualifiers("undertow", native, given, {})
        state, label = _UNDERTOW_EXIT_CHECK[native]
        return ProductState(state, label, None, "undertow", native)
    if native in _UNDERTOW_EVIDENCE:
        _qualifiers("undertow", native, given, {})
        state, label = _UNDERTOW_EVIDENCE[native]
        return ProductState(state, label, None, "undertow", native)
    if native == _UNDERTOW_RIGHTS:
        # The input-rights boundary withheld the whole result.
        _qualifiers("undertow", native, given, {})
        return ProductState(RESTRICTED, "Restricted", None, "undertow", native)
    if native == _UNDERTOW_NO_MEASURES:
        # Board policy: no qualifying measures. Being scored contradicts it.
        values = _qualifiers("undertow", native, given, {"scored": (bool,)})
        if values.get("scored") is True:
            raise ValueError("an UNAVAILABLE segment cannot be scored")
        return ProductState(UNAVAILABLE, native, None, "undertow", native)
    if native in _UNDERTOW_TIERS or native in _UNDERTOW_GAP_WORDS:
        values = _qualifiers("undertow", native, given, {"scored": (bool,)})
        if native in _UNDERTOW_GAP_WORDS:
            return ProductState(PARTIAL, native, None, "undertow", native)
        if values.get("scored") is not True:
            # Not scored: a gap, and the tier word is never shown as a reading.
            return ProductState(PARTIAL, "PARTIAL", None, "undertow", native)
        severity = _UNDERTOW_TIERS[native]
        return ProductState(OBSERVED, native, severity, "undertow", native)
    values = _qualifiers(
        "undertow",
        native,
        given,
        {"ok": (bool,), "status": (str,), "note": (str,)},
    )
    status = _optional_text(values.get("status"), "status")
    note = _optional_text(values.get("note"), "note")
    if values.get("ok") is False or native == "unreachable":
        return ProductState(UNAVAILABLE, status or native, None, "undertow", native)
    if native in _UNDERTOW_BOARD_WORDS:
        return ProductState(STALE, note or native, None, "undertow", native)
    raise ValueError(f"unknown Undertow value: {native!r}")


def state_for(product: str, native: str, **qualifiers: object) -> ProductState:
    """Map a product's native value to its display state and literal label.

    This is section 4 of the contract, the one table every surface uses.
    Unknown products, unknown values and qualifiers that do not apply raise:
    nothing falls back to a calm state.

    LiquiLens values: ``observed`` ("Accepted filing evidence"); ``stale``
    ("Stale evidence"); ``historical``; ``unavailable``; ``not_disclosed``
    (Unavailable, "Not disclosed"); ``withheld`` (Restricted, "Withheld");
    ``not_covered``, ``lender_not_listed`` and ``not_in_slim`` (Not covered;
    print the page's sentence as the reading's reason); ``not_applicable``
    (Not covered, "Not applicable"; print the producer's basis as the reason);
    ``not_assessed`` (Not covered, "Not assessed"); ``PARTIAL``; ``identity``
    (Structural, "Identity record": ontology ids, names, keys, aliases and
    links); and ``provisional`` or, with ``provisional_business_update=True``,
    ``stale`` for a provisional business update. ``observed`` also takes ``age_days``
    and ``annual``: past 400 days (annual) or 183 days (other, and when
    ``annual`` is not given) it is "Stale evidence". ``failure_radar`` with
    ``tier_publication`` (the served ``tier_publication.state``) is Research,
    "Research only", with no tier or severity while tiers are paused.
    ``CALM``, ``WATCH`` and ``ALARM`` (board and regime words) are Observed
    with the word as severity (good, warn, bad), like a scored Undertow tier;
    a reading that is itself a gap takes its own gap row instead.

    Seiche values: ``observed``, ``derived``, ``structural``, ``restricted``
    and ``unavailable``, each labelled with its own word. ``stale`` and the
    market atlas's ``STALE`` (Seiche's own stale rule) are Stale; the atlas's
    availability words ``RESTRICTED`` and ``UNAVAILABLE`` are Restricted and
    Unavailable. Freshness words that are not states (``fresh``, ``aging``,
    ``dead``, ``unknown`` in either case) and coverage words such as
    ``available`` or ``partial`` raise.

    Undertow values: a tier word (``SURPLUS``, ``NORMAL``, ``STRAINED``,
    ``DEFICIENT``) with ``scored=True`` is Observed with the tier as severity
    (good, good, warn, bad); without ``scored=True`` it is Partial labelled
    "PARTIAL". ``ACCRUING`` and ``PARTIAL`` are Partial. ``UNAVAILABLE`` (no
    qualifying measures) is Unavailable, and ``scored=True`` with it raises.
    Board ``behind`` and ``stale`` are Stale labelled with ``note`` (the
    board's own note); ``unreachable``, or any board value with ``ok=False``,
    is Unavailable labelled with ``status``. Exit checks are namespaced:
    ``exit_check.available`` (Derived, "Available"), ``exit_check.unavailable``
    and ``exit_check.partial``; print the producer's reason codes as the
    reading's reason. ``restricted`` (the input-rights boundary withheld the
    whole result) is Restricted; pass the ``source_rights`` summary as the
    reason or limits. A reading's own evidence state, in lower case, maps
    too: Seiche's words ``observed``, ``derived``, ``structural`` and
    ``unavailable`` (the same state, labelled with the word capitalised) and
    the Exit Atlas words ``issuer_disclosed`` (Observed, "Issuer disclosed"),
    ``context`` (Research, "Context (display only)": a sibling lab surface's
    reading, never scored), ``accruing`` (Partial, "Accruing") and ``partial``
    (Partial, "Partial"). Licence values such as ``consent_required`` are not
    states: they belong in the reading's rights and limits, and raise here.

    Check words (LiquiLens reconciliation, Undertow ``verify()`` and sealed
    outcomes) and name
    resolution statuses are not readings: they raise here; use
    ``check_label`` and ``resolution_notice``.
    """

    if not isinstance(product, str):
        raise TypeError("product must be a string")
    if product not in _STATE_PRODUCTS:
        raise ValueError(f"the contract has no section 4 rows for {product!r}")
    if not isinstance(native, str):
        raise TypeError("native must be the product's served value, a string")
    folded = native.strip().casefold()
    if folded in _RESOLUTION_STATUSES:
        raise ValueError(
            f"{native!r} is a name-resolution status, not a display state; "
            "use resolution_notice()"
        )
    if folded in _ALL_CHECK_WORDS:
        raise ValueError(
            f"{native!r} is a check result, not a reading; use check_label()"
        )
    if product == "liquilens":
        return _liquilens_state(native, qualifiers)
    if product == "seiche":
        return _seiche_state(native, qualifiers)
    return _undertow_state(native, qualifiers)


def check_label(product: str, word: str) -> str:
    """The human word for a check result, which is not a reading.

    LiquiLens reconciliation (``reconciled``, ``within_rounding``,
    ``inconsistent``), LiquiLens historical replay verdicts (``clean``,
    ``clean_pass``, ``flagged``, ``warned``, ``false_alarm``, ``missed``,
    ``missed_fraud_masked``, ``insufficient_data``), Undertow ``verify()``
    (``match``, ``mismatch``, ``indeterminate``) and Undertow sealed-record
    outcomes (``HIT``, ``MISS``). The word gets no display state, mark or state
    colour. The words in ``NEVER_HIDDEN_CHECKS`` (``inconsistent``, ``missed``,
    ``missed_fraud_masked``, ``mismatch``, ``MISS``) are always printed, never
    cut to fit, and ``indeterminate`` is never a pass or a mismatch. Unknown
    words raise, including the sealed outcomes ``VOID`` and ``PENDING``.
    """

    if not isinstance(product, str) or not isinstance(word, str):
        raise TypeError("product and word must be strings")
    words = _CHECK_WORDS.get(product)
    if words is None:
        raise ValueError(f"no check words are defined for {product!r}")
    if word not in words:
        raise ValueError(f"unknown {product} check result: {word!r}")
    text = word.replace("_", " ")
    return text[:1].upper() + text[1:]


def resolution_notice(status: str, *, candidates: int | None = None) -> str | None:
    """Words for a name lookup's resolution status, which is not a display state.

    ``not_found`` means the name matched nothing: the subject may still be
    covered under another name or key, so it is never "Not covered".
    ``resolved`` needs no notice and returns None.
    """

    if status == "resolved":
        return None
    if status == "not_found":
        return "No match for this name"
    if status != "ambiguous":
        raise ValueError(f"unknown resolution status: {status!r}")
    if candidates is None:
        return f"Ambiguous (candidates: {NOT_STATED})"
    if isinstance(candidates, bool) or not isinstance(candidates, int):
        raise TypeError("candidates must be an integer count")
    if candidates < 0:
        raise ValueError("candidates cannot be negative")
    noun = "candidate" if candidates == 1 else "candidates"
    return f"Ambiguous ({candidates} {noun})"


# ---------------------------------------------------------------------------
# Building readings.


def _mapping_at(container: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    item = container.get(key)
    if not isinstance(item, Mapping):
        raise TypeError(f"{where}.{key} must be an object")
    return item


def _text_at(container: Mapping[str, Any], key: str, where: str) -> str | None:
    item = container.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise TypeError(f"{where}.{key} must be a string")
    return item


def _path_parts(path: object, name: str) -> tuple[str | int, ...]:
    parts: tuple[Any, ...]
    if isinstance(path, str):
        parts = (path,)
    elif isinstance(path, Sequence):
        parts = tuple(path)
    else:
        raise TypeError(f"{name} must be a key or a sequence of keys and indexes")
    if not parts:
        raise ValueError(f"{name} must not be empty")
    for part in parts:
        if isinstance(part, bool) or not isinstance(part, (str, int)):
            raise TypeError(f"{name} holds keys (str) and list indexes (int) only")
    return parts


def _lookup(payload: Mapping[str, Any], parts: tuple[str | int, ...]) -> object:
    node: Any = payload
    for part in parts:
        keyed = isinstance(node, Mapping) and isinstance(part, str) and part in node
        indexed = (
            isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node)
        )
        if not (keyed or indexed):
            return None
        node = node[part]
    if isinstance(node, (Mapping, list)):
        raise TypeError("the payload path leads to an object or a list, not a value")
    return node


def _check_identity(carrier: Mapping[str, Any], verification: object) -> None:
    theirs: Mapping[str, Any] | None
    if isinstance(verification, Mapping):
        theirs = verification
    else:
        attached = getattr(verification, "carrier", None)
        theirs = attached if isinstance(attached, Mapping) else None
    if theirs is None:
        return
    for key in ("carrier_id", "record_hash"):
        if key in theirs and theirs[key] != carrier.get(key):
            raise ValueError(f"the verification result is for another carrier ({key})")


def _check_verification_clock(verification: object, checked: datetime) -> None:
    if not isinstance(verification, Mapping) or not verification.get("evaluated_at"):
        return
    if parse_clock(verification["evaluated_at"]) != checked:
        raise ValueError(
            "the verification was evaluated at a different clock than evaluated_at"
        )


def _carrier_sources(carrier: Mapping[str, Any]) -> tuple[SourceRef, ...]:
    sources = carrier.get("sources")
    if not isinstance(sources, list):
        raise TypeError("carrier.sources must be an array")
    return _coerce_sources(sources)


def _carrier_number(found: object) -> float | None:
    if found is None:
        return None
    if isinstance(found, bool) or not isinstance(found, (int, float, Decimal)):
        raise TypeError("the value at numeric_path must be a number")
    return float(found)


def reading_from_carrier(
    carrier: Mapping[str, Any],
    verification: object,
    *,
    evaluated_at: ClockInput,
    value_path: str | Sequence[str | int],
    unit: str | None = None,
    unit_path: str | Sequence[str | int] | None = None,
    numeric_path: str | Sequence[str | int] | None = None,
    insufficient_history: bool = False,
    stale: bool = False,
    provisional: bool = False,
    product_state: ProductState | None = None,
    label: str | None = None,
    reason: str | None = None,
    limits: str | None = None,
    boundary: str | None = None,
    known_basis: str | None = None,
) -> Reading:
    """Build the slots for one carrier verified at ``evaluated_at``.

    ``verification`` is what ``verify_evidence_carrier`` returned at that
    clock (or the MCP ``verify_carrier`` result); this module never verifies.
    The value is read from ``value_path`` in the payload, and only when the
    disposition is ``full``. A ``reject`` disposition discloses nothing from
    the carrier beyond the product, identity and reason codes; ``label`` then
    names what the surface asked for. A disclosed claim summary is always the
    label, verbatim. ``product_state`` (from ``state_for``) adds the product's
    own row: it can make the reading less calm, never calmer, and its label
    and severity show only when its state is the one displayed.
    """

    if not isinstance(carrier, Mapping):
        raise TypeError("carrier must be the carrier JSON object")
    if product_state is not None and not isinstance(product_state, ProductState):
        raise TypeError("product_state must come from state_for")
    if unit is not None and unit_path is not None:
        raise ValueError("pass unit or unit_path, not both")
    disposition, codes = verification_outcome(verification)
    _check_identity(carrier, verification)
    checked = parse_clock(evaluated_at)
    if not isinstance(checked, datetime):
        raise TypeError("evaluated_at must be an instant, not a date")
    _check_verification_clock(verification, checked)
    value_parts = _path_parts(value_path, "value_path")
    unit_parts = None if unit_path is None else _path_parts(unit_path, "unit_path")
    numeric_parts = (
        None if numeric_path is None else _path_parts(numeric_path, "numeric_path")
    )
    if carrier.get("authority") != _AUTHORITY_BOUNDARY:
        raise ValueError("carrier.authority must keep the all-false boundary")
    producer = _mapping_at(carrier, "producer", "carrier")
    claim = _mapping_at(carrier, "claim", "carrier")
    derivation = derive_display_state(
        disposition,
        codes,
        claim_status=_text_at(claim, "status", "claim"),
        insufficient_history=insufficient_history,
        stale=stale,
        provisional=provisional,
        native_state=None if product_state is None else product_state.state,
    )
    state = derivation.state

    shown: ValueInput | None = None
    unit_value = unit
    numeric: float | None = None
    if disposition == "full":
        payload = _mapping_at(carrier, "payload", "carrier")
        if unit_parts is not None:
            found_unit = _lookup(payload, unit_parts)
            if found_unit is not None and not isinstance(found_unit, str):
                raise TypeError("the unit at unit_path must be a string")
            unit_value = found_unit
        if state in VALUE_STATES:
            shown = _shown_value(_lookup(payload, value_parts))
            if shown is None:
                # A value state never shows without a value: it is a gap.
                state = UNAVAILABLE
            elif numeric_parts is not None:
                numeric = _carrier_number(_lookup(payload, numeric_parts))

    own_row = product_state if product_state and product_state.state == state else None
    common: dict[str, Any] = {
        "state": state,
        "value": shown,
        "unit": unit_value,
        "numeric": numeric,
        "product": _text_at(producer, "name", "producer"),
        "checked_at": evaluated_at,
        "state_label": None if own_row is None else own_row.label,
        "severity": None if own_row is None else own_row.severity,
        "reason": reason,
        "reason_codes": derivation.reason_codes,
        "limits": limits,
        "boundary": boundary,
        "known_basis": known_basis,
        "carrier_id": _text_at(carrier, "carrier_id", "carrier"),
        "record_hash": _text_at(carrier, "record_hash", "carrier"),
        "disposition": disposition,
    }
    if disposition == "reject":
        return Reading(label=label, **common)

    subject = _mapping_at(carrier, "subject", "carrier")
    clocks = _mapping_at(carrier, "clocks", "carrier")
    rights = _mapping_at(carrier, "rights", "carrier")
    identifiers = subject.get("identifiers")
    pairs: tuple[tuple[str, str], ...] = ()
    if isinstance(identifiers, Mapping):
        pairs = tuple((str(key), str(item)) for key, item in identifiers.items())
    return Reading(
        label=_text_at(claim, "summary", "claim"),
        subject=_text_at(subject, "name", "subject"),
        subject_identifiers=pairs,
        claim_kind=_text_at(claim, "kind", "claim"),
        period=_text_at(clocks, "event_time", "clocks"),
        known_at=_text_at(clocks, "knowledge_time", "clocks"),
        as_of=_text_at(clocks, "as_of", "clocks"),
        expires_at=_text_at(clocks, "expires_at", "clocks"),
        sources=_carrier_sources(carrier),
        rights_status=_text_at(rights, "status", "rights"),
        attribution=_text_at(rights, "attribution", "rights"),
        rights_license=_text_at(rights, "license", "rights"),
        **common,
    )


def reading_from_native(
    product_state: ProductState,
    *,
    label: str | None = None,
    subject: str | None = None,
    value: object = None,
    unit: str | None = None,
    numeric: float | None = None,
    period: ClockInput | None = None,
    known_at: ClockInput | None = None,
    known_basis: str | None = None,
    as_of: ClockInput | None = None,
    checked_at: ClockInput | None = None,
    sources: object = (),
    attribution: str | None = None,
    reason: str | None = None,
    limits: str | None = None,
    boundary: str | None = None,
) -> Reading:
    """Build the slots for a product's native JSON reading (no carrier).

    ``product_state`` comes from ``state_for`` and decides the state. A gap
    state drops any value it was given; a value state with nothing to show
    becomes Unavailable.
    """

    if not isinstance(product_state, ProductState):
        raise TypeError("product_state must come from state_for")
    state = derive_display_state(native_state=product_state.state).state
    shown: ValueInput | None = None
    if state in VALUE_STATES:
        shown = _shown_value(value)
        if shown is None:
            state = UNAVAILABLE
    own_row = state == product_state.state
    return Reading(
        state=state,
        product=product_state.product,
        label=label,
        subject=subject,
        value=shown,
        unit=unit,
        numeric=numeric if shown is not None else None,
        period=period,
        known_at=known_at,
        known_basis=known_basis,
        as_of=as_of,
        checked_at=checked_at,
        sources=_coerce_sources(sources),
        attribution=attribution,
        state_label=product_state.label if own_row else None,
        severity=product_state.severity if own_row else None,
        reason=reason,
        limits=limits,
        boundary=boundary,
    )


def reading_not_covered(
    *,
    message: str | None,
    product: str | None = None,
    label: str | None = None,
    subject: str | None = None,
    checked_at: ClockInput | None = None,
    boundary: str | None = None,
) -> Reading:
    """An uncovered subject has no carrier: Not covered with the producer's
    message, printed verbatim as the reason."""

    state = derive_display_state(not_covered=True).state
    return Reading(
        state=state,
        product=product,
        label=label,
        subject=subject,
        reason=message,
        checked_at=checked_at,
        boundary=boundary,
    )


def reading_access_refused(
    *,
    message: str | None,
    product: str | None = None,
    label: str | None = None,
    subject: str | None = None,
    checked_at: ClockInput | None = None,
    http_status: int | None = None,
    boundary: str | None = None,
) -> Reading:
    """An access gate's refusal (HTTP 401 or 402 asking for a key).

    It shows as Restricted with the literal label "Withheld" and the gate's
    own message as the reason, verbatim. No value is ever shown.
    """

    if http_status is not None and (
        isinstance(http_status, bool) or http_status not in (401, 402)
    ):
        raise ValueError("an access-gate refusal is HTTP 401 or 402")
    state = derive_display_state(access_refused=True).state
    return Reading(
        state=state,
        product=product,
        label=label,
        subject=subject,
        state_label="Withheld",
        reason=message,
        checked_at=checked_at,
        boundary=boundary,
    )


# ---------------------------------------------------------------------------
# Plain text (contract section 5).


def _require_mode(value: str, allowed: tuple[str, ...], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}")


def _paint(text: str, role: str | None, colour: str) -> str:
    """Colour one span with a role's foreground code. Never a background."""

    if colour == "none" or role is None:
        return text
    code = _ANSI_EXPORT["roles"][role][colour]
    return f"\x1b[{code}m{text}{_ANSI_EXPORT['reset']}"


def _title(reading: Reading) -> str:
    if reading.label is not None:
        head = reading.label
    elif reading.product is not None:
        head = product_name(reading.product)
    else:
        head = NOT_STATED
    parts = [head] if reading.subject is None else [head, reading.subject]
    return _clean(", ".join(parts))


def _publishers(reading: Reading) -> str:
    names = dict.fromkeys(_clean(source.publisher) for source in reading.sources)
    return "; ".join(names)


def _shows_label(reading: Reading) -> bool:
    label = reading.state_label
    word = DISPLAY_STATES[reading.state].word
    return label is not None and label.casefold() != word.casefold()


def _state_text(reading: Reading, marks: str, colour: str) -> str:
    state = DISPLAY_STATES[reading.state]
    mark = state.mark if marks == "unicode" else state.mark_ascii
    text = _paint(f"{mark} {state.word}", state.role, colour)
    if _shows_label(reading) and reading.state_label is not None:
        severity = None if state.key in GAP_STATES else reading.severity
        text += f" ({_paint(_clean(reading.state_label), severity, colour)})"
    return text


def _known_text(known_at: ClockInput, basis: str | None) -> str:
    text = format_period(known_at)
    if basis == "proxy":
        text += " (availability proxy)"
    return text


def _codes_text(reading: Reading) -> str:
    return ", ".join(_clean(code) for code in reading.reason_codes)


def _gap_lines(reading: Reading, title: str, head: str, codes: str) -> list[str]:
    """Gap form: the state replaces the value and the reason follows."""

    segments = []
    if reading.reason is not None:
        segments.append(f"reason: {_clean(reading.reason)}")
        if codes:
            segments.append(f"codes: {codes}")
    else:
        segments.append(f"reason: {codes or NOT_STATED}")
    if reading.period is not None:
        segments.append(f"period {format_period(reading.period)}")
    if reading.known_at is not None:
        segments.append(f"known {_known_text(reading.known_at, reading.known_basis)}")
    if reading.sources:
        segments.append(f"source {_publishers(reading)}")
    if reading.attribution is not None:
        segments.append(f"attribution {_clean(reading.attribution)}")
    checked = (
        NOT_STATED if reading.checked_at is None else format_instant(reading.checked_at)
    )
    segments.append(f"checked {checked}")
    lines = [f"{title}: {head}", " | ".join(segments)]
    if reading.limits is not None:
        lines.append(f"Limits: {_clean(reading.limits)}")
    return lines


def _value_lines(
    reading: Reading, value: ValueInput, title: str, head: str, codes: str
) -> list[str]:
    """Value form: value line, state and clock line, then limits."""

    unit = _clean(reading.unit) if reading.unit is not None else f"(unit {NOT_STATED})"
    period = NOT_STATED if reading.period is None else format_period(reading.period)
    known = (
        NOT_STATED
        if reading.known_at is None
        else _known_text(reading.known_at, reading.known_basis)
    )
    segments = [
        head,
        f"period {period}",
        f"known {known}",
        f"source {_publishers(reading) or NOT_STATED}",
    ]
    if reading.attribution is not None:
        segments.append(f"attribution {_clean(reading.attribution)}")
    lines = [f"{title}: {_clean(_value_text(value))} {unit}", " | ".join(segments)]
    if reading.limits is not None:
        third = f"Limits: {_clean(reading.limits)}"
        lines.append(f"{third} | codes: {codes}" if codes else third)
    elif codes:
        lines.append(f"codes: {codes}")
    return lines


def _reading_lines(reading: Reading, marks: str, colour: str) -> list[str]:
    title = _title(reading)
    head = _state_text(reading, marks, colour)
    codes = _codes_text(reading)
    if reading.state in GAP_STATES or reading.value is None:
        return _gap_lines(reading, title, head, codes)
    return _value_lines(reading, reading.value, title, head, codes)


def _readings(readings: Reading | Iterable[Reading]) -> list[Reading]:
    items = [readings] if isinstance(readings, Reading) else list(readings)
    for item in items:
        if not isinstance(item, Reading):
            raise TypeError("render a Reading or an iterable of Readings")
    return items


def _boundaries(items: list[Reading], boundary: str | None) -> list[str]:
    if boundary is not None:
        if not isinstance(boundary, str):
            raise TypeError("boundary must be the producer's sentence")
        if not boundary.strip():
            raise ValueError("boundary must not be blank")
        return [_clean(boundary)]
    found = dict.fromkeys(
        _clean(item.boundary) for item in items if item.boundary is not None
    )
    return list(found) or [DEFAULT_BOUNDARY]


def render_reading(
    reading: Reading, *, marks: str = "unicode", colour: str = "none"
) -> str:
    """One reading: value line, state and clock line, then limits when present.

    A gap prints its state in place of the value, then a reason line. Colour
    goes only on the mark and state word (and a scored Undertow tier word),
    never on a background or on body text.
    """

    if not isinstance(reading, Reading):
        raise TypeError("reading must be a Reading")
    _require_mode(marks, MARK_MODES, "marks")
    _require_mode(colour, COLOUR_MODES, "colour")
    return "\n".join(_reading_lines(reading, marks, colour))


def _count_line(items: list[Reading]) -> str:
    parts = []
    for key in STATE_ORDER:
        count = sum(1 for item in items if item.state == key)
        if count:
            parts.append(f"{DISPLAY_STATES[key].word} {count}")
    return f"Readings: {len(items)}. " + ", ".join(parts) + "."


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _capped(items: list[Reading], blocks: list[str], boundary: str, max_bytes: int) -> str:
    """Gap readings first, then the rest in served order, cut from the end."""

    head = [_count_line(items)] if len(items) > 1 else []
    sizes = [_utf8_size(block) for block in blocks]
    fixed = sum(_utf8_size(line) for line in head) + _utf8_size(boundary)
    cut_size = _utf8_size(FULL_LIST_LINE)

    def total(shown: int) -> int:
        cut = shown < len(blocks)
        parts = len(head) + shown + int(cut) + 1
        return fixed + sum(sizes[:shown]) + (cut_size if cut else 0) + 2 * (parts - 1)

    shown = len(blocks)
    if total(shown) > max_bytes:
        shown = -1
        while shown + 1 < len(blocks) and total(shown + 1) <= max_bytes:
            shown += 1
        if shown < 0 and total(0) > max_bytes:
            raise ValueError(
                "max_bytes is too small for the count line, the closing line "
                "and the boundary"
            )
        shown = max(shown, 0)
    parts = head + blocks[:shown]
    if shown < len(blocks):
        parts.append(FULL_LIST_LINE)
    parts.append(boundary)
    return "\n\n".join(parts)


def render_text(
    readings: Reading | Iterable[Reading],
    *,
    marks: str = "unicode",
    colour: str = "none",
    boundary: str | None = None,
    max_bytes: int | None = None,
) -> str:
    """A view: each reading as a block, then the boundary sentence once.

    Without an explicit ``boundary``, each distinct producer sentence carried
    by the readings prints once; with none, the contract's default does.

    With ``max_bytes`` the view fits in that many UTF-8 bytes and is laid out
    for a reader who may not see every reading: a count of the readings per
    display state first (when there are two or more), then every gap reading,
    then the other readings in served order, and, when any reading had to be
    cut, the line "Full list in the JSON block." before the boundary. Readings
    are cut from the end of that order, so a gap is never cut before a calm
    reading. The count, the closing line and the boundary are never cut; when
    they alone do not fit, ``ValueError``.
    """

    _require_mode(marks, MARK_MODES, "marks")
    _require_mode(colour, COLOUR_MODES, "colour")
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
    ):
        raise TypeError("max_bytes must be a whole number of bytes")
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    items = _readings(readings)
    boundary_text = "\n".join(_boundaries(items, boundary))
    if max_bytes is None:
        blocks = ["\n".join(_reading_lines(item, marks, colour)) for item in items]
        return "\n\n".join([*blocks, boundary_text])
    ordered = [item for item in items if item.state in GAP_STATES]
    ordered += [item for item in items if item.state not in GAP_STATES]
    blocks = ["\n".join(_reading_lines(item, marks, colour)) for item in ordered]
    return _capped(items, blocks, boundary_text, max_bytes)


# ---------------------------------------------------------------------------
# Notebook HTML (PORTABLE-SURFACES, Python and Jupyter).


def _h(text: str) -> str:
    return html.escape(_clean(text), quote=True)


def _font_stack(names: Sequence[str]) -> str:
    return ",".join(f"'{name}'" if " " in name else name for name in names)


def _rgba(token: tuple[str, float]) -> str:
    hex_value, alpha = token
    red, green, blue = (int(hex_value[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{alpha})"


def _https_href(url: str | None) -> str | None:
    if url is None or not url.lower().startswith("https://"):
        return None
    if any(char.isspace() or ord(char) < 0x20 or char in "<>\"'`\\" for char in url):
        return None
    if any(char in _DASHES for char in url):
        # Normalising a dash would change the link target: no link at all.
        return None
    return html.escape(url, quote=True)


def _source_html(source: SourceRef) -> str:
    tip = [source.title] if source.title else []
    if source.retrieved_at is not None:
        tip.append(f"retrieved {format_instant(source.retrieved_at)}")
    if source.content_sha256:
        tip.append(f"sha256 {source.content_sha256}")
    title = f' title="{_h("; ".join(tip))}"' if tip else ""
    href = _https_href(source.url)
    name = _h(source.publisher)
    if href is None:
        return f"<span{title}>{name}</span>"
    accent = _FAMILY_TOKENS["color.accent"]
    return f'<a href="{href}"{title} style="color:{accent}">{name}</a>'


def _role_colour(role: str | None) -> str | None:
    return None if role is None else str(_FAMILY_TOKENS[f"color.{role}"])


def _html_styles() -> dict[str, str]:
    t = _FAMILY_TOKENS
    line = f"1px solid {t['color.line']}"
    pad = f"{t['space.1']}px {t['space.2']}px"
    return {
        "wrap": (
            f"background-color:{t['color.surface']};color:{t['color.text']};"
            f"font-family:{_font_stack(t['font.family.text'])};"
            f"font-size:{t['font.size.small']}px;"
            f"line-height:{t['font.leading.tight']};"
            f"padding:{t['space.3']}px {t['space.4']}px;border:{line};"
            f"border-radius:{t['radius.panel']}px;overflow-x:auto"
        ),
        "table": (
            f"border-collapse:collapse;width:100%;color:{t['color.text']};"
            f"background-color:{t['color.surface']};"
            "font-variant-numeric:tabular-nums lining-nums"
        ),
        "th": (
            f"padding:{pad};color:{t['color.muted']};"
            f"font-weight:{t['font.weight.medium']};border-bottom:{line};"
            "white-space:nowrap"
        ),
        "td": f"padding:{pad};border-bottom:{line};vertical-align:top",
        "caption": (
            f"color:{t['color.muted']};font-size:{t['font.size.label']}px;"
            f"margin:{t['space.2']}px 0 0"
        ),
        "code": f"font-family:{_font_stack(t['font.family.code'])}",
    }


_HTML_HEADERS = (
    ("Product", "left"),
    ("Subject", "left"),
    ("Reading", "left"),
    ("Value", "right"),
    ("Unit", "left"),
    ("State", "left"),
    ("Period", "left"),
    ("Known at", "left"),
    ("Reason and limits", "left"),
)


def _html_row(item: Reading, styles: Mapping[str, str]) -> str:
    td = styles["td"]
    state = DISPLAY_STATES[item.state]
    role_colour = _role_colour(state.role)
    state_cell = (
        f'<span style="color:{role_colour}">{_h(f"{state.mark} {state.word}")}</span>'
    )
    if _shows_label(item) and item.state_label is not None:
        label_html = _h(item.state_label)
        tier_colour = _role_colour(None if state.gap else item.severity)
        if tier_colour is not None:
            label_html = f'<span style="color:{tier_colour}">{label_html}</span>'
        state_cell += f" ({label_html})"
    if state.gap or item.value is None:
        soft = _FAMILY_TOKENS["color.bad-soft" if state.role == "bad" else "color.warn-soft"]
        value_cell = (
            f'<td style="{td};text-align:right;color:{role_colour};'
            "background-image:repeating-linear-gradient(135deg,"
            f'{_rgba(soft)} 0 3px,transparent 3px 7px)">{_h(state.word)}</td>'
        )
    else:
        value_cell = (
            f'<td style="{td};text-align:right;white-space:nowrap">'
            f"{_h(_value_text(item.value))}</td>"
        )
    subject_title = ""
    if item.subject_identifiers:
        ids = ", ".join(f"{key}: {value}" for key, value in item.subject_identifiers)
        subject_title = f' title="{_h(ids)}"'
    notes: list[str] = []
    if item.reason is not None:
        notes.append(f"reason: {_h(item.reason)}")
    if item.reason_codes:
        notes.append(f"codes: {_h(', '.join(item.reason_codes))}")
    if item.limits is not None:
        notes.append(f"Limits: {_h(item.limits)}")
    if not notes and state.gap:
        notes.append(f"reason: {NOT_STATED}")
    period = NOT_STATED if item.period is None else format_period(item.period)
    known = (
        NOT_STATED
        if item.known_at is None
        else _known_text(item.known_at, item.known_basis)
    )
    cells = (
        f'<td style="{td}">{_h(product_name(item.product))}</td>',
        f'<td style="{td}"{subject_title}>{_h(item.subject or NOT_STATED)}</td>',
        f'<td style="{td}">{_h(item.label or NOT_STATED)}</td>',
        value_cell,
        f'<td style="{td}">{_h(item.unit or NOT_STATED)}</td>',
        f'<td style="{td};white-space:nowrap">{state_cell}</td>',
        f'<td style="{td};white-space:nowrap">{_h(period)}</td>',
        f'<td style="{td};white-space:nowrap">{_h(known)}</td>',
        f'<td style="{td}">{"<br>".join(notes)}</td>',
    )
    return f"<tr>{''.join(cells)}</tr>"


def _html_footer(items: list[Reading], styles: Mapping[str, str]) -> str:
    sources: dict[tuple[str, str | None], SourceRef] = {}
    for item in items:
        for source in item.sources:
            sources.setdefault((source.publisher, source.url), source)
    source_html = "; ".join(_source_html(source) for source in sources.values())
    as_of = dict.fromkeys(
        format_instant(item.as_of) for item in items if item.as_of is not None
    )
    checked = dict.fromkeys(
        format_instant(item.checked_at) for item in items if item.checked_at is not None
    )
    code = f'<span style="{styles["code"]}">'
    parts = [f"Source: {source_html or NOT_STATED}"]
    if as_of:
        parts.append(f"as of {code}{_h(', '.join(as_of))}</span>")
    parts.append(f"checked {code}{_h(', '.join(checked) or NOT_STATED)}</span>")
    return f'<p style="{styles["caption"]}">{" | ".join(parts)}</p>'


def render_html(
    readings: Reading | Iterable[Reading], *, boundary: str | None = None
) -> str:
    """A self-contained HTML table on an explicit dark ground from the tokens.

    Figures are right-aligned in tabular numerals; a gap's value cell holds its
    state word over a hatch; the state cell holds mark and word; a source and
    clock line and the boundary sentence follow the table. There is no script,
    no stylesheet, no external font or resource, and every producer string is
    escaped.
    """

    items = _readings(readings)
    styles = _html_styles()
    head = "".join(
        f'<th scope="col" style="{styles["th"]};text-align:{align}">{name}</th>'
        for name, align in _HTML_HEADERS
    )
    rows = "".join(_html_row(item, styles) for item in items)
    boundary_html = "<br>".join(_h(text) for text in _boundaries(items, boundary))
    return (
        f'<div style="{styles["wrap"]}">'
        f'<table style="{styles["table"]}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        f"{_html_footer(items, styles)}"
        f'<p style="{styles["caption"]}">{boundary_html}</p>'
        "</div>"
    )


class ReadingTable:
    """A notebook table of readings: ``display(ReadingTable(readings))``."""

    def __init__(
        self, readings: Reading | Iterable[Reading], *, boundary: str | None = None
    ) -> None:
        self.readings: tuple[Reading, ...] = tuple(_readings(readings))
        self.boundary = boundary
        _boundaries(list(self.readings), boundary)

    def _repr_html_(self) -> str:
        return render_html(self.readings, boundary=self.boundary)

    def to_text(self, *, marks: str = "unicode", colour: str = "none") -> str:
        return render_text(
            self.readings, marks=marks, colour=colour, boundary=self.boundary
        )

    def __repr__(self) -> str:
        return self.to_text()


# ---------------------------------------------------------------------------
# Data frames: display columns beside the carrier's flat row.


def reading_number(reading: Reading) -> float:
    """The value as a float for a numeric column: NaN when absent, never 0.

    This always returns NaN for an absent value, which suits pandas and other
    data frames. NaN is not valid JSON: for a JSON surface use
    ``add_display_columns(..., json_safe=True)``, which gives None instead.
    Numbers convert directly. A string converts only when it is a plain
    machine number; a producer-formatted string (lakh grouping, for example)
    gives NaN unless the reading carries ``numeric``.
    """

    if reading.state in GAP_STATES or reading.value is None:
        return math.nan
    if reading.numeric is not None:
        return float(reading.numeric)
    value = reading.value
    plain = isinstance(value, str) and _PLAIN_NUMBER.fullmatch(value.strip())
    if not plain and not isinstance(value, (int, float, Decimal)):
        return math.nan
    try:
        number = float(value)
    except OverflowError:
        return math.nan
    return number if math.isfinite(number) else math.nan


def _non_finite_path(value: object, path: str) -> str | None:
    if isinstance(value, float) and not math.isfinite(value):
        return path
    if isinstance(value, Decimal) and not value.is_finite():
        return path
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _non_finite_path(item, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _non_finite_path(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def add_display_columns(
    row: Mapping[str, Any], reading: Reading, *, json_safe: bool = False
) -> dict[str, Any]:
    """Return ``row`` (for example ``to_flat_row`` output) plus the display
    columns ``display_state``, ``value``, ``unit`` and ``source_publisher``.

    No existing column is renamed, reordered or changed. ``display_state`` is
    the literal state word; ``value`` is a float and never 0 for an absent
    value. The default form is for pandas and other data frames: an absent
    value is NaN. ``json_safe=True`` is the form for JSON surfaces (APIs,
    OpenBB ``OBBject`` rows, Workspace widgets): an absent value is None, and
    a row that would carry NaN or Infinity anywhere raises instead.
    """

    if not isinstance(row, Mapping):
        raise TypeError("row must be a mapping such as to_flat_row output")
    if not isinstance(reading, Reading):
        raise TypeError("reading must be a Reading")
    _flag(json_safe, "json_safe")
    if json_safe:
        bad = _non_finite_path(dict(row), "row")
        if bad is not None:
            raise ValueError(f"{bad} is NaN or Infinity, which is not valid JSON")
    clash = [name for name in DISPLAY_COLUMNS if name in row]
    if clash:
        raise ValueError(f"row already has display columns: {', '.join(clash)}")
    identity = (("carrier_id", reading.carrier_id), ("record_hash", reading.record_hash))
    for key, ours in identity:
        if ours is not None and key in row and row[key] != ours:
            raise ValueError(f"row and reading describe different carriers ({key})")
    out = dict(row)
    number = reading_number(reading)
    out["display_state"] = DISPLAY_STATES[reading.state].word
    out["value"] = None if json_safe and math.isnan(number) else number
    out["unit"] = reading.unit if reading.unit is not None else NOT_STATED
    publishers = dict.fromkeys(source.publisher for source in reading.sources)
    out["source_publisher"] = "; ".join(publishers) or NOT_STATED
    return out


# ---------------------------------------------------------------------------
# Terminals: environment reading kept apart, so the renderers stay pure.


def terminal_style(
    stream: object = None, environ: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """Choose ``(marks, colour)`` for a terminal stream.

    ``NO_COLOR``, a dumb terminal or output that is not a terminal gets words
    and ASCII marks only. Otherwise colour is truecolor when ``COLORTERM`` is
    ``truecolor`` or ``24bit``, else the 256-colour index when ``TERM`` names
    one, else the 16-colour code; marks are Unicode when the stream's encoding
    can carry them.
    """

    env = os.environ if environ is None else environ
    out = sys.stdout if stream is None else stream
    if env.get("NO_COLOR", "") != "" or env.get("TERM", "") == "dumb":
        return "ascii", "none"
    isatty = getattr(out, "isatty", None)
    try:
        interactive = bool(isatty()) if callable(isatty) else False
    except (OSError, ValueError):
        interactive = False
    if not interactive:
        return "ascii", "none"
    if env.get("COLORTERM", "").strip().lower() in ("truecolor", "24bit"):
        colour = "truecolor"
    elif "256" in env.get("TERM", ""):
        colour = "ansi256"
    else:
        colour = "ansi16"
    encoding = getattr(out, "encoding", None) or "ascii"
    try:
        "".join(state.mark for state in DISPLAY_STATES.values()).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "ascii", colour
    return "unicode", colour
