"""Turn one served Seiche MCP result into presentation readings.

Everything comes from the structuredContent this caller was served, after
``sanitize_public_fault_payload``, so nothing the sanitizer replaced can reach
the text. States come only from the shared presenter's section 4 table
(``present.state_for``): Seiche's five words, its stale rule (``stale``,
``STALE``) and the market atlas's ``RESTRICTED`` and ``UNAVAILABLE``.

An item is drawn only when mapped words decide its state, and a value state
also needs a served value. Seiche's freshness words ``fresh`` and ``aging``
(``FRESH``, ``AGING``) are its current words: they keep an item's own claim
word and add no state. ``available`` and ``AVAILABLE`` say a value is present;
they are not a claim. When a word that decides an item has no row, the item is
not drawn and the word goes, verbatim, on the view's "Not mapped to a display
state" line: printed under no state, never a calm one. Clocks are served clocks
only; nothing reads the render time.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from seiche.mcp_text.vendor import present

PRODUCT = "seiche"
# Seiche's words for a reading inside its freshness limit (StalenessState fresh
# and aging): the money-market desk counts them as available, the India and
# country desks as current.
CURRENT = frozenset({"fresh", "aging", "FRESH", "AGING"})
# A value is present; neither word says whether it is observed or derived.
PRESENT = frozenset({"available", "AVAILABLE"})
# Contract section 3: the rule at which each gap state wins.
_GAP_RULE = {
    present.NOT_COVERED: 1,
    present.UNAVAILABLE: 3,
    present.RESTRICTED: 4,
    present.PARTIAL: 5,
    present.STALE: 6,
}
_WORLD_DOMAINS = ("money_markets", "forex", "capital_markets", "china_macro")
# Context blocks inside a world-markets domain that summarise, rather than carry,
# readings: their status words describe the block.
_WORLD_CONTEXT_KEYS = frozenset(
    {
        "coverage",
        "freshness",
        "regime",
        "strongest_signal",
        "countercase",
        "publication_projection",
        "clocks",
        "economic_context",
        "boundaries",
    }
)
_LABEL_KEYS = ("label", "title", "id", "key", "pair", "aspect", "basin", "harbor", "node")
_MAX_DEPTH = 8


@dataclass
class View:
    """What one tool result contributes to the appended text block."""

    readings: list[present.Reading] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    open_words: list[str] = field(default_factory=list)  # "slot word", once each
    limits: str | None = None
    boundary: str | None = None
    incomplete: bool = False


def text(value: Any) -> str | None:
    """Producer text as served, or None when absent or blank."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def clock(value: Any) -> str | None:
    """A served clock the presenter can print, else None ("Not stated")."""
    if not isinstance(value, str):
        return None
    try:
        present.parse_clock(value)
    except (ValueError, TypeError):
        return None
    return value


def figure(value: Any) -> int | float | str | None:
    """A served value as served: a finite number or a non-blank string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return text(value)


def joined(value: Any) -> str | None:
    """A producer's list of sentences, in served order."""
    if isinstance(value, str):
        return text(value)
    if not isinstance(value, list):
        return None
    parts = [part for part in (text(item) for item in value) if part is not None]
    return " ".join(parts) or None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def state(word: Any) -> present.ProductState | None:
    """The section 4 row for a served word; None when the contract has none."""
    if not isinstance(word, str):
        return None
    try:
        return present.state_for(PRODUCT, word)
    except (ValueError, TypeError):
        return None


def open_word(view: View, slot: str, word: Any) -> None:
    """A deciding word with no row: printed once, verbatim, under no state."""
    view.incomplete = True
    shown = text(word)
    if shown is None:
        return
    entry = f"{slot} {shown}"
    if entry not in view.open_words:
        view.open_words.append(entry)


def add(view: View, row: present.ProductState, **slots: Any) -> None:
    view.readings.append(present.reading_from_native(row, **slots))


