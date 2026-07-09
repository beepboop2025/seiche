"""seiche.mcp_server — the funding-stress judgment layer, as an agent tool.

A Model Context Protocol (MCP) server that lets any LLM agent read the same
board a human sees: the current stress regime, the forward odds of a funding
event, the nearest historical analogs, the honest backtest, and the Time
Machine replay.

Design matches the project ethos — *no new dependencies, fail loud, nothing
clever*: it speaks JSON-RPC 2.0 using only the standard library, and every tool
wraps the same ``assemble.snapshot()`` the CLI and REST API read, so there is
exactly one source of truth. FactIQ-style data feeds hand an agent raw macro
numbers; Seiche hands it the *conclusion* — a regime read, a probability, and a
track record — which is the part raw data can't answer.

Two transports share one dispatch:

  * **stdio** (``seiche mcp`` / ``seiche-mcp``) — newline-delimited JSON per the
    MCP stdio contract, for a locally-installed agent.
  * **HTTP** (``POST /mcp`` in api.py) — the hosted, metered endpoint an agent
    adds by URL, no install. That layer decides the surface per request.

Surface: the *public* surface exposes only the free tools (the conclusion, the
forward odds, the analogs, the PROOF scoreboard, data health, the brief),
mirroring the anonymous ``/api/public`` surface; the *full* surface adds the
subscriber tools (positioning book, desk assistant). For stdio the surface is
fixed by ``SEICHE_MCP_PUBLIC``; for HTTP it is chosen from the caller's token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from typing import Any

# The MCP protocol revision this server implements. If a client asks for a
# different one we echo back what it requested (servers negotiate down); this is
# only the default we advertise when the client sends nothing usable.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "seiche"

# Default surface for the stdio transport. HTTP overrides this per request.
PUBLIC_ONLY = os.getenv("SEICHE_MCP_PUBLIC", "0") == "1"

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _resolve_public(public: bool | None) -> bool:
    """None means 'use the transport default' (the stdio env flag)."""
    return PUBLIC_ONLY if public is None else public


# ---------------------------------------------------------------------------
# Snapshot access — one assemble per TTL, shared across tool calls. The
# assembler does its own upstream caching; this avoids re-assembling the
# (expensive) board on every tool call within a short window.
# ---------------------------------------------------------------------------

_CACHE_TTL_S = 300
_cache: dict[str, Any] = {"snap": None, "at": 0.0}


def _get_snapshot(force: bool = False) -> dict:
    """Return the live board, memoised for _CACHE_TTL_S. Synchronous wrapper
    around the async assembler (mirrors how cli.py bridges with asyncio.run).
    Must be called off the event loop — the HTTP layer runs it in a worker
    thread, stdio runs it at top level."""
    from seiche import assemble

    now = time.time()
    if not force and _cache["snap"] is not None and now - _cache["at"] < _CACHE_TTL_S:
        return _cache["snap"]
    snap = asyncio.run(assemble.snapshot(force=force))
    _cache["snap"] = snap
    _cache["at"] = now
    return snap


def _get_asof(date: str) -> dict:
    from seiche import assemble

    return asyncio.run(assemble.snapshot_asof(date))


@contextlib.contextmanager
def _stdout_to_stderr():
    """Keep the stdio protocol stream clean: any stray print() from the backend
    goes to stderr, never into the JSON-RPC stdout channel. Used only by the
    stdio loop — the HTTP transport shares the process's stdout with uvicorn."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


class ToolError(Exception):
    """Raised by a tool handler for an expected, reportable failure (surfaced to
    the agent as an isError result, not a protocol error)."""


# ---------------------------------------------------------------------------
# Tools. Each handler takes (validated arguments, public flag) and returns
# either a JSON-serialisable object (rendered as pretty JSON) or a markdown
# string. The public flag shapes content for anonymous callers.
# ---------------------------------------------------------------------------


def _need(section: dict | None, label: str) -> dict:
    """Fail loud when a board section is missing or reported itself down."""
    if not section:
        raise ToolError(f"{label} is unavailable in this snapshot")
    if section.get("ok") is False:
        raise ToolError(f"{label} unavailable: {section.get('reason', 'unknown reason')}")
    return section


