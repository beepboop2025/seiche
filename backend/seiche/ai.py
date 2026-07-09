"""The desk assistant — an LLM strictly moored to the board.

Architecture: a deterministic CONTEXT PACK (compact JSON of the live board —
composite decomposition, headline, Tell/Turn/ML summaries, calendar, movers,
faults, staleness) is the model's ONLY source of truth. The system prompt
forbids outside numbers, requires an engine + as-of citation for every figure,
and mandates "not in the pack" over improvisation. Temperature low. This is a
reading assistant for the instrument, not an oracle.

Routing: free-llm-router (Groq→Cerebras→Google→Mistral→OpenRouter free tiers)
when importable and keyed; otherwise any OpenAI-compatible endpoint via
SEICHE_LLM_BASE_URL / SEICHE_LLM_API_KEY / SEICHE_LLM_MODEL; otherwise the
call fails open and returns the context pack itself — still useful, paste it
into any chat you like.
"""

from __future__ import annotations

import json
import os
import re

import httpx


_META_OPENERS = re.compile(
    r"^\s*(we need to|the user|let'?s|i need to|i should|we should|okay[, ]|first[, ])",
    re.IGNORECASE,
)


def _strip_reasoning(text: str) -> str:
    """Free-tier reasoning models leak chain-of-thought. Three passes:
    <think> blocks, 'Final answer:' markers, and the gpt-oss-style pattern
    where plain-text deliberation precedes the real answer — there, the final
    paragraph is the deliverable."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    m = re.search(r"(?:final answer|answer)\s*[:\-]\s*", text, flags=re.IGNORECASE)
    if m and m.start() > 80:  # only treat as a marker if real preamble precedes it
        return text[m.end():].strip()
    if _META_OPENERS.match(text):
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        # walk from the end past any trailing meta paragraphs
        for p in reversed(paras):
            if not _META_OPENERS.match(p):
                return p
    return text

SYSTEM_PROMPT = """You are the desk assistant inside SEICHE, a funding-stress terminal.
You will receive a CONTEXT PACK (JSON) describing the live board, then a question.

