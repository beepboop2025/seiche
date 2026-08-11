"""Structured editorial record for every Seiche letter.

The markdown dispatch is for a human reader. This module emits the parallel
machine record that lets another desk quote it without reverse engineering
prose. Observed prints, Seiche derivations and Palimpsest context remain
separate: a related reading never enters the funding composite just because
it made a story more interesting.

Pure and stdlib-only: the same snapshot and letter produce the same record.
"""

from __future__ import annotations

import hashlib
import json


SCHEMA = "seiche.analytical-story.v1"
EVIDENCE_CONTRACT = "lab-evidence-envelope/v1"
SITE = "https://seiche.info"
PALIMPSEST_NEWSROOM = "https://palimpsest.info/readings/newsroom-latest.json"


def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return round(out, 6)


def _instant(value: str | None, fallback_date: str) -> str:
    """Return one UTC instant without pretending to know a source's clock."""
    raw = str(value or "").strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return raw + "T00:00:00Z"
    if raw:
        return raw.replace("+00:00", "Z")
    return fallback_date + "T00:00:00Z"


def _safe_event_time(dates: list[str], knowledge_time: str, fallback: str) -> str:
    """Choose the latest known event that cannot post-date desk knowledge."""
    knowledge_day = knowledge_time[:10]
    safe = sorted({str(d)[:10] for d in dates if d and str(d)[:10] <= knowledge_day})
    return _instant(safe[-1] if safe else fallback, fallback)


def _regime_changed(previous: dict, current: str) -> bool:
    prior = str((previous or {}).get("regime") or "").upper()
    return bool(prior and current and prior != current)


def _tell_change(snap: dict, previous: dict):
    tell = snap.get("deep", {}).get("tell") or {}
    if not tell.get("ok"):
        return None
    now = _number(tell.get("tell"))
    before = _number((previous or {}).get("tell"))
    return None if now is None or before is None else round(now - before, 6)


def _newsworthiness(*, novel_movers: list[dict], composite_delta,
                    regime_changed: bool, tell_delta, has_forward: bool,
                    coverage, faults: list, palimpsest_live: bool) -> dict:
    """Classify the analytical contribution, not the emotional temperature."""
    factors = {
        "fresh_observed_print": 30 if novel_movers else 0,
        "material_composite_change": 20 if composite_delta is not None
                                      and abs(composite_delta) >= 5 else 0,
        "regime_transition": 20 if regime_changed else 0,
        "cross_signal_divergence_change": 15 if tell_delta is not None
                                           and abs(tell_delta) >= 1 else 0,
        "dated_forward_test": 10 if has_forward else 0,
        "evidence_coverage": 10 if coverage is not None and coverage >= 80 else 0,
        "context_braid": 5 if palimpsest_live else 0,
        "fault_penalty": -20 if faults else 0,
    }
    score = max(0, min(100, sum(factors.values())))
    has_change = bool(novel_movers or regime_changed or
                      (composite_delta is not None and abs(composite_delta) >= 5) or
                      (tell_delta is not None and abs(tell_delta) >= 1))
    if score >= 55 and has_change:
        decision = "full_story"
    elif score >= 30 or has_forward:
        decision = "desk_brief"
    else:
        decision = "watch_note"
    return {
        "score": score,
        "decision": decision,
        "factors": factors,
        "rule": ("a full story needs a fresh measured change and at least 55 points; "
                 "levels, persistence and quiet tapes remain labelled briefs or watch notes"),
    }


def _palimpsest_context(snap: dict) -> list[dict]:
    far = snap.get("engines", {}).get("farbasin") or {}
    if not far.get("ok"):
        return []
    readings = []
    for key, row in (far.get("channels") or {}).items():
        if not isinstance(row, dict) or row.get("last") is None:
            continue
        readings.append({
            "signal": key,
            "label": row.get("label"),
            "value": row.get("last"),
            "unit": row.get("unit"),
            "as_of": row.get("asof"),
            "observations": row.get("n_obs"),
            "change_vs_prior_10_median": row.get("chg_vs_prior10"),
        })
    status = far.get("status") or {}
    return [{
        "product": "palimpsest",
        "relation": "topic-surface-only",
        "context_only": True,
        "used_in_score": False,
        "source_url": PALIMPSEST_NEWSROOM,
        "as_of": far.get("asof"),
        "backtestable": bool(status.get("backtestable")),
        "status_note": status.get("note"),
        "readings": readings,
    }]


def _original_contribution(*, novel_movers: list[dict], composite_delta,
                           regime_changed: bool, tell_delta, has_forward: bool) -> dict:
    kinds = []
    if novel_movers:
        kinds.append("fresh_longitudinal_delta")
    if composite_delta is not None and abs(composite_delta) >= 5:
        kinds.append("cross_component_attribution")
    if regime_changed:
        kinds.append("regime_transition")
    if tell_delta is not None and abs(tell_delta) >= 1:
        kinds.append("cross_signal_divergence")
    if has_forward:
        kinds.append("dated_forward_test")
    if not kinds:
        kinds.append("bounded_no_change_record")
    return {
        "kinds": kinds,
        "statement": (
            "Seiche's contribution is the change against its accrued point-in-time record, "
            "its funding-system attribution and the dated test that follows; raw public "
            "prints are evidence, not the finished analysis."
        ),
        "exclusive_fact_claimed": False,
    }