def tool_stress_now(_args: dict, public: bool) -> Any:
    snap = _get_snapshot()
    if public:
        from seiche import public_view

        return public_view.public_payload(snap)
    comp = snap.get("engines", {}).get("composite", {})
    tell = snap.get("deep", {}).get("tell", {})
    one = (
        f"SEICHE {comp.get('value')} {comp.get('regime')} "
        f"(coverage {comp.get('coverage_pct')}%)"
    )
    if tell.get("ok"):
        one += f" · tell {tell.get('tell'):+.0f}"
    return {
        "as_of": snap.get("generated_at"),
        "headline": one,
        "composite": comp,
        "tell": tell,
        "faults": snap.get("faults") or [],
        "version": snap.get("version"),
        "reading": (
            "composite is a 0-100 funding-stress index; regime is one of "
            "CALM / EROSION / STRAIN / STRESS. 'decomposition' lists each "
            "component's contribution; a DEAD component means its input went "
            "stale (fail-loud, not silently dropped)."
        ),
    }


def tool_forecast(_args: dict, public: bool) -> Any:
    if public:
        raise ToolError("the forward forecast is a subscriber tool — sign in with a token")
    snap = _get_snapshot()
    deep = snap.get("deep", {})
    out: dict[str, Any] = {"as_of": snap.get("generated_at"), "sources": {}}

    swell = deep.get("swell", {})
    if swell.get("ok"):
        out["sources"]["swell"] = {
            "p_event_by_horizon": swell.get("event_by_horizon", {}),
            "peak_day": swell.get("peak", {}),
            "validation": swell.get("validation", {}),
        }
    bath = deep.get("bathymetry", {})
    if bath.get("ok"):
        out["sources"]["bathymetry"] = {
            "p_event_by_horizon": bath.get("p_by_horizon", {}),
            "expected_days_to_event_bd": bath.get("mfpt_bd"),
            "state_now": bath.get("state_now", {}),
            "validation": bath.get("validation", {}),
        }
    ml = deep.get("ml", {})
    if ml.get("ok"):
        out["sources"]["ml"] = {
            "p_event_5bd": ml.get("p_event_5bd"),
            "verdict": ml.get("verdict"),
            "validation": ml.get("validation", {}),
        }
    mk = deep.get("markov", {})
    if mk.get("ok"):
        out["sources"]["markov"] = {
            "current_regime": mk.get("current_regime"),
            "p_reach_stress_by_horizon": mk.get("p_reach_stress", {}),
            "expected_dwell_bd": mk.get("expected_dwell_bd"),
        }
    oj = deep.get("oujump", {})
    if oj.get("ok"):
        out["sources"]["oujump"] = {
            "level_now": oj.get("level_now"),
            "half_life_bd": (oj.get("fit") or {}).get("half_life_bd"),
            "p_above_stress_by_horizon": {
                str(h["h"]): h["p_above_stress"] for h in oj.get("horizons", [])
            },
        }
    mc = deep.get("montecarlo", {})
    if mc.get("ok"):
        out["sources"]["montecarlo"] = {
            "level_now": mc.get("level_now"),
            "fan": mc.get("fan", []),
            "p_touch_stress_by_horizon": mc.get("p_touch_stress", {}),
            "p_back_to_calm_by_horizon": mc.get("p_back_to_calm", {}),
        }
    if not out["sources"]:
        raise ToolError(
            "no forecast engine is available yet — the board needs enough "
            "history to fit them (run a full pull first)"
        )
    out["reading"] = (
        "independent forward views of the same board. P(event) sources: Swell "
        "(term-structure), Bathymetry (first-passage physics), ML (gradient "
        "boosting). Scenario sources on the index: Markov (regime-transition "
        "odds of reaching STRESS), oujump (analytic OU+jump endpoint marginal), "
        "montecarlo (simulated path fan and path-max odds of touching STRESS). "
        "Agreement across sources is the strong signal; divergence is a reason "
        "to widen your uncertainty. Levels are for ranking, not literal odds — "
        "check proof_backtest for the honest track record and its blind spots."
    )
    return out


def tool_analogs(_args: dict, _public: bool) -> Any:
    snap = _get_snapshot()
    t = _need(snap.get("deep", {}).get("tidetables"), "Tide Tables (historical analogs)")
    return {
        "as_of": snap.get("generated_at"),
        "event_odds": t.get("event_odds", {}),
        "novelty": t.get("novelty", {}),
        "hindcast_skill": t.get("skill", {}),
        "nearest_analogs": t.get("analogs", [])[:8],
        "forward_fan": (t.get("fan") or [])[-1:],
        "horizon_bd": t.get("horizon_bd"),
        "reading": (
            "finds the historical days whose funding conditions most resemble "
            "today, then reports how often those analogs saw a stress event "
            "within the horizon. 'novelty: uncharted' means today has no close "
            "precedent — treat the odds with extra caution."
        ),
    }