def reading(
    view: View,
    claim: Any,
    *,
    freshness: Any = None,
    value: Any = None,
    reason: str | None = None,
    claim_slot: str = "status",
    freshness_slot: str = "freshness",
    **slots: Any,
) -> None:
    """Draw one served item from its freshness word and its claim word.

    A word without a row (other than a current or presence word) is printed
    under no state. A gap from either word still wins, the earliest contract
    rule first, with the producer's reason: an unmapped word beside it can only
    name another gap, never a calm state. Otherwise an unmapped word stops the
    item, and the claim word decides: a value state needs a served value, and
    without one the item is left to the JSON block.
    """
    rows: dict[str, present.ProductState] = {}
    unmapped = False
    for slot, word in ((freshness_slot, freshness), (claim_slot, claim)):
        if word is None or word in CURRENT or word in PRESENT:
            continue
        row = state(word)
        if row is None:
            open_word(view, slot, word)
            unmapped = True
            continue
        rows[slot] = row
    gaps = [row for row in rows.values() if row.state in present.GAP_STATES]
    if gaps:
        add(view, min(gaps, key=lambda row: _GAP_RULE[row.state]), reason=reason, **slots)
        return
    if unmapped:
        return
    claimed = rows.get(claim_slot)
    if claimed is None:
        if claim in PRESENT:
            open_word(view, claim_slot, claim)
        view.incomplete = True
        return
    shown = figure(value)
    if shown is None:
        view.incomplete = True
        return
    add(view, claimed, value=shown, **slots)


def _sources(row: Mapping[str, Any]) -> tuple[list[present.SourceRef], str | None]:
    """Publisher names and attribution exactly as a row's ``sources`` serve them."""
    refs: list[present.SourceRef] = []
    attributions: list[str] = []
    for source in _rows(row.get("sources")):
        publisher = text(source.get("publisher"))
        if publisher is not None:
            refs.append(present.SourceRef(publisher=publisher, url=text(source.get("source_url")),
                                          retrieved_at=clock(source.get("fetched_at"))))
        attribution = text(source.get("attribution"))
        if attribution is not None and attribution not in attributions:
            attributions.append(attribution)
    return refs, "; ".join(attributions) or text(row.get("attribution"))


def tool_gap(view: View, out: Mapping[str, Any], checked: str | None) -> bool:
    """A whole result that failed: its own gap word, else D2's ``ok: false``.

    The fault sanitizer upper-cases a status it keeps (``UNAVAILABLE``) and
    replaces an unsafe one with ``FAILED``; either way ``ok: false`` is
    Unavailable with the producer's reason verbatim.
    """
    row = state(out.get("status"))
    if row is None or row.state not in present.GAP_STATES:
        if out.get("ok") is not False:
            return False
        row = state("unavailable")
        if row is None:
            return False
    add(view, row, reason=text(out.get("reason")), checked_at=checked)
    view.incomplete = True
    return True


def _label(node: Mapping[str, Any]) -> str | None:
    for key in _LABEL_KEYS:
        found = text(node.get(key))
        if found is not None:
            return found
    return None


# --------------------------------------------------------------------------
# Release boundary and source health
# --------------------------------------------------------------------------
def source_rights_status(out: Mapping[str, Any], view: View) -> None:
    """Each withheld source is Restricted, with the producer's reason verbatim:
    public_rights.WITHHELD_SOURCES lists sources whose reuse rights are not
    established, the contract's "withheld by rights or policy"."""
    restricted = state("restricted")
    if restricted is not None:
        for key, reason in _mapping(out.get("withheld_sources")).items():
            add(view, restricted, label=text(key), reason=text(reason))
    view.limits = text(out.get("rule"))
    view.boundary = text(out.get("boundary"))
    view.incomplete = True  # tools, decisions and source notes stay in the JSON


def data_health(out: Mapping[str, Any], view: View) -> None:
    """Series rows carry clocks and rights but no value: only gaps are drawn."""
    checked = clock(out.get("generated_at"))
    for row in _rows(out.get("provenance")):
        availability = row.get("availability")
        known = clock(row.get("knowledge_time")) or clock(row.get("fetched_at"))
        reading(view, None if availability in PRESENT else availability,
                freshness=row.get("staleness"), claim_slot="availability",
                freshness_slot="staleness", label=text(row.get("label")),
                subject=text(row.get("mnemonic")), period=clock(row.get("asof")),
                known_at=known, known_basis="capture" if known else None,
                attribution=text(row.get("attribution")), checked_at=checked,
                limits=text(row.get("freshness_basis")))
    for fault in _rows(out.get("faults")):
        reading(view, fault.get("status"), reason=text(fault.get("detail")),
                claim_slot="fault status", label=text(fault.get("source")), checked_at=checked)
    view.limits = text(out.get("reading"))
    view.incomplete = True  # current rows have no value to draw; they stay in the JSON


