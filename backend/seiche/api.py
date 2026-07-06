"""Seiche REST API. Run: uvicorn seiche.api:app --port 8787"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from seiche import assemble, store
from seiche.config import ALL_SERIES

app = FastAPI(title="Seiche", version="0.1.0",
              description="Funding-stress & leveraged-positioning early-warning terminal")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/overview")
async def overview(force: bool = False):
    return await assemble.snapshot(force=force)


@app.get("/api/engines/{name}")
async def engine(name: str):
    snap = await assemble.snapshot()
    if name not in snap["engines"]:
        raise HTTPException(404, f"unknown engine '{name}'")
    return snap["engines"][name]


@app.get("/api/series/{mnemonic}")
async def series(mnemonic: str, n: int = 750):
    if mnemonic not in ALL_SERIES:
        raise HTTPException(404, f"unknown series '{mnemonic}'")
    await assemble.snapshot()  # ensure fetched
    s = store.load_series(mnemonic)
    if s is None:
        raise HTTPException(503, f"series '{mnemonic}' not yet available")
    return {"provenance": s.provenance(), "points": s.tail_records(n)}


@app.get("/api/health")
async def health():
    snap = await assemble.snapshot()
    return {
        "generated_at": snap["generated_at"],
        "faults": snap["faults"],
        "provenance": snap["provenance"],
    }


# Serve the built frontend when present (single-process deploy).
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
