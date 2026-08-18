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
7. If liquilens layers or liquilens_evidence_markets are in the pack, repeat the served available / cannot_see / validated_backtest_eligible / real_money_eligible flags. An unavailable sibling is UNAVAILABLE, never CALM. Do not invent a joint Seiche+LiquiLens+Undertow score. Do not promote construction-PIT diagnostics to validated-backtest or real-money evidence.
8. Output ONLY the final answer. No reasoning preamble, no "we need to", no meta-commentary about the task."""


def _fp(deep: dict) -> dict:
    """Funding Pop reading, canonical key first, legacy `riptide` as fallback.

    The engine was renamed on 2026-08-04. Boards assembled before that date
    carry only the old key, so read both rather than going dark on history.
    """
    return (deep.get("funding_pop") or deep.get("riptide") or {})


def context_pack(snap: dict) -> dict:
    """Compact, deterministic extract of the payload — the model's whole world."""
    eng = snap.get("engines") or {}
    deep = snap.get("deep") or {}
    comp = eng.get("composite") or {}
    tell = deep.get("tell") or {}
    turn = (deep.get("turn") or {}).get("next_turn")
    ml = deep.get("ml") or {}
    backtest = deep.get("backtest") or {}
    bt = backtest.get("event_capture") or {}
    sonar = eng.get("sonar") or {}
    basins = eng.get("basins") or {}
    moor = eng.get("moorings") or {}
    kink = eng.get("kink") or {}
    weather = eng.get("weather") or {}
    resonance = eng.get("resonance") or {}
    warehouse = eng.get("warehouse") or {}
    echo = eng.get("echo") or {}
    book = deep.get("book") or {}
    stacker = deep.get("stacker") or {}
    farbasin = eng.get("farbasin") or {}
    tidetables = deep.get("tidetables") or {}
    undertow = eng.get("undertow") or {}
    bathymetry = deep.get("bathymetry") or {}
    swell = deep.get("swell") or {}
    merian = eng.get("merian") or {}
    gyre = deep.get("gyre") or {}
    roguewave = eng.get("roguewave") or {}
    communique = eng.get("communique") or {}
    breakwater = eng.get("breakwater") or {}
    playbook = deep.get("playbook") or {}
    prov = snap.get("provenance") or []
    if isinstance(prov, dict):
        # Compatibility with the first packaged restart seed. Full assembled
        # boards use a list, but an older durable handoff must not crash /ask.
        prov = list(prov.values())
    stale_counts: dict[str, int] = {}
    for p in prov:
        if not isinstance(p, dict):
            continue
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
        "kink": {k: kink.get(k) for k in ("kink_reserves_b", "current_reserves_b", "distance_b", "days_to_kink", "r2", "asof")} if kink.get("ok") else None,
        "weather_crunches": (weather.get("crunch_windows") or [])[:5],
        "resonance": {
            "score": resonance.get("score"),
            "worst_mode": resonance.get("worst_mode"),
        } if resonance.get("ok") else None,
        "warehouse": {k: warehouse.get(k) for k in ("total_net_b", "total_pctl", "long_end_share_pct", "asof")} if warehouse.get("ok") else None,
        "echo_top": echo.get("top"),
        "book": {
            "today": book.get("today"),
            "verdict": (book.get("backtest") or {}).get("verdict"),
            "live": book.get("live"),
        } if book.get("ok") else None,
        "stacker": {
            "p_now": stacker.get("p_now"),
            "published": stacker.get("published"),
            "dispersion_now": stacker.get("dispersion_now"),
            "verdict": stacker.get("verdict"),
        } if stacker.get("ok") else None,
        "farbasin": {
            "channels": {k: {kk: vv for kk, vv in (v or {}).items() if kk != "series"}
                          for k, v in (farbasin.get("channels") or {}).items()},
            "status": farbasin.get("status"),
        } if farbasin.get("ok") else None,
        "tidetables": {
            "event_odds": tidetables.get("event_odds"),
            "novelty": tidetables.get("novelty"),
            "skill_verdict": (tidetables.get("skill") or {}).get("verdict"),
            "asof": tidetables.get("asof"),
        } if tidetables.get("ok") else None,
        "undertow": {
            "score": undertow.get("score"),
            "per_series": {
                k: {kk: v.get(kk) for kk in ("ac1_pctl", "tau_bd", "var_pctl")}
                for k, v in (undertow.get("per_series") or {}).items()
            },
            "asof": undertow.get("asof"),
        } if undertow.get("ok") else None,
        "bathymetry": {
            "p_event_5bd": bathymetry.get("p_event_5bd"),
            "mfpt_bd": bathymetry.get("mfpt_bd"),
            "floor": {
                k: (bathymetry.get("floor") or {}).get(k)
                for k in ("well_bp", "stiffness", "barrier_kt")
            },
            "tau_bd": (bathymetry.get("spectrum") or {}).get("tau_bd"),
            "tau_pctl": (bathymetry.get("spectrum") or {}).get("tau_pctl"),
            "entropy_pctl": (bathymetry.get("arrow") or {}).get("pctl"),
            "validation_verdict": (bathymetry.get("validation") or {}).get("verdict"),
            "asof": bathymetry.get("asof"),
        } if bathymetry.get("ok") else None,
        "swell": {
            "p_event_5bd": swell.get("p_event_5bd"),
            "event_by_horizon": swell.get("event_by_horizon"),
            "peak": swell.get("peak"),
            "validation_verdict": (swell.get("validation") or {}).get("verdict"),
            "asof": swell.get("asof"),
        } if swell.get("ok") else None,
        "merian": {
            "instability": merian.get("instability"),
            "modes": (merian.get("modes") or [])[:3],
            "asof": merian.get("asof"),
        } if merian.get("ok") else None,
        "gyre": {
            "determinism_verdict": (gyre.get("determinism") or {}).get("verdict"),
            "nonlinearity_verdict": (gyre.get("nonlinearity") or {}).get("verdict"),
            "stability": gyre.get("stability"),
            "forecast": gyre.get("forecast"),
            "asof": gyre.get("asof"),
        } if gyre.get("ok") else None,
        "roguewave": {
            "tail_verdict": roguewave.get("tail_verdict"),
            "fit": roguewave.get("fit"),
            "return_levels": roguewave.get("return_levels"),
            "sample_max_bp": roguewave.get("sample_max_bp"),
            "asof": roguewave.get("asof"),
        } if roguewave.get("ok") else None,
        "basins": basins.get("basins") if basins.get("ok") else None,
        "swap_lines_30d_m": (basins.get("swap_lines") or {}).get("ops_30d_total_m") if basins.get("ok") else None,
        "moorings": {
            "usdt_dev_bp": (moor.get("usdt") or {}).get("dev_bp"),
            "stable_total_b": (moor.get("demand") or {}).get("total_b"),
            "stable_chg_30d_pct": (moor.get("demand") or {}).get("chg_30d_pct"),
        } if moor.get("ok") else None,
        "communique": {
            "latest": communique.get("latest"),
            "flags": communique.get("flags"),
            "n_statements": communique.get("n_statements"),
        } if communique.get("ok") else None,
        # Read the canonical key, fall back to the legacy one so a board
        # assembled before the 2026-08-04 rename still answers.
        "funding_pop": {
            "live": _fp(deep).get("live"),
            "flat_water": _fp(deep).get("flat_water"),
            "asof": _fp(deep).get("asof"),
        } if _fp(deep).get("ok") else None,
        "breakwater": {
            "rescue_proximity": breakwater.get("rescue_proximity"),
            "revealed_threshold": breakwater.get("revealed_threshold"),
            "reading": breakwater.get("reading"),
        } if breakwater.get("ok") else None,
        "sonar_flagged": [m for m in sonar.get("movers", []) if m.get("flag")][:6],
        "calendar": snap.get("calendar", {}),
        "playbook": playbook.get("tables") if playbook.get("ok") else None,
        "playbook_state": playbook.get("state"),
        "backtest_headline": {
            "recall": bt.get("recall"), "precision": bt.get("precision"),
            "base_rate": bt.get("base_rate"), "median_lead_d": bt.get("median_lead_d"),
        },
        "backtest_caveats": backtest.get("caveats"),
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


