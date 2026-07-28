"""The Ampleness Check page: the reserve-ampleness checklist, its thresholds,
its verdict tokens and its honest degrades. Synthetic board payloads only, no
network, no filesystem outside tmp_path."""

import copy
import html
import json

from seiche import ampleness
from seiche.ampleness import AMPLE, SCARCE, WATCH
from seiche.dispatch_daily import lint_letter

# A board payload shaped like /api/overview, trimmed to the fields this page
# reads. Values are synthetic but shaped exactly like the live ones, including
# the em dashes the engines emit and this page has to normalize away.
_SNAP = {
    "generated_at": "2026-07-28T16:34:07+00:00",
    "version": "0.7.0 test",
    "headline": {
        "sofr_pct": {"value": 3.64, "asof": "2026-07-24"},
        "effr_pct": {"value": 3.63, "asof": "2026-07-24"},
        "iorb_pct": {"value": 3.65, "asof": "2026-07-28"},
        "reserves_b": {"value": 3062.149, "asof": "2026-07-22"},
        "rrp_b": {"value": 1.38, "asof": "2026-07-27"},
        "srf_accepted_b": {"value": 0.01, "asof": "2026-07-28"},
        "dw_b": {"value": 4.9, "asof": "2026-07-22"},
    },
    "engines": {
        "kink": {
            "ok": True,
            "predicted_spread_now_bp": -2.1,
            "observed_spread_now_bp": -3.7,
            "consistency": 0.87,
            "kink_reserves_b": 3634.4,
            "current_reserves_b": 3062.1,
            "distance_b": -572.3,
            "drain_per_bday_b": 2.39,
            "days_to_kink": None,
            "kink_ratio": 0.11405,
            "slope_bp_per_ratio": 328.1,
            "r2": 0.617,
            "asof": "2026-07-22",
        },
        "rdenowcast": {
            "ok": True,
            "ours_bp_per_1pct": -0.315,
            "nyfed_bp_per_1pct": -0.268,
            "nyfed_band_68": [-0.439, -0.093],
            "nyfed_band_95": [-0.592, 0.085],
            "divergence_bp": -0.047,
            "within_68_band": True,
            "nyfed_asof": "2026-07-06",
            "nowcast_lead_days": 16,
            "scorecard_summary": {"n": 18, "within_band": 8,
                                  "direction_agree": 10, "mean_abs_diff_bp": 0.265},
            "asof": "2026-07-22",
        },
        "tails": {
            "ok": True,
            "spread": {"sofr_iorb_bp": -1.0, "sofr_iorb_z": -0.22},
        },
        "breakwater": {"ok": True, "current": {"spread_pctl": 75.7}},
        "stigma": {
            "ok": True,
            "asof": "2026-07-27",
            "ceiling": {"source": "srf_rate", "latest_pct": 3.75},
            "bp_days_above_ceiling": {"p99_last_bp": 0.0, "p75_last_bp": 0.0,
                                      "p99_sum20_bp_days": 5.0,
                                      "p75_sum20_bp_days": 0.0,
                                      "days_above_20d": 1},
            "takeup": {"latest_b": 0.01, "asof": "2026-07-28", "max20_b": 0.1,
                       "share_of_facility_pct": 0.001, "facility_scale_b": 500.0,
                       "classification": "de_minimis"},
            "stigma_score": 9.5,
            "caveats": ["only P1/P25/P75/P99 are published, so mass above the "
                        "ceiling is bounded, not measured — a P99 breach proves "
                        "at least 1 percent of volume paid up"],
        },
        "runway": {
            "ok": True,
            "asof": "2026-07-22",
            "scenarios": {
                "base": {"crossing_date": "2026-07-22", "crossing_week": 0,
                         "verdict": "already below the estimated kink at the "
                                    "start of the window"},
                "fast_drain": {"crossing_date": "2026-07-22", "crossing_week": 0,
                               "verdict": "already below the estimated kink"},
                "slow": {"crossing_date": "2026-07-22", "crossing_week": 0,
                         "verdict": "already below the estimated kink"},
            },
            "assumptions": {"horizon_weeks": 13, "trailing_drift_b_per_week": 12.3,
                            "kink_reserves_b": 3634.4, "rrp_now_b": 1.4},
            "caveats": ["arithmetic on stated assumptions — not a forecast of policy"],
        },
    },
    "deep": {"turn": {"ok": True, "features": {"res_gdp_pctl": 0.26}}},
}


