"""Complete source metadata must never imply observations or export rights."""

from dataclasses import replace

import pandas as pd
import pytest

from seiche import assemble, config, methodology
from seiche.sources.base import Series


def series(mnemonic, *, empty=False):
    spec = config.ALL_SERIES[mnemonic]
    return Series(
        mnemonic=mnemonic,
        source=spec.source,
        remote_id=spec.remote_id,
        label=spec.label,
        unit=spec.unit,
        freq=spec.freq,
        fetched_at="2026-10-03T08:29:37+00:00",
        points=pd.Series(
            [] if empty else [1.0],
            index=pd.to_datetime([] if empty else ["2026-10-02"]),
            dtype=float,
        ),
    )


@pytest.fixture(autouse=True)
def no_durable_source_reads(monkeypatch):
    monkeypatch.setattr(assemble.store, "load_series", lambda _: None)


def by_name(rows):
    return {row["mnemonic"]: row for row in rows}


def test_every_registry_entry_appears_once_even_without_data():
    rows = assemble._provenance({})
    registered = [row for row in rows if row["mnemonic"] in config.ALL_SERIES]
    assert len(registered) == len(config.ALL_SERIES)
    assert {row["mnemonic"] for row in registered} == set(config.ALL_SERIES)
    for row in registered:
        assert row["availability"] == "unavailable"
        assert row["staleness"] == "unknown"
        assert row["fetched_at"] is None
        assert row["asof"] is None
        assert row["n_obs"] == 0
        assert row["unavailable_reason"] == "not_in_completed_source_snapshot"
    assert all(row["availability"] == "unavailable" for row in rows)


@pytest.mark.parametrize(
    "group,mnemonic",
    [
        ("fred_cp_rates", "CP_FIN_3M"),
        ("fred_cp_rates", "CP_NONFIN_3M"),
        ("fred_cp_rates", "DGS3M"),
        ("fred_custody", "CUSTODY_TSY"),
        ("fred_custody", "FIMA_REPO"),
        ("ofr_gcf", "GCF_RATE_OO"),
        ("ofr_gcf", "GCF_VOL_OO"),
        ("ofr_pd_financing", "PD_FIN_TOT"),
        ("boj", "TONA"),
        ("bis", "GLI_OFFSHORE_USD"),
        ("bis", "GLI_OFFSHORE_LOANS"),
        ("bis", "GLI_OFFSHORE_DEBT"),
        ("bis", "GLI_EME_USD"),
        ("bis", "CREDIT_GAP_US"),
        ("bis", "CREDIT_GAP_CN"),
    ],
)
def test_previously_omitted_groups_keep_native_metadata(group, mnemonic):
    captured = series(mnemonic)
    actual = by_name(assemble._provenance({group: {mnemonic: captured}}))[mnemonic]
    assert actual["availability"] == "available"
    for name, value in captured.provenance().items():
        assert actual[name] == value
    assert "points" not in actual
    assert "value" not in actual


@pytest.mark.parametrize(
    "group,mnemonic",
    [("ecb_fx", "ECBFX_USD"), ("cbuae_fx", "CBUAEFX_USD")],
)
def test_fx_native_publication_policy_is_not_replaced(group, mnemonic):
    captured = series(mnemonic)
    actual = by_name(assemble._provenance({group: {mnemonic: captured}}))[mnemonic]
    original = captured.provenance()
    for name, value in original.items():
        assert actual[name] == value
    assert actual["history_export_allowed"] is True
    assert actual["freq"] == "D"
    assert actual["remote_id"] == config.ALL_SERIES[mnemonic].remote_id


def test_empty_capture_retains_fetch_clock_and_no_observation():
    captured = series("GDP", empty=True)
    row = by_name(assemble._provenance({"fred": {"GDP": captured}}))["GDP"]
    assert row["availability"] == "empty"
    assert row["fetched_at"] == captured.fetched_at
    assert row["asof"] is None
    assert row["n_obs"] == 0
    assert row["staleness"] == captured.provenance()["staleness"]


def test_gdp_quarter_start_is_not_relabelled_as_a_publication_date():
    captured = series("GDP")
    captured.points.index = pd.to_datetime(["2026-04-01"])
    row = by_name(assemble._provenance({"fred": {"GDP": captured}}))["GDP"]
    original = captured.provenance()
    for key in ("asof", "fetched_at", "age_days", "freshness_grace_days", "staleness"):
        assert row[key] == original[key]
    assert row["observation_date_semantics"] == "quarter_start"
    assert row["official_publication_at"] is None
    assert row["publication_freshness"] == "unknown"
    assert "quarter start" in row["freshness_basis"]


@pytest.mark.parametrize("field", ["mnemonic", "source", "remote_id", "unit", "freq"])
def test_foreign_source_identity_cannot_gain_freshness(field):
    captured = series("GDP")
    setattr(captured, field, "foreign")
    row = by_name(assemble._provenance({"fred": {"GDP": captured}}))["GDP"]
    assert row["unavailable_reason"] == "source_identity_mismatch"
    assert row["fetched_at"] is None
    assert row["asof"] is None
    assert row["staleness"] == "unknown"


def test_duplicate_and_unregistered_series_do_not_expand_or_choose_coverage():
    captured = series("GDP")
    rows = assemble._provenance(
        {"fred": {"GDP": captured, "UNREGISTERED": captured}, "ofr": {"GDP": captured}}
    )
    names = by_name(rows)
    assert "UNREGISTERED" not in names
    assert names["GDP"]["unavailable_reason"] == "ambiguous_source"
    assert names["GDP"]["asof"] is None


def test_new_registry_entry_is_visible_without_a_completed_capture(monkeypatch):
    added = replace(config.ALL_SERIES["GDP"], mnemonic="FUTURE_SERIES", remote_id="NEW")
    monkeypatch.setattr(
        assemble, "ALL_SERIES", {**config.ALL_SERIES, added.mnemonic: added}
    )
    row = by_name(assemble._provenance({}))[added.mnemonic]
    assert row["availability"] == "unavailable"
    assert row["staleness"] == "unknown"
    assert row["remote_id"] == "NEW"


def test_registry_metadata_never_opens_restricted_histories():
    rows = by_name(assemble._provenance({}))
    for mnemonic in config.ALL_SERIES:
        reason = methodology.csv_restriction(mnemonic)
        assert rows[mnemonic]["history_export_allowed"] is (reason is None)
        if reason:
            assert rows[mnemonic]["history_export_restriction"] == reason
    assert rows["ESTR"]["history_export_allowed"] is False
    assert rows["ECBFX_USD"]["history_export_allowed"] is True


@pytest.mark.parametrize(
    "group", ["nyfed_rde", "mspd", "llama_hacks", "windfetch", "fedtext", "gdelt"]
)
def test_additional_structured_envelopes_never_infer_observation_freshness(group):
    row = by_name(
        assemble._provenance({group: {"fetched_at": "2026-10-03T08:00:00Z"}})
    )[group]
    assert row["availability"] == "available"
    assert row["fetched_at"] == "2026-10-03T08:00:00Z"
    assert row["asof"] is None
    assert row["staleness"] == "unknown"


def test_existing_stablecoin_cache_keeps_its_actual_clock(monkeypatch):
    captured = series("STABLE_TOTAL")
    monkeypatch.setattr(assemble.store, "load_series", lambda mnemonic: captured)
    row = by_name(assemble._provenance({}))["STABLE_TOTAL"]
    assert row["fetched_at"] == captured.fetched_at
    assert row["n_obs"] == 1
    assert row["history_export_allowed"] is False