_INSTITUTION_WORDS = ("bank", "nbfc", "lender", "mfi", "microfinance", "sfb",
                      "institution", "credit suisse", "svb", "silicon valley",
                      "northern rock", "esaf", "indusind", "counterparty",
                      "tandem")


def wants_liquilens_sibling(question: str) -> bool:
    """Institution or tandem questions attach fail-closed LiquiLens screens."""
    q = (question or "").lower()
    return any(word in q for word in _INSTITUTION_WORDS)


def compact_liquilens_evidence_markets(data: dict | None) -> dict:
    """Fail-closed eligibility surface. Missing or drifted payload is UNAVAILABLE."""
    evidence_id = "liquilens:evidence_markets"
    unavailable = {
        "evidence_id": evidence_id,
        "available": False,
        "reading": "UNAVAILABLE",
        "validated_backtest_eligible": False,
        "real_money_eligible": False,
    }
    if not isinstance(data, dict):
        return {**unavailable, "reason": "reading unavailable"}
    markets = data.get("markets")
    if not isinstance(markets, list):
        return {
            **unavailable,
            "reason": "invalid reading schema: evidence_markets.markets must be an array",
        }
    compact_markets = []
    for row in markets[:16]:
        if not isinstance(row, dict):
            continue
        hist = row.get("historical_evidence")
        hist = hist if isinstance(hist, dict) else {}
        compact_markets.append({
            "name": row.get("name"),
            "historical_evidence": {
                "validated_backtest_eligible": hist.get("validated_backtest_eligible") is True,
                "real_money_eligible": hist.get("real_money_eligible") is True,
            },
        })
    return {
        "evidence_id": evidence_id,
        "available": True,
        "markets": compact_markets,
        "validated_backtest_eligible": data.get("validated_backtest_eligible") is True,
        "real_money_eligible": data.get("real_money_eligible") is True,
    }


