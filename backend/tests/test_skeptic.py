"""The Skeptic Pack page: the leak audit, the orthogonal test, the replay
proof, the notary, and the falsifier list. Synthetic board payloads only, no
network, no filesystem outside tmp_path."""

import json

import pytest

from seiche import skeptic
from seiche.dispatch_daily import lint_letter

# A board payload shaped like /api/overview, trimmed to the fields the page
# reads. Values are synthetic but shaped exactly like the live ones, including
# the em dashes the engines emit and this page has to normalize away.
_SNAP = {
    "generated_at": "2026-07-28T15:46:58+00:00",
    "version": "0.7.0 test",
    "deep": {
        "leakaudit": {
            "ok": True,
            "asof": "2026-07-24",
            "bit_reproducible": True,
            "clean_index_sha256": "e8f248e21e637a42",
            "rows": [
                {"toggle": "clean",
                 "what_breaks": "the published pipeline: expanding windows, trailing smoothers",
                 "auroc": 0.798, "recall": 0.615, "precision_runs": 0.174,
                 "lg_auroc": 0.0, "lg_recall": 0.0},
                {"toggle": "NORM_GLOBAL",
                 "what_breaks": "every z/percentile standardized on the FULL sample",
                 "auroc": 0.824, "recall": 0.714, "precision_runs": 0.444,
                 "lg_auroc": 0.026, "lg_recall": 0.099},
                {"toggle": "TEMP_CENTER",
                 "what_breaks": "tails smoother centered — peeks 2 days forward",
                 "auroc": 0.808, "recall": 0.769, "precision_runs": 0.217,
                 "lg_auroc": 0.01, "lg_recall": 0.154},
            ],
            "reading": "leak selectivity, measured on ourselves: the forward-peeking "
                       "smoother would buy +0.010 AUROC — refused",
            "caveats": ["the leaky variants exist ONLY inside this audit — they are "
                        "never published as signals"],
        },
        "backtest": {
            "ok": True,
            "event_capture": {"recall": 0.615, "precision_runs": 0.174, "n_events": 13,
                              "n_alert_runs": 23, "median_lead_d": 60.0,
                              "base_rate": 0.046},
            "episodes": [
                {"episode": "Mar 2020 dash-for-cash", "date": "2020-03-16",
                 "class": "exogenous", "first_alert_lead_d": None},
                {"episode": "Sep 2025 tax-date squeeze", "date": "2025-09-15",
                 "class": "endogenous", "first_alert_lead_d": 42},
                {"episode": "Dec 2025 year-end squeeze", "date": "2025-12-31",
                 "class": "endogenous", "first_alert_lead_d": 21},
            ],
            "class_split": {"endogenous": {"n": 2, "caught": 2, "recall": 1.0},
                            "exogenous": {"n": 3, "caught": 0, "recall": 0.0}},
            "rigor": {"event_auroc": 0.798,
                      "significance": {"ok": True, "p_value": 0.0945,
                                       "n_permutations": 2000,
                                       "verdict": "NOT distinguishable from chance "
                                                  "placement of the same alerts"}},
            "orthogonal": {
                "ok": True,
                "event_capture": {"recall": 0.692, "recall_ci95": [0.424, 0.873],
                                  "precision_runs": 0.167, "n_events": 13,
                                  "n_alert_runs": 18, "median_lead_d": 60.0},
                "weights": {"kink": 0.289, "confession": 0.267, "rvxray": 0.244,
                            "auctions": 0.133, "buffers": 0.067},
                "excluded_components": ["tails", "weather", "hydrophone"],
                "why": "same event-capture test with the target's own variable family "
                       "removed from the signal (no spread, no tails) — kink-proxy/"
                       "confession/rvxray/auctions/buffers only",
            },
        },
    },
}


def _page(snap=None) -> str:
    return skeptic.render_skeptic_html(snap if snap is not None else _SNAP)


# ---- the page as a whole -----------------------------------------------------

def test_page_is_deterministic():
    assert _page() == _page()


def test_page_carries_every_section_and_the_two_questions():
    text = _page()
    for heading in ("1. The leak audit",
                    "2. The orthogonal test",
                    "3. The point-in-time proof",
                    "4. The notary",
                    "5. What would falsify the whole board"):
        assert heading in text
    # the two questions a skeptic opens with, asked before they have to
    assert "autocorrelation" in text and "look-ahead" in text
    # every section states its own limit
    assert text.count("<b>Limit</b>") >= 5


def test_all_five_sections_are_real_on_a_full_payload():
    assert skeptic.section_status(_SNAP) == {k: True for k in skeptic.SECTION_KEYS}


def test_house_style_no_dashes_and_lint_clean():
    text = _page()
    assert "—" not in text and "–" not in text
    # the same publish-blocking lint the daily letter runs, reused not rewritten
    assert lint_letter(text) == []


