"""Reviewed public projections; acquired snapshots and publisher clocks stay intact.

A value, card or engine reading is public only when every input it reads is a
direct capture from an allowed original publisher, or an open source the
policy names below, and the snapshot's own provenance proves it. Anything that
reads a FRED copy, one of the blocked proprietary series, a source whose reuse
terms are not established, or an input whose identity cannot be verified is
withheld as metadata. Withheld values are never reconstructed.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from seiche import source_decisions

POLICY = "seiche.public-source-rights.2026-11-09"
PRIOR_POLICY = "seiche.public-source-rights.2026-10-28"
REASON = (
    "Public redistribution permission for these FRED-sourced observations and "
    "dependent readings is not established. Values are withheld pending "
    "source-specific permission or qualified original-publisher acquisition."
)
TERMS = "https://fred.stlouisfed.org/legal/terms/"
# Rendered static surfaces state a withheld board in these words.
WITHHELD_STATE = "Withheld pending source-rights qualification"
WITHHELD_NOTE = ("Values are not reconstructed. A withheld reading is not a calm, "
                 "zero or last-known reading.")
METADATA = frozenset({
    "id", "schema", "label", "name", "unit", "cadence", "source", "source_url",
    "source_id", "publisher", "series", "asof", "as_of", "generated_at",
    "snapshot_generated_at", "served_at", "coverage_start", "fetched_at",
    "reference_date", "release_date", "event_time", "knowledge_time",
    "published_at", "revision_id", "evidence_hash", "freshness", "formula", "explanation",
})

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_FRB = ("Board of Governors of the Federal Reserve System",
        "https://www.federalreserve.gov/disclaimer.htm", "public_domain_with_citation",
        "Source: Board of Governors of the Federal Reserve System (US).")
_NYFED = ("Federal Reserve Bank of New York", "https://www.newyorkfed.org/privacy/termsofuse",
          "nyfed_terms_of_use_licence_with_required_notices",
          "Content from the New York Fed subject to the Terms of Use at newyorkfed.org.")
_UST = ("U.S. Department of the Treasury", "https://www.copyright.gov/title17/92chap1.html",
        "us_government_work_17_usc_105", "Source: U.S. Department of the Treasury.")
_CFTC = ("Commodity Futures Trading Commission", "https://www.copyright.gov/title17/92chap1.html",
         "us_government_work_17_usc_105", "Source: U.S. Commodity Futures Trading Commission.")


def _entry(label: str, owner: tuple[str, str, str, str], *, decision: str | None = None,
           free_public_only: bool = False) -> dict:
    publisher, terms_url, basis, attribution = owner
    out = {"label": label, "publisher": publisher, "terms_url": terms_url,
           "basis": basis, "attribution": attribution}
    if decision:
        out["decision"] = decision
    if free_public_only:
        out["surfaces"] = "free_public_only"
    return out


# Every source a public reading may read, keyed by the provenance "source"
# value (series) or by the snapshot envelope key (tables). Decision-gated
# entries are allowed only while their dated owner decision is recorded.
SOURCES: dict[str, dict] = {
    "fed_h41": _entry("Federal Reserve Board H.4.1 release XML (direct)", _FRB),
    "fed_h15": _entry("Federal Reserve Board H.15 release XML (direct)", _FRB),
    "fed_h10": _entry("Federal Reserve Board H.10 release XML (direct)", _FRB),
    "fed_g17": _entry("Federal Reserve Board G.17 release XML (direct)", _FRB),
    "fed_prates": _entry("Federal Reserve Board PRATES release XML (direct)", _FRB),
    "fed_cp": _entry("Federal Reserve Board Commercial Paper release XML (direct; derived by the Board from DTCC data)",
                     _FRB, decision=source_decisions.FED_CP),
    "fedtext": _entry("Federal Reserve Board FOMC statements", _FRB),
    "nyfed_original": _entry("Federal Reserve Bank of New York Markets API (direct)", _NYFED),
    "nyfed_rates": _entry("NY Fed reference-rate distributions (direct)", _NYFED),
    "nyfed_srf": _entry("NY Fed repo operation results (direct)", _NYFED),
    "nyfed_pd": _entry("NY Fed primary dealer statistics (direct)", _NYFED),
    "nyfed_fxs": _entry("NY Fed USD liquidity swap operations (direct)", _NYFED),
    "nyfed_rde": _entry("NY Fed reserve demand elasticity (direct)", _NYFED),
    "tga": _entry("US Treasury Daily Treasury Statement (FiscalData)", _UST),
    "auctions": _entry("US Treasury auction results (FiscalData)", _UST),
    "upcoming": _entry("US Treasury upcoming auctions (FiscalData)", _UST),
    "mspd": _entry("US Treasury Monthly Statement of the Public Debt", _UST),
    "tff": _entry("CFTC Traders in Financial Futures", _CFTC),
    "commodity_cot": _entry("CFTC disaggregated Commitments of Traders", _CFTC),
    "ofr": _entry("OFR Short-Term Funding Monitor", (
        "Office of Financial Research", "https://www.financialresearch.gov/legal-notices/",
        "us_government_work_credit_requested", "Source: Office of Financial Research.")),
    "bea_original": _entry("BEA NIPA Table 1.1.5 (direct)", (
        "U.S. Bureau of Economic Analysis", "https://www.bea.gov/help/faq/147",
        "public_domain_with_citation", "Source: U.S. Bureau of Economic Analysis.")),
    "bls_original": _entry("BLS Public Data API, CPI (direct)", (
        "U.S. Bureau of Labor Statistics", "https://www.bls.gov/developers/termsOfService.htm",
        "public_domain_with_bls_api_terms_of_service_accepted",
        "Source: U.S. Bureau of Labor Statistics. BLS.gov cannot vouch for the data or analyses "
        "derived from these data after the data have been retrieved from BLS.gov."),
        decision=source_decisions.BLS_API),
    "eia_original": _entry("EIA weekly petroleum stocks (direct)", (
        "U.S. Energy Information Administration", "https://www.eia.gov/about/copyrights_reuse.php",
        "public_domain_eia_survey_data", "Source: U.S. Energy Information Administration.")),
    "eia": _entry("EIA weekly petroleum stocks (direct)", (
        "U.S. Energy Information Administration", "https://www.eia.gov/about/copyrights_reuse.php",
        "public_domain_eia_survey_data", "Source: U.S. Energy Information Administration.")),
    "ecb_original": _entry("ECB Data Portal (direct)", (
        "European Central Bank",
        "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "reuse_with_attribution_and_free_source_notice",
        "Source: European Central Bank. This information is available free of charge from the ECB.")),
    "ecb": _entry("ECB Data Portal (direct)", (
        "European Central Bank",
        "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "reuse_with_attribution_and_free_source_notice", "Source: European Central Bank.")),
    "ecb_fx": _entry("ECB euro reference exchange rates (direct)", (
        "European Central Bank",
        "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "reuse_with_attribution_and_free_source_notice", "Source: European Central Bank.")),
    "boe_original": _entry("Bank of England Database SONIA (direct, delayed copy)", (
        "Bank of England", "https://www.bankofengland.co.uk/legal", "OGL_v3_with_SONIA_attribution",
        "SONIA and/or SONIA Compounded Index data licensed under the Open Government Licence v3.0 "
        "and copyright the Governor and Company of the Bank of England."),
        decision=source_decisions.BOE_SONIA),
    "boj_original": _entry("Bank of Japan statistics API, BS01 total assets (direct)", (
        "Bank of Japan", "https://www.stat-search.boj.or.jp/info/notice_en.html",
        "bank_of_japan_credit_free_public_site_only", "Source: Bank of Japan."),
        decision=source_decisions.BOJ_TOTAL_ASSETS, free_public_only=True),
    # TONA from the BoJ flat files is already served raw by the JP-JPY atlas
    # pack (boj_rates ALLOWED); the board follows that existing policy.
    "boj": _entry("Bank of Japan flat files, TONA (direct)", (
        "Bank of Japan", "https://www.stat-search.boj.or.jp/info/notice_en.html",
        "bank_of_japan_credit_free_public_site_only", "Source: Bank of Japan."),
        free_public_only=True),
    # BIS global-liquidity readings are already served by world_markets_context.
    "bis": _entry("BIS statistics (direct; derived readings)", (
        "Bank for International Settlements", "https://www.bis.org/about/legal/permitted-use-statistics",
        "bis_statistics_reuse_with_citation", "Source: Bank for International Settlements.")),
}
# Sources that keep a reading withheld, and why. Absent keys fail closed too.
WITHHELD_SOURCES: dict[str, str] = {
    "fred": REASON,
    "crypto": "Exchange and stablecoin market data reuse terms are not established for this release.",
    "llama_hacks": "DeFiLlama exploit-record reuse terms are not established for this release.",
    "gdelt": "GDELT text-sample reuse terms are not established for this release.",
    "palimpsest": "Palimpsest context is quarantined from the funding board's public projection.",
    "windfetch": "The Undertow current-affairs pack is not qualified for this projection.",
    "chinamoney": "CFETS China fixings are licensed data and stay outside the public board.",
}
# Snapshot envelope keys (non-series tables) and the source they come from.
ENVELOPES = frozenset({
    "nyfed_rates", "nyfed_srf", "nyfed_pd", "nyfed_fxs", "nyfed_rde", "tga", "auctions",
    "upcoming", "mspd", "tff", "commodity_cot", "fedtext", "crypto", "llama_hacks",
    "gdelt", "palimpsest", "windfetch",
})


def source_allowed(source: object, *, surface: str = "free") -> bool:
    """One source identity, after its owner decision and surface condition."""
    entry = SOURCES.get(source) if isinstance(source, str) else None
    if entry is None:
        return False
    if entry.get("surfaces") == "free_public_only" and surface != "free":
        return False
    return "decision" not in entry or source_decisions.allows(entry["decision"])


def _qualified_source(mnemonic: str) -> str | None:
    """The original-publisher source identity a qualified alias must carry."""
    from seiche.sources import original_publishers

    contract = original_publishers.CONTRACTS.get(mnemonic)
    if contract is None:
        return None
    if "release" in contract:
        return "fed_" + contract["release"]
    return contract["provider"] + "_original"


def _registry_source(mnemonic: str) -> str | None:
    from seiche.config import ALL_SERIES

    spec = ALL_SERIES.get(mnemonic)
    return spec.source if spec is not None else None


def input_can_be_public(name: str) -> bool:
    """Static check: can this input ever reach a public reading in this release?"""
    if name in ENVELOPES:
        return source_allowed(name)
    qualified = _qualified_source(name)
    if qualified is not None and source_allowed(qualified):
        return True
    registry = _registry_source(name)
    return registry not in (None, "fred") and source_allowed(registry)


# ---------------------------------------------------------------------------
# Inputs each legacy reading reads (mirrors assemble._run_engines/_deep_layer)
# ---------------------------------------------------------------------------

_SPREAD = ("SOFR", "IORB", "IOER")                 # derived SOFR minus IORB/IOER splice
_TAIL = ("nyfed_rates",)                           # SOFR P99 minus median
_RES_GDP = ("WRESBAL", "GDP")
_KINK = _SPREAD + _RES_GDP
_PANEL = _SPREAD + ("EFFR", "BGCR", "TGCR", "DVP_RATE_OO", "TRI_RATE_OO", "nyfed_rates",
                    "nyfed_srf", "RRPONTSYD", "tga", "DVP_VOL", "TRI_VOL")
_HISTORY = _SPREAD + _TAIL + ("nyfed_srf", "DISCOUNT_WINDOW", "RRPONTSYD") + _RES_GDP + (
    "tff", "DVP_VOL", "auctions")
_WEATHER = ("WRESBAL", "WALCL", "tga", "upcoming") + _KINK
_H10 = ("EURUSD", "GBP", "JPY", "AUD", "CAD", "CHF", "CNY", "INR", "KRW", "MXN", "BRL", "ZAR",
        "NZD", "DKK", "HKD", "MYR", "NOK", "SEK", "SGD", "TWD", "THB", "LKR")


def _union(*parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for part in parts for item in part))


_COMPOSITE = _union(
    ("nyfed_rates", "IORB", "IOER"), _KINK, _WEATHER, ("nyfed_srf", "DISCOUNT_WINDOW"),
    ("tff", "DVP_VOL"), _SPREAD, _PANEL, _SPREAD + _TAIL, ("auctions",), ("nyfed_pd",),
    ("RRPONTSYD",),
)


def _sonar_inputs() -> tuple[str, ...]:
    """SONAR sweeps every FRED-registry and OFR series, so it reads blocked ones."""
    from seiche.config import ALL_SERIES

    swept = tuple(m for m, spec in ALL_SERIES.items() if spec.source in {"fred", "ofr"})
    return _union(swept, _SPREAD, _TAIL, ("nyfed_srf", "tga", "crypto", "palimpsest"))


ENGINE_INPUTS: dict[str, tuple[str, ...]] = {
    "money_market": ("SOFR", "EFFR", "IORB", "IOER", "nyfed_rates", "BGCR", "TGCR", "DVP_RATE_OO",
                     "DVP_VOL", "TRI_RATE_OO", "TRI_VOL", "GCF_RATE_OO", "GCF_VOL_OO", "MMF_TOT",
                     "MMF_REPO_FICC", "MMF_REPO_FED", "MMF_REPO_TOT", "CP_NONFIN_3M", "CP_FIN_3M",
                     "DGS3M", "TB4W", "TB3M", "WRESBAL", "tga", "RRPONTSYD", "nyfed_srf",
                     "DISCOUNT_WINDOW"),
    "kink": _KINK,
    "rdenowcast": _KINK + ("nyfed_rde",),
    "weather": _WEATHER,
    "supplydesk": ("upcoming", "auctions", "mspd"),
    "runway": _union(("WRESBAL", "RRPONTSYD", "tga", "upcoming"), _KINK),
    "tails": ("nyfed_rates", "IORB", "IOER"),
    "stigma": ("nyfed_rates", "SRF_CEILING", "nyfed_srf", "IORB", "IOER"),
    "echo": _SPREAD + ("EFFR", "BGCR", "RRPONTSYD", "tga", "WRESBAL", "nyfed_srf"),
    "rvxray": ("tff", "DVP_VOL"),
    "crowding": ("tff",),
    "officialbid": ("CUSTODY_TSY", "FOREIGN_RRP", "FIMA_REPO"),
    "auctions": ("auctions",),
    "reportcard": _SPREAD + ("auctions", "nyfed_srf", "WRESBAL"),
    "ledger": ("WALCL", "WCURCIR", "WRESBAL", "WTREGEN", "RRPONTSYD", "FOREIGN_RRP"),
    "resonance": _SPREAD,
    "undertow": _SPREAD + _TAIL,
    "phasemap": _SPREAD + ("auctions",),
    "edetect": _SPREAD + _TAIL,
    "communique": ("fedtext",),
    "scuttlebutt": ("gdelt",),
    "breakwater": _SPREAD + ("nyfed_srf",),
    "hydrophone": _PANEL,
    "merian": _PANEL,
    "roguewave": _SPREAD,
    "caesar": _SPREAD,
    "warehouse": ("nyfed_pd",),
    "basins": _SPREAD + ("ESTR", "ECB_DFR", "SONIA", "DXY_BROAD", "SWAP_LINES", "FOREIGN_RRP",
                         "nyfed_fxs", "INR", "crypto", "TONA", "CNY", "JPY", "KRW"),
    "thermohaline": ("GLI_OFFSHORE_USD", "GLI_OFFSHORE_LOANS", "GLI_OFFSHORE_DEBT", "GLI_EME_USD",
                     "CREDIT_GAP_US", "CREDIT_GAP_CN"),
    "harbors": ("ESTR", "EURUSD", "CNY", "CALL_IN", "INR", "TONA", "JPY", "CALL_KR", "KRW", "EFFR"),
    "spillover": ("EFFR", "ESTR", "TONA", "EURUSD", "CNY", "JPY", "INR", "KRW"),
    "stationkeeping": ("tga", "RRPONTSYD", "WALCL"),
    "farbasin": ("palimpsest",),
    "moorings": ("crypto",),
    "cpsentinel": ("llama_hacks", "CP_NONFIN_3M", "DGS3M"),
    "oilfunding": _SPREAD + ("WTI_SPOT", "BRENT_SPOT", "CP_NONFIN_3M", "CP_FIN_3M", "DGS3M", "INR",
                             "ENERGY_CPI", "CORE_CPI", "CUSTODY_TSY", "FOREIGN_RRP", "CUSHING_STOCKS"),
    "ballast": _SPREAD + ("commodity_cot", "WTI_SPOT", "HENRY_HUB_SPOT", "CRUDE_STOCKS_EX_SPR",
                          "CP_NONFIN_3M", "DGS3M"),
    "estuary": _union(_H10, _SPREAD, (
        "DXY_BROAD", "DXY_AFE", "DXY_EME", "WTI_SPOT", "BRENT_SPOT", "NATGAS_SPOT", "COAL",
        "COMMODITY_ALL", "COPPER", "ALUMINUM", "NICKEL", "WHEAT", "CORN", "ESTR", "SONIA", "TONA",
        "CALL_IN", "CALL_KR", "EFFR", "CP_NONFIN_3M", "CP_FIN_3M", "DGS3M", "SWAP_LINES",
        "FOREIGN_RRP", "FIMA_REPO", "GLI_OFFSHORE_USD")),
    "windfetch": ("windfetch",),
    "sonar": _sonar_inputs(),
    "composite": _COMPOSITE,
}
_TELL = _HISTORY + ("VIX", "HY_OAS", "IG_OAS", "DGS10")
_OUTCOMES = ("SP500", "VIX", "HY_OAS", "IG_OAS", "DGS10", "crypto")
_TIDE = _SPREAD + _TAIL + ("EFFR", "BGCR", "RRPONTSYD", "tga", "WRESBAL", "nyfed_srf")
_SWELL = _SPREAD + _TAIL + ("auctions", "upcoming")
_ML = _union(_HISTORY, ("VIX", "HY_OAS", "DGS10", "INR", "TED", "crypto"))
_STACKER = _union(_ML, _TIDE, _SWELL, _TELL)
_FUNDING_POP = _SPREAD + _TAIL + ("RRPONTSYD",)
DEEP_INPUTS: dict[str, tuple[str, ...]] = {
    "history": _HISTORY,
    "tell": _TELL,
    "playbook": _union(_TELL, _OUTCOMES),
    "turn": _SPREAD + _TAIL + ("RRPONTSYD",) + _RES_GDP,
    "backtest": _union(_HISTORY, _OUTCOMES),
    "microseism": _SPREAD,
    "leakaudit": _HISTORY,
    "tidetables": _TIDE,
    "swell": _SWELL,
    "bathymetry": _SPREAD,
    "markov": _union(_HISTORY, _COMPOSITE),
    "oujump": _union(_HISTORY, _COMPOSITE),
    "montecarlo": _union(_HISTORY, _COMPOSITE),
    "funding_pop": _FUNDING_POP,
    "riptide": _FUNDING_POP,
    "gyre": _SPREAD,
    "refereegli": ("FED_ASSETS_LONG", "ECB_ASSETS", "BOJ_ASSETS", "EURUSD_LONG", "JPY_LONG",
                   "NASDAQ", "INDPRO", "TGA_LONG", "RRP_LONG"),
    "ml": _ML,
    "stacker": _STACKER,
    "regatta": _STACKER,
    "searoom": _STACKER,
    "seastate": _SPREAD,
    "book": _union(_STACKER, ("DGS2", "DGS10", "SP500", "crypto", "TB3M")),
    "modelcourt": _STACKER,
}
# Deep keys that are metadata, not readings.
_DEEP_METADATA = frozenset({"ok", "historical_evidence", "reason"})
HEADLINE_INPUTS: dict[str, tuple[str, ...]] = {
    "sofr_pct": ("SOFR",), "effr_pct": ("EFFR",), "iorb_pct": ("IORB",),
    "reserves_b": ("WRESBAL",), "rrp_b": ("RRPONTSYD",), "tga_b": ("tga",),
    "srf_accepted_b": ("nyfed_srf",), "dw_b": ("DISCOUNT_WINDOW",),
    "vix": ("VIX",), "hy_oas_pct": ("HY_OAS",),
}
CALENDAR_INPUTS: dict[str, tuple[str, ...]] = {
    "fomc_next_90d": (), "corporate_tax_next_90d": (),
    "upcoming_settlements": _WEATHER, "crunch_windows": _WEATHER,
    "next_turn": DEEP_INPUTS["turn"], "bill_desk": ("auctions", "upcoming"),
}
# Board sections that combine every reading into prose or a forecast record.
WITHHELD_SECTIONS = ("editorial", "navigator")


def engine_can_be_public(name: str, table: Mapping[str, tuple[str, ...]] | None = None) -> bool:
    inputs = (ENGINE_INPUTS if table is None else table).get(name)
    return inputs is not None and all(input_can_be_public(item) for item in inputs)


class _Ledger:
    """The snapshot's provenance, read as proof of each input's identity."""

    def __init__(self, snapshot: Mapping, surface: str = "free") -> None:
        self.surface = surface
        self.rows: dict[str, Mapping] = {}
        self.ambiguous: set[str] = set()
        rows = snapshot.get("provenance")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not isinstance(row.get("mnemonic"), str):
                continue
            if row["mnemonic"] in self.rows:
                self.ambiguous.add(row["mnemonic"])
            self.rows[row["mnemonic"]] = row

    def check(self, name: str) -> tuple[bool, str | None, str | None]:
        if name in ENVELOPES:
            if source_allowed(name, surface=self.surface):
                return True, name, None
            return False, name, WITHHELD_SOURCES.get(name, "reuse terms for this source are not established")
        row = self.rows.get(name)
        if row is None or name in self.ambiguous:
            return False, None, "no single provenance row proves this input's source"
        source = row.get("source")
        if row.get("availability") == "unavailable":
            return False, source, "input absent from this snapshot; its source is not proven"
        expected = _qualified_source(name)
        registry = _registry_source(name)
        if source == expected:
            # A qualified capture always states its decision-aware status.
            if row.get("redistribution_status") != "allowed":
                return False, source, "the capture's owner source-use decision is not recorded"
        elif source != registry:
            return False, source, "provenance source does not match this input's registry identity"
        if not source_allowed(source, surface=self.surface):
            reason = WITHHELD_SOURCES.get(source)
            entry = SOURCES.get(source) if isinstance(source, str) else None
            if entry is not None and "decision" in entry:
                reason = "the owner source-use decision for this source is not recorded"
            return False, source, reason or "reuse terms for this source are not established"
        status = row.get("redistribution_status")
        if status is not None and status != "allowed":
            return False, source, "provenance does not allow redistribution"
        return True, source, None

    def verdict(self, inputs: tuple[str, ...] | None) -> dict:
        if inputs is None:
            return {"status": "withheld", "sources": [], "restricted_inputs": [
                {"input": None, "source": None, "reason": "no reviewed input declaration"}]}
        sources, restricted = [], []
        for item in inputs:
            ok, source, reason = self.check(item)
            if ok:
                sources.append(source)
            else:
                restricted.append({"input": item, "source": source, "reason": reason})
        return {"status": "withheld" if restricted else "public",
                "sources": sorted(set(sources)), "restricted_inputs": restricted}


def _withheld(name: str, verdict: Mapping) -> dict:
    """A withheld reading: no values and no observation dates, only why."""
    restricted = [row for row in verdict.get("restricted_inputs", []) if isinstance(row, Mapping)]
    names = sorted({str(row.get("input")) for row in restricted if row.get("input")})
    urls = [TERMS] if any(row.get("source") in (None, "fred") for row in restricted) else []
    urls += sorted({SOURCES[row["source"]]["terms_url"] for row in restricted
                    if row.get("source") in SOURCES})
    out = {
        "ok": False, "status": "restricted", "availability": "withheld",
        "publication_allowed": False, "rights_status": "redistribution_review_required",
        "policy": POLICY, "values_reconstructed": False,
        "reason": (f"{name} is withheld because it reads inputs whose public redistribution is not "
                   f"established in this snapshot: {', '.join(names) or 'undeclared inputs'}. "
                   + WITHHELD_NOTE),
        "restricted_inputs": names, "rights_urls": urls or [TERMS],
    }
    return out


def _source_notes(source_ids: set[str]) -> dict[str, dict]:
    out = {}
    for source in sorted(source_ids):
        entry = SOURCES.get(source)
        if entry is None:
            continue
        note = {key: entry[key] for key in ("label", "publisher", "terms_url", "attribution", "basis")}
        if entry.get("surfaces"):
            note["surfaces"] = entry["surfaces"]
        if entry.get("decision"):
            note["decision"] = source_decisions.summary(entry["decision"])
        out[source] = note
    return out


# ---------------------------------------------------------------------------
# Field-level projections
# ---------------------------------------------------------------------------


def metadata(value: object, *, reason: str = REASON, urls: list[str] | None = None) -> dict:
    node = value if isinstance(value, Mapping) else {}
    out = {key: item for key, item in node.items()
           if key in METADATA and (item is None or isinstance(item, str))}
    alignment = node.get("alignment")
    if isinstance(alignment, dict):
        out["alignment"] = {key: alignment[key] for key in ("method", "no_forward_fill") if key in alignment}
    out.update(value=None, status="restricted", availability="metadata_only",
               publication_allowed=False, redistribution_status="metadata_only",
               rights_status="redistribution_review_required", rights_reason=reason,
               rights_urls=list(urls) if urls else [TERMS])
    return out


_CARD_FIELDS = METADATA | {
    "value", "status", "robust_z_1y", "robust_z_1y_n", "percentile_3y",
    "percentile_3y_n", "change_1d", "change_5d", "change_20d", "change_1w",
    "change_4w", "change_13w", "change_1m", "change_3m", "change_12m",
    "change_basis", "change_unit", "alignment", "unavailable_reason",
    "age_days_vs_evaluation_asof", "age_days_vs_desk_asof",
    "freshness_policy", "publication_schedule", "source_ids",
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
# Money desk source ids that name a direct acquisition, by registry source.
_DESK_SOURCE_PREFIXES = {
    "nyfed_original": "nyfed_original", "fed_prates": "fed_prates", "fed_cp": "fed_cp",
    "fed_h15": "fed_h15", "fed_h41": "fed_h41", "nyfed_sofr_rate": "nyfed_rates",
    "ofr": "ofr", "nyfed_srf": "nyfed_srf",
}


def _desk_source(source_id: object) -> str | None:
    if not isinstance(source_id, str):
        return None
    return _DESK_SOURCE_PREFIXES.get(source_id.split(":", 1)[0])


def _desk_id_allowed(source_id: object) -> bool:
    return source_allowed(_desk_source(source_id))


def _direct_card(row: dict) -> bool:
    ident, source = row.get("id"), row.get("source")
    ids = row.get("source_ids")
    if ids is not None:
        # The engine names every leg of a FRED-shaped card when it knows the
        # real acquisitions; each leg must be an allowed direct source.
        from seiche.engines.money_market import CARD_INPUTS

        return (isinstance(ids, list) and ident in CARD_INPUTS
                and len(ids) == len(CARD_INPUTS[ident]) and all(_desk_id_allowed(i) for i in ids))
    if ident in _DIRECT:
        return source == _DIRECT[ident]
    if ident in _OFR_IDS:
        return source == "OFR Short-Term Funding Monitor"
    parts = ident.split(".") if isinstance(ident, str) else []
    return (len(parts) == 3 and parts[0] == "distribution"
            and parts[1] in {"sofr", "tgcr", "bgcr"}
            and parts[2] in _DISTRIBUTION_FIELDS
            and source == f"NY Fed Markets API {parts[1].upper()} distribution")


def _chart(name: str, value: object, *, every_column: bool = False) -> dict:
    node = value if isinstance(value, Mapping) else {}
    columns, rows = node.get("columns"), node.get("rows")
    if ((name not in _CHART_COLUMNS and not every_column) or not isinstance(columns, list)
            or not columns or columns[0] != "date"
            or any(not isinstance(column, str) for column in columns)
            or len(columns) != len(set(columns))
            or not isinstance(rows, list)
            or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        return metadata(node)
    allowed = set(columns) if every_column else _CHART_COLUMNS[name]
    out = {key: copy.deepcopy(node[key]) for key in
           ("id", "title", "cadence", "row_limit", "no_forward_fill") if key in node}
    out["columns"] = list(columns)
    out["rows"] = [[copy.deepcopy(item) if column in allowed else None
                    for column, item in zip(columns, row)] for row in rows]
    out["withheld_columns"] = [column for column in columns if column not in allowed]
    return out


def _desk_source_row_withheld(row: Mapping) -> bool:
    """FRED copies, and direct ids whose source is not (or no longer) allowed."""
    ident = str(row.get("id", ""))
    if ident.startswith("fred_"):
        return True
    return ":" in ident and not _desk_id_allowed(ident)


def _desk_fully_direct(node: Mapping, sections: list) -> bool:
    supplied = node.get("input_sources")
    diagnostics = node.get("diagnostics")
    return (
        isinstance(supplied, Mapping) and bool(supplied)
        and all(isinstance(row, Mapping) and _desk_id_allowed(row.get("source_id")) for row in supplied.values())
        and all(_direct_card(row) for section in sections for row in section["metrics"])
        and isinstance(diagnostics, Mapping)
        and all(_desk_id_allowed(item) for item in diagnostics.get("source_ids") or [None])
    )


def _desk_notices(node: Mapping) -> list[dict]:
    supplied = node.get("input_sources") if isinstance(node.get("input_sources"), Mapping) else {}
    sources = {_desk_source(row.get("source_id")) for row in supplied.values() if isinstance(row, Mapping)}
    sources |= {"ofr", "nyfed_rates", "nyfed_srf", "tga"}
    return [{"source": note["label"], "terms_url": note["terms_url"], "attribution": note["attribution"]}
            for source, note in _source_notes({s for s in sources if s and source_allowed(s)}).items()]


def _merged_notices(node: Mapping) -> list:
    """The desk's own notices plus each allowed source's, once (idempotent)."""
    existing = node.get("legal_notices") if isinstance(node.get("legal_notices"), list) else []
    out = copy.deepcopy(existing)
    out.extend(note for note in _desk_notices(node) if note not in out)
    return out


def money_desk(value: object) -> dict:
    """Project a saved USD desk without recomputation or source relabelling.

    Required funding cards retain their exact identity, date, unit and source
    even when restricted. A card is public when each of its inputs is an
    allowed direct acquisition; the desk-level regime, narratives, diagnostics
    and charts are public only when every desk input is.
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
    if _desk_fully_direct(node, sections):
        out = copy.deepcopy(dict(node))
        out["legal_notices"] = _merged_notices(node)
        out["publication_projection"] = {"policy": POLICY, "status": "public",
                                         "reason": "Every desk input is an allowed direct acquisition.",
                                         "original_snapshot_unchanged": True}
        return out
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
    out["source_metadata"] = [
        metadata(row) if _desk_source_row_withheld(row)
        else {key: copy.deepcopy(item) for key, item in row.items()
              if key in METADATA or key in {"available", "observations", "age_days_vs_desk_asof", "age_days_vs_evaluation_asof"}}
        for row in sources if isinstance(row, dict)]
    out["sources"] = copy.deepcopy(out["source_metadata"])
    out["legal_notices"] = _merged_notices(node)
    out["caveats"] = [REASON, "Retained direct-source observations keep their original clocks; missing values are not treated as calm."]
    return out


