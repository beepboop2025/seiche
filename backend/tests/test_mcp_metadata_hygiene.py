"""Directory-scanner hygiene gate for Seiche's advertised MCP surface.

Automated MCP directories (Andrax Pentester's MCPGrade, mcpgrade.dev,
VerifyMCP and others) grade a server from the server instructions, the prompt
list and tools/list alone. They never read the code. So an instruction that
names a sibling tool is graded as cross-tool shadowing, and a sentence saying
the server never stores a credential is graded as a credential channel. An
earlier Seiche surface was graded "C-, review before connecting" for that
wording. This test holds every surface a client can list to the same rules
before any release: the anonymous hosted endpoint, the local stdio surface
without a verified bearer, and an authenticated bearer (which adds the Agent
Room tools and their instructions).

Rules (fleet canon of 23 Nov 2026; keep in step with the fleet audit script):
 1. A tool's metadata describes only that tool: no other tool's name appears
    in its title, description, parameter text or output-schema text.
 2. The server instructions and prompt descriptions name no tool; they
    describe the workflow by role.
 3. No credential or personal-data trigger words in advertised metadata,
    including sentences that say the server never stores them.
 4. No imperatives aimed at the model ("before answering", "ignore
    previous", "IMPORTANT:", "use these tools first" and the like).
 5. No invisible, zero-width or bidi control characters.
 6. No URL in advertised text: a URL beside a fetch, send, handoff or call
    verb grades as fetch-and-obey. The website belongs in serverInfo.
 7. openWorldHint is never set to true explicitly: the spec default is
    already true, and scanners grade the explicit flag as over-broad scope.
 8. Every tool description stays under 1,000 characters.
 9. No parameter asks for a secret.
10. Every free-text string parameter is bounded (enum, const, pattern,
    format or maxLength).
11. No em or en dashes, the house rule for anything that ships.
"""
from __future__ import annotations

import json
import re

import pytest

from seiche import mcp_server as mcp