# --------------------------------------------------------------------------
# Money markets, India and country desks
# --------------------------------------------------------------------------
def _atlas_metric(view: View, card: Mapping[str, Any], checked: str | None,
                  label: str | None = None) -> None:
    """A market-atlas card: status is its staleness (upper-cased) or availability."""
    known = clock(card.get("knowledge_time"))
    source = text(card.get("source"))
    reading(view, card.get("availability"), freshness=card.get("status"), value=card.get("value"),
            reason=text(card.get("reason")), claim_slot="availability", freshness_slot="status",
            label=label or text(card.get("label")), subject=text(card.get("id")),
            unit=text(card.get("unit")), period=clock(card.get("asof")), known_at=known,
            known_basis="capture" if known else None, sources=[source] if source else [],
            attribution=text(card.get("attribution")), checked_at=checked)


def _india(view: View, india: Mapping[str, Any], checked: str | None) -> None:
    checked = clock(india.get("generated_at")) or checked
    if india.get("view") == "metric":
        if india.get("selection_status") == "not_found":
            words = present.resolution_notice("not_found")
            name = text(india.get("metric_id"))
            view.notices.append(f"{name}: {words}" if name else str(words))
            return
        card = _mapping(india.get("metric"))
        if card:
            _atlas_metric(view, card, checked)
        return
    for item in _rows(india.get("missing_series")):
        reading(view, item.get("status"), claim_slot="missing series",
                label=text(item.get("id")), checked_at=checked)
    spreads = _rows(india.get("spreads")) + _rows(_mapping(india.get("monthly_curve")).get("spreads"))
    for item in spreads:
        reading(view, item.get("status"), reason=text(item.get("reason")), claim_slot="spread",
                label=text(item.get("id")), subject=text(item.get("curve_kind")),
                period=clock(item.get("asof")), checked_at=checked)
    policy = _mapping(india.get("policy_comparison"))
    if policy:
        reading(view, policy.get("status"), claim_slot="policy comparison", label="policy_comparison",
                period=clock(policy.get("asof")), limits=text(policy.get("interpretation")),
                checked_at=checked)
    liquidity = _mapping(india.get("liquidity_summary"))
    if liquidity:
        reading(view, liquidity.get("status"), claim_slot="liquidity summary",
                label="liquidity_summary", period=clock(liquidity.get("asof")), checked_at=checked)
    mechanism = _mapping(india.get("mechanism"))
    if mechanism:
        reading(view, mechanism.get("status"), claim_slot="mechanism", label="mechanism",
                checked_at=checked)
    curve = _mapping(india.get("curve"))
    if "fixed_tenor_status" in curve:
        reading(view, curve.get("fixed_tenor_status"), reason=text(curve.get("fixed_tenor_reason")),
                claim_slot="fixed tenor status", label="fixed_tenor_curve", checked_at=checked)
    for section in _rows(india.get("sections")):
        for card in _rows(section.get("metrics")):
            _atlas_metric(view, card, checked)
    for key in ("curve", "monthly_curve"):
        for card in _rows(_mapping(india.get(key)).get("nodes")):
            _atlas_metric(view, card, checked)
    view.incomplete = True  # values without a claim word stay in the JSON
    view.limits = view.limits or joined(india.get("caveats"))


def _country(view: View, desk: Mapping[str, Any]) -> None:
    checked = clock(desk.get("generated_at"))
    if desk.get("status") == "unavailable":
        reading(view, "unavailable", label=text(desk.get("country_name")), checked_at=checked)
        return
    for section in _rows(desk.get("sections")):
        for card in _rows(section.get("metrics")):
            _atlas_metric(view, card, checked)
    view.incomplete = True


def _desk_card(view: View, card: Mapping[str, Any], checked: str | None) -> None:
    """A desk metric card: ``status`` says whether it is live, ``freshness`` how old."""
    status = card.get("status")
    reading(view, None if status in PRESENT else status, freshness=card.get("freshness"),
            reason=text(card.get("unavailable_reason")), claim_slot="card status",
            label=text(card.get("label")), subject=text(card.get("id")),
            period=clock(card.get("asof")), checked_at=checked)


