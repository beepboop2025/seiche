"""Keep the reviewed SDL pack and later, source-checked RBI releases in one read model."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from seiche.sources import rbi_sdl

PACK = Path(__file__).with_name("india_sdl_auctions.json")
MAX_DOCUMENT = 2 * 1024 * 1024


def supplement_path():
    from seiche.config import DATA_DIR
    return DATA_DIR / "rbi-sdl" / "publication.json"


def merge(base, releases):
    if not releases:
        return base
    selected = {k: v for k, v in releases.items() if
                k not in base["releases"] or datetime.fromisoformat(v["release"]["retrieved_utc"]) > datetime.fromisoformat(base["releases"][k]["retrieved_utc"])}
    if not selected:
        return base
    rows = [r for r in base["rows"] if str(r[1]) not in selected]
    for item in selected.values():
        rows.extend(item["rows"])
    rows.sort(key=lambda r: (r[0], r[2], r[4]))
    sources = {**base["releases"], **{k: v["release"] for k, v in selected.items()}}
    source = {**base["source"], "releases": len(sources), "last_auction": rows[-1][0], "first_auction": rows[0][0],
              "releases_with_total_matched": sum(r["total_check"] == "matched" for r in sources.values())}
    return {**base, "rows": rows, "releases": sources, "source": source,
            "generated_utc": max(base["generated_utc"], *(r["release"]["retrieved_utc"] for r in selected.values()))}


@lru_cache(maxsize=1)
def _read(pack, supplement, stamp):
    base = json.loads(pack.read_text())
    if stamp is None:
        return base
    published = json.loads(supplement.read_text())
    return merge(base, published["releases"])


def published(pack=PACK):
    path = supplement_path()
    try:
        stat = path.stat()
        stamp = (stat.st_ino, stat.st_mtime_ns)
    except FileNotFoundError:
        stamp = None
    return _read(pack, path, stamp)


def update(directory=None):
    """Called only by the source worker. Keep every completed release if an upstream read fails."""
    import fcntl
    import httpx

    dest = directory / "publication.json" if directory is not None else supplement_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        previous = json.loads(dest.read_text()) if dest.exists() else {"releases": {}, "revisions": []}
        now = datetime.now(timezone.utc)
        last = previous.get("checked_at")
        if last and 0 <= (now - datetime.fromisoformat(last)).total_seconds() < 6 * 3600:
            return
        base = json.loads(PACK.read_text())
        releases = dict(previous["releases"])
        revisions = list(previous["revisions"])
        with httpx.Client(timeout=10, headers={"User-Agent":"LiquiLens-Research/1.0 (+https://seiche.info)"}) as client:
            def fetch(url):
                with client.stream("GET", url) as res:
                    res.raise_for_status()
                    buf = bytearray()
                    for chunk in res.iter_bytes(65536):
                        if len(buf) + len(chunk) > MAX_DOCUMENT:
                            raise ValueError("RBI response exceeds the release limit")
                        buf.extend(chunk)
                    return bytes(buf)
            ids = rbi_sdl.discover(fetch(rbi_sdl.INDEX))
            if len(ids) > 20:
                raise ValueError("Unexpected RBI release index")
            for prid in ids:
                key = str(prid)
                item = rbi_sdl.packet(prid, fetch(f"{rbi_sdl.INDEX}?prid={prid}"), now.isoformat())
                prior = releases.get(key)
                if not prior and key in base["releases"]:
                    prior = {"release": base["releases"][key], "rows": [r for r in base["rows"] if r[1] == prid]}
                if prior and sorted(item["rows"], key=lambda r:r[4]) == sorted(prior["rows"], key=lambda r:r[4]):
                    continue
                if prior:
                    revisions.append({"prid": key, **prior})
                releases[key] = item
        body = {"checked_at": now.isoformat(), "releases": releases, "revisions": revisions}
        with NamedTemporaryFile(mode="w", dir=dest.parent, prefix=".rbi-", delete=False) as temp:
            try:
                json.dump(body,temp,separators=(",", ":"))
                temp.flush()
                os.fsync(temp.fileno())
                temp.close()
                os.replace(temp.name, dest)
            finally:
                Path(temp.name).unlink(missing_ok=True)
