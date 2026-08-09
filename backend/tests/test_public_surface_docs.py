"""The docs must name the same anonymous surface the code serves.

README.md claimed `/mcp` served "the full tool surface, free for everyone, no
token needed" for the whole life of that sentence. It was true once and stopped
being true at 82d5700, which correctly cut anonymous callers to a curated
surface. The
code was already tested; the prose was not, so nothing failed and the README
went on telling agent-builders they could call `replay_asof` without a token.

This is the test that would have caught it. It reads `is_public` out of
`TOOLS` and asserts the prose agrees, in both directions: a tool that goes
public must be added to the docs, and a tool that stops being public must be
removed from them. Names, not adjectives, because "the full surface" is exactly
the kind of phrase that stays grammatical while going false.
"""

from pathlib import Path
import re

import pytest

from seiche import mcp_server

REPO = Path(__file__).resolve().parents[2]

PUBLIC = {n for n, t in mcp_server.TOOLS.items() if t[4]}
GATED = {n for n, t in mcp_server.TOOLS.items() if not t[4]}

# Prose that names the anonymous surface. Each must list every public tool and
# no gated one anywhere near that claim, so the file is checked whole.
DOCS = [
    REPO / "README.md",
    REPO / "docs" / "MCP.md",
    REPO / "backend" / "seiche" / "mcp_server.py",
]


def test_the_surface_is_eight_tools():
    """A guard on the guard: if this number moves, every sentence below moves
    with it, and someone has to decide that deliberately."""
    assert len(PUBLIC) == 8, sorted(PUBLIC)
    assert len(GATED) == 5, sorted(GATED)
    assert PUBLIC == {
        "funding_stress_now", "historical_analogs", "proof_backtest",
        "data_health", "crypto_stress_record", "institutional_flows",
        "oil_funding_context", "fx_materials_passage",
    }


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_docs_name_every_anonymous_tool(path):
    text = path.read_text()
    missing = sorted(n for n in PUBLIC if n not in text)
    assert not missing, f"{path.name} does not name the free tools: {missing}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_docs_do_not_promise_the_full_surface_for_free(path):
    """The exact phrasings that were false. Not a spelling test: each of these
    said, in prose, that an anonymous caller gets everything."""
    lowered = path.read_text().lower()
    for claim in ("the full tool surface, free for everyone",
                  "full tool surface, free",
                  "full surface, free for everyone",
                  "every tool, no token"):
        assert claim not in lowered, f"{path.name} still claims: {claim!r}"


def test_the_llms_index_names_the_free_tools():
    """llms.txt is generated, so the assertion belongs on the generator's copy
    (`frontend/public/llms.txt` is gitignored and only exists after a publish)."""
    from seiche import dispatch_pages

    preamble = dispatch_pages._LLMS_PREAMBLE
    assert "api.seiche.info/mcp" in preamble
    for name in PUBLIC:
        assert name in preamble, f"llms.txt preamble omits the free tool {name}"
    # It may name the gated ones too, but it must not imply they are free.
    assert "no auth needed;" not in preamble


def test_server_json_describes_the_public_flag_accurately():
    import json

    meta = json.loads((REPO / "server.json").read_text())
    desc = ""
    for pkg in meta.get("packages", []):
        for env in pkg.get("environmentVariables", []):
            if env.get("name") == "SEICHE_MCP_PUBLIC":
                desc = env.get("description", "")
    assert desc, "SEICHE_MCP_PUBLIC is no longer documented in server.json"
    missing = sorted(n for n in PUBLIC if n not in desc)
    assert not missing, f"server.json omits free tools: {missing}"


def test_frontend_percentiles_use_the_shared_ordinal_helper():
    """Prevent live values such as 62 from rendering as the malformed `62th`."""
    offenders = []
    for path in (REPO / "frontend" / "src").rglob("*.tsx"):
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"}\s*th\b", line):
                offenders.append(f"{path.relative_to(REPO)}:{line_no}: {line.strip()}")
    assert not offenders, "frontend hard-codes an ordinal suffix:\n" + "\n".join(offenders)


def test_account_copy_matches_the_authenticated_mcp_surface():
    """Accounts may hold alerts, but bearer identity also gates MCP tools."""
    account = (REPO / "frontend" / "src" / "tabs" / "Account.tsx").read_text()
    support = (REPO / "frontend" / "public" / "support.html").read_text()
    assert "exists for exactly one thing" not in account
    assert len(GATED) == 5
    assert "five additional hosted MCP tools" in account
    assert "five bearer-token MCP tools" in support