# ---------------------------------------------------------------------------
# The legacy board, projected per engine
# ---------------------------------------------------------------------------

PROJECTION_SCHEMA = "seiche.public-board-projection.v1"


def is_projection(value: object) -> bool:
    return (isinstance(value, Mapping) and isinstance(value.get("rights_projection"), Mapping)
            and value["rights_projection"].get("schema") == PROJECTION_SCHEMA)


def _project_blocks(blocks: Mapping, table: Mapping[str, tuple[str, ...]], ledger: _Ledger,
                    verdicts: dict, used: set[str], *, prefix: str) -> dict:
    out = {}
    for name, value in blocks.items():
        if not isinstance(name, str) or name.startswith("_"):
            continue
        if prefix == "deep" and name in _DEEP_METADATA:
            out[name] = copy.deepcopy(value)
            continue
        if prefix == "engines" and name == "money_market":
            desk = money_desk(value)
            public = (desk.get("publication_projection") or {}).get("status") == "public"
            verdicts[name] = ledger.verdict(table.get(name)) if public else {
                "status": "card_projection", "sources": [], "restricted_inputs": []}
            if public:
                used.update(verdicts[name]["sources"])
            out[name] = desk
            continue
        verdict = ledger.verdict(table.get(name))
        verdicts[name] = verdict
        if verdict["status"] == "public":
            used.update(verdict["sources"])
            out[name] = copy.deepcopy(value)
        else:
            out[name] = _withheld(f"{prefix}.{name}", verdict)
    return out