def money_market_context(out: Mapping[str, Any], view: View) -> None:
    freshness = _mapping(out.get("freshness"))
    checked = clock(freshness.get("evaluation_asof")) or clock(out.get("snapshot_generated_at"))
    view.limits = joined(out.get("caveats"))
    india = _mapping(out.get("india"))
    if india:
        _india(view, india, checked)
        return
    countries = _mapping(out.get("countries"))
    if countries:
        if "sections" in countries:
            _country(view, countries)
        view.incomplete = True
        return
    if tool_gap(view, out, checked):
        return
    drawn: set[str] = set()
    regime = _mapping(out.get("regime"))
    if "state" in regime:
        # A desk regime word is a severity; Seiche has no severity row.
        open_word(view, "regime", regime.get("state"))
    for indicator in _rows(regime.get("indicators")):
        before = len(view.readings)
        reading(view, None, freshness=indicator.get("freshness"),
                label=text(indicator.get("label")), subject=text(indicator.get("metric_id")),
                period=clock(indicator.get("asof")), checked_at=checked)
        if len(view.readings) > before and isinstance(indicator.get("metric_id"), str):
            drawn.add(indicator["metric_id"])
    for section in _rows(out.get("sections")):
        status = section.get("status")
        if status in ("stale", "unavailable"):
            reading(view, status, reason=text(section.get("explanation")),
                    label=text(section.get("title")) or text(section.get("label")),
                    subject=text(section.get("id")), checked_at=checked)
            continue
        for card in _rows(section.get("metrics")):
            if card.get("id") not in drawn:
                _desk_card(view, card, checked)
                if isinstance(card.get("id"), str):
                    drawn.add(card["id"])
    coverage = _mapping(out.get("coverage"))
    for word, key in (("stale", "stale_metrics"), ("unavailable", "unavailable_metrics")):
        listed = coverage.get(key)
        for metric_id in listed if isinstance(listed, list) else []:
            if isinstance(metric_id, str) and metric_id not in drawn:
                reading(view, word, label=text(metric_id), checked_at=checked)
                drawn.add(metric_id)
    diagnostics = _mapping(out.get("diagnostics"))
    if diagnostics.get("status") == "unavailable":
        reading(view, "unavailable", reason=text(diagnostics.get("reason")),
                label="diagnostics", checked_at=checked)
    view.incomplete = True  # levels, percentiles and the methodology stay in the JSON


# --------------------------------------------------------------------------
# World markets, FX workbench and the GIFT City desk
# --------------------------------------------------------------------------
def _world_items(view: View, node: Any, checked: str | None, parent: str | None,
                 name: str | None, depth: int = 0) -> None:
    """Walk one selected domain: world.py serves Seiche's five words per item."""
    if depth > _MAX_DEPTH:
        view.incomplete = True
        return
    if isinstance(node, list):
        for item in node:
            _world_items(view, item, checked, parent, name, depth + 1)
        return
    if not isinstance(node, Mapping):
        return
    own = _label(node)
    claim = node.get("evidence_status", node.get("status"))
    if isinstance(claim, str) and claim not in PRESENT:
        reading(view, claim, freshness=node.get("freshness"), value=node.get("value"),
                reason=text(node.get("reason")), label=own or parent,
                subject=None if own else name, unit=text(node.get("unit")),
                period=clock(node.get("as_of")) or clock(node.get("asof")),
                known_at=clock(node.get("knowledge_time")), checked_at=checked)
    for key, child in node.items():
        if key in _WORLD_CONTEXT_KEYS or not isinstance(child, (Mapping, list)):
            continue
        _world_items(view, child, checked, own or parent, key, depth + 1)


def world_markets_context(out: Mapping[str, Any], view: View) -> None:
    clocks = _mapping(out.get("clocks"))
    checked = clock(clocks.get("evaluation_at")) or clock(out.get("generated_at"))
    view.boundary = " ".join(part for part in (text(out.get("disclaimer")),
                                                text(clocks.get("boundary"))) if part) or None
    if tool_gap(view, out, checked):
        return
    for domain in _rows(_mapping(out.get("coverage")).get("domains")):
        row = state(domain.get("status"))
        if row is None:
            open_word(view, "domain status", domain.get("status"))
        elif row.state in present.GAP_STATES:
            add(view, row, label=text(domain.get("id")), period=clock(domain.get("as_of")),
                reason=None, checked_at=checked)
    for key in _WORLD_DOMAINS:
        if isinstance(out.get(key), Mapping):
            _world_items(view, out[key], checked, key, None)
    view.incomplete = True  # domain summaries and context blocks stay in the JSON


def _fx_row(view: View, row: Mapping[str, Any], checked: str | None) -> None:
    refs, attribution = _sources(row)
    reading(view, row.get("evidence_status"), freshness=row.get("status"), value=row.get("value"),
            reason=text(row.get("reason")), claim_slot="evidence_status", freshness_slot="status",
            label=text(row.get("pair")), subject=text(row.get("provider")),
            unit=text(row.get("unit")), period=clock(row.get("as_of")), sources=refs,
            attribution=attribution, checked_at=checked)


