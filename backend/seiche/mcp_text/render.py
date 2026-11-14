"""The one text block appended after the JSON block of a Seiche MCP result.

Decisions (docs/MCP_TEXT_RENDERING.md):
1. ``content[0]`` and ``structuredContent`` stay byte-identical; this block is
   appended after them and never replaces anything.
2. It is built only from the structuredContent served, after
   ``sanitize_public_fault_payload``, so it states nothing that object lacks.
3. Name lookups print resolution notices, never a display state.
4. Seiche has no access gate; nothing here suggests one.
5. At most ``BLOCK_CAP_BYTES``. The shared presenter lays the readings out
   (``render_text`` with ``max_bytes``): a count per display state, gap readings
   first, then the rest in served order, and "Full list in the JSON block."
   when any is cut. Ahead of them come the lookup notices, the line of words
   with no display state, the limits and the pointer. Nothing is cut before a
   gap reading: the limits give way first, then the unmapped-word line, and
   calm readings are cut before either.
6. A slot the result lacks prints "Not stated"; the boundary prints once.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from seiche.mcp_text import adapters
from seiche.mcp_text.vendor import present

logger = logging.getLogger("seiche.mcp_text")

BLOCK_CAP_BYTES = 4096
MARKS = "unicode"
COLOUR = "none"
INCOMPLETE = "Not every served item is rendered here; the JSON block has the full result."
OPEN_LEAD = "Not mapped to a display state: "
# Anything a rendering bug or an unprintable number can raise (ArithmeticError covers
# ZeroDivisionError, OverflowError and decimal errors; LookupError covers KeyError and
# IndexError). The block is an addition: it must never fail a call.
_FAILURES = (ArithmeticError, AttributeError, LookupError, RecursionError, TypeError, ValueError)


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _one_line(text: str) -> str:
    """Producer text on one line, cleaned exactly as the presenter cleans a boundary."""
    return present.render_text([], marks=MARKS, colour=COLOUR, boundary=text)


def _kept(body: str, view: adapters.View) -> int:
    """How many reading blocks a capped body kept (gaps come first)."""
    paragraphs = body.split("\n\n")
    fixed = 1 + int(len(view.readings) > 1) + int(present.FULL_LIST_LINE in paragraphs)
    return len(paragraphs) - fixed


def compose(view: adapters.View, cap: int = BLOCK_CAP_BYTES) -> str:
    """Notices, the unmapped-word line, limits and the pointer, then the readings."""
    gaps = sum(1 for reading in view.readings if reading.state in present.GAP_STATES)
    notices = [_one_line(notice) for notice in view.notices]
    words = _one_line(OPEN_LEAD + ", ".join(view.open_words) + ".") if view.open_words else None
    limits = _one_line("Limits: " + view.limits) if view.limits else None
    pointer = [INCOMPLETE] if view.incomplete or view.open_words else []
    fallback = None
    for optional in dict.fromkeys(((words, limits), (words, None), (None, None))):
        header = notices + [line for line in optional if line] + pointer
        budget = cap - (_size("\n\n".join(header)) + 2 if header else 0)
        if budget < 1:
            continue
        try:
            body = present.render_text(view.readings, marks=MARKS, colour=COLOUR,
                                       boundary=view.boundary, max_bytes=budget)
        except ValueError:
            continue
        block = "\n\n".join([*header, body])
        if _kept(body, view) >= gaps:
            return block
        fallback = block
    if fallback is None:
        raise ValueError("the notices, the pointer and the boundary exceed the cap")
    return fallback


def _block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def result_block(name: str, out: Any) -> dict[str, str] | None:
    """The appended block for a successful tool result, or None for none."""
    if not isinstance(out, Mapping):
        return None
    try:
        view = adapters.build(name, out)
        return None if view is None else _block(compose(view))
    except _FAILURES as exc:
        # The JSON block is complete without this one: never fail the call over it.
        logger.warning("mcp text block skipped for %s: %s", name, type(exc).__name__)
        return None


def legacy_block(payload: Any) -> dict[str, str] | None:
    """A retired tool's rights refusal: Unavailable, the producer's reason verbatim."""
    if not isinstance(payload, Mapping):
        return None
    try:
        return _block(compose(adapters.legacy_unavailable(payload)))
    except _FAILURES as exc:
        logger.warning("mcp legacy text block skipped: %s", type(exc).__name__)
        return None
