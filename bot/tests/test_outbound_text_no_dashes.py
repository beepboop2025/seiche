"""House prose rule for shipped bot text: commas, colons or parentheses.

Every string literal in a deployed bot can reach Telegram, so
none may carry an em or en dash. Docstrings never ship and are exempt, as is
a bare dash passed as the first argument of a str method that parses or
cleans upstream text (replace, split, startswith and the like). Comments
are not string literals.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHES = ("\u2014", "\u2013")
PARSE_METHODS = {
    "count", "endswith", "find", "index", "lstrip", "partition", "replace",
    "rfind", "rindex", "rpartition", "rsplit", "rstrip", "split", "startswith",
    "strip",
}
# The Seiche desk bot, synced byte-for-byte into fleet-bots and deployed
# from there. bot/rissaga*.py are not deployed from this repository.
CHECKED = (
    "bot/seiche_bot.py",
)


def _exempt_nodes(tree: ast.AST) -> set[int]:
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                exempt.add(id(body[0].value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in PARSE_METHODS and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in DASHES):
            exempt.add(id(node.args[0]))
    return exempt


def dash_literals(source: str, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    exempt = _exempt_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in exempt
                and any(dash in node.value for dash in DASHES)):
            found.append(f"{filename}:{node.lineno}: {node.value[:90]!r}")
    return sorted(found)


@pytest.mark.parametrize("relative", CHECKED)
def test_shipped_bot_text_has_no_em_or_en_dash(relative: str) -> None:
    path = ROOT / relative
    offenders = dash_literals(path.read_text(encoding="utf-8"), relative)
    assert not offenders, "\n".join(offenders[:20])


def test_checker_flags_plain_and_formatted_text_but_not_comments() -> None:
    em, en = DASHES
    source = (
        '"""Module docstring may keep ' + em + ' its voice."""\n'
        "def f(x):\n"
        '    """Function docstring ' + en + ' exempt."""\n'
        "    # a comment " + em + " never ships\n"
        '    clean = x.replace("' + em + '", "-").replace("' + en + '", "-")\n'
        '    word = x.split("' + em + '")[0]\n'
        '    plain = "Gauge ' + em + ' now"\n'
        '    formatted = f"{x} ' + en + ' {clean}"\n'
        '    escaped = "board \\u2014 quiet"\n'
        "    return plain + formatted + escaped\n"
    )
    offenders = dash_literals(source)
    assert [line.split(":")[1] for line in offenders] == ["7", "8", "9"]
