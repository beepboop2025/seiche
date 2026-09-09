"""The workbench serves only the signed, accepted China historical projection."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from seiche import market_workbench as wb
from seiche.palimpsest_china_intake import PalimpsestChinaIntakeError

import test_palimpsest_china_intake as china_fixtures

# Expose the existing pytest fixtures without invoking their wrapped functions.
fixed_acceptance_clock = china_fixtures.fixed_acceptance_clock
signer = china_fixtures.signer


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
MONEY = "cn.wdi.broad_money_growth"
CEREAL = "cn.wdi.cereal_production"


def _history_bundle(*, withdraw_cereal: bool = False):
    manifest, _, artifact, _, _ = china_fixtures._bundle()
    wrappers = [json.loads(line) for line in artifact.splitlines()]
    cereal = wrappers[0]["observation"]
    original_money = wrappers[1]["observation"]
    history = [
        china_fixtures._observation(
            series_id=MONEY,
            source_series_id="FM.LBL.BMNY.ZG",
            value=value,
            unit="annual percent",
            year=year,
        )
        for year, value in ((2022, 5.5), (2023, 7.2))
    ]
    revised_money = china_fixtures._observation(
        series_id=MONEY,
        source_series_id="FM.LBL.BMNY.ZG",
        value=8.5,
        unit="annual percent",
        year=2024,
        revision=1,
        released_at="2026-07-14T23:59:59+00:00",
        collected_at="2026-08-24T12:00:10+00:00",
        source_document_version="2026-07-14",
    )
    cereal_old = china_fixtures._observation(
        series_id=CEREAL,
        source_series_id="AG.PRD.CREL.MT",
        value=640_000_000,
        unit="metric tons",
        year=2023,
    )
    ledger = [cereal_old, cereal, *history, original_money, revised_money]
    rows = (
        [*history, revised_money]
        if withdraw_cereal
        else [cereal_old, cereal, *history, revised_money]
    )
    handoff = china_fixtures._rebuild_v3(
        manifest,
        wrappers=[
            china_fixtures._wrapper(
                row,
                *("capital_market", "money_market")
                if row["series_id"] == MONEY
                else ("capital_market",),
            )
            for row in rows
        ],
        ledger_rows=ledger,
        availability_entries=[
            {
                "available": not (
                    withdraw_cereal and indicator == "AG.PRD.CREL.MT" and year == 2024
                ),
                "footnote": None,
                "indicator_id": indicator,
                "year": year,
            }
            for indicator, year in (
                ("AG.PRD.CREL.MT", 2023),
                ("AG.PRD.CREL.MT", 2024),
                ("FM.LBL.BMNY.ZG", 2022),
                ("FM.LBL.BMNY.ZG", 2023),
                ("FM.LBL.BMNY.ZG", 2024),
            )
        ],
    )
    return (json.loads(handoff[0]), *handoff)


@pytest.fixture
def accepted_history(tmp_path, signer, monkeypatch):
    bundle = _history_bundle()
    # Reuse the complete signed fixture writer, including handoff checksums,
    # producer evidence and lineage. No acceptance authority is synthesized.
    monkeypatch.setattr(china_fixtures, "_bundle", lambda: bundle)
    paths = china_fixtures._write_signed_bundle(tmp_path, signer)
    context = china_fixtures._load_written_bundle(paths, attest_dir=signer[2], now=NOW)
    assert context.owner_attested
    return context, paths


def test_accepted_china_numeric_context_remains_structural_and_unscored(
    accepted_history,
):
    context, _ = accepted_history
    result = wb.project(
        {}, {"china_series": MONEY}, china_context=context, evaluated_at=NOW
    )
    china = result["china"]

    assert result["status"] == "partial"
    assert china["status"] == "structural"
    assert china["reason"] is None
    assert {row["series_id"] for row in china["series"]} == {MONEY, CEREAL}
    money = next(row for row in china["series"] if row["series_id"] == MONEY)
    assert money["value"] == 8.5
    assert money["revision"] == 1
    assert money["period_end"] == "2024-12-31"
    assert money["unit"] == "annual percent"
    economic = china["economic_context"]
    assert economic["rights"]["decision"] == "allowed"
    assert (
        economic["rights"]["attribution"] == "World Bank, World Development Indicators"
    )
    assert economic["publication_status"] == "provisional"
    assert economic["provenance"]["owner_attestation"] == "ed25519"
    assert economic["scoring_eligible"] is False
    assert economic["cn_cny_gauge_eligible"] is False
    assert economic["market_observation_eligible"] is False
    assert result["eligibility"] == {
        "scoring": False,
        "forecast": False,
        "execution": False,
    }
    assert china["fx"]["status"] == "unavailable"
    assert china["fx"]["value"] is None
    funding = next(
        channel for channel in china["channels"] if channel["id"] == "funding"
    )
    assert funding["series_ids"] == [MONEY]
    assert funding["available_series"] == 1
    assert {gap["id"]: gap["status"] for gap in china["gaps"]} == {
        "cfets": "restricted",
        "cnh": "unavailable",
        "forwards": "unavailable",
        "fixing": "unavailable",
    }
    json.dumps(result, allow_nan=False)


def test_accepted_history_keeps_exact_latest_vintage_rows_and_all_four_clocks(
    accepted_history,
):
    context, _ = accepted_history
    china = wb.project(
        {}, {"china_series": MONEY}, china_context=context, evaluated_at=NOW
    )["china"]
    expected = sorted(
        [
            row.public_record(accepted_at=context.accepted_at)
            for row in context.observations
            if row.series_id == MONEY
        ],
        key=lambda row: row["period_end"],
    )
    assert china["history"] == expected
    assert [row["value"] for row in china["history"]] == [5.5, 7.2, 8.5]
    assert [row["period_end"] for row in china["history"]] == [
        "2022-12-31",
        "2023-12-31",
        "2024-12-31",
    ]
    assert china["selected_series"] == MONEY
    assert china["history_vintage"] == "latest_revision_in_accepted_export"
    assert china["history_total"] == 3
    assert china["history_truncated"] is False
    latest = china["history"][-1]
    assert latest["period_start"] == "2024-01-01"
    assert latest["released_at"] == "2026-07-14T23:59:59+00:00"
    assert latest["collected_at"] == "2026-08-24T12:00:10+00:00"
    assert latest["accepted_at"] == "2026-08-24T12:02:00Z"
    assert latest["source_id"] == "world_bank_wdi"
    assert latest["metadata"]["source_series_id"] == "FM.LBL.BMNY.ZG"
    assert (
        latest["metadata"]["release_time_semantics"]
        == "dataset_lastupdated_upper_bound"
    )
    assert latest["frequency"] == "A"
    assert latest["freshness"]["is_live_market_data"] is False
    # The revision-zero 8.1 value remains in the signed input ledger, but the
    # accepted projection exposes only its current replacement, 8.5.
    assert not any(row["value"] == 8.1 for row in china["history"])


def test_unsigned_verified_export_and_serialized_mapping_publish_no_values(
    accepted_history,
):
    context, _ = accepted_history
    _, manifest, artifact, ledger, availability = china_fixtures._bundle()
    unsigned = china_fixtures._verify(manifest, artifact, ledger, availability)
    assert not unsigned.owner_attested
    for untrusted in (unsigned, context.to_dict(), None):
        china = wb.project({}, china_context=untrusted, evaluated_at=NOW)["china"]
        assert china["status"] == "unavailable"
        assert china["series"] == []
        assert china["history"] == []
        assert china["economic_context"] == {}


def test_accepted_export_rejects_requested_series_not_in_current_projection(
    accepted_history,
):
    context, _ = accepted_history
    with pytest.raises(ValueError, match="not present in the accepted export"):
        wb.project(
            {},
            {"china_series": "cn.wdi.nonexistent"},
            china_context=context,
            evaluated_at=NOW,
        )


def test_withdrawn_series_cannot_return_prior_numeric_history(
    tmp_path, signer, monkeypatch
):
    bundle = _history_bundle(withdraw_cereal=True)
    monkeypatch.setattr(china_fixtures, "_bundle", lambda: bundle)
    context = china_fixtures._load_written_bundle(
        china_fixtures._write_signed_bundle(tmp_path, signer),
        attest_dir=signer[2],
        now=NOW,
    )
    assert context.owner_attested
    china = wb.project({}, china_context=context, evaluated_at=NOW)["china"]
    assert {row["series_id"] for row in china["series"]} == {MONEY}
    assert {row["series_id"] for row in china["history"]} == {MONEY}
    assert china["selected_series"] == MONEY
    assert not any(row.series_id == CEREAL for row in context.observations)
    with pytest.raises(ValueError, match="not present in the accepted export"):
        wb.project(
            {}, {"china_series": CEREAL}, china_context=context, evaluated_at=NOW
        )


def test_serve_time_rights_expiry_cannot_become_an_empty_successful_workbench(
    accepted_history, signer, monkeypatch
):
    _, paths = accepted_history
    monkeypatch.setattr(wb.store, "load_series_window", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        wb.context_views,
        "public_china_economic_context",
        lambda: china_fixtures._load_written_bundle(
            paths,
            attest_dir=signer[2],
            now=datetime(2027, 8, 24, tzinfo=UTC),
        ),
    )
    with pytest.raises(PalimpsestChinaIntakeError, match="expired at serve time"):
        wb.read({"china_series": MONEY})
