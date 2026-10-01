"""Opening and closing DTS rows share a value field but are distinct series."""

import asyncio
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from seiche.sources import fiscaldata, official
from seiche.sources.canonical import FetchedDocument


def _row(account: str, value: str, day: str = "2026-09-28") -> dict:
    return {"account_type": account, "open_today_bal": value, "record_date": day}


def _document(rows: list[dict]) -> FetchedDocument:
    return FetchedDocument(
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance",
        "application/json",
        json.dumps({"data": rows}).encode(),
        "fiscal_tga",
    )


def test_canonical_tga_keeps_opening_basis_when_closing_row_is_present() -> None:
    points = official.parse_fiscal_tga(_document([
        _row("Treasury General Account (TGA) Closing Balance", "959572"),
        _row("Treasury General Account (TGA) Opening Balance", "945290"),
    ]))
    assert len(points) == 1
    assert points[0].event_time == date(2026, 9, 28)
    assert points[0].raw_value == Decimal("945.290")
    assert b"Opening Balance" in points[0].row_evidence


def test_canonical_tga_does_not_substitute_a_closing_only_row() -> None:
    with pytest.raises(ValueError, match="no TGA rows"):
        official.parse_fiscal_tga(_document([
            _row("Treasury General Account (TGA) Closing Balance", "959572"),
        ]))


def test_cached_daily_tga_excludes_closing_only_dates_and_preserves_legacy(monkeypatch) -> None:
    cached = {"fetched_at": "2026-10-01T00:00:00Z", "rows": [
        _row("Federal Reserve Account", "100000", "2020-01-02"),
        _row("Treasury General Account (TGA)", "200000", "2021-01-04"),
        _row("Treasury General Account (TGA) Closing Balance", "959572"),
        _row("Treasury General Account (TGA) Opening Balance", "945290"),
        _row("Treasury General Account (TGA) Closing Balance", "936600", "2026-09-29"),
    ]}

    async def load_cached(function, *args):
        assert function is fiscaldata.store.load_blob
        return cached

    monkeypatch.setattr(fiscaldata, "run_store", load_cached)

    async def fetch():
        async with httpx.AsyncClient() as client:
            return await fiscaldata.fetch_tga_daily(client)

    result = asyncio.run(fetch())
    assert result["tga"].tolist() == [100.0, 200.0, 945.29]
    assert result["tga"].index[-1].date() == date(2026, 9, 28)