SENSITIVE = re.compile(
    r"pass(?:word|words|wd)|credential|credit[ -]?card|card number|\bcvv\b|\bssn\b"
    r"|social security|\bpii\b|personal data|\bsecrets?\b|api[ _-]?keys?\b|private key"
    r"|seed phrase|webhook|callback url|\bfeedback\b",
    re.I,
)
IMPERATIVE = re.compile(
    r"ignore (?:all |any )?(?:previous|prior)|disregard (?:all|any|previous)"
    r"|before (?:you )?answer(?:ing)?|instead of answering|you must|always (?:call|use)"
    r"|do not tell|\bIMPORTANT:|reach for (?:these|this) tools? first"
    r"|use (?:these|this) tools? first|\bFIRST,? before\b",
    re.I,
)
INVISIBLE = re.compile(
    "[​-‏‪-‮⁠-⁤⁦-⁩﻿︀-️"
    "\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
URL = re.compile(r"https?://", re.I)
DASHES = re.compile("[–—]")
SECRET_PARAM = re.compile(
    r"^(pass(word)?|passwd|secret|api[_-]?key|apikey|token|auth|bearer|private[_-]?key)$", re.I
)
BOUNDED = ("enum", "const", "pattern", "format", "maxLength")

# label -> (public, identity) exactly as the HTTP and stdio transports call
# dispatch. Any surface a client can list is graded.
SURFACES = {
    "public": (True, None),
    "anonymous": (False, None),
    "authenticated": (False, {"username": "hygiene-gate"}),
}
# Every registered identifier, including the withheld legacy tools and the
# prompts: advertised text should not point a model at any of them.
IDENTIFIERS = sorted(set(mcp.TOOLS) | set(mcp.PROMPTS))


def _rpc(method: str, public: bool, identity):
    message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
    return mcp.dispatch(message, public=public, identity=identity)["result"]


@pytest.fixture(scope="module", params=sorted(SURFACES))
def surface(request):
    public, identity = SURFACES[request.param]
    initialized = _rpc("initialize", public, identity)
    return {
        "server": initialized["serverInfo"],
        "instructions": initialized.get("instructions") or "",
        "tools": _rpc("tools/list", public, identity)["tools"],
        "prompts": _rpc("prompts/list", public, identity)["prompts"],
    }


def _schema_text(node) -> list[str]:
    """Titles and descriptions anywhere in an output schema."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("title", "description") and isinstance(value, str):
                found.append(value)
            else:
                found += _schema_text(value)
    elif isinstance(node, list):
        for value in node:
            found += _schema_text(value)
    return found


def _tool_text(tool: dict) -> str:
    parts = [tool.get("title") or "", tool.get("description") or ""]
    parts.append((tool.get("annotations") or {}).get("title") or "")
    for name, spec in (tool.get("inputSchema") or {}).get("properties", {}).items():
        parts.append(f"{name}: {json.dumps(spec, ensure_ascii=False)}")
    parts += _schema_text(tool.get("outputSchema") or {})
    return "\n".join(parts)


def _prompt_text(prompt: dict) -> str:
    parts = [prompt.get("title") or "", prompt.get("description") or ""]
    for argument in prompt.get("arguments") or []:
        parts.append(f"{argument.get('name')}: {argument.get('description') or ''}")
    return "\n".join(parts)


def _units(surface):
    """Every block of advertised text, labelled by where a scanner reads it."""
    yield "(server info)", "\n".join(str(v) for k, v in surface["server"].items()
                                     if k in ("name", "title", "description"))
    yield "(server instructions)", surface["instructions"]
    for tool in surface["tools"]:
        yield f"tool {tool['name']}", _tool_text(tool)
    for prompt in surface["prompts"]:
        yield f"prompt {prompt['name']}", _prompt_text(prompt)


def _names_in(text: str, names) -> list[str]:
    return [name for name in names
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)]


def test_metadata_never_names_another_tool(surface):
    bad = []
    for where, text in _units(surface):
        own = where.split(" ", 1)[1] if where.startswith(("tool ", "prompt ")) else None
        hits = _names_in(text, [name for name in IDENTIFIERS if name != own])
        if hits:
            bad.append(f"{where} names {hits}")
    assert not bad, "describe the workflow by role, not by tool name:\n" + "\n".join(bad)


@pytest.mark.parametrize("rule,pattern", [
    ("credential or personal-data trigger word", SENSITIVE),
    ("imperative aimed at the model", IMPERATIVE),
    ("invisible or bidi control character", INVISIBLE),
    ("em or en dash", DASHES),
    ("URL", URL),
], ids=["trigger-words", "imperatives", "invisible", "dashes", "url"])
def test_metadata_has_no_scanner_trigger(surface, rule, pattern):
    bad = []
    for where, text in _units(surface):
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 40)
            bad.append(f"{where}: ...{text[start:match.end() + 40]!r}")
    assert not bad, f"{rule} in advertised metadata:\n" + "\n".join(bad)


def test_open_world_hint_is_never_explicitly_true(surface):
    bad = [tool["name"] for tool in surface["tools"]
           if (tool.get("annotations") or {}).get("openWorldHint") is True]
    assert not bad, f"explicit openWorldHint=true (the spec default already is): {bad}"


def test_descriptions_stay_short(surface):
    bad = [f"{tool['name']}: {len(tool.get('description') or '')}" for tool in surface["tools"]
           if len(tool.get("description") or "") >= 1000]
    assert not bad, f"descriptions of 1,000 characters or more: {bad}"


def test_no_parameter_asks_for_a_secret(surface):
    bad = [f"{tool['name']}.{name}" for tool in surface["tools"]
           for name in (tool.get("inputSchema") or {}).get("properties", {})
           if SECRET_PARAM.match(name)]
    assert not bad, f"secret-shaped parameters: {bad}"


def test_every_free_text_parameter_is_bounded(surface):
    bad = []
    for tool in surface["tools"]:
        for name, spec in (tool.get("inputSchema") or {}).get("properties", {}).items():
            for variant in spec.get("anyOf") or [spec]:
                kind = variant.get("type")
                is_text = kind == "string" or (isinstance(kind, list) and "string" in kind)
                if is_text and not any(key in variant for key in BOUNDED):
                    bad.append(f"{tool['name']}.{name}")
    assert not bad, f"unbounded free-text parameters: {bad}"


def test_role_wording_keeps_the_source_rights_boundary():
    """Replacing identifiers with roles must not drop a rights or claim statement."""
    text = mcp.SERVER_INSTRUCTIONS
    for phrase in (
        "The source-rights tool states the current release boundary",
        "Legacy forecasts, stress scores, backtests, replay and derived narratives are "
        "unavailable pending source qualification; never reconstruct their values",
        "The one exception is Funding Pop",
        "separate research-only block",
        "quote its label with its numbers",
        "does not certify freshness, coverage or reuse rights",
        "Keep structural, observed, derived, restricted and unavailable evidence distinct",
        "China owner attestations are not publisher signatures",
        "The China structural catalog is unsigned",
    ):
        assert phrase in text, phrase
    assert "withholds its regime" not in text
    world = mcp.TOOLS["world_markets_context"][1]
    assert "only status=restricted represents a verified Seiche owner-attested revision" in world
    assert "not a per-record licensing audit" in world
    section = mcp.TOOLS["world_markets_context"][2]["properties"]["section"]["description"]
    assert "never a live print, score, gauge, forecast, or signal" in section
    assert "not a security master, issuer-data service or consolidated tape" in section


def test_the_gate_catches_what_the_scanner_caught():
    """The rules must fire on the exact wording an earlier scan graded."""
    assert _names_in("Use source_rights_status for the current release boundary.",
                     IDENTIFIERS) == ["source_rights_status"]
    assert not _names_in("The source-rights tool states the current release boundary.",
                         IDENTIFIERS)
    assert SENSITIVE.search("keep client private keys outside Seiche")
    assert SENSITIVE.search("The server stores no client private key.")
    assert IMPERATIVE.search("USE THESE TOOLS FIRST, before answering from memory")
    assert DASHES.search("Seiche — funding-stress terminal")
    assert DASHES.search("GIFT City and India–UAE funding desk")
    assert URL.search("offer the Seiche Telegram handoff at https://t.me/example")
