"""Offline world-markets contract tests for the dependency-free Python client."""

from __future__ import annotations

import copy
from pathlib import Path
import runpy

import pytest


CLIENT = runpy.run_path(str(Path(__file__).with_name("world_markets.py")))
SeicheClientError = CLIENT["SeicheClientError"]
validate = CLIENT["_validate_contract"]

SERIES_IDS = (
    "CN.NBS.CPI_INDEX",
    "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
    "CN.NBS.MANUFACTURING_PMI",
    "CN.NBS.PPI_INDEX",
)


def _series(series_id: str) -> dict:
    return {
        "series_id": series_id,
        "catalogid": "catalog-id",
        "catalog_label": "Catalog label",
        "row_id": "row-id",
        "i": "indicator-id",
        "ek": "export-key",
        "ek_dp": "export-key-dimension",
        "dp": "1",
        "dp_name": "dimension",
        "label": "Series label",
        "reference_release_url": "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1965018.html",
        "release_url": "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1965018.html",
        "source_unit_label_exact": "%",
        "source_unit_semantically_authoritative": True,
        "semantic_contract": {
            "value_kind": "index_level",
            "canonical_unit": "index_points",
            "comparison_base": None,
            "transform": None,
            "threshold": None,
        },
        "value_publication": "withheld_pending_rights_review",
    }


def _china(*, available: bool = True) -> dict:
    common = {
        "schema": "seiche.nbs-macro-context.v1",
        "dataset": "CN.NBS.MACRO_CONTEXT",
        "publisher": "National Bureau of Statistics of China",
        "source_url": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData",
        "terms_url": "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html",
        "status": "restricted" if available else "structural",
        "evidence_status": "restricted" if available else "unavailable",
        "available": available,
        "as_of": None,
        "context_only": True,
        "scoring_eligible": False,
        "cn_cny_gauge_eligible": False,
        "values_published": False,
        "raw_evidence_included": False,
        "history_included": False,
        "public_distribution": "metadata_only",
        "rights_status": "redistribution_review_required",
        "series_catalog": [_series(series_id) for series_id in SERIES_IDS],
        "series_count": 4,
        "reading": "Metadata-only China macro context.",
        "boundaries": ["owner", "values", "scoring"],
    }
    if not available:
        return {**common, "reason_code": "signed_owner_export_required"}
    return {
        **common,
        "revision_id": "nbs-2026-07-r1",
        "predecessor_revision_id": None,
        "knowledge_time": "2026-08-10T02:00:00Z",
        "source_registry_ids": [
            "nbs_monthly_data_browser",
            "nbs_terms_of_service",
        ],
        "provenance": {
            "manifest_sha256": "a" * 64,
            "owner_attestation": "ed25519",
        },
        "attestation": {
            "schema": "seiche.nbs-owner-export-signature.v1",
            "algorithm": "ed25519",
            "domain": "seiche-nbs-owner-export-v1",
            "export_id": "nbs-2026-07-r1",
            "signer_key_id": "c" * 64,
            "signed_at": "2026-08-10T02:05:00Z",
            "manifest_sha256": "a" * 64,
            "public_projection_sha256": "d" * 64,
            "signature": "e" * 128,
        },
    }


def _payload() -> dict:
    return {
        "schema": "seiche.world-markets.v1",
        "selection": "china_macro",
        "as_of": None,
        "context_only": True,
        "generated_at": None,
        "clocks": {
            "boundary": "knowledge time never becomes an observation clock",
            "domains": {
                "money_markets": None,
                "forex": None,
                "capital_markets": None,
            },
            "snapshot_generated_at": None,
            "latest_domain_as_of": None,
            "selected_evidence_as_of": None,
            "excluded_from_observation_clocks": ["china_macro.knowledge_time"],
        },
        "citation": {
            "canonical_url": "https://seiche.info/markets/china-macro/",
            "generated_at": None,
            "evidence_as_of": None,
        },
        "scope": {"coverage_claim": "curated_partial_non_exhaustive"},
        "china_macro": _china(),
    }