def _snap() -> dict:
    """A private copy to mutate; the module-level payload stays pristine."""
    return copy.deepcopy(_SNAP)


def _page(snap=None) -> str:
    return ampleness.render_ampleness_html(snap if snap is not None else _SNAP)


def _by_key(snap=None) -> dict:
    return {i["key"]: i for i in ampleness.indicators(snap or _SNAP)}


def _flat(text: str) -> str:
    """The page wraps its copy, so prose assertions run against one long line."""
    return " ".join(text.split())


def _prose(snippet: str) -> str:
    """A fragment of generated copy as it appears in the rendered page."""
    return html.escape(" ".join(snippet.split()))


# ---- the page as a whole -----------------------------------------------------

def test_page_is_deterministic():
    assert _page() == _page()
    # and the data layer under it, so a reordering bug cannot hide in the HTML
    assert ampleness.indicators(_SNAP) == ampleness.indicators(_SNAP)


def test_page_carries_every_indicator_in_the_checklist():
    text = _page()
    for title in ("Reserves: the level and the share of GDP",
                  "Reserves as a share of bank assets",
                  "Distance to the fitted reserve demand kink",
                  "The NY Fed&#x27;s reserve demand elasticity print",
                  "SOFR minus IORB",
                  "The share of repo volume printing above the ceiling",
                  "EFFR minus IORB",
                  "SRF and discount window take-up",
                  "The ON RRP buffer left to drain",
                  "The runway&#x27;s kink crossing dates"):
        assert title in text, title
    assert len(ampleness.indicators(_SNAP)) == len(ampleness.INDICATOR_KEYS)
    assert [i["key"] for i in ampleness.indicators(_SNAP)] == list(
        ampleness.INDICATOR_KEYS)


def test_every_indicator_prints_a_token_or_says_not_available():
    for ind in ampleness.indicators(_SNAP):
        if ind["verdict"] is None:
            assert ind["dark"], ind["key"]
        else:
            assert ind["verdict"] in ampleness.TOKENS
            assert ind["threshold"], ind["key"]


def test_house_style_no_dashes_and_lint_clean():
    text = _page()
    assert "—" not in text and "–" not in text
    # the same publish-blocking lint the daily letter runs, reused not rewritten
    assert lint_letter(text) == []


def test_engine_text_with_an_em_dash_is_normalized_not_rejected():
    # the live stigma and runway payloads carry em dashes in their caveats
    text = _page()
    assert "bounded, not measured - a P99 breach" in text
    assert "arithmetic on stated assumptions - not a forecast of policy" in text


def test_engine_caveats_are_attributed_to_the_engine_that_wrote_them():
    text = _flat(_page())
    assert "The stigma engine&#x27;s own caveat, verbatim:" in text
    assert "The runway engine&#x27;s own caveat, verbatim:" in text
    assert "The pair grades as: SRF AMPLE, the discount window AMPLE." in text


def test_page_is_self_contained():
    text = _page()
    assert "@import" not in text
    assert "<script" not in text
    assert 'src="http' not in text
    assert 'rel="stylesheet"' not in text
    assert "<img" not in text
    # the styles ship inline, sharing the methodology page's variables
    assert "--accent-soft" in text and "<style>" in text
    # only same-origin links leave the page, plus the repo and the site itself
    assert "cdn." not in text and "fonts.googleapis" not in text


def test_page_says_it_is_not_another_index():
    text = _flat(_page())
    assert "The overall reading, which is a count" in text
    assert "It is not a new index." in text
    assert "counts tokens and refuses to average them" in text
    assert "would blend these ten readings into a single number" in text


# ---- the count ---------------------------------------------------------------

def test_verdict_counting_matches_the_indicators():
    inds = ampleness.indicators(_SNAP)
    t = ampleness.tally(inds)
    assert t["n"] == 10
    assert t["counts"] == {AMPLE: 4, WATCH: 2, SCARCE: 3}
    assert t["graded"] == 9
    assert t["not_available"] == ["bank_assets"]
    assert sum(t["counts"].values()) + len(t["not_available"]) == t["n"]
    # the count the page prints is the count the data layer computed
    assert ("4 AMPLE, 2 WATCH, 3 SCARCE, 1 not available today, out of 10 "
            "indicators.") in _page()


