"""A same-day source revision must not reuse models fitted to older values."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from seiche import assemble
from seiche.sources.base import Series


def inputs():
    index = pd.date_range("2026-06-01", periods=8)
    points = pd.Series(np.arange(8, dtype=float) + 20, index=index)
    gdp = Series(
        "GDP",
        "fred",
        "GDP",
        "GDP",
        "$B SAAR",
        "Q",
        "2026-10-03T12:00:00Z",
        points.copy(),
    )
    drv = {
        key: points.copy()
        for key in (
            "spread_bp",
            "tail_bp",
            "srf",
            "dw_b",
            "rrp",
            "res_gdp",
            "res_gdp_pctl",
            "iorb",
            "tga",
            "usdt_peg_bp",
            "btc",
        )
    }
    src = {
        "fred": {"GDP": gdp, "VIX": replace(gdp, mnemonic="VIX", remote_id="VIX")},
        "auctions": {
            "auctions": pd.DataFrame({"date": index, "amount": points.to_numpy()})
        },
        "upcoming": {
            "upcoming": pd.DataFrame({"date": index, "amount": points.to_numpy()})
        },
        "ofr": {"BGCR": replace(gdp, mnemonic="BGCR", source="ofr", remote_id="BGCR")},
        "crypto": {"candles": {}, "stable": {"total": points.copy()}},
    }
    engines = {
        "rvxray": {"_pair_full": points.copy()},
        "auctions": {"_index_full": points.copy()},
        "undertow": {"_damping_pctl": points.copy()},
        "composite": {"value": 30.0, "regime": "calm"},
    }
    ledger = [{"date": "2026-06-01", "book": {"positions": [["btc", 0.1]]}}]
    return src, drv, engines, ledger


def digest(values):
    value = assemble._deep_cache_inputs_sha256(*values)
    assert value is not None
    return value


class RecomputeReached(BaseException):
    pass


def cached_boundary(monkeypatch, values, *, age=10, all_ok=False, legacy=False):
    src, drv, engines, ledger = values
    record = {
        "ok": True,
        "history": {"ok": True, "current": {"value": 14.7}},
        "_all_ok": all_ok,
        "_computed_at": (datetime.now(UTC) - timedelta(minutes=age)).isoformat(),
    }
    if not legacy:
        record["_inputs_sha256"] = digest(values)
    load = Mock(return_value=deepcopy(ledger))
    monkeypatch.setattr(assemble.store, "load_pit_records", load)
    monkeypatch.setattr(assemble.store, "load_blob", lambda _key: deepcopy(record))
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda *_args: pytest.fail("unexpected cache write"),
    )
    monkeypatch.setattr(
        assemble.eng_history, "build", Mock(side_effect=RecomputeReached)
    )
    return record, load


def test_unchanged_input_hits_cache_but_revised_gdp_and_ratio_recompute(monkeypatch):
    values = inputs()
    record, load = cached_boundary(monkeypatch, values)
    src, drv, engines, _ledger = values
    first = assemble._deep_layer(src, drv, engines, [])
    assert first["history"]["current"] == record["history"]["current"]
    assert first["history"]["real_money_eligible"] is False
    src["fred"]["GDP"].points.iloc[-1] += 0.25
    drv["res_gdp"].iloc[-1] -= 0.001
    with pytest.raises(RecomputeReached):
        assemble._deep_layer(src, drv, engines, [])
    assert load.call_count == 2


def test_retrieval_clock_and_label_do_not_invalidate_identical_values(monkeypatch):
    values = inputs()
    before = digest(values)
    record, _load = cached_boundary(monkeypatch, values)
    src, drv, engines, _ledger = values
    src["fred"]["GDP"].fetched_at = "2026-10-03T15:00:00Z"
    src["fred"]["GDP"].label = "Nominal gross domestic product"
    assert digest(values) == before
    assert (
        assemble._deep_layer(src, drv, engines, [])["history"]["current"]
        == record["history"]["current"]
    )


@pytest.mark.parametrize(
    "category",
    [
        "ratio",
        "ratio_percentile",
        "source_identity",
        "unit",
        "freq",
        "market",
        "bgcr",
        "auction",
        "upcoming",
        "stablecoin",
        "pair",
        "damping",
        "composite",
        "ledger",
    ],
)
def test_other_consumed_dependencies_change_identity(category):
    values = inputs()
    before = digest(values)
    src, drv, engines, ledger = values
    if category == "ratio":
        drv["res_gdp"].iloc[-1] += 0.01
    elif category == "ratio_percentile":
        drv["res_gdp_pctl"].iloc[-1] += 0.01
    elif category in ("source_identity", "unit", "freq"):
        field = "remote_id" if category == "source_identity" else category
        setattr(src["fred"]["GDP"], field, "different")
    elif category == "market":
        src["fred"]["VIX"].points.iloc[0] += 0.01
    elif category == "bgcr":
        src["ofr"]["BGCR"].points.iloc[-1] += 0.01
    elif category in ("auction", "upcoming"):
        name = "auctions" if category == "auction" else "upcoming"
        src[name][name].iloc[-1, 1] += 1
    elif category == "stablecoin":
        src["crypto"]["stable"]["total"].iloc[-1] += 1
    elif category == "pair":
        engines["rvxray"]["_pair_full"].iloc[-1] += 1
    elif category == "damping":
        engines["undertow"]["_damping_pctl"].iloc[-1] += 1
    elif category == "composite":
        engines["composite"]["regime"] = "stress"
    else:
        ledger[0]["book"]["positions"][0][1] = 0.2
    assert digest(values) != before


def test_crypto_outcome_and_full_precision_are_bound():
    values = inputs()
    src, _drv, _engines, _ledger = values
    name = next(iter(assemble.PLAYBOOK_OUTCOMES))
    src["crypto"]["candles"][name] = deepcopy(src["fred"]["GDP"])
    before = digest(values)
    points = src["crypto"]["candles"][name].points
    points.iloc[-1] = np.nextafter(points.iloc[-1], np.inf)
    assert digest(values) != before


@pytest.mark.parametrize(
    ("age", "all_ok", "hit"),
    [(10, False, True), (31, False, False), (60, True, True), (721, True, False)],
)
def test_original_ttl_branches_remain(monkeypatch, age, all_ok, hit):
    values = inputs()
    cached_boundary(monkeypatch, values, age=age, all_ok=all_ok)
    if hit:
        assert (
            assemble._deep_layer(*values[:3], [])["history"]["current"]["value"] == 14.7
        )
    else:
        with pytest.raises(RecomputeReached):
            assemble._deep_layer(*values[:3], [])


def test_legacy_cache_misses_even_when_timestamp_is_fresh(monkeypatch):
    values = inputs()
    cached_boundary(monkeypatch, values, legacy=True)
    with pytest.raises(RecomputeReached):
        assemble._deep_layer(*values[:3], [])


def test_pit_ledger_change_misses_and_failed_read_cannot_hide_behind_cache(monkeypatch):
    values = inputs()
    _record, load = cached_boundary(monkeypatch, values)
    load.return_value[0]["book"]["positions"][0][1] = 0.5
    with pytest.raises(RecomputeReached):
        assemble._deep_layer(*values[:3], [])
    load.side_effect = RuntimeError("ledger unavailable")
    with pytest.raises(RecomputeReached):
        assemble._deep_layer(*values[:3], [])


def test_unknown_input_type_disables_reuse(monkeypatch):
    values = inputs()
    cached_boundary(monkeypatch, values)
    values[2]["composite"]["value"] = object()
    assert assemble._deep_cache_inputs_sha256(*values) is None
    with pytest.raises(RecomputeReached):
        assemble._deep_layer(*values[:3], [])


def test_prebuilt_without_input_digest_is_not_seeded(monkeypatch):
    monkeypatch.setattr(
        assemble.store,
        "load_blob",
        lambda *_args: pytest.fail("unbound prebuild read cache"),
    )
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda *_args: pytest.fail("unbound prebuild wrote cache"),
    )
    payload = {
        "version": assemble.VERSION_LABEL,
        "deep": {"ok": True},
        "provenance": [],
        "generated_at": "2026-10-03T12:00:00Z",
    }
    assert assemble.seed_prebuilt_deep_cache(payload) is None


def test_mapping_order_stable_and_typed_float_encoding_has_no_list_collision():
    assert assemble._deep_cache_value({"a": 1, "b": 2}) == assemble._deep_cache_value(
        {"b": 2, "a": 1}
    )
    assert assemble._deep_cache_value(1.0) != assemble._deep_cache_value(
        ["float", (1.0).hex()]
    )


def test_saved_cache_and_book_consume_one_ledger_capture(monkeypatch):
    src, drv, engines, ledger = inputs()
    load = Mock(return_value=ledger)
    monkeypatch.setattr(assemble.store, "load_pit_records", load)
    cached = {}
    monkeypatch.setattr(
        assemble.store, "load_blob", lambda key: deepcopy(cached.get(key))
    )
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda key, value: cached.update({key: deepcopy(value)}),
    )
    series = drv["spread_bp"]
    history_result = {
        "index": series,
        "pctl": series / 100.0,
        "regime_series": pd.Series("calm", index=series.index),
        "weights": {},
        "excluded": [],
        "vintage_evidence": assemble.eng_history.vintage_evidence(None),
        "method": "test history computation",
    }
    history = Mock(return_value=history_result)
    monkeypatch.setattr(assemble.eng_history, "build", history)
    for module, name in (
        (assemble.eng_market, "tell"),
        (assemble.eng_playbook, "analyze"),
        (assemble.eng_turn, "analyze"),
        (assemble.eng_microseism, "analyze"),
        (assemble.eng_leakaudit, "run"),
        (assemble.eng_tidetables, "analyze"),
        (assemble.eng_swell, "analyze"),
        (assemble.eng_bathymetry, "analyze"),
        (assemble.eng_markov, "analyze"),
        (assemble.eng_oujump, "analyze"),
        (assemble.eng_montecarlo, "analyze"),
        (assemble.eng_funding_pop, "analyze"),
        (assemble.eng_gyre, "analyze"),
        (assemble.eng_refereegli, "analyze"),
        (assemble.eng_regatta, "analyze"),
        (assemble.eng_searoom, "analyze"),
        (assemble.eng_seastate, "analyze"),
    ):
        monkeypatch.setattr(
            module,
            name,
            lambda *_args, **_kwargs: {"ok": False, "reason": "test boundary"},
        )
    monkeypatch.setattr(
        assemble.eng_market, "market_stress", lambda *_args: (series, {})
    )
    monkeypatch.setattr(assemble.rubric, "build", lambda *_args: {})
    monkeypatch.setattr(
        assemble.eng_mlpred,
        "build_features",
        Mock(side_effect=ValueError("test model unavailable")),
    )
    monkeypatch.setattr(
        assemble.eng_stacker, "build_member_matrix", lambda **_kw: series.to_frame()
    )
    monkeypatch.setattr(assemble.eng_stacker, "event_labels", lambda *_args: series)
    stack = {
        "ok": True,
        **{
            key: series
            for key in ("_p", "_member_probs", "_dispersion", "_cal", "_p_pub", "_y")
        },
    }
    monkeypatch.setattr(
        assemble.eng_stacker,
        "walk_forward_stack",
        lambda *_args, **_kw: deepcopy(stack),
    )
    monkeypatch.setattr(
        assemble.eng_book, "build_returns", lambda **_kw: series.to_frame()
    )
    book = Mock(return_value={"ok": True, "live": {"n_days": 1}})
    monkeypatch.setattr(assemble.eng_book, "run", book)
    result = assemble._deep_layer(src, drv, engines, [])
    assert load.call_count == 1
    assert book.call_args.kwargs["pit_records"] is ledger
    assert result["_inputs_sha256"] == assemble._deep_cache_inputs_sha256(
        src, drv, engines, ledger
    )
    assert result["backtest"]["status"] == "UNVERIFIED"
    assert (
        result["backtest"]["vintage_evidence"]["validated_backtest_eligible"] is False
    )
    assert result["_all_ok"] is False
    second = assemble._deep_layer(src, drv, engines, [])
    assert second["_computed_at"] == result["_computed_at"]
    assert history.call_count == 1 and book.call_count == 1
    ledger[0]["book"]["positions"][0][1] = 0.5
    third = assemble._deep_layer(src, drv, engines, [])
    assert history.call_count == 2 and book.call_count == 2
    assert third["_inputs_sha256"] != result["_inputs_sha256"]


def test_bound_prebuild_still_compares_actual_local_inputs(monkeypatch):
    values = inputs()
    src, drv, engines, ledger = values
    cached = {}
    monkeypatch.setattr(assemble.store, "load_pit_records", lambda: ledger)
    monkeypatch.setattr(
        assemble.store, "load_blob", lambda key: deepcopy(cached.get(key))
    )
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda key, value: cached.update({key: deepcopy(value)}),
    )
    monkeypatch.setattr(
        assemble.eng_history, "build", Mock(side_effect=RecomputeReached)
    )
    day = drv["spread_bp"].index[-1].date().isoformat()
    payload = {
        "version": assemble.VERSION_LABEL,
        "generated_at": datetime.now(UTC).isoformat(),
        "provenance": [{"source": "fred", "mnemonic": "SOFR", "asof": day}],
        "deep": {
            "ok": True,
            "history": {"ok": True, "current": {"value": 14.7}},
            "_inputs_sha256": digest(values),
        },
    }
    assert (
        assemble.seed_prebuilt_deep_cache(payload) == f"deep:{assemble.VERSION}:{day}"
    )
    assert (
        assemble._deep_layer(src, drv, engines, [])["history"]["current"]["value"]
        == 14.7
    )
    drv["res_gdp"].iloc[-1] += 0.1
    with pytest.raises(RecomputeReached):
        assemble._deep_layer(src, drv, engines, [])


def test_nan_infinity_signed_zero_and_index_identity_are_explicit():
    values = inputs()
    drv = values[1]
    hashes = []
    for number in (float("nan"), float("inf"), float("-inf"), 0.0, -0.0):
        drv["res_gdp"].iloc[-1] = number
        hashes.append(digest(values))
    assert len(set(hashes)) == 5
    before = digest(values)
    drv["res_gdp"].index = drv["res_gdp"].index + pd.Timedelta(nanoseconds=1)
    assert digest(values) != before