def tool_replay(args: dict, public: bool) -> Any:
    if public:
        raise ToolError("the Time Machine replay is a subscriber tool — sign in with a token")
    date = (args or {}).get("date", "")
    if not isinstance(date, str) or not _is_iso_date(date):
        raise ToolError("`date` must be a calendar date as YYYY-MM-DD")
    p = _get_asof(date)
    if p.get("ok") is False:
        raise ToolError(f"replay unavailable for {date}: {p.get('reason', 'no data')}")
    comp = p.get("engines", {}).get("composite", {})
    weather = p.get("engines", {}).get("weather", {})
    return {
        "as_of": p.get("asof", date),
        "composite": {
            "value": comp.get("value"),
            "regime": comp.get("regime"),
            "coverage_pct": comp.get("coverage_pct"),
            "decomposition": comp.get("decomposition", []),
        },
        "crunch_windows": (weather.get("crunch_windows") or [])[:5],
        "vintage_note": p.get("vintage_note"),
        "reading": (
            "the whole board reconstructed as it would have read on that date, "
            "point-in-time (no lookahead). Use it to test a thesis against how "
            "Seiche actually called a past episode."
        ),
    }


def tool_proof(_args: dict, _public: bool) -> Any:
    snap = _get_snapshot()
    bt = _need(snap.get("deep", {}).get("backtest"), "PROOF backtest")
    return {
        "as_of": snap.get("generated_at"),
        "sample": bt.get("sample", {}),
        "event_capture": bt.get("event_capture", {}),
        "orthogonal": bt.get("orthogonal", {}),
        "episodes": bt.get("episodes", []),
        "caveats": bt.get("caveats", []),
        "reading": (
            "the track record, stated honestly: recall/precision with 95% CIs "
            "over the labelled funding events, an orthogonal test that strips "
            "the signal's own variables, and every named episode including the "
            "misses. This is the credibility layer — read the caveats."
        ),
    }


def tool_book(_args: dict, public: bool) -> Any:
    if public:
        raise ToolError("the positioning book is a subscriber tool — sign in with a token")
    snap = _get_snapshot()
    deep = snap.get("deep", {})
    bk = _need(deep.get("book"), "The Book (positioning)")
    out = {
        "as_of": snap.get("generated_at"),
        "today": bk.get("today", {}),
        "walk_forward": bk.get("backtest", {}),
        "live_record": bk.get("live", {}),
        "caveats": bk.get("caveats", []),
    }
    stk = deep.get("stacker", {})
    if stk.get("ok"):
        out["ensemble"] = {
            "p_event_5bd": stk.get("p_now"),
            "published": stk.get("published"),
            "dispersion": stk.get("dispersion_now"),
            "verdict": stk.get("verdict"),
        }
    out["reading"] = (
        "a stance (risk_on / risk_off / neutral) and the positions implied by "
        "the stress read, with the walk-forward Sharpe and the live "
        "as-published record. Not investment advice — a codified reading."
    )
    return out


def tool_brief(_args: dict, public: bool) -> Any:
    if public:
        raise ToolError("the desk brief is a subscriber tool — sign in with a token")
    from seiche import brief

    snap = _get_snapshot()
    return brief.render_markdown(snap)


def tool_health(_args: dict, _public: bool) -> Any:
    snap = _get_snapshot()
    return {
        "generated_at": snap.get("generated_at"),
        "version": snap.get("version"),
        "faults": snap.get("faults") or [],
        "provenance": snap.get("provenance"),
        "reading": (
            "data freshness and provenance for every input. A non-empty "
            "'faults' list means one or more series are stale or unreachable — "
            "Seiche surfaces that rather than papering over it."
        ),
    }