def public_board(snapshot: object, *, surface: str = "free") -> dict:
    """The legacy board with each reading public only when its inputs are.

    The completed snapshot is never mutated. Unknown sections, engines and
    deep blocks are withheld; editorial prose, the Navigator and the alert log
    combine every reading and stay withheld.
    """
    if is_restriction(snapshot):
        return copy.deepcopy(dict(snapshot))
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("engines"), Mapping):
        return legacy_unavailable("/api/overview")
    ledger = _Ledger(snapshot, surface)
    used: set[str] = set()
    engines: dict = {}
    deep_verdicts: dict = {}
    out = {key: copy.deepcopy(snapshot[key]) for key in
           ("generated_at", "version", "historical_evidence", "faults", "provenance", "data_quality")
           if key in snapshot}
    out["engines"] = _project_blocks(snapshot["engines"], ENGINE_INPUTS, ledger, engines, used, prefix="engines")
    deep = snapshot.get("deep")
    out["deep"] = (_project_blocks(deep, DEEP_INPUTS, ledger, deep_verdicts, used, prefix="deep")
                   if isinstance(deep, Mapping) else {})
    headline = snapshot.get("headline") if isinstance(snapshot.get("headline"), Mapping) else {}
    out["headline"], headline_verdicts = {}, {}
    for key, value in headline.items():
        verdict = ledger.verdict(HEADLINE_INPUTS.get(key))
        headline_verdicts[key] = verdict
        out["headline"][key] = copy.deepcopy(value) if verdict["status"] == "public" else None
        if verdict["status"] == "public":
            used.update(verdict["sources"])
    calendar = snapshot.get("calendar") if isinstance(snapshot.get("calendar"), Mapping) else {}
    out["calendar"] = {}
    for key, value in calendar.items():
        verdict = ledger.verdict(CALENDAR_INPUTS.get(key))
        out["calendar"][key] = copy.deepcopy(value) if verdict["status"] == "public" else None
    for section in WITHHELD_SECTIONS:
        if section in snapshot:
            out[section] = _withheld(section, {"restricted_inputs": [
                {"input": "every board reading", "source": None, "reason": "combines withheld readings"}]})
    system = snapshot.get("system") if isinstance(snapshot.get("system"), Mapping) else None
    if system is not None:
        out["system"] = {}
        if "editorial_config" in system:
            out["system"]["editorial_config"] = copy.deepcopy(system["editorial_config"])
        if "alert_log" in system:
            out["system"]["alert_log"] = {"status": "restricted", "alerts": [],
                                          "reason": "Alerts combine withheld readings. " + WITHHELD_NOTE}
    out["rights_projection"] = {
        "schema": PROJECTION_SCHEMA, "policy": POLICY, "original_snapshot_unchanged": True,
        "rule": ("A reading is public only when every input it reads is an allowed direct or open "
                 "source proven by this snapshot's provenance. Anything else is withheld, never "
                 "reconstructed. " + WITHHELD_NOTE),
        "engines": engines, "deep": deep_verdicts,
        "headline": {key: verdict["status"] for key, verdict in headline_verdicts.items()},
        "withheld_sections": [s for s in (*WITHHELD_SECTIONS, "system.alert_log")
                              if s.split(".")[0] in snapshot],
        "sources": _source_notes(used),
        "decisions": [source_decisions.summary(row["id"]) for row in source_decisions.registry()["decisions"]],
    }
    return out


