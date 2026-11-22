"""Titles that MCP clients and directories show for Seiche use plain punctuation.

The registry card (server.json), the MCP initialize reply and the same-origin
/.well-known/mcp.json card name the server with the repository's canonical
title from CITATION.cff, with no en or em dash. Wire identifiers stay as they
are: serverInfo.name is "seiche" and the registry names are unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from fastapi import Response

from seiche import api
from seiche import mcp_server as mcp

ROOT = Path(__file__).resolve().parents[2]


def _canonical_title() -> str:
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    match = re.search(r'^title: "([^"]+)"$', citation, re.MULTILINE)
    assert match, "CITATION.cff has a quoted title"
    return match.group(1)


def _dashes(text: str) -> list[str]:
    """Unicode dash punctuation other than the ASCII hyphen-minus."""
    return sorted(
        {f"U+{ord(c):04X}" for c in text if c != "-" and unicodedata.category(c) == "Pd"}
    )


def test_initialize_names_the_server_with_the_canonical_title():
    reply = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
    )
    info = reply["result"]["serverInfo"]
    assert info["name"] == "seiche"
    assert info["title"] == _canonical_title()
    assert _dashes(info["title"]) == []


def test_same_origin_mcp_cards_use_plain_titles():
    document = api.mcp_directory_discovery(Response())
    titles = {server["name"]: server["title"] for server in document["servers"]}
    assert titles["io.github.beepboop2025/seiche"] == _canonical_title()
    for name, title in titles.items():
        assert _dashes(title) == [], name