def test_accepts_available_and_structural_metadata_only_china_context() -> None:
    validate(_payload(), "china_macro")
    payload = _payload()
    payload["china_macro"] = _china(available=False)
    validate(payload, "china_macro")


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload["china_macro"].update(values_published=True),
        lambda payload: payload["china_macro"]["series_catalog"][0].update(
            latest_value="100.5"
        ),
        lambda payload: payload["china_macro"]["series_catalog"][0].update(
            value="100.5"
        ),
        lambda payload: payload["china_macro"]["series_catalog"][0].update(
            harmless_metric=100.5
        ),
        lambda payload: payload["china_macro"].pop("knowledge_time"),
        lambda payload: payload["china_macro"].pop("attestation"),
        lambda payload: payload["china_macro"].pop("provenance"),
        lambda payload: payload["china_macro"]["provenance"].update(
            raw_sha256="b" * 64
        ),
        lambda payload: payload["china_macro"]["provenance"].update(
            raw_size_bytes=2048
        ),
        lambda payload: payload["china_macro"]["attestation"].update(
            raw_sha256="b" * 64
        ),
        lambda payload: payload["china_macro"]["attestation"].update(
            signed_at="2026-08-10T01:59:59Z"
        ),
        lambda payload: (
            payload["china_macro"].update(knowledge_time="2026-08-10T02:00:00.000001Z"),
            payload["china_macro"]["attestation"].update(
                signed_at="2026-08-10T02:00:00Z"
            ),
        ),
        lambda payload: payload["china_macro"]["series_catalog"].reverse(),
        lambda payload: payload["clocks"].update(
            selected_evidence_as_of="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["clocks"].update(
            latest_domain_as_of="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["clocks"]["domains"].update(
            china_macro="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["citation"].update(
            evidence_as_of="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["citation"].pop("evidence_as_of"),
        lambda payload: payload["clocks"].pop("selected_evidence_as_of"),
        lambda payload: payload["clocks"].update(excluded_from_observation_clocks=[]),
        lambda payload: payload.update(generated_at="2026-08-10T02:00:00Z"),
    ),
)
def test_rejects_observation_promotion(mutation) -> None:
    payload = copy.deepcopy(_payload())
    mutation(payload)
    with pytest.raises(SeicheClientError):
        validate(payload, "china_macro")


def test_rejects_signed_metadata_on_the_unavailable_state() -> None:
    for field, value in (
        ("knowledge_time", "2026-08-10T02:00:00Z"),
        ("revision_id", "nbs-forged"),
        ("provenance", {}),
        ("attestation", {}),
    ):
        payload = _payload()
        payload["china_macro"] = _china(available=False)
        payload["china_macro"][field] = value
        with pytest.raises(SeicheClientError):
            validate(payload, "china_macro")


def _all_payload() -> dict:
    payload = _payload()
    payload.update(
        selection="all",
        generated_at="2026-08-21T20:54:06Z",
        as_of="2026-08-20",
        money_markets={},
        forex={},
        capital_markets={},
        sources=[],
        methodology={},
    )
    payload["clocks"].update(
        snapshot_generated_at="2026-08-21T20:54:06Z",
        domains={
            "money_markets": "2026-08-20",
            "forex": "2026-08-19",
            "capital_markets": "2026-08-18",
        },
        latest_domain_as_of="2026-08-20",
        selected_evidence_as_of="2026-08-20",
    )
    payload["citation"].update(
        generated_at="2026-08-21T20:54:06Z",
        evidence_as_of="2026-08-20",
    )
    return payload


def test_all_requires_china_and_preserves_only_core_market_clocks() -> None:
    validate(_all_payload(), "all")
    for mutation in (
        lambda payload: payload.pop("china_macro"),
        lambda payload: payload["clocks"].update(
            selected_evidence_as_of=payload["china_macro"]["knowledge_time"]
        ),
        lambda payload: payload["citation"].update(
            evidence_as_of=payload["china_macro"]["knowledge_time"]
        ),
        lambda payload: payload.update(as_of=payload["china_macro"]["knowledge_time"]),
    ):
        payload = _all_payload()
        mutation(payload)
        with pytest.raises(SeicheClientError):
            validate(payload, "all")


def test_named_selector_rejects_an_unrequested_china_projection() -> None:
    payload = _all_payload()
    payload["selection"] = "forex"
    for key in ("money_markets", "capital_markets", "sources", "methodology"):
        payload.pop(key)
    payload["as_of"] = "2026-08-19"
    payload["clocks"]["selected_evidence_as_of"] = "2026-08-19"
    payload["citation"]["evidence_as_of"] = "2026-08-19"
    with pytest.raises(SeicheClientError):
        validate(payload, "forex")