Hard rules:
1. The context pack is your ONLY source of numbers. Never use outside data or memory of markets.
2. Cite the engine and as-of date for every figure you use, like: (composite, 2026-07-06).
3. If the pack does not contain what is asked, say "not on the board" — do not improvise.
4. Plain prose, tight, desk-note voice. No headers unless asked. Max ~180 words unless asked for more.
5. You describe readings and mechanics; you do not give investment advice. If asked for a trade, restate what the Playbook table shows (with n) and say the decision is the operator's.
6. Respect the tool's honesty: mention coverage %, DEAD inputs, staleness or backtest caveats when they materially qualify the answer.
7. Output ONLY the final answer. No reasoning preamble, no "we need to", no meta-commentary about the task."""


def context_pack(snap: dict) -> dict:
    """Compact, deterministic extract of the payload — the model's whole world."""
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})
    comp = eng.get("composite", {})
    tell = deep.get("tell", {})
    turn = (deep.get("turn") or {}).get("next_turn")
    ml = deep.get("ml", {})
    bt = (deep.get("backtest") or {}).get("event_capture", {})
    sonar = eng.get("sonar", {})
    basins = eng.get("basins", {})
    moor = eng.get("moorings", {})
    prov = snap.get("provenance", [])
    stale_counts: dict[str, int] = {}
    for p in prov:
        stale_counts[p.get("staleness", "?")] = stale_counts.get(p.get("staleness", "?"), 0) + 1

    return {
        "generated_at": snap.get("generated_at"),
        "version": snap.get("version"),
        "composite": {
            "value": comp.get("value"), "regime": comp.get("regime"),
            "coverage_pct": comp.get("coverage_pct"), "dead_inputs": comp.get("dead_inputs"),
            "decomposition": comp.get("decomposition"),
        },
        "headline": snap.get("headline"),
        "tell": {k: tell.get(k) for k in ("tell", "plumbing_pctl", "market_pctl", "reading", "asof")} if tell.get("ok") else None,
        "next_turn": turn,
        "ml": {k: ml.get(k) for k in ("p_event_5bd", "verdict", "asof")} if ml.get("ok") else None,
        "kink": {k: eng.get("kink", {}).get(k) for k in ("kink_reserves_b", "current_reserves_b", "distance_b", "days_to_kink", "r2", "asof")} if eng.get("kink", {}).get("ok") else None,
        "weather_crunches": eng.get("weather", {}).get("crunch_windows", [])[:5],
        "resonance": {
            "score": eng.get("resonance", {}).get("score"),
            "worst_mode": eng.get("resonance", {}).get("worst_mode"),
        } if eng.get("resonance", {}).get("ok") else None,
        "warehouse": {k: eng.get("warehouse", {}).get(k) for k in ("total_net_b", "total_pctl", "long_end_share_pct", "asof")} if eng.get("warehouse", {}).get("ok") else None,
        "echo_top": eng.get("echo", {}).get("top"),
        "book": {
            "today": deep.get("book", {}).get("today"),
            "verdict": (deep.get("book", {}).get("backtest") or {}).get("verdict"),
            "live": deep.get("book", {}).get("live"),
        } if (deep.get("book") or {}).get("ok") else None,
        "stacker": {
            "p_now": deep.get("stacker", {}).get("p_now"),
            "published": deep.get("stacker", {}).get("published"),
            "dispersion_now": deep.get("stacker", {}).get("dispersion_now"),
            "verdict": deep.get("stacker", {}).get("verdict"),
        } if (deep.get("stacker") or {}).get("ok") else None,
        "farbasin": {
            "channels": {k: {kk: vv for kk, vv in (v or {}).items() if kk != "series"}
                          for k, v in (eng.get("farbasin", {}).get("channels") or {}).items()},
            "status": eng.get("farbasin", {}).get("status"),
        } if eng.get("farbasin", {}).get("ok") else None,
        "tidetables": {
            "event_odds": deep.get("tidetables", {}).get("event_odds"),
            "novelty": deep.get("tidetables", {}).get("novelty"),
            "skill_verdict": (deep.get("tidetables", {}).get("skill") or {}).get("verdict"),
            "asof": deep.get("tidetables", {}).get("asof"),
        } if (deep.get("tidetables") or {}).get("ok") else None,
        "undertow": {
            "score": eng.get("undertow", {}).get("score"),
            "per_series": {
                k: {kk: v.get(kk) for kk in ("ac1_pctl", "tau_bd", "var_pctl")}
                for k, v in (eng.get("undertow", {}).get("per_series") or {}).items()
            },
            "asof": eng.get("undertow", {}).get("asof"),
        } if eng.get("undertow", {}).get("ok") else None,
        "bathymetry": {
            "p_event_5bd": deep.get("bathymetry", {}).get("p_event_5bd"),
            "mfpt_bd": deep.get("bathymetry", {}).get("mfpt_bd"),
            "floor": {
                k: (deep.get("bathymetry", {}).get("floor") or {}).get(k)
                for k in ("well_bp", "stiffness", "barrier_kt")
            },
            "tau_bd": (deep.get("bathymetry", {}).get("spectrum") or {}).get("tau_bd"),
            "tau_pctl": (deep.get("bathymetry", {}).get("spectrum") or {}).get("tau_pctl"),
            "entropy_pctl": (deep.get("bathymetry", {}).get("arrow") or {}).get("pctl"),
            "validation_verdict": (deep.get("bathymetry", {}).get("validation") or {}).get("verdict"),
            "asof": deep.get("bathymetry", {}).get("asof"),
        } if (deep.get("bathymetry") or {}).get("ok") else None,
        "swell": {
            "p_event_5bd": deep.get("swell", {}).get("p_event_5bd"),
            "event_by_horizon": deep.get("swell", {}).get("event_by_horizon"),
            "peak": deep.get("swell", {}).get("peak"),
            "validation_verdict": (deep.get("swell", {}).get("validation") or {}).get("verdict"),
            "asof": deep.get("swell", {}).get("asof"),
        } if (deep.get("swell") or {}).get("ok") else None,
        "merian": {
            "instability": eng.get("merian", {}).get("instability"),
            "modes": (eng.get("merian", {}).get("modes") or [])[:3],
            "asof": eng.get("merian", {}).get("asof"),
        } if eng.get("merian", {}).get("ok") else None,
        "gyre": {
            "determinism_verdict": (deep.get("gyre", {}).get("determinism") or {}).get("verdict"),
            "nonlinearity_verdict": (deep.get("gyre", {}).get("nonlinearity") or {}).get("verdict"),
            "stability": deep.get("gyre", {}).get("stability"),
            "forecast": deep.get("gyre", {}).get("forecast"),
            "asof": deep.get("gyre", {}).get("asof"),
        } if (deep.get("gyre") or {}).get("ok") else None,
        "roguewave": {
            "tail_verdict": eng.get("roguewave", {}).get("tail_verdict"),
            "fit": eng.get("roguewave", {}).get("fit"),
            "return_levels": eng.get("roguewave", {}).get("return_levels"),
            "sample_max_bp": eng.get("roguewave", {}).get("sample_max_bp"),
            "asof": eng.get("roguewave", {}).get("asof"),
        } if eng.get("roguewave", {}).get("ok") else None,
        "basins": basins.get("basins") if basins.get("ok") else None,
        "swap_lines_30d_m": (basins.get("swap_lines") or {}).get("ops_30d_total_m") if basins.get("ok") else None,
        "moorings": {
            "usdt_dev_bp": (moor.get("usdt") or {}).get("dev_bp"),
            "stable_total_b": (moor.get("demand") or {}).get("total_b"),
            "stable_chg_30d_pct": (moor.get("demand") or {}).get("chg_30d_pct"),
        } if moor.get("ok") else None,
        "communique": {
            "latest": eng.get("communique", {}).get("latest"),
            "flags": eng.get("communique", {}).get("flags"),
            "n_statements": eng.get("communique", {}).get("n_statements"),
        } if eng.get("communique", {}).get("ok") else None,
        "riptide": {
            "live": deep.get("riptide", {}).get("live"),
            "flat_water": deep.get("riptide", {}).get("flat_water"),
            "asof": deep.get("riptide", {}).get("asof"),
        } if (deep.get("riptide") or {}).get("ok") else None,
        "breakwater": {
            "rescue_proximity": eng.get("breakwater", {}).get("rescue_proximity"),
            "revealed_threshold": eng.get("breakwater", {}).get("revealed_threshold"),
            "reading": eng.get("breakwater", {}).get("reading"),
        } if eng.get("breakwater", {}).get("ok") else None,
        "sonar_flagged": [m for m in sonar.get("movers", []) if m.get("flag")][:6],
        "calendar": snap.get("calendar", {}),
        "playbook": deep.get("playbook", {}).get("tables") if (deep.get("playbook") or {}).get("ok") else None,
        "playbook_state": (deep.get("playbook") or {}).get("state"),
        "backtest_headline": {
            "recall": bt.get("recall"), "precision": bt.get("precision"),
            "base_rate": bt.get("base_rate"), "median_lead_d": bt.get("median_lead_d"),
        },
        "backtest_caveats": (deep.get("backtest") or {}).get("caveats"),
        "faults": snap.get("faults"),
        "provenance_staleness": stale_counts,
    }


