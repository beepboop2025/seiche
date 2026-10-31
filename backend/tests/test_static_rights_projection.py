"""No withheld board value may reach a public static file.

The publish job writes /data/overview.json, /data/public.json and
/data/book_history.json and renders the home page, link cards and three
derived pages from them. Each file must carry exactly what its API route may
serve. These tests run the real publish steps in order over a board whose
withheld fields hold unique sentinel strings, then search every public file.
The same pipeline with the routes opened must leak the sentinels, which proves
the search can see a leak.
"""

from __future__ import annotations

import importlib.util
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from seiche import ampleness, prerender, public_rights, publisher, referee, skeptic

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "frontend" / "index.html"
STATIC = ("/data/overview.json", "/data/public.json", "/data/book_history.json")
SENTINELS = (
    "SENTINELREGIME",
    "Sentinel thesis 7f3a9c",
    "Sentinel standfirst 7f3a9c",
    "Sentinel evidence 7f3a9c",
    "2031-12-25",
    "2031-12-24T06:07",
    "2031-12-23",
    "sentinelstance",
)


def _script(name: str):
    path = ROOT / "backend" / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"rights_{name.removesuffix('.py')}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sentinel_board(fake_snap: dict) -> dict:
    board = deepcopy(fake_snap)
    board["generated_at"] = "2031-12-24T06:07:08+00:00"
    board["engines"]["composite"].update(value=46.537, regime="SENTINELREGIME", coverage_pct=100.0)
    board["editorial"] = {
        "thesis": "Sentinel thesis 7f3a9c",
        "standfirst": "Sentinel standfirst 7f3a9c",
        "evidence": [{"label": "Sentinel", "claim": "Sentinel evidence 7f3a9c",
                      "source": "fixture", "asof": "2031-12-25"}],
    }
    board["headline"] = {key: {"value": 3.9417, "asof": "2031-12-25", "status": "observed",
                               "source": "fixture"} for key in ("sofr_pct", "reserves_b", "vix")}
    deep = board.setdefault("deep", {})
    deep["tell"] = {"ok": True, "tell": 13.137, "reading": "aligned", "plumbing_pctl": 70,
                    "market_pctl": 57, "asof": "2031-12-25"}
    deep["book"] = {"ok": True, "today": {"stance": "sentinelstance",
                                          "positions": [{"sleeve": "cash", "weight": 0.4242}],
                                          "p_ensemble": 0.07731, "dispersion": 0.04131}}
    return board


def _prior_book() -> list[dict]:
    history: list[dict] = []
    record = {"date": "2031-12-23", "stance": "sentinelstance", "positions": [["cash", 1.0]],
              "p_ensemble": 0.07731, "dispersion": 0.04131, "index": 46.537,
              "regime": "SENTINELREGIME"}
    history.append(publisher.get_publisher().publish(record, history))
    assert publisher.verify_chain(history)[0]
    return history


def _publish(tmp_path: Path, board: dict) -> tuple[Path, Path]:
    """Run the publish steps in publish.yml order against a built-site layout."""
    site = tmp_path / "dist"
    private = tmp_path / "private" / "board.json"
    captured = tmp_path / "captured.json"
    captured.write_text(json.dumps(board))
    data = site / "data"

    assert _script("export_public.py").main([
        "--snapshot", str(captured), "--private-snapshot", str(private),
        str(data / "public.json"), str(data / "overview.json"),
    ]) == 0
    previous = tmp_path / "previous_book.json"
    previous.write_text(json.dumps(_prior_book()))
    assert _script("append_book_record.py").main([
        "--snapshot", str(data / "overview.json"), str(previous), str(data / "book_history.json"),
    ]) == 0

    # The page steps read the same baked file the static site serves.
    published = skeptic.load_snapshot(data / "overview.json")
    skeptic.write_skeptic(published, site / "skeptic" / "index.html")
    ampleness.write_ampleness(published, site / "ampleness" / "index.html")
    referee.write_referee(published, site / "referee" / "index.html")

    (site / "dispatches").mkdir(parents=True)
    (site / "dispatches" / "index.json").write_text("[]")
    (site / "index.html").write_text(SHELL.read_text().replace(
        "</head>", '<link rel="stylesheet" href="/assets/index-test.css" />\n</head>', 1))
    assert prerender.build(site) >= 1000
    return site, private


