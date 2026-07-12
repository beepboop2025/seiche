"""Ping IndexNow after a publish so Bing (which feeds ChatGPT search) and
other IndexNow engines learn about new or refreshed URLs within minutes
instead of waiting for a crawl.

The key is public by design; the matching key file is served at
https://seiche.info/{KEY}.txt so the endpoint can verify we own the host.
Submits the homepage, the dispatch archive and every letter page — the list
is small and IndexNow deduplicates on its side.

Run from the repo root after the site push:
  PYTHONPATH=backend python backend/scripts/ping_indexnow.py
Stdlib only. Exits non-zero on a refused submission (the publish step wraps
this in continue-on-error, so a flaky ping never blocks the site).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

SITE_HOST = "seiche.info"
KEY = "e4230e3f1ce1f04b8cbb6a9f377aafad"
ENDPOINT = "https://api.indexnow.org/indexnow"

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "frontend" / "public" / "dispatches" / "index.json"


def main() -> int:
    entries = json.loads(INDEX.read_text())
    urls = [
        f"https://{SITE_HOST}/",
        f"https://{SITE_HOST}/dispatches/",
        f"https://{SITE_HOST}/llms.txt",
    ] + [f"https://{SITE_HOST}/dispatches/{e['slug']}.html" for e in entries]

    body = json.dumps({
        "host": SITE_HOST,
        "key": KEY,
        "keyLocation": f"https://{SITE_HOST}/{KEY}.txt",
        "urlList": urls,
    }).encode()
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
