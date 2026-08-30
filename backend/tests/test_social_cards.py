"""Contextual cards are exact static publication artifacts, never live work."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
from copy import deepcopy
from pathlib import Path

from PIL import ImageDraw

from seiche import social_cards
from seiche.social_cards import CardMetric, CardSpec

ROOT = Path(__file__).resolve().parents[2]


def _png_size(payload: bytes) -> tuple[int, int]:
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


def _spec(**overrides) -> CardSpec:
    values = {
        "kind": "board",
        "identifier": "composite",
        "canonical_url": "https://seiche.info/views/board/composite/",
        "eyebrow": "live funding board",
        "title": "Dollar funding stress: EROSION",
        "description": "The public reading with its coverage, clock and known faults.",
        "status": "EROSION",
        "value": "41",
        "unit": "out of 100",
        "metrics": (
            CardMetric("coverage", "96%"),
            CardMetric("the Tell", "+12"),
            CardMetric("source faults", "0"),
        ),
        "as_of": "2026-08-30T00:00:00Z",
        "generated_at": "2026-08-30T00:00:00Z",
        "source": "Seiche composite",
        "rights": "Research context, not investment advice.",
    }
    values.update(overrides)
    return CardSpec(**values)


def _page(title: str, canonical: str, *, jsonld: bool = False) -> str:
    structured = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","image":"https://seiche.info/og.png"}'
        "</script>"
        if jsonld
        else ""
    )
    return f"""<!doctype html><html><head>
