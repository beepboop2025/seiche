"""CFTC commodity contract selection, normalization, and stale fallback."""

from __future__ import annotations

import asyncio

import pandas as pd

from seiche.sources import cftc


def _row(code: str, date: str, open_interest: str) -> dict:
    return {
        "report_date_as_yyyy_mm_dd": date,
        "market_and_exchange_names": "TEST EXCHANGE",
        "contract_market_name": "TEST CONTRACT",
        "cftc_contract_market_code": code,
        "commodity_name": "TEST",
        "open_interest_all": open_interest,
        "prod_merc_positions_long": "100",
        "prod_merc_positions_short": "110",
        "swap_positions_long_all": "120",
        "swap__positions_short_all": "130",
        "m_money_positions_long_all": "140",
        "m_money_positions_short_all": "150",
        "other_rept_positions_long": "160",
        "other_rept_positions_short": "170",
        "traders_tot_all": "42",
        "conc_gross_le_4_tdr_long": "10.5",
        "conc_gross_le_4_tdr_short": "11.5",
        "conc_gross_le_8_tdr_long": "20.5",
        "conc_gross_le_8_tdr_short": "21.5",
    }


class _Response:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return self.rows


class _Client:
    def __init__(self, rows: list[dict], *, error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return _Response(self.rows)


def test_fetch_uses_stable_contract_codes_and_normalizes_numbers(monkeypatch) -> None:
    rows = [
        _row("067651", "2026-07-07T00:00:00.000", "1000"),
        _row("023651", "2026-07-07T00:00:00.000", "800"),
        _row("999999", "2026-07-07T00:00:00.000", "900"),
    ]
    saved: dict[str, dict] = {}
    monkeypatch.setattr(cftc.store, "load_blob", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cftc.store, "save_blob", lambda key, value: saved.__setitem__(key, value)
    )
    client = _Client(rows)

    out = asyncio.run(cftc.fetch_disaggregated_commodities(client))

    frame = out["positions"]
    assert set(frame["contract"]) == {"WTI", "HENRY_HUB"}
    assert pd.api.types.is_numeric_dtype(frame["open_interest_all"])
    assert frame.loc[frame["contract"] == "WTI", "open_interest_all"].iloc[0] == 1000
    assert frame.loc[frame["contract"] == "WTI", "available_date"].iloc[0] == (
        pd.Timestamp("2026-07-10")
    )
    assert "cftc_disagg_ballast" in saved
    url, kwargs = client.calls[0]
    assert url.endswith("72hh-3qpy.json")
    where = kwargs["params"]["$where"]
    assert "067651" in where and "023651" in where
    assert "999999" not in where


def test_fetch_serves_unexpired_shape_from_stale_blob_on_failure(monkeypatch) -> None:
    cached = {
        "fetched_at": "2026-07-10T00:00:00+00:00",
        "rows": [_row("067651", "2026-07-07T00:00:00.000", "1000")],
    }

    def load_blob(_key: str, ttl_minutes: int | None = None):
        return None if ttl_minutes is not None else cached

    monkeypatch.setattr(cftc.store, "load_blob", load_blob)
    client = _Client([], error=RuntimeError("CFTC unavailable"))

    out = asyncio.run(cftc.fetch_disaggregated_commodities(client))

    assert out["fetched_at"] == cached["fetched_at"]
    assert out["positions"].iloc[0]["contract"] == "WTI"