def _mcp_payload(result: dict) -> dict | None:
    """Accept either structuredContent or the text-content fallback."""
    if result.get("isError"):
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content") or []
    if content and isinstance(content[0], dict) and content[0].get("text"):
        parsed = json.loads(content[0]["text"])
        return parsed if isinstance(parsed, dict) else None
    return None


async def _liquilens_mcp(tool: str, args: dict | None = None) -> dict | None:
    """Optional LiquiLens MCP read. Callers must fail-close the result;
    a missing sibling is UNAVAILABLE, not omitted-as-calm."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                "https://api.liquilens.in/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": tool, "arguments": args or {}}})
            r.raise_for_status()
            return _mcp_payload(r.json().get("result") or {})
    except Exception:
        # Isolation boundary: sibling MCP is never a Seiche board input.
        # Absence is returned as None so the caller can render UNAVAILABLE.
        return None


async def _liquilens_board() -> dict | None:
    """Compatibility wrapper — prefer ``_liquilens_mcp`` for new callers."""
    return await _liquilens_mcp("failure_radar_board")


async def ask(question: str, snap: dict) -> dict:
    pack = context_pack(snap)
    if wants_liquilens_sibling(question):
        from seiche import event_analysis

        pack["liquilens"] = await event_analysis.liquilens_desk_sibling(question)
        pack["liquilens_evidence_markets"] = compact_liquilens_evidence_markets(
            await _liquilens_mcp("evidence_markets")
        )
        pack["liquilens_note"] = (
            "LiquiLens institution screens use the same fail-closed REST "
            "compaction as event_analysis; unavailable layers are UNAVAILABLE, "
            "never CALM; cite as (liquilens <layer>, as_of); do not invent a "
            "joint score"
        )
        pack["liquilens_evidence_note"] = (
            "historical diagnostics retain their served eligibility flags; "
            "do not treat them as validated-backtest or real-money evidence "
            "unless the cited payload says so"
        )
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