def _public_text(site: Path) -> dict[str, str]:
    """Every text file the site would serve; images are covered by the card manifest."""
    return {path.relative_to(site).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(site.rglob("*"))
            if path.is_file() and path.suffix in {".html", ".json", ".xml", ".txt", ".md"}}


def _leaks(files: dict[str, str]) -> dict[str, list[str]]:
    found = {name: [s for s in SENTINELS if s in body] for name, body in files.items()}
    return {name: hits for name, hits in found.items() if hits}


def _open_routes(monkeypatch) -> None:
    monkeypatch.setattr(public_rights, "LEGACY_PATHS", public_rights.LEGACY_PATHS
                        - {public_rights.STATIC_SURFACES[path] for path in STATIC})


def test_every_static_board_file_is_its_routes_restriction():
    for path in STATIC:
        api = public_rights.STATIC_SURFACES[path]
        assert public_rights.legacy_http_restricted(api)
        assert public_rights.static_restriction(path) == public_rights.legacy_unavailable(path)
        assert public_rights.is_restriction(public_rights.legacy_unavailable(api))


def test_no_withheld_value_reaches_any_public_file(tmp_path, fake_snap):
    board = _sentinel_board(fake_snap)
    site, private = _publish(tmp_path, board)
    files = _public_text(site)

    for path in STATIC:
        assert json.loads(files[path.lstrip("/")]) == public_rights.legacy_unavailable(path)
    assert _leaks(files) == {}

    home = prerender.body_text(files["index.html"])
    assert "The funding board is withheld pending source-rights qualification" in home
    assert public_rights.POLICY in home
    assert "The composite reads" not in home
    assert "og:updated_time" not in files["index.html"]

    # The full board survives only at the private path, outside the site.
    assert json.loads(private.read_text()) == board
    assert private.stat().st_mode & 0o077 == 0
    assert site not in private.parents


def test_no_withheld_value_reaches_any_link_card(tmp_path, fake_snap):
    pytest.importorskip("PIL")
    from seiche import social_cards

    site, _private = _publish(tmp_path, _sentinel_board(fake_snap))
    manifest = social_cards.build(site)
    files = _public_text(site)

    assert _leaks(files) == {}
    by_kind: dict[str, list[dict]] = {}
    for view in manifest["views"]:
        by_kind.setdefault(view["kind"], []).append(view)
    assert {view["status"] for view in by_kind["board"]} == {"WITHHELD · RIGHTS RESTRICTED"}
    assert all(view["value"] == "WITHHELD" and view["generated_at"] is None
               for kind in ("board", "series", "tab") for view in by_kind[kind])
    assert sorted(view["identifier"] for view in by_kind["series"]) == sorted(
        identifier.replace("_", "-") for identifier in social_cards._WITHHELD_SERIES)
    meta = re.search(r'<meta property="og:title" content="([^"]+)">', files["index.html"])
    assert meta and meta.group(1) == "Dollar funding board withheld"


def test_the_leak_search_detects_an_open_route(tmp_path, fake_snap, monkeypatch):
    """Opening the routes republishes the board, so the search must find it."""
    _open_routes(monkeypatch)
    site, _private = _publish(tmp_path, _sentinel_board(fake_snap))
    leaks = _leaks(_public_text(site))

    for name in ("data/overview.json", "data/public.json", "data/book_history.json", "index.html"):
        assert name in leaks, name
    book = json.loads((site / "data" / "book_history.json").read_text())
    assert [row["date"] for row in book] == ["2031-12-23", "2031-12-24"]
