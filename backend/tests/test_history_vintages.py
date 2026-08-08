from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from seiche.engines import history
from seiche.vintage import VintageCutVerificationError, VintageEvidenceStore


def _inputs() -> dict[str, pd.Series]:
    index = pd.bdate_range("2024-01-01", periods=320)
    base = pd.Series(np.linspace(0.0, 1.0, len(index)), index=index)
    return {
        "spread_bp": base + 1.0,
        "tail_bp": base + 2.0,
        "srf_accepted": base * 0.0,
        "dw_b": base + 3.0,
        "rrp_b": 300.0 - base,
        "res_gdp": 0.10 + base / 100.0,
        "pair_b": base + 4.0,
        "digestion": base,
    }


def test_current_vintage_history_is_monitoring_only() -> None:
    result = history.build(**_inputs())

    assert result["vintage_evidence"]["status"] == "FINAL_VINTAGE_CONSTRUCTION_PIT"
    assert result["vintage_evidence"]["validated_backtest_eligible"] is False


def test_validated_claim_fails_closed_without_vintage_manifest() -> None:
    with pytest.raises(ValueError, match="ALFRED|as-published"):
        history.build(**_inputs(), claim_mode="validated_backtest")


def test_validated_claim_rejects_self_attested_safe_manifest() -> None:
    manifest = {
        name: "as_published_capture" for name in history.HISTORICAL_INPUTS
    }
    with pytest.raises(ValueError, match="signed content-bound"):
        history.build(
            **_inputs(),
            vintage_manifest=manifest,
            claim_mode="validated_backtest",
        )

    result = history.build(**_inputs(), vintage_manifest=manifest)
    assert result["vintage_evidence"]["status"] == "UNVERIFIED_VINTAGE_ASSERTION"
    assert result["vintage_evidence"]["validated_backtest_eligible"] is False


def _verified_cut(inputs: dict[str, pd.Series]):
    captured = datetime(2026, 1, 1, tzinfo=UTC)
    store = VintageEvidenceStore(
        inputs,
        knowledge_times={name: captured for name in inputs},
        vintage_statuses={name: "as_published_capture" for name in inputs},
        signing_key=b"seiche-history-test-key" * 2,
    )
    return store.verify_cut(store.issue_cut(datetime(2026, 1, 2, tzinfo=UTC)))


def test_validated_claim_accepts_signed_content_bound_cut() -> None:
    inputs = _inputs()
    result = history.build(
        **inputs,
        verified_vintage_cut=_verified_cut(inputs),
        claim_mode="validated_backtest",
    )

    assert result["vintage_evidence"]["validated_backtest_eligible"] is True
    assert result["vintage_evidence"]["cut_id"].startswith("vintagecut_")


def test_verified_cut_cannot_be_reused_beside_mutated_history() -> None:
    inputs = _inputs()
    cut = _verified_cut(inputs)
    altered = dict(inputs)
    altered["spread_bp"] = inputs["spread_bp"].copy()
    altered["spread_bp"].iloc[-1] += 100.0

    with pytest.raises(VintageCutVerificationError, match="exact series"):
        history.build(
            **altered,
            verified_vintage_cut=cut,
            claim_mode="validated_backtest",
        )
