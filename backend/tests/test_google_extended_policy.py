"""Keep the owner's narrow Google reuse permission tied to reviewed text."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs/google-extended-reviewed-pages.json"


def test_google_extended_only_allows_reviewed_exact_urls():
    review = json.loads(REVIEW.read_text())
    robots = (ROOT / "frontend/public/robots.txt").read_text()
    groups = re.split(r"(?im)^User-agent:", robots)
    groups = [g for g in groups if g.splitlines()[0].strip() == "Google-Extended"]
    assert len(groups) == 1
    rules = [line.strip() for line in groups[0].splitlines()[1:]
             if line.startswith(("Allow:", "Disallow:"))]
    expected = {"Allow: " + p["path"] + "$" for p in review["pages"]}
    assert set(rules) == expected | {"Disallow: /"}
    assert len(rules) == len(expected) + 1
    for page in review["pages"]:
        assert re.fullmatch(r"/use-cases/[a-z-]+/", page["path"])
        assert page["source_file"] == "frontend/public" + page["path"] + "index.html"
    terms = (ROOT / "frontend/public/terms.html").read_text()
    assert all(f'href="{p["path"]}"' in terms for p in review["pages"])


def test_allowed_page_and_shared_script_changes_require_content_review():
    review = json.loads(REVIEW.read_text())
    for entry in review["pages"] + [review["shared_script"]]:
        source = ROOT / entry["source_file"]
        assert source.is_file() and not source.is_symlink()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"], (
            f"Review {entry['source_file']} for owned explanatory content, then "
            "update the review receipt or remove its Google-Extended allowance"
        )


def test_general_search_access_and_other_training_exclusions_stay_separate():
    robots = (ROOT / "frontend/public/robots.txt").read_text()
    for agent in ("Googlebot", "Bingbot", "OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"):
        assert f"User-agent: {agent}\nAllow: /\n" in robots
    for agent in ("GPTBot", "ClaudeBot", "Applebot-Extended", "CCBot"):
        assert f"User-agent: {agent}\nDisallow: /\n" in robots
