"""Reviewed public projections; acquired snapshots and publisher clocks stay intact."""
from __future__ import annotations

import copy
from collections.abc import Mapping

POLICY = "seiche.public-source-rights.2026-10-28"
REASON = (
    "Public redistribution permission for these FRED-sourced observations and "
    "dependent readings is not established. Values are withheld pending "
    "source-specific permission or qualified original-publisher acquisition."
)
TERMS = "https://fred.stlouisfed.org/legal/terms/"
METADATA = frozenset({
    "id", "schema", "label", "name", "unit", "cadence", "source", "source_url",
    "source_id", "publisher", "series", "asof", "as_of", "generated_at",
    "snapshot_generated_at", "served_at", "coverage_start", "fetched_at",
    "reference_date", "release_date", "event_time", "knowledge_time",
    "published_at", "revision_id", "evidence_hash", "freshness", "formula", "explanation",
})


def metadata(value: object) -> dict:
    node = value if isinstance(value, Mapping) else {}
    out = {key: item for key, item in node.items()
           if key in METADATA and (item is None or isinstance(item, str))}
    alignment = node.get("alignment")
    if isinstance(alignment, dict):
        out["alignment"] = {key: alignment[key] for key in ("method", "no_forward_fill") if key in alignment}
    out.update(value=None, status="restricted", availability="metadata_only",
               publication_allowed=False, redistribution_status="metadata_only",
               rights_status="redistribution_review_required", rights_reason=REASON,
               rights_urls=[TERMS])
    return out


_CARD_FIELDS = METADATA | {
    "value", "status", "robust_z_1y", "robust_z_1y_n", "percentile_3y",
    "percentile_3y_n", "change_1d", "change_5d", "change_20d", "change_1w",
    "change_4w", "change_13w", "change_1m", "change_3m", "change_12m",
    "change_basis", "change_unit", "alignment", "unavailable_reason",
    "age_days_vs_evaluation_asof", "age_days_vs_desk_asof",
    "freshness_policy", "publication_schedule",
}

_DISTRIBUTION_FIELDS = {
    "rate", "p01", "p25", "p75", "p99", "volume", "p99_minus_rate",
    "iqr", "p99_p01_width", "tail_skew",
}
_OFR_IDS = {
    "repo.dvp_rate", "repo.tri_rate", "repo.gcf_rate", "repo.dvp_volume",
    "repo.tri_volume", "repo.gcf_volume", "repo.dvp_minus_tri",
    "repo.reported_segment_volume", "repo.dvp_volume_share", "repo.tri_volume_share",
    "repo.gcf_volume_share", "mmf.total_assets", "mmf.total_repo", "mmf.repo_ficc",
    "mmf.repo_fed", "mmf.repo_share_of_assets", "mmf.ficc_share_of_repo",
    "mmf.fed_share_of_repo", "mmf.other_repo",
}
_DIRECT = {
    "policy.sofr": "Federal Reserve Bank of New York direct reference-rates feed",
    "liquidity.tga": "US Treasury Daily Treasury Statement",
    "liquidity.srf": "NY Fed repo operation results",
}
_CHART_COLUMNS = {
    "sofr_distribution": {"date", "p01_pct", "rate_pct", "p99_pct", "p99_minus_rate_bp", "volume_b"},
    "repo_volumes": {"date", "dvp_b", "tri_b", "gcf_b", "displayed_total_b"},
    "mmf": {"date", "total_assets_b", "total_repo_b", "ficc_repo_b", "fed_repo_b", "other_repo_b"},
    "liquidity": {"date", "tga_b", "srf_b"},
}