def test_engine_text_with_an_em_dash_is_normalized_not_rejected():
    # the live leakaudit payload carries em dashes; the page must clean them on
    # the way in rather than fail the lint or drop the engine's own words
    text = _page()
    assert "the forward-peeking smoother would buy +0.010 AUROC - refused" in text
    assert "tails smoother centered - peeks 2 days forward" in text


def test_page_is_self_contained():
    text = _page()
    assert "@import" not in text
    assert "<script" not in text
    assert 'src="http' not in text
    assert 'rel="stylesheet"' not in text
    # the styles ship inline, sharing the methodology page's variables
    assert "--accent-soft" in text and "<style>" in text


# ---- 1. the leak audit -------------------------------------------------------

def test_leak_audit_prints_the_table_and_the_refused_gain():
    text = _page()
    for toggle in ("clean", "NORM_GLOBAL", "TEMP_CENTER"):
        assert f"<code>{toggle}</code>" in text
    assert "0.824" in text and "0.798" in text and "+0.026" in text
    assert "e8f248e21e637a42" in text          # the determinism hash to pin
    assert "hashed identically both times" in text


def test_leak_audit_degrades_when_the_engine_is_dark():
    snap = json.loads(json.dumps(_SNAP))
    snap["deep"]["leakaudit"] = {"ok": False, "reason": "insufficient scored history"}
    text = skeptic.render_skeptic_html(snap)
    assert skeptic.section_status(snap)["leak_audit"] is False
    assert "The leak audit is dark on this snapshot." in text
    assert "Not yet published." in text
    # no fabricated audit numbers survive the degrade
    assert "+0.026" not in text and "e8f248e21e637a42" not in text
    assert lint_letter(text) == []


def test_an_empty_payload_still_produces_an_honest_page():
    snap = {}
    text = skeptic.render_skeptic_html(snap)
    status = skeptic.section_status(snap)
    assert status["leak_audit"] is False and status["orthogonal"] is False
    # the machinery sections do not depend on the payload and still stand
    assert status["point_in_time"] is True and status["notary"] is True
    assert text.count("Not yet published.") == 2
    assert "1. The leak audit" in text and "5. What would falsify" in text
    assert lint_letter(text) == []


# ---- 2. the orthogonal test --------------------------------------------------

def test_orthogonal_prints_both_columns_and_the_honest_permutation_result():
    text = _page()
    assert "tails removed" in text
    assert "69%" in text and "62%" in text            # ortho recall vs full recall
    assert "42% to 87%" in text                       # the CI, not just the point
    assert "NOT distinguishable from chance placement" in text
    assert "0.095" in text
    # the surviving components, with weights, so the reader sees what carries it
    assert "<code>kink</code>" in text and "0.289" in text


def test_orthogonal_prints_the_unflattering_reading_when_capture_collapses():
    snap = json.loads(json.dumps(_SNAP))
    snap["deep"]["backtest"]["orthogonal"]["event_capture"]["recall"] = 0.08
    text = skeptic.render_skeptic_html(snap)
    assert "it is not the flattering one" in text
    assert "the objection wins" in text
    assert "not one series predicting itself" not in text
    assert lint_letter(text) == []


def test_survival_bar_is_a_computation_not_an_opinion():
    # comfortably above the base rate and no collapse against the full index
    assert skeptic._survives(0.692, 0.615, 0.046) is True
    assert skeptic._survives(0.08, 0.615, 0.046) is False     # near the base rate
    assert skeptic._survives(0.30, 0.615, 0.046) is False     # collapse vs full
    assert skeptic._survives(None, 0.615, 0.046) is False


def test_orthogonal_states_what_it_does_not_prove():
    text = _page()
    assert "not an independence proof" in text
    assert "The event is still defined on the spread." in text


def test_orthogonal_degrades_when_the_run_is_absent():
    snap = json.loads(json.dumps(_SNAP))
    del snap["deep"]["backtest"]["orthogonal"]
    text = skeptic.render_skeptic_html(snap)
    assert skeptic.section_status(snap)["orthogonal"] is False
    assert "The orthogonal run is not on this snapshot." in text
    assert "tails removed" not in text
    assert lint_letter(text) == []


def test_orthogonal_degrades_when_capture_is_empty():
    snap = json.loads(json.dumps(_SNAP))
    snap["deep"]["backtest"]["orthogonal"] = {"ok": True, "event_capture": {}}
    assert skeptic.section_status(snap)["orthogonal"] is False


# ---- 3. the point-in-time replay ---------------------------------------------

