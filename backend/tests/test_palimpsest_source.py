from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest

from seiche import store
from seiche.config import ALL_SERIES
from seiche.sources import palimpsest
from seiche.sources.base import Series


def _cached_cfets_series(mnemonic: str, value: float) -> Series:
    spec = ALL_SERIES[mnemonic]
    return Series(
        mnemonic,
        spec.source,
        spec.remote_id,
        spec.label,
        spec.unit,
        spec.freq,
        "2026-08-20T12:00:00+00:00",
        pd.Series(
            [value],
            index=pd.DatetimeIndex(["2026-08-20"]),
            dtype=float,
        ),
    )


@pytest.mark.asyncio
async def test_palimpsest_collects_only_native_series_and_preserves_cached_cfets(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "palimpsest.sqlite")
    cached = {
        "CN_FDR007": _cached_cfets_series("CN_FDR007", 1.52),
        "CN_PARITY": _cached_cfets_series("CN_PARITY", 7.18),
    }
    for series in cached.values():
        store.save_series(series)

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        filename = request.url.path.rsplit("/", 1)[-1]
        requested.append(filename)
        if filename == "ddti-history.jsonl":
            payload = {
                "generated_at": "2026-08-21T09:00:00+00:00",
                "top_threat": 0.72,
                "n_new": 4,
            }
            return httpx.Response(
                200,
                request=request,
                text=json.dumps(payload) + "\n",
            )
        if filename == "history.jsonl":
            return httpx.Response(
                200,
                request=request,
                text='{"date":"2026-08-21","gfi":41.5}\n',
            )
        if filename == "ddti-latest.json":
            return httpx.Response(
                200,
                request=request,
                json={
                    "generated_at": "2026-08-21T09:00:00+00:00",
                    "n_terms": 12,
                    "ranked": [],
                },
            )
        raise AssertionError(f"unexpected Palimpsest request: {request.url}")

    faults: list[dict] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await palimpsest.fetch_all(client, faults)

    assert requested == [
        "ddti-history.jsonl",
        "history.jsonl",
        "ddti-latest.json",
    ]
    assert "china-econ-history.jsonl" not in requested
    assert set(result["series"]) == {
        "PALIMPSEST_FEAR",
        "PALIMPSEST_NEW",
        "PALIMPSEST_GFI",
    }
    assert faults == []
    for mnemonic, prior in cached.items():
        retained = store.load_series(mnemonic)
        assert retained is not None
        assert retained.fetched_at == prior.fetched_at
        pd.testing.assert_series_equal(retained.points, prior.points)