def _direct_card(row: dict) -> bool:
    ident, source = row.get("id"), row.get("source")
    if ident in _DIRECT:
        return source == _DIRECT[ident]
    if ident in _OFR_IDS:
        return source == "OFR Short-Term Funding Monitor"
    parts = ident.split(".") if isinstance(ident, str) else []
    return (len(parts) == 3 and parts[0] == "distribution"
            and parts[1] in {"sofr", "tgcr", "bgcr"}
            and parts[2] in _DISTRIBUTION_FIELDS
            and source == f"NY Fed Markets API {parts[1].upper()} distribution")


def _chart(name: str, value: object) -> dict:
    node = value if isinstance(value, Mapping) else {}
    columns, rows = node.get("columns"), node.get("rows")
    if (name not in _CHART_COLUMNS or not isinstance(columns, list)
            or not columns or columns[0] != "date"
            or any(not isinstance(column, str) for column in columns)
            or len(columns) != len(set(columns))
            or not isinstance(rows, list)
            or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        return metadata(node)
    out = {key: copy.deepcopy(node[key]) for key in
           ("id", "title", "cadence", "row_limit", "no_forward_fill") if key in node}
    out["columns"] = list(columns)
    out["rows"] = [[copy.deepcopy(item) if column in _CHART_COLUMNS[name] else None
                    for column, item in zip(columns, row)] for row in rows]
    out["withheld_columns"] = [column for column in columns if column not in _CHART_COLUMNS[name]]
    return out


def money_desk(value: object) -> dict:
    """Project a saved USD desk without recomputation or source relabelling.

    Required funding cards retain their exact identity, date, unit and source
    even when restricted. The operator can target those dates for an independent
    original-publisher acquisition, but this does not make the desk review ready.
    """
    node = value if isinstance(value, Mapping) else {}
    sections = node.get("sections")
    if (node.get("schema") != "seiche.money-market-desk.v1"
            or not isinstance(sections, list)
            or any(not isinstance(section, dict) or not isinstance(section.get("metrics"), list)
                   or any(not isinstance(row, dict) for row in section["metrics"])
                   for section in sections)):
        return {**metadata(node), "ok": False, "sections": [], "charts": {},
                "reason": "Unexpected USD desk schema or shape; public values are withheld."}
    out = {key: copy.deepcopy(node[key]) for key in
           ("schema", "ok", "asof", "snapshot_generated_at", "served_at", "context_only")
           if key in node}
    public_sections = []
    for section in sections:
        metrics = [{key: copy.deepcopy(item) for key, item in row.items() if key in _CARD_FIELDS}
                   if _direct_card(row) else metadata(row)
                   for row in section["metrics"]]
        available = sum(row.get("status") == "available" for row in metrics)
        public_sections.append({
            **{key: section[key] for key in ("id", "title", "label", "explanation")
               if isinstance(section.get(key), str)},
            "metrics": metrics, "status": "partial" if available else "restricted",
            "available_metrics": available, "total_metrics": len(metrics),
        })
    out.update(sections=public_sections,
               charts={key: _chart(key, item) for key, item in
                       (node.get("charts") if isinstance(node.get("charts"), dict) else {}).items()},
               plain_language=REASON, quant_read=None, strongest_signal=None, countercase=None,
               regime={"state": "CANNOT_ASSESS", "status": "redistribution_restricted"},
               coverage={"status": "rights_projection", "available_metrics":
                         sum(section["available_metrics"] for section in public_sections),
                         "total_metrics": sum(section["total_metrics"] for section in public_sections)},
               diagnostics=metadata(node.get("diagnostics")),
               publication_projection={"policy": POLICY, "reason": REASON,
                                       "original_snapshot_unchanged": True})
    freshness = node.get("freshness") if isinstance(node.get("freshness"), dict) else {}
    out["freshness"] = {key: freshness[key] for key in ("desk_asof", "evaluation_asof")
                        if isinstance(freshness.get(key), str)}
    method = node.get("methodology") if isinstance(node.get("methodology"), dict) else {}
    out["methodology"] = {key: method[key] for key in
                          ("evidence_horizon", "alignment", "purpose", "changes", "statistics", "regime", "charts", "availability")
                          if isinstance(method.get(key), str)}
    out["formulas"] = [{key: item[key] for key in ("id", "expression", "unit", "alignment")
                        if isinstance(item.get(key), str)}
                       for item in node.get("formulas", []) if isinstance(item, dict)]
    if not isinstance(node.get("diagnostics"), dict):
        out["diagnostics"] = {"status": "unavailable", "used_in_regime": False,
                              "reason": "Diagnostics are absent from this snapshot."}
    sources = node.get("source_metadata") if isinstance(node.get("source_metadata"), list) else []
    out["source_metadata"] = [metadata(row) if str(row.get("id", "")).startswith("fred_")
                              else {key: copy.deepcopy(item) for key, item in row.items()
                                    if key in METADATA or key in {"available", "observations", "age_days_vs_desk_asof", "age_days_vs_evaluation_asof"}}
                              for row in sources if isinstance(row, dict)]
    out["sources"] = copy.deepcopy(out["source_metadata"])
    out["legal_notices"] = copy.deepcopy(node.get("legal_notices", []))
    out["caveats"] = [REASON, "Retained direct-source observations keep their original clocks; missing values are not treated as calm."]
    return out


# Explicit release scope approved on 2026-10-28. These paths mix legacy FRED
# inputs into measurements, forecasts, diagnostics or prose. Authentication,
# payment, an old snapshot, or a successful request cannot grant reuse rights.
LEGACY_TOOLS = frozenset({
    "latest_article", "funding_stress_now", "funding_stress_forecast",
    "historical_analogs", "replay_asof", "proof_backtest", "crypto_stress_record",
    "institutional_flows", "oil_funding_context", "fx_materials_passage",
    "positioning_book", "desk_brief", "ask_desk", "trade_safety_risk_context",
})
LEGACY_PATHS = frozenset({
    "/api/overview", "/api/public", "/api/gauge", "/api/oil-funding",
    "/api/estuary", "/api/wrecks", "/api/deep", "/api/book", "/api/brief",
    "/api/ask", "/api/event-analysis", "/api/pit", "/api/alerts",
    "/api/badge/record", "/api/trade-safety/risk-context",
})
LEGACY_PREFIXES = ("/api/engines/", "/api/asof/", "/api/dispatch/")


def legacy_http_restricted(path: str) -> bool:
    normalized = path.rstrip("/")
    return normalized in LEGACY_PATHS or any(
        normalized.startswith(prefix) for prefix in LEGACY_PREFIXES
    )


def legacy_unavailable(surface: str) -> dict:
    return {
        "schema": "seiche.source-rights-restriction.v1", "ok": False,
        "status": "FAILED", "category": "source_rights_restricted",
        "availability": "unavailable", "rights_status": "redistribution_review_required",
        "publication_allowed": False, "surface": surface, "policy": POLICY,
        "reason": "This legacy feature is unavailable while its source reuse rights and replacement acquisitions are qualified. " + REASON,
        "rights_urls": [TERMS], "context_only": True,
        "values_reconstructed": False, "execution_authority": "none",
        "available_research": ["money_market_context", "market_workbench", "world_markets_context", "data_health"],
    }


def rights_status() -> dict:
    return {
        "schema": "seiche.source-rights-status.v1", "status": "partial_release",
        "policy": POLICY, "context_only": True,
        "available_tools": ["source_rights_status", "money_market_context", "market_workbench",
                            "world_markets_context", "gift_city_context", "gold_inventory_carry",
                            "research_network", "data_health"],
        "unavailable_tools": sorted(LEGACY_TOOLS), "rights_urls": [TERMS],
        "boundary": "Available tools still report field-specific restrictions, missing values and source clocks. Availability is not a blanket reuse license, freshness certification or investment advice. ECB FX is the default; legacy H.10/FRED values stay withheld. Gold carry is arithmetic over caller-supplied assumptions.",
    }