def build_story(dispatch: dict, snap: dict, *, novel_movers: list[dict] | None = None,
                previous_value=None, letter_previous: dict | None = None) -> dict:
    """Build the typed companion record for one already-linted dispatch."""
    novel_movers = [m for m in (novel_movers or []) if isinstance(m, dict)]
    previous = letter_previous or {}
    comp = snap.get("engines", {}).get("composite", {}) or {}
    current_value = _number(comp.get("value"))
    before_value = _number(previous_value)
    composite_delta = (None if current_value is None or before_value is None
                       else round(current_value - before_value, 6))
    regime = str(comp.get("regime") or "UNRATED").upper()
    changed_regime = _regime_changed(previous, regime)
    tell_delta = _tell_change(snap, previous)
    coverage = _number(comp.get("coverage_pct"))
    faults = [str(f) for f in (snap.get("faults") or [])]
    palimpsest = _palimpsest_context(snap)
    calendar = snap.get("calendar") or {}
    weather = snap.get("engines", {}).get("weather") or {}
    crunches = calendar.get("crunch_windows") or weather.get("crunch_windows") or []
    has_forward = bool(crunches or dispatch.get("odds"))
    quality = _newsworthiness(
        novel_movers=novel_movers,
        composite_delta=composite_delta,
        regime_changed=changed_regime,
        tell_delta=tell_delta,
        has_forward=has_forward,
        coverage=coverage,
        faults=faults,
        palimpsest_live=bool(palimpsest),
    )

    date = dispatch["date"]
    knowledge_time = _instant(snap.get("generated_at"), date)
    event_dates = [str(m.get("asof")) for m in novel_movers if m.get("asof")]
    event_dates += [str(c.get("date")) for c in crunches if c.get("date")]
    event_time = _safe_event_time(event_dates, knowledge_time, date)

    claims = [{
        "evidence_status": "DERIVED",
        "statement": (f"Seiche's composite reads {current_value:g} in {regime}."
                      if current_value is not None else f"Seiche's regime reads {regime}."),
        "metric": {"label": "Seiche composite", "value": current_value,
                   "unit": "index points", "coverage_pct": coverage},
        "method_url": f"{SITE}/methodology.html",
    }]
    for mover in novel_movers[:3]:
        claims.append({
            "evidence_status": "OBSERVED",
            "statement": f"A fresh {mover.get('label', 'series')} print entered the mover set.",
            "metric": {
                "label": mover.get("label"),
                "value": mover.get("value"),
                "unit": mover.get("unit"),
                "robust_z": mover.get("max_abs_z"),
                "as_of": mover.get("asof"),
            },
            "source_url": "https://api.seiche.info/api/series/index.json",
        })

    editorial = snap.get("editorial") or {}
    limitations = [
        "The composite is a Seiche derivation, not an observed market price.",
        "Source publication time is not yet collected uniformly; event, desk-knowledge and publication clocks remain separate.",
        "Related Palimpsest readings are context-only and never enter the composite or a model feature.",
    ]
    limitations.extend(f"Collection fault: {f}" for f in faults)
    fingerprint_core = {
        "slug": dispatch["slug"],
        "headline": dispatch["title"],
        "dek": dispatch["summary"],
        "claims": claims,
        "related_evidence": palimpsest,
    }
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "evidence_contract": EVIDENCE_CONTRACT,
        "id": f"seiche:{dispatch['slug']}",
        "product": "seiche",
        "slug": dispatch["slug"],
        "canonical_url": f"{SITE}/dispatches/{dispatch['slug']}.html",
        "headline": dispatch["title"],
        "dek": dispatch["summary"],
        "beat": "dollar-funding-plumbing",
        "editorial_class": quality["decision"],
        "publication_status": "PUBLISHED",
        "published_at": knowledge_time,
        "clocks": {
            "event_time": event_time,
            "source_publication_time": None,
            "knowledge_time": knowledge_time,
            "publication_time": knowledge_time,
            "source_publication_status": "NOT_UNIFORMLY_COLLECTED",
        },
        "original_contribution": _original_contribution(
            novel_movers=novel_movers,
            composite_delta=composite_delta,
            regime_changed=changed_regime,
            tell_delta=tell_delta,
            has_forward=has_forward,
        ),
        "newsworthiness": quality,
        "claims": claims,
        "evidence_braid": {
            "primary_product": "seiche",
            "relationships": palimpsest,
            "cross_product_score": None,
        },
        "countercase": editorial.get("countercase") or [],
        "sealed_call": None,
        "adjudication": {"status": "NOT_APPLICABLE_TO_THIS_STORY"},
        "limitations": limitations,
        "claim_fingerprint": fingerprint,
    }