def test_family_counts_partition_the_graded_indicators():
    t = ampleness.tally(ampleness.indicators(_SNAP))
    total = sum(v for fam in t["by_family"].values() for v in fam.values())
    assert total == t["graded"]
    assert t["by_family"]["price"] == {AMPLE: 4, WATCH: 0, SCARCE: 0}
    assert t["by_family"]["quantity"] == {AMPLE: 0, WATCH: 1, SCARCE: 3}


def test_the_split_observation_is_computed_from_the_tokens():
    # quantity under pressure, price calm: the 2018-19 ordering
    assert "The split is the finding." in _flat(_page())
    # flip it: an all-ample quantity side with a hot price side reads differently
    snap = _snap()
    snap["engines"]["kink"]["distance_b"] = 900.0
    snap["engines"]["kink"]["current_reserves_b"] = 3800.0
    snap["engines"]["runway"]["scenarios"] = {
        "base": {"crossing_date": None, "verdict": "no crossing in the window"},
        "fast_drain": {"crossing_date": None, "verdict": "no crossing"},
        "slow": {"crossing_date": None, "verdict": "no crossing"},
    }
    snap["headline"]["rrp_b"]["value"] = 480.0
    snap["engines"]["tails"]["spread"]["sofr_iorb_bp"] = 14.0
    t = ampleness.tally(ampleness.indicators(snap))
    assert t["by_family"]["quantity"] == {AMPLE: 4, WATCH: 0, SCARCE: 0}
    text = ampleness.render_ampleness_html(snap)
    assert "The split runs the other way today." in _flat(text)
    assert lint_letter(text) == []


def test_check_status_reports_one_verdict_per_indicator():
    status = ampleness.check_status(_SNAP)
    assert set(status) == set(ampleness.INDICATOR_KEYS)
    assert status["rrp"] == SCARCE
    assert status["sofr_iorb"] == AMPLE
    assert status["bank_assets"] == "not available"


# ---- thresholds are printed, and editorial ones are labelled -----------------

def test_every_graded_line_prints_its_threshold_on_the_page():
    text = _flat(_page())
    graded = [i for i in ampleness.indicators(_SNAP) if i["verdict"]]
    assert text.count("<b>Threshold</b>") == len(graded)
    for ind in graded:
        # a distinctive fragment of each threshold survives into the page
        assert _prose(ind["threshold"].split(".")[0][:60]) in text, ind["key"]


def test_editorial_thresholds_are_labelled_as_editorial():
    text = _page()
    editorial = [i for i in ampleness.indicators(_SNAP)
                 if i["verdict"] and i["editorial"]]
    # every editorial line carries the badge, plus the two in the page copy
    assert text.count("desk editorial") == len(editorial) + 2
    # the RDE line grades on the New York Fed's published bands, so it is not
    # editorial and must not claim to be
    assert _by_key()["rde"]["editorial"] is False
    assert "not numbers chosen here" in _by_key()["rde"]["threshold"]


def test_thresholds_name_their_numbers():
    inds = _by_key()
    assert "$200B" in inds["kink"]["threshold"]
    assert "8%" in inds["reserves"]["threshold"]
    assert "5 bp" in inds["effr_iorb"]["threshold"]
    assert "$100B" in inds["rrp"]["threshold"] and "$25B" in inds["rrp"]["threshold"]
    assert "68%" in inds["rde"]["threshold"] and "95%" in inds["rde"]["threshold"]


# ---- the individual verdicts -------------------------------------------------

def test_reserves_line_grades_the_ratio_not_the_dollar_level():
    ind = _by_key()["reserves"]
    assert ind["verdict"] == WATCH          # 9.61% of GDP, under the 11.41% kink
    values = {label: value for label, value, _ in ind["readings"]}
    assert values["reserves"] == "$3,062.1B"
    assert values["reserves as a share of GDP"] == "9.61%"
    # the history percentile the payload does carry, on the 0 to 100 scale
    assert "percentile 26" in ind["percentile"]


def test_reserves_line_goes_scarce_under_the_editorial_floor():
    snap = _snap()
    snap["engines"]["kink"]["current_reserves_b"] = 2000.0
    assert _by_key(snap)["reserves"]["verdict"] == SCARCE
    snap["engines"]["kink"]["current_reserves_b"] = 3700.0
    assert _by_key(snap)["reserves"]["verdict"] == AMPLE