<title>{title}</title>
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:title" content="old">
<meta property="og:image" content="https://seiche.info/og2.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://seiche.info/og2.png">
{structured}</head><body>{title}</body></html>"""


def _built_site(tmp_path: Path, fake_snap: dict) -> Path:
    site = tmp_path / "dist"
    (site / "data").mkdir(parents=True)
    snapshot = deepcopy(fake_snap)
    snapshot["headline"] = {
        "sofr_pct": {
            "value": 5.31,
            "asof": "2026-08-29",
            "status": "observed",
            "source": "Federal Reserve Bank of New York",
        },
        "tga_b": {
            "value": None,
            "asof": "2026-08-27",
            "status": "stale",
            "fresh": False,
            "source": "U.S. Treasury",
        },
    }
    (site / "data" / "overview.json").write_text(json.dumps(snapshot))
    (site / "index.html").write_text(
        _page("Seiche", "https://seiche.info/").replace(
            "</body>", '<div id="root"></div><script type="module"></script></body>'
        )
    )

    source_catalog = (
        social_cards.REPO_ROOT
        / "frontend"
        / "public"
        / "money-markets"
        / "catalog.json"
    )
    (site / "money-markets").mkdir()
    shutil.copy(source_catalog, site / "money-markets" / "catalog.json")
    (site / "money-markets" / "index.html").write_text(
        _page("Money markets", "https://seiche.info/money-markets/")
    )
    for relative in (
        "markets",
        "markets/forex",
        "markets/capital-markets",
        "markets/china-macro",
    ):
        target = site / relative
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(
            _page(relative, f"https://seiche.info/{relative}/")
        )

    dispatch = {
        "slug": "2026-08-30-daily",
        "title": "The cash clock moved while the screen stayed calm",
        "date": "2026-08-30",
        "tag": "EROSION",
        "summary": "A dated funding argument with its countercase and source clock.",
    }
    (site / "dispatches").mkdir()
    (site / "dispatches" / "index.json").write_text(json.dumps([dispatch]))
    (site / "dispatches" / f"{dispatch['slug']}.html").write_text(
        _page(
            dispatch["title"],
            f"https://seiche.info/dispatches/{dispatch['slug']}",
            jsonld=True,
        )
    )

    article = {
        "slug": "cash-clock-analysis",
        "headline": "The cash clock moved before the screen",
        "dek": "A point-in-time analysis of the published funding evidence.",
        "date": "2026-08-30",
        "published_at": "2026-08-30T10:00:00Z",
        "evidence_as_of": "2026-08-29",
        "article_type": "analysis",
        "editorial_class": "desk_brief",
        "word_count": 917,
    }
    article_dir = site / "articles" / article["slug"]
    article_dir.mkdir(parents=True)
    (site / "articles" / "index.json").write_text(json.dumps([article]))
    (article_dir / "index.html").write_text(
        _page(
            article["headline"],
            f"https://seiche.info/articles/{article['slug']}/",
            jsonld=True,
        )
    )
    (site / "sitemap.xml").write_text(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://seiche.info/</loc></url></urlset>"
    )
    return site


def test_renderer_is_deterministic_and_exactly_1200_by_630() -> None:
    first = social_cards.render_card(_spec())
    second = social_cards.render_card(_spec())
    unavailable = social_cards.render_card(
        _spec(
            status="UNAVAILABLE",
            value=None,
            description="No completed evidence is available.",
        )
    )

    assert first == second
    assert _png_size(first) == (1200, 630)
    assert first != unavailable


def test_evidence_row_and_footer_text_remain_inside_the_safe_area(
    monkeypatch,
) -> None:
    drawn: list[tuple[str, tuple[int, int, int, int]]] = []
    original = ImageDraw.ImageDraw.text

    def tracked(draw, xy, value, *args, **kwargs):
        bounds = draw.textbbox(
            xy,
            value,
            font=kwargs.get("font"),
            anchor=kwargs.get("anchor"),
            stroke_width=kwargs.get("stroke_width", 0),
        )
        drawn.append((str(value), bounds))
        return original(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", tracked)
    social_cards.render_card(
        _spec(
            title="The cash clock moved before the screen and the evidence stayed attached",
            source="Seiche analysis desk with a deliberately extended publication identity",
            generated_at="2026-08-30T10:00:00Z-with-an-impossibly-long-clock-suffix",
            metrics=(
                CardMetric("article type", "analysis"),
                CardMetric("words", "917"),
                CardMetric("editorial class", "one_unbroken_editorial_class_token" * 3),
            ),
        )
    )

    assert drawn
    for value, (left, top, right, bottom) in drawn:
        assert left >= 0, (value, left, top, right, bottom)
        assert top >= 0, (value, left, top, right, bottom)
        assert right <= social_cards.WIDTH, (value, left, top, right, bottom)
        assert bottom <= social_cards.HEIGHT, (value, left, top, right, bottom)
    lower_rows = [bounds for _value, bounds in drawn if bounds[1] >= 400]
    assert lower_rows
    assert min(bounds[0] for bounds in lower_rows) >= social_cards.SAFE_GUTTER
    assert max(bounds[2] for bounds in lower_rows) <= (
        social_cards.WIDTH - social_cards.SAFE_GUTTER
    )


def test_board_card_keeps_source_faults_in_the_visible_contract(
    fake_snap: dict,
) -> None:
    snapshot = deepcopy(fake_snap)
    snapshot["faults"] = [{"source": "NY Fed", "reason": "upstream timeout"}]

    spec = social_cards._board_spec(
        snapshot,
        canonical_url="https://seiche.info/views/board/composite/",
        identifier="composite",
    )

    assert spec.status == "EROSION · 1 SOURCE FAULT"
    assert CardMetric("source faults", "1") in spec.metrics[:3]
    assert spec.as_of_label == "SNAPSHOT GENERATED"
    assert spec.as_of == snapshot["generated_at"]

    snapshot["provenance"]["WALCL"]["fresh"] = False
    stale = social_cards._board_spec(
        snapshot,
        canonical_url="https://seiche.info/views/board/composite/",
        identifier="composite",
    )
    assert "1 STALE INPUT" in stale.status
    assert CardMetric("stale inputs", "1") in stale.metrics[:3]


def test_metadata_rewrite_is_complete_escaped_and_idempotent() -> None:
    spec = _spec(title='Cash & collateral say "watch"')
    image = "https://seiche.info/share/cards/board/composite.abc123.png"
    once = social_cards.patch_page_metadata(
        _page("old", "https://seiche.info/old", jsonld=True),
        spec,
        image,
        patch_jsonld_image=True,
    )
    twice = social_cards.patch_page_metadata(once, spec, image, patch_jsonld_image=True)

    assert once == twice
    for key in (
        "og:url",
        "og:title",
        "og:description",
        "og:image",
        "og:image:secure_url",
        "og:image:type",
        "og:image:width",
        "og:image:height",
        "og:image:alt",
        "twitter:card",
        "twitter:title",
        "twitter:description",
        "twitter:image",
        "twitter:image:alt",
    ):
        assert once.count(f'="{key}"') == 1
    assert "Cash &amp; collateral" in once
    assert f'"image":"{image}"' in once


def test_build_emits_real_views_unique_editorial_cards_and_fail_closed_states(
    tmp_path: Path, fake_snap: dict
) -> None:
    site = _built_site(tmp_path, fake_snap)
    manifest = social_cards.build(site)

    assert manifest["schema"] == "seiche.social-cards.v1"
    assert manifest["image_contract"] == {
        "width": 1200,
        "height": 630,
        "type": "image/png",
        "content_addressed": True,
    }
    assert manifest["request_time_collection"] is False
    assert manifest["request_time_model_fitting"] is False
    assert "published headline series" in manifest["share_route_contract"]["covered"]
    assert manifest["share_route_contract"]["fragment_only_gaps"] == []
    assert set(manifest["share_route_contract"]["non_shareable_surfaces"]) == {
        "CORPUS",
        "TIME MACHINE",
        "ACCOUNT",
    }
    assert "unbounded" in manifest["known_gap"]
    assert "download=null" in manifest["known_gap"]

    expected_views = (
        site / "views" / "board" / "composite" / "index.html",
        site / "views" / "world-markets" / "forex" / "index.html",
        site / "views" / "money-markets" / "US-USD" / "index.html",
        site / "views" / "series" / "sofr-pct" / "index.html",
        site / "views" / "series" / "tga-b" / "index.html",
        site / "views" / "tabs" / "today" / "index.html",
        site / "views" / "tabs" / "oil-funding" / "index.html",
        site / "views" / "tabs" / "proof" / "index.html",
        site / "views" / "tabs" / "system" / "index.html",
    )
    assert all(path.exists() for path in expected_views)
    assert "STALE" in (site / "views" / "series" / "tga-b" / "index.html").read_text()
    assert (
        "not available"
        in (site / "views" / "series" / "tga-b" / "index.html").read_text()
    )

    root = (site / "index.html").read_text()
    forex = (site / "markets" / "forex" / "index.html").read_text()
    dispatch = (site / "dispatches" / "2026-08-30-daily.html").read_text()
    article = (site / "articles" / "cash-clock-analysis" / "index.html").read_text()
    for page in (root, forex, dispatch, article):
        assert 'content="1200"' in page
        assert 'content="630"' in page
        assert 'content="image/png"' in page
        assert "og:image:secure_url" in page
        assert "twitter:image:alt" in page

    images = {
        re.search(r'<meta property="og:image" content="([^"]+)">', page).group(1)
        for page in (root, forex, dispatch, article)
    }
    assert len(images) == 4
    for image_url in images:
        relative = image_url.removeprefix("https://seiche.info/")
        payload = (site / relative).read_bytes()
        assert _png_size(payload) == (1200, 630)
        digest = hashlib.sha256(payload).hexdigest()[:16]
        assert f".{digest}.png" in image_url

    assert (
        "https://seiche.info/views/world-markets/forex/"
        in (site / "sitemap.xml").read_text()
    )
    forex_view = (site / "views" / "world-markets" / "forex" / "index.html").read_text()
    assert "pricing.. Status" not in forex_view
    assert "pricing. Status" in forex_view
    assert (site / "share" / "cards" / "manifest.json").exists()


def test_social_card_toolchain_is_hash_locked_before_every_full_suite() -> None:
    requirements_path = ROOT / "ops" / "requirements-social-cards.txt"
    requirements = requirements_path.read_text()
    match = re.search(
        r"Pillow==12\.3\.0\s+\\\s*--hash=sha256:([0-9a-f]{64})",
        requirements,
    )
    assert match
    assert match.group(1) == (
        "78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91"
    )
    assert "Pillow" not in (ROOT / "backend" / "pyproject.toml").read_text()
    publication_gate = (
        ROOT / "ops" / "release" / "verify_catalog_publication.py"
    ).read_text()
    assert '"backend/pyproject.toml"' in publication_gate
    assert "requirements-social-cards" not in publication_gate

    workflows = ROOT / ".github" / "workflows"
    full_suites: dict[str, str] = {}
    for workflow_path in workflows.glob("*.yml"):
        document = workflow_path.read_text()
        if "pytest backend/tests" in document:
            full_suites[workflow_path.name] = document
    assert set(full_suites) == {"publish.yml", "railway-release-gate.yml"}
    for name, document in full_suites.items():
        dependency = document.index("ops/requirements-social-cards.txt")
        collection = document.index("pytest backend/tests")
        assert dependency < collection, name
        assert "--only-binary=:all:" in document[:collection]
        assert "--require-hashes" in document[:collection]

    pr_lane = (workflows / "market-platform-ci.yml").read_text()
    dependency = pr_lane.index("ops/requirements-social-cards.txt")
    collection = pr_lane.index("backend/tests/test_social_cards.py")
    assert dependency < collection
    assert "--only-binary=:all:" in pr_lane[:collection]
    assert "--require-hashes" in pr_lane[:collection]

    gate_dockerfile = (ROOT / "ops" / "railway" / "Dockerfile.gate").read_text()
    assert gate_dockerfile.index(
        "ops/requirements-social-cards.txt"
    ) < gate_dockerfile.index("ENTRYPOINT")
