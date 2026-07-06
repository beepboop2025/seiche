"""Seiche configuration: the codified-judgment layer.

Everything in this file is an *opinion* — weights, thresholds, episode dates,
contract constants. The math lives in engines/; the judgment lives here so it
can be tuned without touching engine code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "seiche.sqlite"

USER_AGENT = "seiche/0.1 (open-source funding-stress monitor)"

# ---------------------------------------------------------------------------
# Series registry: everything the collectors pull, with cadence-aware TTLs.
# freq: D=daily, W=weekly. ttl_minutes: how long a cached fetch stays fresh.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeriesSpec:
    mnemonic: str          # internal name
    source: str            # fred | nyfed | ofr | fiscaldata | cftc
    remote_id: str         # upstream identifier
    label: str
    unit: str
    freq: str = "D"
    ttl_minutes: int = 360


FRED_SERIES = [
    SeriesSpec("WALCL", "fred", "WALCL", "Fed total assets (H.4.1)", "$M", "W", 720),
    SeriesSpec("WRESBAL", "fred", "WRESBAL", "Reserve balances with Federal Reserve Banks", "$M", "W", 720),
    SeriesSpec("WTREGEN", "fred", "WTREGEN", "Treasury General Account (weekly avg)", "$M", "W", 720),
    SeriesSpec("RRPONTSYD", "fred", "RRPONTSYD", "ON RRP take-up", "$B", "D", 360),
    SeriesSpec("IORB", "fred", "IORB", "Interest on reserve balances", "%", "D", 720),
    SeriesSpec("IOER", "fred", "IOER", "Interest on excess reserves (pre-2021 splice leg)", "%", "D", 100000),
    SeriesSpec("EFFR", "fred", "EFFR", "Effective federal funds rate", "%", "D", 360),
    SeriesSpec("SOFR", "fred", "SOFR", "Secured overnight financing rate", "%", "D", 360),
    SeriesSpec("BGCR", "fred", "BGCR", "Broad general collateral rate", "%", "D", 360),
    SeriesSpec("TGCR", "fred", "TGCR", "Tri-party general collateral rate", "%", "D", 360),
    SeriesSpec("GDP", "fred", "GDP", "Nominal GDP (SAAR)", "$B", "Q", 10080),
]

OFR_SERIES = [
    SeriesSpec("DVP_VOL", "ofr", "REPO-DVP_TV_TOT-P", "DVP repo total volume (preliminary)", "$B", "D", 360),
    SeriesSpec("TRI_VOL", "ofr", "REPO-TRI_TV_TOT-P", "Tri-party repo total volume (preliminary)", "$B", "D", 360),
    SeriesSpec("DVP_RATE_OO", "ofr", "REPO-DVP_AR_OO-P", "DVP overnight/open avg rate", "%", "D", 360),
    SeriesSpec("TRI_RATE_OO", "ofr", "REPO-TRI_AR_OO-P", "Tri-party overnight/open avg rate", "%", "D", 360),
    SeriesSpec("MMF_TOT", "ofr", "MMF-MMF_TOT-M", "Money market fund total assets", "$B", "M", 10080),
    SeriesSpec("MMF_REPO_FICC", "ofr", "MMF-MMF_RP_wFICC-M", "MMF repo with FICC (sponsored)", "$B", "M", 10080),
    SeriesSpec("MMF_REPO_FED", "ofr", "MMF-MMF_RP_wFR-M", "MMF repo with the Fed (RRP)", "$B", "M", 10080),
    SeriesSpec("MMF_REPO_TOT", "ofr", "MMF-MMF_RP_TOT-M", "MMF total repo lending", "$B", "M", 10080),
]

# NY Fed + FiscalData + CFTC are fetched through dedicated collectors
# (structured payloads, not single series). TTLs below.
NYFED_TTL_MIN = 240        # rates with percentiles, SRF ops
FISCAL_TTL_MIN = 360       # daily TGA, auctions
CFTC_TTL_MIN = 1440        # COT is weekly; daily check is plenty

ALL_SERIES: dict[str, SeriesSpec] = {s.mnemonic: s for s in FRED_SERIES + OFR_SERIES}

# ---------------------------------------------------------------------------
# Staleness classification (fail-loud provenance).
# Age is measured against the series' expected cadence.
# ---------------------------------------------------------------------------

STALENESS_GRACE_DAYS = {"D": 4, "W": 10, "M": 45, "Q": 120}

# ---------------------------------------------------------------------------
# Episode library for the Echo Engine: date the stress *peaked/broke*.
# The engine matches today's trajectory against windows ENDING `lead` days
# before each episode date.
# ---------------------------------------------------------------------------

EPISODES = {
    "2019-09-17": "Sep 2019 repo spike (SOFR 5.25%, GC 10%)",
    "2020-03-16": "Mar 2020 dash-for-cash",
    "2023-03-13": "Mar 2023 SVB / regional bank run",
    "2025-04-09": "Apr 2025 tariff shock basis unwind",
    "2025-09-15": "Sep 2025 tax-date squeeze (SOFR +18bp over EFFR)",
    "2025-12-31": "Dec 2025 year-end squeeze (SRF $74.6B record)",
}
ECHO_WINDOW = 30           # business days of trajectory to match
ECHO_LEADS = range(0, 31)  # how many days before the episode the window ends

# ---------------------------------------------------------------------------
# CFTC TFF: Treasury futures contract constants for the RV X-Ray.
# face: contract face value ($). dv01: rough per-contract dollar value of 1bp
# (approximate CTD DV01s; transparent, tunable — these drive the margin-shock
# simulator, not any displayed "size" number).
# ---------------------------------------------------------------------------

TFF_DATASET = "gpe5-46if"  # Traders in Financial Futures, futures-only (Socrata)

UST_CONTRACTS = {
    "UST 2Y NOTE":     {"face": 200_000, "dv01": 38.0},
    "UST 5Y NOTE":     {"face": 100_000, "dv01": 43.0},
    "UST 10Y NOTE":    {"face": 100_000, "dv01": 64.0},
    "ULTRA UST 10Y":   {"face": 100_000, "dv01": 92.0},
    "UST BOND":        {"face": 100_000, "dv01": 180.0},
    "ULTRA UST BOND":  {"face": 100_000, "dv01": 265.0},
}

# ---------------------------------------------------------------------------
# Liquidity Weather.
# ---------------------------------------------------------------------------

WEATHER_HORIZON_BDAYS = 42          # ~6 weeks
CORPORATE_TAX_DAYS = {(3, 15), (4, 15), (6, 15), (9, 15), (12, 15)}
# Reserve cushion (in $B) above the estimated kink at which we start flagging
# crunch windows. Quarter-/year-end days get flagged at a wider cushion.
CRUNCH_CUSHION_B = 150.0
CRUNCH_CUSHION_QEND_B = 300.0

# ---------------------------------------------------------------------------
# Seiche Index: composite weights and regime thresholds.
# TUNING POINT — this is the tool's editorial voice. Weights sum to 1.
# Rationale for defaults: tails + spreads are the fastest-moving true signals
# (every 2025/26 episode), kink/weather capture the structural runway,
# positioning and auctions are slower fragility/amplifier terms.
# ---------------------------------------------------------------------------

COMPOSITE_WEIGHTS = {
    "tails": 0.24,        # Tail Seismograph (incl. SOFR-IORB pressure)
    "kink": 0.18,         # proximity to reserve-scarcity kink
    "weather": 0.16,      # forward crunch-window risk
    "srf": 0.12,          # SRF/SRP usage (the confession channel)
    "rvxray": 0.16,       # RV complex size/fragility
    "auctions": 0.08,     # supply digestion
    "buffers": 0.06,      # RRP buffer emptiness (0 = no shock absorber left)
}

REGIMES = [
    (25.0, "CALM"),
    (45.0, "EROSION"),
    (70.0, "STRAIN"),
    (100.1, "STRESS"),
]

# Echo similarity is reported alongside the index but not weighted into it:
# resemblance is context, not evidence. (Change of opinion welcome — add a
# weight above and wire engines/composite.py.)