def test_bank_assets_line_is_dark_and_refuses_to_guess():
    ind = _by_key()["bank_assets"]
    assert ind["verdict"] is None
    assert "H.8" in ind["dark"]
    text = _page()
    assert "Not available today." in text
    assert "NOT AVAILABLE TODAY" in text
    # and no invented ratio anywhere on the line
    assert ind["readings"] == []


def test_kink_line_prints_the_distance_and_what_the_slope_is_worth():
    ind = _by_key()["kink"]
    assert ind["verdict"] == SCARCE
    values = {label: value for label, value, _ in ind["readings"]}
    assert values["distance to the kink"] == "-$572.3B"
    assert values["what the slope is worth here"] == "+5.9 bp"
    assert values["days to the kink at that drift"] == "not published today"


def test_kink_line_grades_the_cushion_band():
    snap = _snap()
    snap["engines"]["kink"]["distance_b"] = 100.0
    assert _by_key(snap)["kink"]["verdict"] == WATCH
    snap["engines"]["kink"]["distance_b"] = 400.0
    assert _by_key(snap)["kink"]["verdict"] == AMPLE


def test_kink_line_withholds_a_verdict_below_the_boards_own_fit_gate():
    snap = _snap()
    snap["engines"]["kink"]["r2"] = 0.21
    ind = _by_key(snap)["kink"]
    assert ind["verdict"] is None
    assert "confidence gate" in ind["dark"]
    # the reading survives even though the verdict does not
    assert any(label == "distance to the kink" for label, _, _ in ind["readings"])


def test_rde_line_uses_the_published_bands():
    ind = _by_key()["rde"]
    assert ind["verdict"] == WATCH          # zero outside the 68, inside the 95
    snap = _snap()
    snap["engines"]["rdenowcast"]["nyfed_band_68"] = [-0.2, 0.15]
    assert _by_key(snap)["rde"]["verdict"] == AMPLE
    snap["engines"]["rdenowcast"]["nyfed_band_68"] = [-0.9, -0.4]
    snap["engines"]["rdenowcast"]["nyfed_band_95"] = [-1.1, -0.2]
    assert _by_key(snap)["rde"]["verdict"] == SCARCE


def test_rde_line_refuses_to_read_a_positive_elasticity_as_scarcity():
    snap = _snap()
    snap["engines"]["rdenowcast"]["nyfed_bp_per_1pct"] = 0.30
    snap["engines"]["rdenowcast"]["nyfed_band_68"] = [0.05, 0.55]
    snap["engines"]["rdenowcast"]["nyfed_band_95"] = [0.01, 0.62]
    ind = _by_key(snap)["rde"]
    assert ind["verdict"] == AMPLE
    assert any("wrong sign" in n for n in ind["notes"])


def test_sofr_iorb_threshold_uses_the_live_srf_ceiling_when_it_is_there():
    ind = _by_key()["sofr_iorb"]
    assert ind["verdict"] == AMPLE
    assert ind["editorial"] is False
    assert "administered ceiling rather than a number chosen here" in ind["threshold"]
    assert "percentile 76" in ind["percentile"]      # from the breakwater replay
    snap = _snap()
    snap["engines"]["tails"]["spread"]["sofr_iorb_bp"] = 4.0
    assert _by_key(snap)["sofr_iorb"]["verdict"] == WATCH
    snap["engines"]["tails"]["spread"]["sofr_iorb_bp"] = 18.0
    assert _by_key(snap)["sofr_iorb"]["verdict"] == SCARCE


def test_sofr_iorb_falls_back_to_an_editorial_ceiling_when_the_srf_rate_is_dark():
    snap = _snap()
    del snap["engines"]["stigma"]["ceiling"]
    ind = _by_key(snap)["sofr_iorb"]
    assert ind["editorial"] is True
    assert "stand-in for the missing administered ceiling" in ind["threshold"]


