"""Funding Pop is shown research only, with its skill, on every surface.

Owner ruling, 12 Nov 2026 ("Show it as research-only"): the MCP shows Funding
Pop, clearly marked research only with its skill, so agents and the website
say the same thing. These tests hold the label every surface quotes, the
subscriber alert's own text, the one-block projection the MCP reads, the text
block, and the named exception in every copy surface that used to say every
legacy forecast is unavailable. The MCP tool result itself is tested in
test_mcp_server.py. The alert code stays riptide_sticky and the riptide
alias stays on REST: stored preferences and readers use them.
"""
import ast
import copy
from pathlib import Path

import pytest

from seiche import alerts, public_rights
from seiche.engines import funding_pop
from seiche.mcp_text import adapters, render

REPO = Path(__file__).resolve().parents[2]
DASHES = ("—", "–")
EXCEPTION = "Funding Pop"


def _block(sticky=-0.038, escalates=0.012, live=None):
    return {"ok": True, "asof": "2026-11-12", "live": live, "flat_water": live is None,
            "validation": {"sticky": {"n_scored": 60, "skill": sticky},
                           "escalates": {"n_scored": 60, "skill": escalates}},
            "validated_backtest": False, "real_money_eligible": False}


@pytest.mark.parametrize("block,label", [
    (_block(), "research only, not validated: sticky skill -0.038, escalates skill +0.012 vs the base rate"),
    (_block(sticky=0.21, escalates=None), "research only, not validated: sticky skill +0.210 vs the base rate"),
    ({"validation": {"sticky": {"n_scored": 4, "verdict": "too few resolved pops"}}},
     "research only, not validated: skill not yet scored"),
    ({"validation": {"sticky": {"skill": True}}}, "research only, not validated: skill not yet scored"),
    (None, "research only, not validated: skill not yet scored"),
])
def test_the_label_comes_from_the_blocks_own_validation(block, label):
    assert funding_pop.research_label(block) == label
    assert funding_pop.CLAIM_STATUS == "research_only"


def test_the_subscriber_alert_carries_the_label_and_keeps_its_code(monkeypatch):
    monkeypatch.setitem(alerts.ALERT_RULES, "riptide_sticky", 0.6)
    live = {"date": "2026-11-12", "pop_bp": 9, "p_sticky": 0.7, "p_escalates": 0.2,
            "rrp_cosigned": False}
    snap = {"engines": {}, "headline": {}, "deep": {"funding_pop": _block(live=live)}}
    fired = [row for row in alerts._candidates(snap) if row[0] == "riptide_sticky"]
    assert len(fired) == 1
    message = fired[0][2]
    assert message.startswith("Funding Pop (research only, not validated: sticky skill -0.038, "
                              "escalates skill +0.012 vs the base rate): the 9bp pop on 2026-11-12")
    # a board assembled before the rename still fires under the alias
    legacy = {"engines": {}, "headline": {}, "deep": {"riptide": _block(live=live)}}
    assert [row[0] for row in alerts._candidates(legacy) if row[0] == "riptide_sticky"] == ["riptide_sticky"]


def _served_constants_with_dashes(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and any(dash in node.value for dash in DASHES)):
            continue
        if isinstance(parents.get(node), ast.Expr):
            continue  # a docstring or bare string statement: never sent
        found.append((node.lineno, node.value[:70]))
    return found


@pytest.mark.parametrize("relative", ["backend/seiche/engines/funding_pop.py", "backend/seiche/alerts.py"])
def test_no_alert_or_funding_pop_string_ships_an_en_or_em_dash(relative):
    assert _served_constants_with_dashes(REPO / relative) == []


def test_the_mcp_reads_exactly_the_block_the_overview_publishes(monkeypatch):
    monkeypatch.setattr(public_rights._Ledger, "verdict", lambda self, inputs: {
        "status": "public", "sources": [], "restricted_inputs": []})
    snap = {"engines": {}, "deep": {"funding_pop": _block()}}
    block = public_rights.public_deep(snap, "funding_pop")
    assert block == public_rights.cached_public_board(snap)["deep"]["funding_pop"]
    block["validation"] = "mutated"
    assert public_rights.public_deep(snap, "funding_pop")["validation"] != "mutated"
    assert public_rights.public_deep(snap, "no_such_block") is None


def test_the_text_block_names_funding_pop_and_draws_no_number_from_it():
    out = {"ok": True, "selection": "summary", "caveats": [],
           "funding_pop": {"claim_status": "research_only", "status": "research",
                           "label": funding_pop.research_label(_block()),
                           "reading": copy.deepcopy(_block())}}
    text = render.compose(adapters.build("money_market_context", out))
    assert "funding_pop research_only" in text
    assert "-0.038" not in text and "0.012" not in text


COPY_SURFACES = {
    "backend/README.md": "Funding Pop\nis the one exception",
    "backend/seiche/dispatch_pages.py": "except Funding Pop, which money_market_context serves research only",
    "frontend/public/developers.html": "Funding Pop is the one exception, served research only with its skill",
    "frontend/public/support.html": "Funding Pop is the one exception, shown research only with its skill",
    "frontend/src/tabs/Account.tsx": "Funding Pop is the one exception, shown research only with its skill",
    "server.json": "except Funding Pop, served research only with its skill",
    "frontend/public/.well-known/ai-catalog.json": "except Funding Pop, served research only with its skill",
    "integrations/mcp-clients/README.md": "except Funding Pop, served research only with its skill",
}


@pytest.mark.parametrize("relative,phrase", sorted(COPY_SURFACES.items()))
def test_every_surface_that_states_the_boundary_names_the_exception(relative, phrase):
    assert phrase in (REPO / relative).read_text(encoding="utf-8")