async def _via_router(messages: list[dict]) -> str | None:
    try:
        from free_llm_router import FreeLLMRouter
    except ImportError:
        return None
    router = FreeLLMRouter()
    try:
        # router envelope: {"text", "model", "provider", "tokens", ...}.
        # fast tier first (non-reasoning models: clean output for a read-the-
        # pack task); smart as fallback, with _strip_reasoning as the net for
        # chain-of-thought leakage.
        last: Exception | None = None
        for tier in ("fast", "smart"):
            try:
                resp = await router.chat_completion(messages, tier=tier, temperature=0.2, max_tokens=700)
                return resp["text"]
            except Exception as e:  # noqa: BLE001 — try the other tier
                last = e
        if last:
            raise last
        return None
    finally:
        await router.close()


async def _via_env(messages: list[dict]) -> str | None:
    base = os.environ.get("SEICHE_LLM_BASE_URL")
    if not base:
        return None
    key = os.environ.get("SEICHE_LLM_API_KEY", "")
    model = os.environ.get("SEICHE_LLM_MODEL", "gpt-4o-mini")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 700},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def ask(question: str, snap: dict) -> dict:
    pack = context_pack(snap)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "CONTEXT PACK:\n" + json.dumps(pack, default=str)
                                    + f"\n\nQUESTION: {question}"},
    ]
    errors = []
    for route, fn in (("free-llm-router", _via_router), ("env-endpoint", _via_env)):
        try:
            answer = await fn(messages)
            if answer:
                return {"ok": True, "route": route, "answer": _strip_reasoning(answer),
                        "grounding": "answers are restricted to the context pack; verify against the board"}
        except Exception as e:
            errors.append(f"{route}: {type(e).__name__}: {str(e)[:80]}")
    return {
        "ok": False,
        "reason": "no LLM route available (" + ("; ".join(errors) if errors else
                  "free-llm-router unkeyed and SEICHE_LLM_BASE_URL unset") + ")",
        "context_pack": pack,
        "hint": "the context pack above is self-contained — paste it into any chat model",
    }
