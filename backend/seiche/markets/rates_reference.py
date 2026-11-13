"""Declared official rate sources for the rates and funding research routes.

Each source here rests on its publisher's own reuse terms, quoted below with the
date they were read. Declarations are pure data: no network, no adapters.
Curve nodes are the tenors the publisher prints; nothing is interpolated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time

from seiche.domain.observation import (
    ConnectorClassification, DayCountConvention, RateCompounding,
    RedistributionStatus, SemanticRole,
)
from seiche.markets.base import (
    MarketPack, PublicationClock, PublicationClockPrecision, SourceAdapterSpec,
)
from seiche.markets.reference import rate_instrument


ECB_TERMS_URL = "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html"
# ECB disclaimer and copyright notice, read 2026-11-13.
ECB_TERMS_QUOTE = (
    "users of this website may make free use of the information obtained directly from it "
    "subject to the following conditions: When such information is distributed or reproduced, "
    "it must appear accurately and the ECB must be cited as the source. ... If the information "
    "is modified by the user ... this must be stated explicitly."
)
ECB_ATTRIBUTION = (
    "Source: European Central Bank (ECB Data Portal). Values are reproduced as published; "
    "spreads, slopes, forwards and percentiles are Seiche calculations from those values."
)

RATES_SOURCES = {
    "ecb_yield_curve": {
        "publisher": "European Central Bank",
        "source_url": "https://data.ecb.europa.eu/data/datasets/YC",
        "rights_url": ECB_TERMS_URL,
        "terms_url": ECB_TERMS_URL,
        "terms_read_on": "2026-11-13",
        "licence": "ECB disclaimer and copyright: free use with accurate reproduction and ECB attribution",
        "terms": ("Free reuse when the values appear accurately and the ECB is cited as the source; "
                  "modifications must be stated."),
        "terms_quote": ECB_TERMS_QUOTE,
        "attribution": ECB_ATTRIBUTION,
        "methodology_url": ("https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/"
                            "euro_area_yield_curves/html/index.en.html"),
        "publication_rule": ("Updated every TARGET business day at noon (12:00 CET) for the previous "
                             "TARGET business day; an update may be delayed to the next business day."),
    },
}


@dataclass(frozen=True)
class RateCurve:
    """A publisher curve: printed tenors only, one instrument per node."""

    curve_id: str
    market_id: str
    currency: str
    adapter_id: str
    label: str
    kind: str
    compounding: str | None
    cadence: str
    nodes: tuple[tuple[str, float, str], ...]
    methodology: str
    remote_keys: tuple[tuple[str, str], ...]

    @property
    def node_map(self) -> dict[str, str]:
        return {tenor: identifier for tenor, _, identifier in self.nodes}

    @property
    def tenor_years(self) -> dict[str, float]:
        return {tenor: years for tenor, years, _ in self.nodes}


ECB_YC_TENORS = (
    ("3M", 0.25), ("6M", 0.5), ("1Y", 1.0), ("2Y", 2.0), ("3Y", 3.0), ("5Y", 5.0),
    ("7Y", 7.0), ("10Y", 10.0), ("15Y", 15.0), ("20Y", 20.0), ("30Y", 30.0),
)
# The first published date of the daily euro area curves (ECB yield curve page).
ECB_YC_FIRST_DATE = "2004-09-06"
_ECB_YC_SAMPLES = (
    ("EA_AAA_SPOT", "AAA", "G_N_A",
     "Euro area AAA government bond spot curve (ECB)",
     "ECB Svensson model fitted to euro area central government bonds rated triple A; "
     "spot rates, continuous compounding, daily on TARGET business days."),
    ("EA_ALL_SPOT", "ALL", "G_N_C",
     "Euro area all issuers government bond spot curve (ECB)",
     "ECB Svensson model fitted to all euro area central government bonds, all ratings "
     "included; spot rates, continuous compounding, daily on TARGET business days."),
)


def _ecb_yc_curve(curve_id: str, sample: str, code: str, label: str, method: str) -> RateCurve:
    nodes = tuple((tenor, years, f"EA.ECB.YC_{sample}_SPOT_{tenor}") for tenor, years in ECB_YC_TENORS)
    return RateCurve(
        curve_id=curve_id, market_id="EA-EUR", currency="EUR", adapter_id="ecb_yield_curve",
        label=label, kind="zero_coupon_spot", compounding="continuous", cadence="P1D",
        nodes=nodes, methodology=method,
        remote_keys=tuple((identifier, f"YC.B.U2.EUR.4F.{code}.SV_C_YM.SR_{tenor}")
                          for tenor, _, identifier in nodes),
    )


# Curves whose instruments and adapters are declared by their own packs; these
# routes only read them. Conventions are the publishers' own; no forwards are
# computed for par or constant-maturity curves.
EXISTING_CURVES = (
    RateCurve("US_TREASURY_PAR", "US-USD", "USD", "treasury_par_yields",
              "US Treasury par yield curve (U.S. Department of the Treasury)", "par_yield",
              "semiannual bond-equivalent", "P1D",
              tuple((t, y, f"US.TREASURY.PAR_YIELD_{t}") for t, y in (
                  ("1M", 1 / 12), ("3M", 0.25), ("6M", 0.5), ("1Y", 1.0), ("2Y", 2.0),
                  ("5Y", 5.0), ("10Y", 10.0), ("30Y", 30.0))),
              "Treasury daily par yield curve rates, bond-equivalent basis, read from Treasury's own "
              "daily text view. Par yields are not zero-coupon rates; no forwards are derived.", ()),
    RateCurve("JP_MOF_CMT", "JP-JPY", "JPY", "jp_mof_curve",
              "Japanese government bond constant maturity yields (Ministry of Finance Japan)",
              "constant_maturity_yield", "semiannual", "P1D",
              tuple((f"{t}Y", float(t), f"JP.MOF.JGB_{t}Y") for t in (*range(1, 11), 15, 20, 25, 30, 40)),
              "MOF constant-maturity JGB yields, semiannually compounded. Source-calculated maturity "
              "references, not individual bond quotes; no forwards are derived.", ()),
    RateCurve("GB_BOE_ZERO", "UK-GBP", "GBP", "boe_sovereign_curve",
              "UK nominal government zero coupon spot yields (Bank of England)", "zero_coupon_spot",
              None, "P1D",
              tuple((f"{t}Y", float(t), f"GB.BOE.ZERO_COUPON_{t}Y") for t in (5, 10, 20)),
              "Bank of England estimated nominal zero coupon spot yields at three published tenors. "
              "The compounding convention is not carried with these rows, so no forwards are derived.", ()),
)
DECLARED_CURVES = tuple(_ecb_yc_curve(*sample) for sample in _ECB_YC_SAMPLES)
RATE_CURVES = DECLARED_CURVES + EXISTING_CURVES
CURVE_MAP = {curve.curve_id: curve for curve in RATE_CURVES}
ECB_YC_KEYS = {identifier: key for curve in RATE_CURVES if curve.adapter_id == "ecb_yield_curve"
               for identifier, key in curve.remote_keys}

_LABEL_SAMPLE = {"AAA": "Euro area AAA government spot rate", "ALL": "Euro area all issuers government spot rate"}


def _tenor_words(tenor: str) -> str:
    number, unit = int(tenor[:-1]), tenor[-1]
    word = "month" if unit == "M" else "year"
    return f"{number} {word}{'' if number == 1 else 's'}"


INSTRUMENT_LABELS = {
    identifier: f"{_LABEL_SAMPLE[identifier.split('_')[1]]}, {_tenor_words(tenor)}"
    for curve in RATE_CURVES if curve.adapter_id == "ecb_yield_curve"
    for tenor, _, identifier in curve.nodes
}
INSTRUMENT_TENORS = {identifier: (tenor, years) for curve in RATE_CURVES
                     for tenor, years, identifier in curve.nodes}

# Spreads with a stated formula. Legs are (instrument_id, sign); both legs must be
# observed on the same date, nothing is filled.
@dataclass(frozen=True)
class RateSpread:
    spread_id: str
    label: str
    market_id: str
    legs: tuple[tuple[str, int], ...]
    kind: str

    @property
    def formula(self) -> str:
        (long_id, _), (short_id, _) = self.legs
        return (f"{long_id} minus {short_id} on the same observation date, in basis points; "
                "dates missing either leg are skipped, nothing is filled")


def _slope(curve: RateCurve, short: str, long: str, name: str) -> RateSpread:
    nodes = curve.node_map
    prefix = curve.curve_id.removesuffix("_SPOT")
    return RateSpread(f"{prefix}_{name}",
                      f"{curve.label.split(' (')[0]}, {long} minus {short}",
                      curve.market_id, ((nodes[long], 1), (nodes[short], -1)), "curve_slope")


_SLOPES = {
    "EA_AAA_SPOT": (("2Y", "10Y", "2S10S"), ("3M", "10Y", "3M10Y"), ("5Y", "30Y", "5S30S")),
    "EA_ALL_SPOT": (("2Y", "10Y", "2S10S"), ("3M", "10Y", "3M10Y"), ("5Y", "30Y", "5S30S")),
    "US_TREASURY_PAR": (("2Y", "10Y", "2S10S"), ("3M", "10Y", "3M10Y"), ("5Y", "30Y", "5S30S")),
    "JP_MOF_CMT": (("2Y", "10Y", "2S10S"), ("10Y", "30Y", "10S30S")),
    "GB_BOE_ZERO": (("5Y", "20Y", "5S20S"),),
}
RATE_SPREADS = (
    *(_slope(CURVE_MAP[curve_id], short, long, name) for curve_id, pairs in _SLOPES.items()
      for short, long, name in pairs),
    *(RateSpread(f"EA_ALL_MINUS_AAA_{tenor}", f"Euro area all issuers minus AAA spot rate, {tenor}", "EA-EUR",
                 ((f"EA.ECB.YC_ALL_SPOT_{tenor}", 1), (f"EA.ECB.YC_AAA_SPOT_{tenor}", -1)), "issuer_sample_spread")
      for tenor in ("2Y", "10Y")),
)
SPREAD_MAP = {spread.spread_id: spread for spread in RATE_SPREADS}

# Forward rates computed only where the publisher states continuous compounding.
FORWARDS = (("1y1y", 1.0, 2.0), ("2y1y", 2.0, 3.0), ("5y5y", 5.0, 10.0), ("10y10y", 10.0, 20.0))

_ECB_YC_CLOCK = PublicationClock(
    "Europe/Berlin", time(12, 0), 1, PublicationClockPrecision.SCHEDULED, "TARGET"
)


def extend_rates_pack(pack: MarketPack) -> MarketPack:
    """Add the declared rate sources of one market without changing existing policy."""
    instruments = list(pack.instruments)
    adapters = list(pack.source_adapters)
    for curve in DECLARED_CURVES:
        if curve.market_id != pack.market_id:
            continue
        if not any(item.adapter_id == curve.adapter_id for item in adapters):
            adapters.append(SourceAdapterSpec(
                curve.adapter_id, ConnectorClassification.OFFICIAL_OPEN, curve.cadence,
                _ECB_YC_CLOCK, RedistributionStatus.ALLOWED, retry_limit=2,
            ))
        instruments.extend(rate_instrument(
            identifier, identifier.removeprefix("EA.").replace(".", "_"), SemanticRole.SOVEREIGN_YIELD,
            curve.adapter_id, DayCountConvention.SOURCE_NATIVE, compounding=RateCompounding.SOURCE_NATIVE,
        ) for _, _, identifier in curve.nodes)
    return replace(pack, instruments=tuple(instruments), source_adapters=tuple(adapters))