def test_replay_section_carries_both_exact_commands():
    text = _page()
    # the hosted route
    assert "curl -s --max-time 600 https://api.seiche.info/api/asof/2025-12-31" in text
    # and the route that needs nothing from the operator but the source
    assert "git clone https://github.com/beepboop2025/seiche" in text
    assert 'assemble.snapshot_asof(&quot;2025-12-31&quot;)' in text
    assert "/api/pit" in text
    assert "final-vintage data" in text


def test_replay_example_prefers_an_episode_the_board_flagged_early():
    date, clause = skeptic._replay_example(_SNAP["deep"])
    assert date == "2025-12-31"
    assert "flagged 21 trading days ahead" in clause


def test_replay_example_never_claims_a_catch_it_cannot_show():
    deep = {"backtest": {"episodes": [
        {"date": "2020-03-16", "class": "exogenous", "first_alert_lead_d": None},
    ]}}
    date, clause = skeptic._replay_example(deep)
    assert date == "2020-03-16"
    assert "flagged" not in clause
    # and with no episodes at all it falls back to a published constant
    assert skeptic._replay_example({})[0] == skeptic.FALLBACK_REPLAY_DATE


def test_replay_section_degrades_when_the_endpoint_is_gone(monkeypatch, tmp_path):
    monkeypatch.setattr(skeptic, "BACKEND_DIR", tmp_path)
    snap = _SNAP
    text = skeptic.render_skeptic_html(snap)
    assert skeptic.section_status(snap)["point_in_time"] is False
    assert "The replay endpoint is not in this build." in text
    assert "api/asof/" not in text


# ---- 4. the notary -----------------------------------------------------------

def test_notary_section_carries_the_verification_command():
    text = _page()
    assert "curl -s &#x27;https://api.seiche.info/api/notary?n=500&#x27;" in text
    assert "seiche-notary-genesis-v1" in text
    assert "ots verify -d RECORD_SHA256 reading.ots" in text
    assert "sha256(prev_hash|digest|utc|pit_date)" in text
    assert "book_history.json" in text


def test_notary_section_degrades_when_the_chain_is_gone(monkeypatch, tmp_path):
    monkeypatch.setattr(skeptic, "BACKEND_DIR", tmp_path)
    snap = _SNAP
    text = skeptic.render_skeptic_html(snap)
    assert skeptic.section_status(snap)["notary"] is False
    assert "The notary is not in this build." in text
    assert "ots verify" not in text
    assert lint_letter(text) == []


def test_source_check_reads_the_real_backend():
    # the sections are gated on machinery that actually exists in this repo
    assert skeptic._source_has("notary.py", "def verify_chain", "GENESIS")
    assert skeptic._source_has("api.py", '@app.get("/api/notary")')
    assert not skeptic._source_has("notary.py", "def a_function_that_never_existed")


# ---- 5. the falsifiers -------------------------------------------------------

def test_falsifiers_quote_the_board_against_itself():
    text = _page()
    assert "rests on 2 endogenous episodes" in text
    assert "17% of alert runs are followed by an event" in text
    assert "p 0.095" in text
    assert "smoke alarm that rings at toast" in text


def test_falsifiers_degrade_to_prose_without_numbers():
    items = skeptic._falsifier_items({})
    joined = " ".join(items)
    assert len(items) >= 6
    assert "rests on" in joined
    assert lint_letter(joined) == []


def test_falsifiers_never_claim_a_sample_of_zero_episodes():
    deep = {"backtest": {"class_split": {"endogenous": {"n": 0}}}}
    joined = " ".join(skeptic._falsifier_items(deep))
    assert "rests on 0 endogenous episodes" not in joined
    assert "a handful of dated endogenous episodes" in joined


# ---- inputs and the CLI surface ---------------------------------------------

def test_load_snapshot_reads_the_baked_file_without_touching_the_network(tmp_path):
    p = tmp_path / "overview.json"
    p.write_text(json.dumps(_SNAP))
    assert skeptic.load_snapshot(p, api="http://unused.invalid") == _SNAP


def test_write_skeptic_writes_a_page(tmp_path):
    out = skeptic.write_skeptic(_SNAP, tmp_path / "skeptic.html")
    text = out.read_text()
    assert text.startswith("<!doctype html>")
    assert "<title>Seiche skeptic pack</title>" in text
    assert f"skeptic pack {skeptic.SKEPTIC_VERSION}" in text


def test_lint_blocks_publication_rather_than_shipping_a_dash(monkeypatch):
    # if a future edit slips a dash past the cleaner, the page must refuse to
    # render at all — the same contract the daily letter runs under
    monkeypatch.setattr(skeptic, "_EXTRA_CSS", skeptic._EXTRA_CSS + "\n/* an em dash — here */")
    with pytest.raises(SystemExit) as exc:
        _page()
    assert "em dash" in str(exc.value)
