"""Eurosystem total assets from the ECB Data API: pinned, dated by Friday, context only.

The fixture is the ECB's own response of 2026-11-02 (see fixtures/ecb/README.md).
"""
from __future__ import annotations

import json
from dataclasses import fields
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from seiche.domain.observation import CanonicalUnit, RedistributionStatus, SemanticRole
from seiche.markets.calibration import get_local_calibration
from seiche.markets.registry import default_registry
from seiche.sources.canonical import FetchedDocument
from seiche.sources.official import (
    ECB_EUROSYSTEM_TOTAL_ASSETS_ID,
    parse_ecb_eurosystem_total_assets,
    parse_ecb_liquidity,
)

ROOT = Path(__file__).resolve().parents[2]
CSV = (Path(__file__).parent / "fixtures" / "ecb" / "ILM-W-total-assets-2026-09.csv").read_bytes().decode()
URI = "https://data-api.ecb.europa.eu/service/data/ILM/W.U2.C.T000000.Z5.Z01"


def document(text: str = CSV, label: str = ECB_EUROSYSTEM_TOTAL_ASSETS_ID) -> FetchedDocument:
    return FetchedDocument(URI, "text/csv", text.encode(), label)


def test_weekly_total_assets_are_dated_by_the_statement_friday():
    points = parse_ecb_eurosystem_total_assets(document())
    assert [(p.event_time, p.raw_value) for p in points] == [
        (date(2026, 9, 11), Decimal("5911237")), (date(2026, 9, 18), Decimal("5898477")),
        (date(2026, 9, 25), Decimal("5897262")), (date(2026, 10, 2), Decimal("5947826")),
        (date(2026, 10, 9), Decimal("5943911")), (date(2026, 10, 16), Decimal("5935346")),
        (date(2026, 10, 23), Decimal("5934983")),
    ]
    assert all(p.event_time.weekday() == 4 for p in points)
    assert {p.instrument_id for p in points} == {ECB_EUROSYSTEM_TOTAL_ASSETS_ID}
    # The published W40 row has no unit multiplier; its evidence matches its neighbours'.
    w40 = json.loads(next(p for p in points if p.event_time == date(2026, 10, 2)).row_evidence)
    assert w40["unit_mult"] == "6" and w40["time_period"] == "2026-W40"
    assert points[-1].revision_id.startswith("ecb:ILM-W-total-assets:2026-W43:")


def test_the_liquidity_adapter_routes_each_document_to_its_parser():
    assert parse_ecb_liquidity(document()) == parse_ecb_eurosystem_total_assets(document())
    daily = ("KEY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
             "ILM.D.U2.C.EXLIQ.U2.EUR,D,2026-10-30,2700000,A\n")
    points = parse_ecb_liquidity(document(daily, label="EA.ECB.EXCESS_LIQUIDITY"))
    assert [(p.instrument_id, p.event_time) for p in points] == [("EA.ECB.EXCESS_LIQUIDITY", date(2026, 10, 30))]


@pytest.mark.parametrize("old, new", [
    ("ILM.W.U2.C.T000000.Z5.Z01,W,", "ILM.W.U2.C.T000000.Z5.Z02,W,"),
    ("ILM.W.U2.C.T000000.Z5.Z01,W,U2,C,T000000,Z5,Z01,2026-W43", "ILM.W.U2.C.T000000.Z5.Z01,D,U2,C,T000000,Z5,Z01,2026-W43"),
    ("counterpart\",EUR,6\r\n", "counterpart\",USD,6\r\n"),
    ("counterpart\",EUR,6\r\n", "counterpart\",EUR,3\r\n"),
    ("2026-W43", "2026-10-23"),
    ("2026-W43", "2026-W42"),
    ("2026-W43", "2026-W54"),
    (",2026-W43,5934983,", ",2026-W43,5934983.5.1,"),
    (",2026-W43,5934983,", ",2026-W43,-5934983,"),
    (",2026-W43,5934983,", ",2026-W43,123456789012,"),
    ("KEY,FREQ,", "SERIES,FREQ,"),
], ids=["series key", "frequency", "currency", "multiplier", "not a week", "repeated week", "no such week",
        "malformed value", "negative", "beyond bound", "header"])
def test_a_changed_response_fails_closed(old, new):
    assert old in CSV
    with pytest.raises(ValueError):
        parse_ecb_eurosystem_total_assets(document(CSV.replace(old, new, 1) if old != "counterpart\",EUR,6\r\n"
                                                   else CSV.replace(old, new)))


def test_blank_multipliers_alone_do_not_pin_the_unit():
    with pytest.raises(ValueError, match="multiplier"):
        parse_ecb_eurosystem_total_assets(document(CSV.replace("counterpart\",EUR,6\r\n", "counterpart\",EUR,\r\n")))


def test_the_ea_pack_declares_it_as_context_no_component_reads():
    pack = default_registry().get("EA-EUR")
    spec = pack.instrument_map[ECB_EUROSYSTEM_TOTAL_ASSETS_ID]
    assert (spec.source_adapter_id, spec.semantic_role, spec.canonical_unit) == (
        "ecb_liquidity", SemanticRole.CENTRAL_BANK_TOTAL_ASSETS, CanonicalUnit.LOCAL_CURRENCY_MILLIONS)
    assert spec.normalize(Decimal("5934983")) == Decimal("5934983")
    assert pack.adapter_map["ecb_liquidity"].redistribution_status is RedistributionStatus.ALLOWED
    calibration = get_local_calibration("EA-EUR")
    component_roles = {getattr(component, item.name) for component in calibration.components
                       for item in fields(component) if item.name.endswith("_role")}
    assert SemanticRole.CENTRAL_BANK_TOTAL_ASSETS not in component_roles
    for capability in pack.capabilities:
        assert SemanticRole.CENTRAL_BANK_TOTAL_ASSETS not in capability.required_roles
    # The deposit facility rate was already direct before this change.
    assert pack.instrument_map["EA.ECB.DFR"].source_adapter_id == "ecb_policy"


def test_the_registry_record_names_the_series_and_quotes_the_ecb():
    ledger = json.loads((ROOT / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == "ecb_data_api")
    assert "CENTRAL_BANK_TOTAL_ASSETS" in record["semantic_roles"]
    for text in ("ILM/W.U2.C.T000000.Z5.Z01", ECB_EUROSYSTEM_TOTAL_ASSETS_ID, "ECBASSETSW",
                 "FM/D.U2.EUR.4F.KR.DFR.LEV", "the ECB must be cited as the source"):
        assert text in record["blocker"]