def market_workbench(out: Mapping[str, Any], view: View) -> None:
    checked = clock(out.get("generated_at"))
    forex = _mapping(out.get("forex"))
    china = _mapping(out.get("china"))
    seen: set[tuple[Any, Any]] = set()
    for row in [*_rows(forex.get("rows")), _mapping(china.get("fx"))]:
        key = (row.get("pair"), row.get("provider"))
        if row and key not in seen:
            seen.add(key)
            _fx_row(view, row, checked)
    if china:
        reading(view, china.get("status"), reason=text(china.get("reason")),
                label="china", checked_at=checked)
        for gap in _rows(china.get("gaps")):
            reading(view, gap.get("status"), reason=text(gap.get("reason")),
                    label=text(gap.get("label")), subject=text(gap.get("id")), checked_at=checked)
    view.limits = joined(forex.get("methodology"))
    view.incomplete = True  # changes, history and accepted China series stay in the JSON


def gift_city_context(out: Mapping[str, Any], view: View) -> None:
    checked = clock(out.get("generated_at"))
    for row in _rows(out.get("funding")):
        evidence = _mapping(row.get("evidence"))
        slot = "evidence_class" if "evidence_class" in evidence else "availability"
        known = clock(evidence.get("knowledge_time"))
        source = text(row.get("source"))
        reading(view, evidence.get(slot), freshness=row.get("status"), value=row.get("value"),
                reason=text(row.get("reason")), claim_slot=slot, freshness_slot="status",
                label=text(row.get("label")), subject=text(row.get("instrument")),
                unit=text(row.get("unit")), period=clock(row.get("as_of")), known_at=known,
                known_basis="capture" if known else None, sources=[source] if source else [],
                attribution=text(evidence.get("attribution")), checked_at=checked)
    for row in _rows(_mapping(out.get("forex")).get("rows")):
        _fx_row(view, row, checked)
    positioning = _mapping(_mapping(out.get("gold")).get("positioning"))
    if positioning:
        reading(view, None, freshness=positioning.get("status"),
                label=text(positioning.get("contract")), period=clock(positioning.get("as_of")),
                checked_at=checked, limits=text(positioning.get("boundary")))
    view.limits = joined(out.get("methodology"))
    view.incomplete = True  # funding values without a claim word stay in the JSON


# --------------------------------------------------------------------------
# Research catalog, the calculator and the legacy refusal
# --------------------------------------------------------------------------
def research_network(out: Mapping[str, Any], view: View) -> None:
    """Dataset metadata only (values_included: false): drawn only when unavailable."""
    view.boundary = text(out.get("boundary"))
    if not tool_gap(view, out, clock(out.get("evaluated_at"))):
        view.incomplete = True


def gold_inventory_carry(out: Mapping[str, Any], view: View) -> None:
    """Arithmetic over caller assumptions: a scenario has no display state."""
    open_word(view, "status", out.get("status"))
    view.limits = joined(out.get("assumptions"))
    view.incomplete = True


def legacy_unavailable(out: Mapping[str, Any]) -> View:
    """public_rights.legacy_unavailable: availability unavailable (D2), reason verbatim."""
    view = View()
    reading(view, out.get("availability"), reason=text(out.get("reason")),
            claim_slot="availability", label=text(out.get("surface")))
    return view


Adapter = Callable[[Mapping[str, Any], View], None]
ADAPTERS: dict[str, Adapter] = {
    "source_rights_status": source_rights_status,
    "data_health": data_health,
    "money_market_context": money_market_context,
    "world_markets_context": world_markets_context,
    "market_workbench": market_workbench,
    "gift_city_context": gift_city_context,
    "research_network": research_network,
    "gold_inventory_carry": gold_inventory_carry,
}
# Tools that keep their single JSON block, and why.
SKIPPED: dict[str, str] = {
    name: (
        "Agent Room records are signed documents for an authenticated caller, not "
        "evidence readings; the JSON block carries them exactly as signed."
    )
    for name in (
        "agent_room_register_key",
        "agent_room_create",
        "agent_room_append_event",
        "agent_room_list_events",
        "agent_room_verify",
    )
}


def build(name: str, out: Mapping[str, Any]) -> View | None:
    adapter = ADAPTERS.get(name)
    if adapter is None:
        return None
    view = View()
    adapter(out, view)
    return view