def tool_ask(args: dict, public: bool) -> Any:
    if public:
        raise ToolError("the desk assistant is a subscriber tool — sign in with a token")
    from seiche import ai

    q = (args or {}).get("question", "")
    if not isinstance(q, str) or not q.strip():
        raise ToolError("`question` is required")
    if len(q) > 600:
        raise ToolError("`question` must be 1-600 characters")
    snap = _get_snapshot()
    res = asyncio.run(ai.ask(q, snap))
    if not res.get("ok"):
        raise ToolError(
            res.get("reason", "the desk assistant is not configured "
                    "(set SEICHE_LLM_BASE_URL / SEICHE_LLM_API_KEY)")
        )
    return {"answer": res.get("answer"), "grounding": res.get("grounding"),
            "route": res.get("route")}


def _is_iso_date(s: str) -> bool:
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    y, m, d = s[:4], s[5:7], s[8:10]
    return y.isdigit() and m.isdigit() and d.isdigit()


# name -> (title, description, input JSON Schema, handler, is_public)
TOOLS: dict[str, tuple] = {
    "funding_stress_now": (
        "Current funding-stress read",
        "The live money-market funding-stress reading: a 0-100 composite index, "
        "the regime (CALM/EROSION/STRAIN/STRESS), per-component decomposition, "
        "the market-stress 'Tell', and any data faults. Ask this whenever an "
        "analysis touches US dollar funding, repo, reserves, the Fed's balance "
        "sheet, or liquidity conditions.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_stress_now,
        True,
    ),
    "funding_stress_forecast": (
        "Forward odds of a funding-stress event",
        "Forward odds of a funding-stress event over the next 5/10/21 business "
        "days from six independent views: three P(event) models (term-structure, "
        "first-passage physics, ML) and three stochastic scenarios on the index "
        "(regime-transition Markov, OU+jump analytic marginal, Monte Carlo path "
        "fan). Agreement is the signal. Use for forward-looking liquidity-risk "
        "questions. Subscriber tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_forecast,
        False,
    ),
    "historical_analogs": (
        "Nearest historical analogs",
        "The historical days most similar to today's funding conditions, and "
        "how often those analogs led to a stress event — plus a novelty flag "
        "for whether today has any close precedent. Use to ground a 'what "
        "usually happens from here' question in real history.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_analogs,
        True,
    ),
    "replay_asof": (
        "Time Machine: the board on a past date",
        "Reconstruct the entire funding-stress board as it read on a historical "
        "date, point-in-time with no lookahead. Use to test whether Seiche "
        "would have flagged a past liquidity episode, or to align a backtest "
        "with what was knowable then. Subscriber tool (the Time Machine).",
        {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Calendar date as YYYY-MM-DD (e.g. 2019-09-17).",
                    "pattern": r"^\d{4}-\d{2}-\d{2}$",
                }
            },
            "required": ["date"],
            "additionalProperties": False,
        },
        tool_replay,
        False,
    ),
    "proof_backtest": (
        "PROOF: the honest track record",
        "The backtest scoreboard, stated honestly: recall and precision with "
        "95% confidence intervals over labelled funding events, an orthogonal "
        "robustness test, every named episode (hits and misses), and the "
        "caveats. Use to judge how much to trust the readings.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_proof,
        True,
    ),
    "data_health": (
        "Data freshness & provenance",
        "Freshness, provenance, and fault status for every underlying series "
        "(FRED, NY Fed, OFR, Treasury). Call this to confirm the board is "
        "current before relying on a reading.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_health,
        True,
    ),
    "positioning_book": (
        "The Book: implied stance & positions",
        "The stance (risk_on / risk_off / neutral) and positions implied by the "
        "stress read, with walk-forward Sharpe and the live as-published "
        "record. Not investment advice. Subscriber tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_book,
        False,
    ),
    "desk_brief": (
        "This morning's desk note (markdown)",
        "The full human-readable desk brief for today as markdown — the "
        "narrative summary of the whole board. Good when you want prose to "
        "quote or summarise rather than structured fields. Subscriber tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_brief,
        False,
    ),
    "ask_desk": (
        "Ask the desk assistant (grounded)",
        "Ask a natural-language question answered strictly from the live board, "
        "with the grounding cited. Requires an LLM endpoint configured on the "
        "server. Subscriber tool.",
        {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Your question about funding conditions (1-600 chars).",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        tool_ask,
        False,
    ),
}

# Method names that count as billable tool usage (for the HTTP meter).
BILLABLE_METHODS = {"tools/call"}


def _visible_tools(public: bool | None = None) -> dict[str, tuple]:
    pub = _resolve_public(public)
    if pub:
        return {k: v for k, v in TOOLS.items() if v[4]}
    return TOOLS


