"""Seiche configuration: the codified-judgment layer.

Everything in this file is an *opinion* — weights, thresholds, episode dates,
contract constants. The math lives in engines/; the judgment lives here so it
can be tuned without touching engine code.

v2 ("Deep Water") adds: market-stress series (The Tell), discount-window
confession channel, primary-dealer warehouse, calendar-resonance engine,
hydrophone network, turn barometer, playbook, backtest lab, alert rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "seiche.sqlite"
BRIEF_DIR = DATA_DIR / "briefs"

USER_AGENT = "seiche/0.2 (open-source funding-stress monitor)"
# FRED's CDN bot-detection (verified 2026-07-06): custom UAs like "seiche/0.1"
# hang forever, and a Chrome UA over Python TLS gets tarpitted (JA3 mismatch).
# The one profile that consistently passes is httpx's own default UA — so the
# FRED collector sends NO custom User-Agent. Do not "fix" this by adding one.

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
    SeriesSpec("GDP", "fred", "GDP", "Nominal GDP (SAAR)", "$B", "Q", 10080),
    # Confession channel #2: banks borrowing at the discount window primary
    # credit rate — an even stronger admission than the SRF (stigma priced in).
    SeriesSpec("DISCOUNT_WINDOW", "fred", "WLCFLPCL", "Discount window primary credit (Wed level)", "$M", "W", 720),
]

# Market-priced stress (The Tell's price leg + Playbook outcomes) — all FRED,
# all keyless, deliberately official series rather than scraping quote APIs
# (Yahoo 429s, Stooq blocks bots — verified 2026-07-07; FRED is the honest
# contract). Outcomes are expressed in native units (Δbp, %, index pts).
MARKET_SERIES = [
    SeriesSpec("VIX", "fred", "VIXCLS", "CBOE VIX index", "pts", "D", 360),
    SeriesSpec("HY_OAS", "fred", "BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "%", "D", 360),
    SeriesSpec("IG_OAS", "fred", "BAMLC0A0CM", "ICE BofA US Corporate (IG) OAS", "%", "D", 360),
    SeriesSpec("DGS2", "fred", "DGS2", "2y Treasury constant maturity yield", "%", "D", 360),
    SeriesSpec("DGS10", "fred", "DGS10", "10y Treasury constant maturity yield", "%", "D", 360),
    SeriesSpec("DGS30", "fred", "DGS30", "30y Treasury constant maturity yield", "%", "D", 360),
    SeriesSpec("TB3M", "fred", "DTB3", "3-month T-bill secondary market rate", "%", "D", 360),
    SeriesSpec("TB4W", "fred", "DTB4WK", "4-week T-bill secondary market rate", "%", "D", 360),
    SeriesSpec("SP500", "fred", "SP500", "S&P 500 index", "pts", "D", 360),
    SeriesSpec("NFCI", "fred", "NFCI", "Chicago Fed National Financial Conditions Index", "z", "W", 720),
]

# Global basins (v2): the dollar system is one connected body of water.
# EUR basin from the ECB Data Portal (keyless CSV), UK from FRED's SONIA
# mirror, channels from H.4.1 (swap lines, foreign official RRP) + the broad
# dollar index. Basins we can NOT source keyless-and-reliable (Japan TONA,
# China, Russia, African markets) are stated as out of scope, not faked —
# the engine takes new basins here when a qualifying feed exists.
ECB_SERIES = [
    SeriesSpec("ESTR", "ecb", "EST/B.EU000A2X2A25.WT", "Euro short-term rate (€STR)", "%", "D", 360),
]

GLOBAL_FRED_SERIES = [
    SeriesSpec("ECB_DFR", "fred", "ECBDFR", "ECB deposit facility rate", "%", "D", 720),
    SeriesSpec("SONIA", "fred", "IUDSOIA", "SONIA (UK overnight rate)", "%", "D", 360),
    SeriesSpec("SWAP_LINES", "fred", "SWPT", "Central bank liquidity swaps outstanding (H.4.1)", "$M", "W", 720),
    SeriesSpec("DXY_BROAD", "fred", "DTWEXBGS", "Broad US dollar index", "idx", "D", 360),
    SeriesSpec("FOREIGN_RRP", "fred", "WLRRAFOIAL", "Reverse repo with foreign official accounts", "$M", "W", 720),
]

BASIN_WINDOW_D = 120           # rolling window for cross-basin coupling
BASIN_EDGE_MIN_ABS = 0.25      # min |lagged corr| for a cross-basin edge
SWAP_LINE_OPS_N = 90           # NY Fed FX-swap operations to pull

OFR_SERIES = [
    # TGCR/BGCR are 404 on FRED's CSV endpoint — sourced from OFR instead.
    SeriesSpec("BGCR", "ofr", "FNYR-BGCR-A", "Broad general collateral rate", "%", "D", 360),
    SeriesSpec("TGCR", "ofr", "FNYR-TGCR-A", "Tri-party general collateral rate", "%", "D", 360),
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
PD_TTL_MIN = 720           # primary dealer stats are weekly (Thu 4:15pm ET)

# History starts. Extended in v2 so the Time Machine and backtests can replay
# Sep-2019. (DTS covers TGA from 2019; auctions_query goes back decades.)
FRED_START = "2017-01-01"
NYFED_RATES_START = "2018-04-01"     # SOFR publication starts 2018-04
TGA_START = "2019-01-01"
AUCTIONS_START = "2018-01-01"
CFTC_START = "2018-01-01"

# NY Fed primary dealer stats: net outright positions, $M, weekly. The
# no-seriesbreak endpoint (/api/pd/get/{keyid}.json) returns the full spliced
# history (verified live 2026-07-07). Bills + coupon buckets = the dealer
# "warehouse" — how much duration the street is already sitting on.
PD_POSITION_SERIES = {
    "PDPOSGS-B": "Bills",
    "PDPOSGSC-L2": "Coupons <2y",
    "PDPOSGSC-G2L3": "Coupons 2-3y",
    "PDPOSGSC-G3L6": "Coupons 3-6y",
    "PDPOSGSC-G6L7": "Coupons 6-7y",
    "PDPOSGSC-G7L11": "Coupons 7-11y",
    "PDPOSGSC-G11L21": "Coupons 11-21y",
    "PDPOSGSC-G21": "Coupons >21y",
}

ALL_SERIES: dict[str, SeriesSpec] = {
    s.mnemonic: s
    for s in FRED_SERIES + MARKET_SERIES + GLOBAL_FRED_SERIES + OFR_SERIES + ECB_SERIES
}

# ---------------------------------------------------------------------------
# Staleness classification (fail-loud provenance).
# Age is measured against the series' expected cadence.
# ---------------------------------------------------------------------------

STALENESS_GRACE_DAYS = {"D": 4, "W": 10, "M": 45, "Q": 120}

# ---------------------------------------------------------------------------
# Episode library for the Echo Engine: date the stress *peaked/broke*.
# The engine matches today's trajectory against windows ENDING `lead` days
# before each episode date. Also reused by the backtest lab's event studies.
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

# Extra TFF contracts for the crowding panel (positioning z vs own history,
# normalized by open interest — no DV01 needed). Exact upstream names
# verified live 2026-07-07.
CROWD_EXTRA_CONTRACTS = ["FED FUNDS", "SOFR-1M", "SOFR-3M", "E-MINI S&P 500"]
CROWD_LOOKBACK_WEEKS = 156     # 3y window for crowding percentiles

# ---------------------------------------------------------------------------
# Liquidity Weather.
# ---------------------------------------------------------------------------

WEATHER_HORIZON_BDAYS = 42          # ~6 weeks
CORPORATE_TAX_DAYS = {(3, 15), (4, 15), (6, 15), (9, 15), (12, 15)}
# Reserve cushion (in $B) above the estimated kink at which we start flagging
# crunch windows. Quarter-/year-end days get flagged at a wider cushion.
CRUNCH_CUSHION_B = 150.0
CRUNCH_CUSHION_QEND_B = 300.0
# Auction settlements at/above this size ($B/day) get flagged on the path.
SETTLEMENT_FLAG_B = 90.0

# ---------------------------------------------------------------------------
# Resonance Engine — the seiche made literal.
# A seiche is a standing wave: the basin has resonant modes excited by
# calendar forcing (month-end window dressing, quarter-end balance-sheet
# snapshots, mid-month tax/settlement piles, year-end). We measure the
# amplitude of the market's response to each recurring forcing event and,
# critically, its TREND: a basin that rings louder to the same forcing is
# losing damping — structural fragility rising while levels still look calm.
# ---------------------------------------------------------------------------

RESONANCE_MODES = ["month_end", "quarter_end", "year_end", "mid_month", "tax_date"]
RESONANCE_PRE_BASELINE_D = 10   # business days before event for baseline median
RESONANCE_WINDOW_D = 1          # +/- window around event to catch the slosh
RESONANCE_DECAY_D = 5           # days after event over which decay is measured
RESONANCE_RECENT_N = 6          # last-N vs prior-N events per mode for the trend
RESONANCE_MIN_EVENTS = 6        # minimum events in a mode before we score it
RESONANCE_AMP_SATURATION = 2.5  # recent/prior slosh ratio that maps to max score

# ---------------------------------------------------------------------------
# Hydrophone Array — how connected is the plumbing right now?
# Absorption ratio (Kritzman): share of panel variance explained by the top
# principal components of rolling standardized daily changes. Decoupled
# segments absorb shocks; a densifying network transmits them.
# ---------------------------------------------------------------------------

HYDROPHONE_WINDOW_D = 120       # rolling window (business days)
HYDROPHONE_TOP_PCS = 2          # top-K PCs in the absorption ratio
HYDROPHONE_EDGE_MIN_ABS = 0.30  # min |lagged corr| to report a lead-lag edge
HYDROPHONE_MAX_LAG_D = 3

# ---------------------------------------------------------------------------
# SONAR — daily anomaly sweep across every stored series.
# Robust z = (last - trailing median) / (1.4826 * MAD). Flag |z| >= threshold
# on level or 1d change; rank by max |z|.
# ---------------------------------------------------------------------------

SONAR_LOOKBACK_D = 250
SONAR_Z_FLAG = 2.5
SONAR_TOP_N = 12

# ---------------------------------------------------------------------------
# Turn Barometer — forecast the severity of the NEXT month/quarter-end turn.
# Trained on history with leave-one-out CV; always reported against the naive
# baseline (same-mode trailing median). If we can't beat naive, we SAY so.
# ---------------------------------------------------------------------------

TURN_FEATURE_LAG_D = 5          # features frozen T-5 before the turn
TURN_MIN_HISTORY = 12           # minimum past turns before forecasting
TURN_SEVERITY_BINS = [3.0, 6.0, 12.0, 20.0]   # bp cutoffs -> severity 1..5

# ---------------------------------------------------------------------------
# The Tell — plumbing-vs-price divergence. The whole thesis in one number:
# plumbing percentile minus market-priced-stress percentile, -100..+100.
# Positive = the basin is sloshing and the screens haven't noticed.
# ---------------------------------------------------------------------------

TELL_MARKET_WEIGHTS = {         # market-priced stress index components
    "VIX": 0.35,
    "HY_OAS": 0.30,
    "IG_OAS": 0.15,
    "RATES_VOL": 0.20,          # 10d realized vol of DGS10 daily changes (bp)
}
TELL_PERCENTILE_WINDOW_D = 750  # ~3y expanding-capped percentile basis
TELL_ALERT_ABS = 30.0

# ---------------------------------------------------------------------------
# Playbook — state-conditioned forward outcome tables. NOT advice: honest
# historical distributions with n shown, in native units.
# ---------------------------------------------------------------------------

PLAYBOOK_HORIZONS_BD = [5, 20]
PLAYBOOK_OUTCOMES = {
    # mnemonic -> (label, kind)  kind: "pct" = % return, "diff" = unit change
    "SP500": ("S&P 500 return", "pct"),
    "VIX": ("VIX change (pts)", "diff"),
    "HY_OAS": ("HY OAS change (bp)", "diff_bp"),
    "IG_OAS": ("IG OAS change (bp)", "diff_bp"),
    "DGS10": ("10y yield change (bp)", "diff_bp"),
    "DGS2": ("2y yield change (bp)", "diff_bp"),
}
PLAYBOOK_MIN_N = 8              # below this sample size a cell renders as "n/a"

# ---------------------------------------------------------------------------
# Backtest lab (PROOF).
# The historical index is rebuilt with EXPANDING-window standardization only
# (no look-ahead in the z-scores) from non-revised market prints. Vintage
# caveat: weekly H.4.1 aggregates are lightly revised; stated on the page.
# ---------------------------------------------------------------------------

BACKTEST_SPIKE_BP = 10.0        # "funding event" = SOFR-IORB jumps >= this vs t-1 median
BACKTEST_EVENT_FWD_D = 5        # within this many business days
BACKTEST_ALERT_PCTL = 80.0      # index percentile treated as an "alert"
BACKTEST_MIN_WARMUP_D = 250     # expanding-z warmup before scoring starts

# ---------------------------------------------------------------------------
# FOMC calendar (static; update annually from federalreserve.gov —
# verified 2026-07-07). Dates are the DECISION day (second meeting day).
# ---------------------------------------------------------------------------

FOMC_DECISION_DATES = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]

# ---------------------------------------------------------------------------
# Seiche Index: composite weights and regime thresholds.
# TUNING POINT — this is the tool's editorial voice. Weights sum to 1.
# Rationale for defaults: tails + spreads are the fastest-moving true signals
# (every 2025/26 episode), kink/weather capture the structural runway,
# confession channels (SRF + discount window) are slower but unambiguous,
# resonance/hydrophone capture structural fragility invisible in levels,
# positioning/auctions/warehouse are amplifier terms, buffers the absorber.
# ---------------------------------------------------------------------------

COMPOSITE_WEIGHTS = {
    "tails": 0.18,        # Tail Seismograph (incl. SOFR-IORB pressure)
    "kink": 0.14,         # proximity to reserve-scarcity kink
    "weather": 0.12,      # forward crunch-window risk
    "confession": 0.12,   # SRF usage + discount window (paying up = admission)
    "rvxray": 0.12,       # RV complex size/fragility
    "resonance": 0.10,    # basin amplification (louder ring to same forcing)
    "hydrophone": 0.08,   # plumbing connectivity (shock transmission)
    "auctions": 0.06,     # supply digestion
    "warehouse": 0.04,    # dealer balance-sheet saturation
    "buffers": 0.04,      # RRP buffer emptiness (0 = no shock absorber left)
}

REGIMES = [
    (25.0, "CALM"),
    (45.0, "EROSION"),
    (70.0, "STRAIN"),
    (100.1, "STRESS"),
]

# Echo similarity and The Tell are reported alongside the index but not
# weighted into it: resemblance is context, divergence is a trading signal —
# neither is *evidence of stress* by itself. (Change of opinion welcome —
# add a weight above and wire engines/composite.py.)

# ---------------------------------------------------------------------------
# Alert rules (CLI `seiche alert` / `seiche watch`). Each rule fires once per
# distinct state (deduped via sqlite alert log), fail-loud on engine faults.
# ---------------------------------------------------------------------------

ALERT_RULES = {
    "regime_change": True,          # any regime transition
    "index_jump_5d": 8.0,           # composite +8 pts in 5 days
    "tail_z": 2.0,                  # blended tail z crosses this
    "srf_accepted_b": 5.0,          # any SRF take-up >= $5B
    "discount_window_b": 10.0,      # DW primary credit >= $10B
    "tell_abs": TELL_ALERT_ABS,     # |Tell| crosses threshold
    "crunch_within_d": 10,          # crunch window enters this horizon
    "turn_severity": 4,             # forecast turn severity >= this (1..5)
    "swap_line_usd_m": 1000.0,      # USD swap-line ops, 30d total >= $1B
    "engine_dead": True,            # any composite input DEAD
}
ALERT_WEBHOOK_ENV = "SEICHE_WEBHOOK_URL"   # optional POST target (Slack/TG/...)
