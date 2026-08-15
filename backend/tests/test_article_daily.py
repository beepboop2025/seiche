"""Daily articles are arguments, not renamed dispatches.

These tests pin the editorial contract at the points most likely to regress:
quiet-day historical fallback, full-story selection, model grounding, durable
archives, and crawlable publication artifacts.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json

from seiche import article_daily
from seiche.article_daily import build_article, write_article
from seiche.dispatch_daily import build_dispatch, write_dispatch
from seiche.dispatch_pages import build_all


def _story(snap: dict) -> dict:
    return build_dispatch(snap, prev_value=38.0)["story"]


def _current_snap(fake_snap: dict) -> dict:
    snap = deepcopy(fake_snap)
    snap["editorial"] = {
        "thesis": "A dated reserve drain is testing the system before market prices agree.",
        "standfirst": "The structural signal moved while the overnight tape remained calm.",
        "confidence": "guarded",
        "confidence_note": "Coverage is broad, but confirmation is incomplete.",
        "dominant_driver": {
            "engine": "weather", "label": "settlement calendar",
            "score": 81.0, "contribution": 11.2,
        },
        "evidence": [
            {
                "label": "Reserve path",
                "claim": "The projected path falls into the pressure window.",
                "asof": "2026-07-10",
                "source": "Seiche Weather",
            },
            {
                "label": "Overnight tape",
                "claim": "SOFR remains 2.0bp from IORB.",
                "asof": "2026-07-09",
                "source": "New York Fed",
            },
        ],
        "countercase": [{
            "source": "The Tell", "asof": "2026-07-10",
            "claim": "Market pricing has not confirmed the structural signal.",
        }],
        "watch": [{
            "date": "2026-07-31", "label": "month-end settlement",
            "settlement_b": 282.0, "worst_case_reserves_b": 2800.0,
        }],
    }
    return snap


def test_quiet_tape_publishes_historical_replay(fake_snap):
    story = _story(fake_snap)
    assert story["newsworthiness"]["decision"] != "full_story"
    article = build_article(fake_snap, story, date="2026-07-10", model_config=None)
    assert article["article_type"] == "historical_replay"
    assert article["quality_gate"]["status"] == "PASS"
    assert article["word_count"] >= 800
    assert "not a forecast" in article["body_md"].lower()
    assert article["generation"]["mode"] == "deterministic_fallback"


def test_material_change_publishes_current_argument(fake_snap):
    snap = _current_snap(fake_snap)
    story = _story(snap)
    story["newsworthiness"]["decision"] = "full_story"
    article = build_article(snap, story, date="2026-07-10", model_config=None)
    assert article["article_type"] == "current_analysis"
    assert article["headline"].startswith("A dated reserve drain")
    assert "## The strongest counter-case" in article["body_md"]
    assert "https://liquilens.in/" in article["body_md"]
    assert "https://liquilens-undertow.com/exit/" in article["body_md"]


def test_unsupported_model_number_forces_safe_fallback(fake_snap, monkeypatch):
    story = _story(fake_snap)
    fallback = build_article(fake_snap, story, date="2026-07-10", model_config=None)

    def bad_copy(_dossier, _config):
        return {
            "headline": fallback["headline"],
            "dek": fallback["dek"],
            "body_md": fallback["body_md"] + "\nThe unsupported balance was $987654321B.",
            "review_notes": [],
        }

    monkeypatch.setattr(article_daily, "_draft_with_model", bad_copy)
    article = build_article(
        fake_snap, story, date="2026-07-10",
        model_config={"key": "test", "base_url": "https://invalid", "model": "test-model"},
    )
    assert article["generation"]["mode"] == "deterministic_fallback"
    assert "unsupported numbers" in article["generation"]["fallback_reason"]
    assert "987654321" not in article["body_md"]


def test_two_pass_copy_can_clear_grounding_gate(fake_snap, monkeypatch):
    story = _story(fake_snap)
    fallback = build_article(fake_snap, story, date="2026-07-10", model_config=None)

    def grounded_copy(_dossier, _config):
        return {
            "headline": fallback["headline"],
            "dek": fallback["dek"],
            "body_md": fallback["body_md"],
            "review_notes": ["Tightened the counter-case."],
        }

    monkeypatch.setattr(article_daily, "_draft_with_model", grounded_copy)
    article = build_article(
        fake_snap, story, date="2026-07-10",
        model_config={"key": "test", "base_url": "https://invalid", "model": "test-model"},
    )
    assert article["generation"]["mode"] == "model_assisted"
    assert article["generation"]["passes"] == 2
    assert article["generation"]["model"] == "test-model"


def test_article_archive_feed_sitemap_and_llms_are_built(fake_snap, tmp_path):
    dispatch = build_dispatch(fake_snap, prev_value=38.0)
    write_dispatch(dispatch, repo_root=tmp_path)
    article = build_article(
        fake_snap, dispatch["story"], date="2026-07-10", model_config=None,
    )
    write_article(article, repo_root=tmp_path)

    build_all(repo_root=tmp_path)
    article_dir = tmp_path / "frontend" / "public" / "articles"
    page = (article_dir / article["slug"] / "index.html").read_text()
    archive = (article_dir / "index.html").read_text()
    assert article["headline"] in page and article["headline"] in archive
    assert f'https://seiche.info/articles/{article["slug"]}/' in page
    assert (article_dir / "feed.xml").exists()
    assert f"<loc>https://seiche.info/articles/{article['slug']}/</loc>" in (
        tmp_path / "frontend" / "public" / "sitemap.xml"
    ).read_text()
    assert f"https://seiche.info/articles/{article['slug']}.md" in (
        tmp_path / "frontend" / "public" / "llms.txt"
    ).read_text()
    sidecar = json.loads((article_dir / f"{article['slug']}.json").read_text())
    assert sidecar["quality_gate"]["status"] == "PASS"
    learning = json.loads((article_dir / "learning.json").read_text())
    assert learning["schema"] == "editorial.learning-feed.v1"
    assert learning["authority"]["training_allowed"] is False
    assert learning["articles"][0]["body_markdown"] == article["body_md"]


def test_same_day_rewrite_removes_obsolete_slug_artifacts(fake_snap, tmp_path):
    story = _story(fake_snap)
    first = build_article(fake_snap, story, date="2026-07-10", model_config=None)
    write_article(first, repo_root=tmp_path)
    article_dir = tmp_path / "frontend" / "public" / "articles"
    rendered = article_dir / first["slug"]
    rendered.mkdir()
    (rendered / "index.html").write_text("old")

    second = deepcopy(first)
    second["headline"] = "A stronger same-day funding argument"
    second["slug"] = "2026-07-10-a-stronger-same-day-funding-argument"
    second["id"] = f"seiche:article:{second['slug']}"
    second["canonical_url"] = f"https://seiche.info/articles/{second['slug']}/"
    write_article(second, repo_root=tmp_path)

    assert not (article_dir / f"{first['slug']}.md").exists()
    assert not (article_dir / f"{first['slug']}.json").exists()
    assert not rendered.exists()
    rows = json.loads((article_dir / "index.json").read_text())
    assert [row["slug"] for row in rows if row["date"] == second["date"]] == [second["slug"]]


def test_repeat_selection_ignores_the_edition_being_replaced(tmp_path):
    index = tmp_path / "index.json"
    index.write_text(json.dumps([
        {"date": "2026-07-10", "topic": "today-episode"},
        {"date": "2026-07-09", "topic": "prior-episode"},
    ]))
    assert article_daily._recent_topics(
        index_path=index, exclude_date="2026-07-10"
    ) == ["prior-episode"]


def test_tag_only_editorial_memory_is_bound_and_applied(fake_snap):
    identity = {
        "schema": "mqdnse.editorial-memory.v1",
        "generated_at": "2026-07-10T08:00:00Z",
        "source_run_id": "sha256:" + "a" * 64,
        "source_manifest_sha256": "sha256:" + "b" * 64,
        "rubric_version": "mqdnse.editorial-rubric.v1",
        "global_directives": ["show_mechanism"],
        "products": {
            "seiche": {
                "articleId": "seiche:article:prior",
                "articleRevisionSha256": "sha256:" + "c" * 64,
                "criticStatus": "validated_shadow_critique",
                "verdict": "publishable",
                "score": 12,
                "directives": ["strengthen_thesis"],
            }
        },
        "authority": article_daily.EDITORIAL_MEMORY_AUTHORITY,
    }
    payload = {
        **identity,
        "memory_fingerprint": article_daily._memory_sha(identity),
    }
    memory = article_daily.validate_editorial_memory(
        payload,
        now=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
    )
    story = _story(fake_snap)
    article = build_article(
        fake_snap,
        story,
        date="2026-07-10",
        model_config=None,
        editorial_memory=memory,
    )

    assert memory["directives"] == ["strengthen_thesis", "show_mechanism"]
    assert article["generation"]["editorial_memory"] == memory
    assert article["quality_gate"]["status"] == "PASS"