_MEMO: dict = {"src": None, "board": None}


def cached_public_board(snapshot: object) -> dict:
    """``public_board`` memoised on the completed snapshot object itself.

    Completed snapshots are immutable once published; a rebuild is a new
    object, so identity is the cache key and a stale projection cannot leak.
    """
    if _MEMO["src"] is not snapshot or _MEMO["board"] is None:
        _MEMO.update(src=snapshot, board=public_board(snapshot))
    return _MEMO["board"]


def public_engine(snapshot: object, name: str) -> dict | None:
    """One projected engine, or None when the snapshot has no such engine."""
    board = cached_public_board(snapshot)
    engines = board.get("engines") if isinstance(board.get("engines"), Mapping) else {}
    if name not in engines:
        return None
    block = copy.deepcopy(engines[name])
    verdict = (board.get("rights_projection") or {}).get("engines", {}).get(name, {})
    if isinstance(block, dict) and verdict.get("status") == "public":
        block["publication_rights"] = {
            "policy": POLICY, "status": "public",
            "sources": _source_notes(set(verdict.get("sources") or [])),
        }
    return block


def is_withheld_block(value: object) -> bool:
    return (isinstance(value, Mapping) and value.get("availability") == "withheld"
            and value.get("publication_allowed") is False)


# Explicit release scope. These paths combine every reading into prose,
# forecast records, replays or the Book, or read inputs this release cannot
# qualify. Authentication, payment, an old snapshot, or a successful request
# cannot grant reuse rights.
LEGACY_TOOLS = frozenset({
    "latest_article", "funding_stress_forecast", "replay_asof", "proof_backtest",
    "crypto_stress_record", "institutional_flows", "oil_funding_context",
    "fx_materials_passage", "positioning_book", "desk_brief", "ask_desk",
})
# Tools served from the per-engine projection, with the readings they need.
PROJECTED_TOOLS = {
    "funding_stress_now": ("engines", "composite"),
    "historical_analogs": ("deep", "tidetables"),
    "trade_safety_risk_context": ("engines", "composite"),
}
LEGACY_PATHS = frozenset({
    "/api/public", "/api/oil-funding", "/api/estuary", "/api/wrecks", "/api/book",
    "/api/brief", "/api/ask", "/api/event-analysis", "/api/pit", "/api/alerts",
    "/api/badge/record",
})
LEGACY_PREFIXES = ("/api/asof/", "/api/dispatch/")
# Board routes served from the projection; engine routes are checked per
# engine before any snapshot loads.
PROJECTED_PATHS = frozenset({"/api/overview", "/api/gauge", "/api/deep", "/api/trade-safety/risk-context"})
ENGINE_PREFIX = "/api/engines/"
RESTRICTION_SCHEMA = "seiche.source-rights-restriction.v1"
# Static publication files are copies of these API surfaces and inherit their
# policy. A public file carries exactly what its API route may serve.
STATIC_SURFACES = {
    "/data/overview.json": "/api/overview",
    "/data/public.json": "/api/public",
    "/data/book_history.json": "/api/book",
}


