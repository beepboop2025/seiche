"""The BoE registry record states the database SONIA licence; the pack waits for approval.

Reconciled 2026-11-02: the Bank licenses its Database, SONIA included, under the
UK Open Government Licence v3.0 with a fixed attribution. Serving raw SONIA is
the owner's correction to approve, so the UK pack still serves it derived-only.
"""
from __future__ import annotations

import json
from pathlib import Path

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.markets.registry import default_registry

ROOT = Path(__file__).resolve().parents[2]
SONIA_ATTRIBUTION = (
    "SONIA and/or SONIA Compounded Index data licensed under the Open Government Licence v3.0 and "
    "copyright the Governor and Company of the Bank of England."
)


def record() -> dict:
    ledger = json.loads((ROOT / "docs" / "data-source-expansion.json").read_text())
    return next(item for item in ledger["candidates"] if item["id"] == "boe_iadb")


def test_the_record_quotes_the_database_licence_and_its_sonia_attribution():
    item = record()
    assert item["rights_evidence_url"] == "https://www.bankofengland.co.uk/legal"
    assert "Reproduction of data in the Database is subject to the terms of the UK Open Government Licence" in item["blocker"]
    assert SONIA_ATTRIBUTION in item["blocker"]
    assert "by 10am on the business day after it is first published" in item["blocker"]
    assert "selected exchange rate data and series" in item["blocker"]
    assert "owner" in item["next_action"].lower()


def test_the_uk_pack_still_serves_sonia_derived_only_until_the_correction_is_approved():
    pack = default_registry().get("UK-GBP")
    adapter = pack.adapter_map["boe_sonia"]
    assert adapter.classification is ConnectorClassification.LICENSED
    assert adapter.redistribution_status is RedistributionStatus.DERIVED_ONLY
    assert pack.instrument_map["GB.BOE.SONIA"].source_adapter_id == "boe_sonia"