SERVER_INSTRUCTIONS = (
    "Seiche is a funding-stress early-warning terminal for US money markets, "
    "built entirely from free public data (Fed H.4.1, NY Fed operations, OFR "
    "repo, Treasury cash). It provides the *judgment* on top of the data: a "
    "current stress regime, forward event odds, historical analogs, and an "
    "honest backtest. Reach for these tools whenever a task involves dollar "
    "funding, repo, bank reserves, the Fed balance sheet, liquidity risk, or "
    "'is now a dangerous moment in money markets'. Every reading is "
    "point-in-time and every claim is backed by the PROOF scoreboard — cite it."
)


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 dispatch. Shared by both transports.
# ---------------------------------------------------------------------------


def _result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _server_version() -> str:
    try:
        from seiche import assemble

        return assemble.VERSION
    except Exception:
        return "0.2.0"


def _handle_initialize(msg_id: Any, params: dict) -> dict:
    client_ver = (params or {}).get("protocolVersion")
    version = client_ver if isinstance(client_ver, str) and client_ver else PROTOCOL_VERSION
    return _result(
        msg_id,
        {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": SERVER_NAME,
                "title": "Seiche — funding-stress terminal",
                "version": _server_version(),
            },
            "instructions": SERVER_INSTRUCTIONS,
        },
    )


def _handle_tools_list(msg_id: Any, public: bool | None) -> dict:
    tools = [
        {
            "name": name,
            "title": title,
            "description": desc,
            "inputSchema": schema,
        }
        for name, (title, desc, schema, _handler, _pub) in _visible_tools(public).items()
    ]
    return _result(msg_id, {"tools": tools})


def _handle_tools_call(msg_id: Any, params: dict, public: bool | None) -> dict:
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}
    entry = _visible_tools(public).get(name)
    if entry is None:
        return _error(msg_id, INVALID_PARAMS, f"unknown tool '{name}'")
    handler = entry[3]
    try:
        payload = handler(args, _resolve_public(public))
    except ToolError as exc:
        return _result(
            msg_id,
            {"content": [{"type": "text", "text": f"ERROR: {exc}"}], "isError": True},
        )
    except Exception as exc:  # unexpected — still report as a tool error, loudly
        return _result(
            msg_id,
            {
                "content": [
                    {"type": "text", "text": f"ERROR: {type(exc).__name__}: {exc}"}
                ],
                "isError": True,
            },
        )
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
    return _result(msg_id, {"content": [{"type": "text", "text": text}]})


def dispatch(msg: dict, public: bool | None = None) -> dict | None:
    """Route one JSON-RPC message. Returns a response dict, or None for
    notifications (which take no reply). `public` selects the tool surface;
    None uses the transport default (the stdio env flag)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None,
                      INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    # Notifications: acknowledge silently.
    if is_notification:
        return None

    # params may legally be omitted; a non-object (array/string/number) is
    # malformed — treat as empty so handlers never hit an AttributeError.
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}
    if method == "initialize":
        return _handle_initialize(msg_id, params)
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _handle_tools_list(msg_id, public)
    if method == "tools/call":
        return _handle_tools_call(msg_id, params, public)
    # Politely report empty for capabilities we don't offer, so probing clients
    # don't choke.
    if method == "resources/list":
        return _result(msg_id, {"resources": []})
    if method == "prompts/list":
        return _result(msg_id, {"prompts": []})
    return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")


# ---------------------------------------------------------------------------
# stdio transport.
# ---------------------------------------------------------------------------


def _send(resp: dict) -> None:
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def serve_stdio() -> int:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout.
    Runs until stdin closes."""
    surface = "public" if PUBLIC_ONLY else "full"
    print(f"seiche mcp: serving {len(_visible_tools())} tools ({surface} surface) "
          f"on stdio — protocol {PROTOCOL_VERSION}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send(_error(None, PARSE_ERROR, "invalid JSON"))
            continue
        # stdio in 2025-06-18 drops batching, but tolerate a JSON array anyway.
        msgs = msg if isinstance(msg, list) else [msg]
        for m in msgs:
            with _stdout_to_stderr():                # backend prints -> stderr
                resp = dispatch(m)
            if resp is not None:
                _send(resp)
    return 0


def main() -> None:
    sys.exit(serve_stdio())


if __name__ == "__main__":
    main()