def legacy_http_restricted(path: str) -> bool:
    normalized = path.rstrip("/")
    if normalized.startswith(ENGINE_PREFIX):
        name = normalized[len(ENGINE_PREFIX):]
        # Known engines that read a blocked or unqualified input are refused
        # before collection. Others are checked against the snapshot itself.
        return name in ENGINE_INPUTS and not engine_can_be_public(name)
    return normalized in LEGACY_PATHS or any(
        normalized.startswith(prefix) for prefix in LEGACY_PREFIXES
    )


def static_restriction(path: str) -> dict | None:
    """The payload a static publication file must carry instead of board values.

    None means its API surface is open, so the caller publishes the surface's
    projection (``static_board``), never the raw board.
    """
    return legacy_unavailable(path) if legacy_http_restricted(STATIC_SURFACES[path]) else None


def static_board(path: str, snapshot: object) -> dict:
    """Exactly what a static publication file may carry for this snapshot."""
    withheld = static_restriction(path)
    if withheld is not None:
        return withheld
    if STATIC_SURFACES[path] == "/api/overview":
        return public_board(snapshot)
    return legacy_unavailable(path)


def is_restriction(value: object) -> bool:
    return (isinstance(value, Mapping) and value.get("schema") == RESTRICTION_SCHEMA
            and value.get("category") == "source_rights_restricted")


