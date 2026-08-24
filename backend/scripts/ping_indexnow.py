"""Ping IndexNow after a publish so Bing (which feeds ChatGPT search) and
other IndexNow engines learn about new or refreshed URLs within minutes
instead of waiting for a crawl.

The key is public by design; the matching key file is served at
https://seiche.info/{KEY}.txt so the endpoint can verify we own the host.
Submits the canonical static discovery surfaces, article and dispatch archives,
and every published article and letter. The builder validates slugs, stays on
the seiche.info host, and deduplicates locally so the submitted receipt is
deterministic and reviewable.

Run from the repo root after the site push:
  PYTHONPATH=backend python backend/scripts/ping_indexnow.py
Stdlib only. Exits non-zero on a refused submission (the publish step wraps
this in continue-on-error, so a flaky ping never blocks the site).
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

SITE_HOST = "seiche.info"
KEY = "e4230e3f1ce1f04b8cbb6a9f377aafad"
ENDPOINT = "https://api.indexnow.org/indexnow"

REPO_ROOT = Path(__file__).resolve().parents[2]

# These are stable, meaningful retrieval targets rather than every static
# asset. API URLs live on api.seiche.info and require a different IndexNow host
# proof, so they deliberately do not belong in this seiche.info submission.
STATIC_PATHS = (
    "/",
    "/markets/",
    "/markets/forex/",
    "/markets/capital-markets/",
    "/markets/china-macro/",
    "/money-markets/",
    "/money-markets/catalog.json",
    "/developers",
    "/use-cases",
    "/use-cases/money-market-research/",
    "/use-cases/capital-market-transmission/",
    "/use-cases/china-economy-evidence/",
    "/guide",
    "/methodology",
    "/skeptic",
    "/ampleness",
    "/referee",
    "/investigations/",
    "/investigations/the-282-billion-settlement-test/",
    "/articles/",
    "/articles/feed.json",
    "/articles/feed.xml",
    "/dispatches/",
    "/dispatches/feed.xml",
    "/llms.txt",
    "/product-card.json",
    "/.well-known/ai-catalog.json",
)


def _slugs(index: Path, *, required: bool) -> list[str]:
    if not index.exists():
        if required:
            raise SystemExit(f"no public index at {index}")
        return []
    rows = json.loads(index.read_text())
    if not isinstance(rows, list):
        raise SystemExit(f"public index must be an array at {index}")
    slugs: list[str] = []
    for row in rows:
        slug = str(row.get("slug") if isinstance(row, dict) else "")
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise SystemExit(f"unsafe public slug in {index}: {slug!r}")
        slugs.append(slug)
    return sorted(set(slugs))


def build_urls(repo_root: Path | None = None) -> list[str]:
    """Return one deterministic, deduplicated seiche.info URL list."""
    root = repo_root or REPO_ROOT
    public = root / "frontend" / "public"
    dispatches = _slugs(public / "dispatches" / "index.json", required=True)
    articles = _slugs(public / "articles" / "index.json", required=False)
    paths = [
        *STATIC_PATHS,
        *(f"/dispatches/{slug}" for slug in dispatches),
        *(f"/articles/{slug}/" for slug in articles),
    ]
    unique_paths = list(dict.fromkeys(paths))
    if any(not path.startswith("/") or "://" in path for path in unique_paths):
        raise SystemExit("IndexNow path escaped the seiche.info origin")
    return [f"https://{SITE_HOST}{path}" for path in unique_paths]


def main() -> int:
    urls = build_urls()

    body = json.dumps({
        "host": SITE_HOST,
        "key": KEY,
        "keyLocation": f"https://{SITE_HOST}/{KEY}.txt",
        "urlList": urls,
    }, sort_keys=True, separators=(",", ":")).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        status = r.status
    if status not in (200, 202):
        print(f"indexnow refused: HTTP {status}", file=sys.stderr)
        return 1
    print(f"indexnow accepted {len(urls)} urls (HTTP {status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