def test_sofr_iorb_differences_the_headline_when_the_tails_engine_is_dark():
    snap = _snap()
    snap["engines"]["tails"] = {"ok": False, "reason": "insufficient history"}
    ind = _by_key(snap)["sofr_iorb"]
    assert ind["verdict"] == AMPLE
    values = {label: value for label, value, _ in ind["readings"]}
    assert values["SOFR minus IORB"] == "-1.0 bp"
    assert any("differenced here" in (note or "") for _, _, note in ind["readings"])


def test_above_ceiling_line_states_the_bound_not_a_measured_share():
    ind = _by_key()["above_ceiling"]
    assert ind["verdict"] == AMPLE
    assert "bounded" in ind["threshold"]
    values = {label: value for label, value, _ in ind["readings"]}
    assert values["sessions in the last 20 with the 99th percentile above the "
                  "ceiling"] == "1"
    assert values["the ceiling being measured against"] == "3.75%"


def test_above_ceiling_withholds_a_verdict_without_the_session_count():
    snap = _snap()
    del snap["engines"]["stigma"]["bp_days_above_ceiling"]["days_above_20d"]
    ind = _by_key(snap)["above_ceiling"]
    assert ind["verdict"] is None
    assert "count of sessions above the ceiling" in ind["dark"]
    # the readings it does have still print
    assert any(label.startswith("99th percentile leak")
               for label, _, _ in ind["readings"])


def test_above_ceiling_goes_scarce_on_any_p75_breach():
    snap = _snap()
    snap["engines"]["stigma"]["bp_days_above_ceiling"]["p75_sum20_bp_days"] = 2.0
    assert _by_key(snap)["above_ceiling"]["verdict"] == SCARCE
    snap = _snap()
    snap["engines"]["stigma"]["bp_days_above_ceiling"]["days_above_20d"] = 6
    assert _by_key(snap)["above_ceiling"]["verdict"] == WATCH


def test_effr_iorb_line_grades_the_sign_first():
    inds = _by_key()
    assert inds["effr_iorb"]["verdict"] == AMPLE
    snap = _snap()
    snap["headline"]["effr_pct"]["value"] = 3.68     # +3 bp over IORB
    assert _by_key(snap)["effr_iorb"]["verdict"] == WATCH
    snap["headline"]["effr_pct"]["value"] = 3.78     # +13 bp over IORB
    assert _by_key(snap)["effr_iorb"]["verdict"] == SCARCE


def test_takeup_line_takes_the_worse_of_the_two_facilities():
    ind = _by_key()["takeup"]
    assert ind["verdict"] == AMPLE
    snap = _snap()
    snap["headline"]["dw_b"]["value"] = 14.0
    assert _by_key(snap)["takeup"]["verdict"] == WATCH       # window notable
    snap = _snap()
    snap["engines"]["stigma"]["takeup"]["max20_b"] = 40.0
    assert _by_key(snap)["takeup"]["verdict"] == SCARCE      # SRF material


def test_takeup_line_grades_on_one_facility_when_the_other_is_missing():
    snap = _snap()
    del snap["headline"]["dw_b"]
    ind = _by_key(snap)["takeup"]
    assert ind["verdict"] == AMPLE
    assert "token comes from SRF take-up alone" in ind["threshold"]
    assert not any(label.startswith("discount window")
                   for label, _, _ in ind["readings"])


def test_rrp_line_reads_an_empty_buffer_as_scarce():
    inds = _by_key()
    assert inds["rrp"]["verdict"] == SCARCE
    snap = _snap()
    snap["headline"]["rrp_b"]["value"] = 60.0
    assert _by_key(snap)["rrp"]["verdict"] == WATCH
    snap["headline"]["rrp_b"]["value"] = 900.0
    assert _by_key(snap)["rrp"]["verdict"] == AMPLE


def test_rrp_line_will_not_read_a_missing_print_as_an_empty_buffer():
    snap = _snap()
    del snap["headline"]["rrp_b"]
    del snap["engines"]["runway"]["assumptions"]["rrp_now_b"]
    ind = _by_key(snap)["rrp"]
    assert ind["verdict"] is None
    assert "unknown rather than zero" in ind["dark"]


def test_runway_line_grades_on_which_scenario_crosses():
    assert _by_key()["runway"]["verdict"] == SCARCE
    snap = _snap()
    snap["engines"]["runway"]["scenarios"]["base"]["crossing_date"] = None
    snap["engines"]["runway"]["scenarios"]["slow"]["crossing_date"] = None
    assert _by_key(snap)["runway"]["verdict"] == WATCH       # fast drain only
    snap["engines"]["runway"]["scenarios"]["fast_drain"]["crossing_date"] = None
    assert _by_key(snap)["runway"]["verdict"] == AMPLE