def legacy_unavailable(surface: str) -> dict:
    return {
        "schema": RESTRICTION_SCHEMA, "ok": False,
        "status": "FAILED", "category": "source_rights_restricted",
        "availability": "unavailable", "rights_status": "redistribution_review_required",
        "publication_allowed": False, "surface": surface, "policy": POLICY,
        "reason": "This legacy feature is unavailable while its source reuse rights and replacement acquisitions are qualified. " + REASON,
        "rights_urls": [TERMS], "context_only": True,
        "values_reconstructed": False, "execution_authority": "none",
        "available_research": ["money_market_context", "market_workbench", "world_markets_context", "data_health"],
    }


def withheld_restriction(surface: str, block: Mapping) -> dict:
    """A projected reading that is withheld, in the restriction envelope."""
    out = legacy_unavailable(surface)
    out["reason"] = str(block.get("reason") or out["reason"])
    out["restricted_inputs"] = list(block.get("restricted_inputs") or [])
    out["rights_urls"] = list(block.get("rights_urls") or [TERMS])
    return out


def rights_status() -> dict:
    return {
        "schema": "seiche.source-rights-status.v1", "status": "partial_release",
        "policy": POLICY, "prior_policy": PRIOR_POLICY, "context_only": True,
        "available_tools": ["source_rights_status", "money_market_context", "market_workbench",
                            "world_markets_context", "gift_city_context", "gold_inventory_carry",
                            "research_network", "data_health", *sorted(PROJECTED_TOOLS)],
        "projected_tools": {name: ".".join(path) for name, path in sorted(PROJECTED_TOOLS.items())},
        "unavailable_tools": sorted(LEGACY_TOOLS), "rights_urls": [TERMS],
        "rule": ("A reading is public only when every input it reads is an allowed direct or open "
                 "source; readings that read a FRED copy, a blocked proprietary series or an "
                 "unqualified source stay withheld."),
        "publicly_projectable_engines": sorted(name for name in ENGINE_INPUTS if engine_can_be_public(name)),
        "publicly_projectable_deep": sorted(name for name in DEEP_INPUTS
                                            if engine_can_be_public(name, DEEP_INPUTS)),
        "sources": _source_notes({name for name in SOURCES if source_allowed(name)}),
        "withheld_sources": dict(sorted(WITHHELD_SOURCES.items())),
        "decisions": [source_decisions.summary(row["id"]) for row in source_decisions.registry()["decisions"]],
        "boundary": "Available tools still report field-specific restrictions, missing values and source clocks. Availability is not a blanket reuse license, freshness certification or investment advice. ECB FX is the default workbench provider. Gold carry is arithmetic over caller-supplied assumptions.",
    }
