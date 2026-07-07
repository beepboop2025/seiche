"""The Navigator — an LLM forecaster made accountable.

Frontier language models carry decades of macroeconomic reasoning in their
weights. Every vendor bolting one onto a dashboard asks you to TRUST it.
Seiche doesn't do trust: the Navigator must COMMIT — one P(funding event
within 5bd) per data-day, with a rationale citing the board — and the
commitment goes straight into the hash-chained as-published record, where
its realized Brier accrues in public against climatology and the Stack.

The honesty problem an LLM member uniquely poses, stated plainly: it CANNOT
be backtested. The model has read the history it would be tested on; any
hindcast is an open-book exam. So the Navigator gets no backtest, no stack
membership, and no weight anywhere until its FORWARD record — the only
evidence that means anything for this member — earns it. That is not a
limitation bolted on; it is the design: the fleet's first member whose
entire track record is postdictions-proof by construction.

Mechanics:
  - one commitment per data-day, blob-cached (`nav:{date}`): re-running a
    snapshot must never let the model revise the morning's number;
  - the model's whole world is the deterministic context pack (same
    grounding contract as the desk assistant) — the prompt demands strict
    JSON and a probability, and a malformed answer is a FAULT, not a retry
    into agreement;
  - scoring reads ONLY the pit record: forecasts whose 5bd window has
    closed are scored against realized pops (backtest.pop_bp — the shared
    event definition) and the same-window climatology;
  - no LLM endpoint configured -> {ok: False, reason} — the Navigator
    stays ashore, loudly.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from seiche import store
from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    NAVIGATOR_MIN_RESOLVED,
)
from seiche.engines.backtest import _wilson, pop_bp

SYSTEM_PROMPT = """You are the Navigator on the Seiche funding-stress desk.
Your ONLY world is the CONTEXT PACK (the live board of a dollar-funding
terminal). Commit to a probability that a funding event — SOFR−IORB popping
≥ 10bp over its trailing 5-day median — occurs within the next 5 business days.

Rules:
- Use the board: composite, tails, kink runway, weather crunch windows,
  calendar distances, Swell curve, Stack members, resonance/undertow damping.
- Cite at least two engines with their as-of dates in your rationale.
- The historical base rate is roughly 2-6% per 5-day window; deviate from it
  only for reasons visible on the board.
- Answer with STRICT JSON only, no prose outside it:
  {"p_event_5bd": 0.07, "rationale": "<= 3 sentences, engines cited"}"""


def build_messages(pack: dict) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "CONTEXT PACK:\n" + json.dumps(pack, default=str)},
    ]


def parse_commitment(text: str | None) -> dict | None:
    """Strict-ish JSON extraction: accepts a bare object or one inside a
    code fence; rejects anything without a usable probability."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    p = obj.get("p_event_5bd")
    if not isinstance(p, (int, float)) or not (0.0 <= float(p) <= 1.0):
        return None
    rationale = str(obj.get("rationale") or "").strip()[:600]
    return {"p_event_5bd": round(float(p), 3), "rationale": rationale}


async def commit(pack: dict, asof: str, llm=None) -> dict:
    """One commitment per data-day. `llm` is an async messages->text callable
    (injected in tests); default routing mirrors the desk assistant."""
    key = f"nav:{asof}"
    cached = store.load_blob(key)
    if cached is not None:
        return {**cached, "cached": True}

    if llm is None:
        from seiche import ai

        async def llm(messages):  # noqa: F811 — default route
            out = await ai._via_router(messages)
            if out is None:
                out = await ai._via_env(messages)
            return out

    try:
        text = await llm(build_messages(pack))
    except Exception as e:  # noqa: BLE001 — the fault is the news
        return {"ok": False, "reason": f"LLM route failed: {type(e).__name__}: {e}"}
    if text is None:
        return {"ok": False, "reason": "no LLM endpoint configured — the Navigator stays ashore"}
    parsed = parse_commitment(text)
    if parsed is None:
        return {"ok": False, "reason": "malformed commitment (no valid JSON probability) — not retried"}

    record = {
        "ok": True,
        "asof": asof,
        "p_event_5bd": parsed["p_event_5bd"],
        "rationale": parsed["rationale"],
        "caveats": [
            "an LLM member cannot be backtested (it has read the history) — the forward record below is its ONLY evidence",
            "one commitment per data-day; re-running a snapshot returns the cached commitment unchanged",
        ],
        "method": (
            "context-pack-grounded LLM commitment, strict-JSON, cached per data-day; "
            "scored forward-only against realized pops (shared PROOF definition) once "
            f"each {BACKTEST_EVENT_FWD_D}bd window closes"
        ),
    }
    store.save_blob(key, record)
    return record


def score_record(pit_records: list[dict], spread_bp: pd.Series) -> dict:
    """Realized forward skill from the as-published record only. A forecast
    made on day D is resolved once D+5bd has printed; unresolved ones wait."""
    s = spread_bp.dropna()
    if s.empty:
        return {"ok": False, "reason": "no spread history to resolve against"}
    grid = pd.bdate_range(s.index.min(), s.index.max())
    pop = pop_bp(s, grid)

    resolved, pending = [], 0
    for rec in pit_records:
        nav_p = ((rec.get("forecasts") or {}).get("views") or {}).get("navigator")
        if nav_p is None:
            nav_p = (rec.get("navigator") or {}).get("p_event_5bd")
        if nav_p is None:
            continue
        d = pd.Timestamp(rec.get("date"))
        loc = grid.searchsorted(d, side="right")
        window = pop[loc : loc + BACKTEST_EVENT_FWD_D]
        if len(window) < BACKTEST_EVENT_FWD_D or window.isna().all():
            pending += 1
            continue
        resolved.append((float(nav_p), float(np.nanmax(window) >= BACKTEST_SPIKE_BP)))

    n = len(resolved)
    out: dict = {
        "ok": True,
        "n_resolved": n,
        "n_pending": pending,
        "min_resolved": NAVIGATOR_MIN_RESOLVED,
    }
    if n < NAVIGATOR_MIN_RESOLVED:
        out["verdict"] = (
            f"forward record too short to judge ({n}/{NAVIGATOR_MIN_RESOLVED} resolved) — "
            "the Navigator is earning its stripes; weight stays zero"
        )
        return out
    pa = np.array([p for p, _ in resolved])
    ya = np.array([y for _, y in resolved])
    base = float(ya.mean())
    brier = float(np.mean((pa - ya) ** 2))
    brier_clim = float(np.mean((base - ya) ** 2))
    hits = int(ya.sum())
    out.update({
        "brier": round(brier, 4),
        "brier_climatology": round(brier_clim, 4),
        "base_rate": round(base, 3),
        "events_ci95": _wilson(hits, n),
        "verdict": (
            "the forward record beats climatology — the Navigator has earned a hearing"
            if brier < brier_clim
            else "the forward record does NOT beat climatology — the board reads better than the model"
        ),
    })
    return out