# ---- degrading honestly ------------------------------------------------------

def test_a_missing_engine_takes_its_lines_dark_without_fabricating_numbers():
    snap = _snap()
    del snap["engines"]["kink"]
    text = ampleness.render_ampleness_html(snap)
    status = ampleness.check_status(snap)
    assert status["kink"] == "not available"
    assert status["reserves"] == "not available"     # no GDP denominator left
    assert "-$572.3B" not in text                    # the fitted distance is gone
    assert "9.61%" not in text                       # and so is the ratio
    assert "+5.9 bp" not in text                     # and the slope arithmetic
    assert "$3,062.1B" in text                       # the level we do have stays
    assert "the hinge fit that carries the GDP denominator is dark" in _flat(text)
    assert lint_letter(text) == []


def test_a_dark_engine_reports_its_own_reason():
    snap = _snap()
    snap["engines"]["kink"] = {"ok": False, "reason": "insufficient overlap (12 weeks)"}
    ind = _by_key(snap)["kink"]
    assert "insufficient overlap (12 weeks)" in ind["dark"]


def test_each_missing_field_takes_exactly_one_line_dark():
    baseline = ampleness.check_status(_SNAP)
    for engine, key in (("rdenowcast", "rde"), ("stigma", "above_ceiling"),
                        ("runway", "runway")):
        snap = _snap()
        del snap["engines"][engine]
        status = ampleness.check_status(snap)
        assert status[key] == "not available", engine
        # the lines that did not depend on it are untouched
        assert status["effr_iorb"] == baseline["effr_iorb"]


def test_an_empty_payload_still_produces_an_honest_page():
    text = ampleness.render_ampleness_html({})
    status = ampleness.check_status({})
    assert set(status.values()) == {"not available"}
    t = ampleness.tally(ampleness.indicators({}))
    assert t["graded"] == 0 and len(t["not_available"]) == 10
    assert text.count("Not available today.") == 10
    assert "0 AMPLE, 0 WATCH, 0 SCARCE, 10 not available today" in text
    assert "Too few lines graded on this snapshot" in text
    assert lint_letter(text) == []


def test_a_null_headline_entry_is_treated_as_missing_not_as_zero():
    snap = _snap()
    snap["headline"]["dw_b"] = None
    snap["headline"]["rrp_b"] = None
    del snap["engines"]["runway"]["assumptions"]["rrp_now_b"]
    status = ampleness.check_status(snap)
    # a null print is a dark input, never a zero balance
    assert status["rrp"] == "not available"
    assert status["takeup"] == AMPLE            # SRF still grades it
    assert "token comes from SRF take-up alone" in _by_key(snap)["takeup"]["threshold"]
    assert lint_letter(ampleness.render_ampleness_html(snap)) == []


def test_the_2018_19_note_is_on_every_line_including_the_dark_ones():
    inds = ampleness.indicators({})
    assert all(i["lesson"] for i in inds)
    text = ampleness.render_ampleness_html({})
    assert text.count("What 2018-19 says this level meant") == 10
    # the specific historical anchors the reader is owed
    full = _page()
    assert "September 2019" in full
    assert "6 to 7 percent of GDP" in full
    assert "July 2021" in full                  # when the SRF was created
    assert "5 bp technical adjustments" in full


# ---- the writer + CLI --------------------------------------------------------

def test_write_ampleness_writes_the_page(tmp_path):
    out = tmp_path / "sub" / "ampleness.html"
    path = ampleness.write_ampleness(_SNAP, out)
    assert path == out
    assert out.read_text() == _page()


def test_main_writes_and_reports_the_count(tmp_path, capsys):
    snap_path = tmp_path / "overview.json"
    snap_path.write_text(json.dumps(_SNAP))
    out = tmp_path / "ampleness.html"
    rc = ampleness.main(["--snapshot", str(snap_path), "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr().out
    assert str(out) in captured
    assert "count: 4 AMPLE, 2 WATCH, 3 SCARCE, 1 not available (of 10)" in captured
    assert "not available today: bank_assets" in captured
    assert out.exists()
