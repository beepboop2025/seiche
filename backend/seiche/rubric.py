"""The Rubric: the coded evidence matrix behind every referee verdict, and
the ordering rule that makes it credible: Seiche grades ITSELF first, on the
same rows, with the failures published.

Kernel (arXiv:2606.08285): trading system claims become comparable when the
execution assumptions behind them are coded into a small evidence matrix
instead of prose. Two of the paper's seven rows, cost and turnover treatment
and execution semantics, are vacuous for a terminal that trades nothing, and
grading external firms on all seven while self grading N/A on the hardest
rows would be softball dressed as rigor. Those two rows are therefore
re coded into their forecast provenance analogs, the two that bite hardest
on a forecast publisher: vintage and revision handling (does the record ride
as published values or today's revised history?) and threshold provenance
(who chose the trigger numbers, and against what?). A verdict revision
policy row is added on top, because a grader that cannot itself be regraded
is marketing.

The self grade is the feature, and its honesty is enforced: every row cites
repo facts a reader can check (unit tests by name, config constants, the
attested PIT ledger), the rows where the honest grade is PARTIAL say PARTIAL
and name the gap, and a regression test locks each of those rows so it
cannot drift to PASS without the missing evidence shipping in the same
commit. Display only, like every referee surface: nothing here reads or
moves a score, a weight, a tier or a verdict.
"""

from __future__ import annotations

import json

from seiche.config import BACKTEST_ALERT_PCTL, BACKTEST_SPIKE_BP

PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"
GRADES = (PASS, PARTIAL, FAIL, NOT_APPLICABLE)

KERNEL = "arXiv:2606.08285"

# The seven coded fields, in publication order, plus the revision policy row.
FIELDS: tuple[tuple[str, str], ...] = (
    ("point_in_time_controls", "point-in-time controls"),
    ("split_transparency", "train/eval split transparency"),
    ("held_out_evaluation", "held-out evaluation"),
    ("universe_definition", "universe and series definition"),
    ("artifact_release", "artifact release"),
    ("vintage_handling", "vintage and revision handling"),
    ("threshold_provenance", "threshold provenance"),
    ("verdict_revision_policy", "verdict revision policy"),
)
FIELD_KEYS = tuple(k for k, _ in FIELDS)

# The two rows re coded from the paper's trading specific fields, so the
# adaptation is machine readable and not just asserted in prose.
RECODED_FROM = {
    "vintage_handling": "cost and turnover treatment",
    "threshold_provenance": "execution semantics",
}


def _row(field: str, grade: str, evidence: str,
         na_reason: str | None = None) -> dict:
    row = {"field": field, "grade": grade, "evidence": evidence}
    if na_reason is not None:
        row["na_reason"] = na_reason
    return row


def _tally(rows: list[dict]) -> dict:
    return {g: sum(1 for r in rows if r["grade"] == g) for g in GRADES}


def _case(case: str, subject: str, rows: list[dict], reading: str) -> dict:
    return {"case": case, "subject": subject, "rows": rows,
            "tally": _tally(rows), "reading": reading}


# ------------------------------------------------------------- validation

def validate_case(case: dict) -> list[str]:
    """The completeness contract: every field graded, in order, with real
    evidence; N/A only with a stated reason; house copy rules hold."""
    issues: list[str] = []
    rows = case.get("rows") or []
    if [r.get("field") for r in rows] != list(FIELD_KEYS):
        issues.append(f"case '{case.get('case')}' does not grade every field in order")
    for r in rows:
        if r.get("grade") not in GRADES:
            issues.append(f"row '{r.get('field')}' has unknown grade '{r.get('grade')}'")
        if not str(r.get("evidence") or "").strip():
            issues.append(f"row '{r.get('field')}' has no evidence")
        if r.get("grade") == NOT_APPLICABLE and not str(r.get("na_reason") or "").strip():
            issues.append(f"row '{r.get('field')}' is N/A without a stated reason")
    if case.get("tally") != _tally(rows):
        issues.append(f"case '{case.get('case')}' tally does not match its rows")
    return issues


def validate_block(block: dict) -> list[str]:
    """The block contract, including the ordering rule: the self grade
    publishes first. A matrix that fails here is a bug, not a payload."""
    issues: list[str] = []
    cases = block.get("cases") or []
    if len(cases) < 2:
        issues.append("both matrices must publish: self and the external case")
    if not cases or cases[0].get("case") != "self":
        issues.append("ordering rule broken: the self grade must publish first")
    for c in cases:
        issues.extend(validate_case(c))
    blob = json.dumps(block, default=str)
    for ch in ("—", "–"):
        if ch in blob:
            issues.append("em or en dash in rubric copy")
    return issues


# -------------------------------------------------- case 1: ourselves first

def self_grade() -> dict:
    """Seiche's PROOF and composite claims under the rubric, from repo facts.

    The PARTIAL rows are the point. Each names its gap and is pinned by a
    regression test; flipping one to PASS requires shipping the missing
    evidence and changing the test in the same commit.
    """
    rows = [
        _row("point_in_time_controls", PASS,
             "Expanding window discipline is enforced by unit test: "
             "test_history_has_no_look_ahead appends future data and requires "
             "every already computed index value to stay identical, and the "
             "same invariant is pinned on undertow, tidetables, swell and "
             "bathymetry. The Leak Audit then rebuilds the same index with "
             "the two dominant leak classes deliberately switched on (full "
             "sample standardization, a centered smoother) and publishes the "
             "Leakage Gain each break would buy."),
        _row("split_transparency", PARTIAL,
             "Nothing in the composite is fitted, so there is no training "
             "set to disclose: the weights and the alert percentile are "
             "frozen in config.py and PROOF prints its warmup slice and its "
             "caveat list. The gap: those editorial choices were made by an "
             "operator who had already seen the full history, and no "
             "document separates the data that shaped the design from the "
             "data that now grades it. Disclosed, but not separated."),
        _row("held_out_evaluation", PARTIAL,
             "The genuinely held out sample is the live record since first "
             "publication: the PIT ledger commits one record per day per "
             "stream, and the attest layer signs each committed record "
             "(Ed25519) and anchors it to Bitcoin through OpenTimestamps, so "
             "a post publication reading is sealed before its outcome. The "
             "backtest window itself is not held out: the design saw it. The "
             "true out of sample record is the attested one, and it is still "
             "short."),
        _row("universe_definition", PASS,
             "Every input is a registered SeriesSpec in config.py with "
             "mnemonic, source ID, fetch start and native lag. The funding "
             "event is defined once (backtest.pop_bp: SOFR minus IORB "
             f"against its trailing 5 business day median, {BACKTEST_SPIKE_BP:g}bp "
             "threshold) and every consumer imports that definition. "
             "Registry tests pin fetch starts so a retune cannot silently "
             "starve an engine."),
        _row("artifact_release", PASS,
             "The whole pipeline is public AGPL-3.0-or-later code; the board snapshot "
             "is baked to frontend/public/data/overview.json on every "
             "publish; the Time Machine endpoint (/api/asof/{date}) replays "
             "the full light board as of any historical date; and the PROOF "
             "scoreboard has its own signed and Bitcoin anchored stream "
             "(attest prove-scoreboard)."),
        _row("vintage_handling", PARTIAL,
             "PROOF consumes final vintage values: the FRED collector reads "
             "fredgraph.csv, which serves the current revised history, and "
             "no ALFRED as published replay exists in this codebase. The "
             "PROOF page prints the caveat (weekly H.4.1 aggregates are "
             "lightly revised; daily market prints effectively are not) and "
             "the PIT ledger accrues true as published readings from "
             "publication day forward, but the historical backtest itself "
             "rides revised values. That is the gap, named."),
        _row("threshold_provenance", PARTIAL,
             "Editorial, frozen, disclosed: the alert percentile "
             f"({BACKTEST_ALERT_PCTL:g}) and the composite weights sit in config.py "
             "under a TUNING POINT banner that calls them the tool's "
             "editorial voice, and nothing in the pipeline refits them. The "
             "Leak Audit's THRESH_FIT row publishes what an in sample fitted "
             "threshold would have bought, and PROOF's AUROC is threshold "
             "free, so the cost of the editorial choice is measured. But "
             "measured is not derived: the numbers come from judgment, not "
             "from a walk forward derivation, and this rubric does not round "
             "that up."),
        _row("verdict_revision_policy", PASS,
             "Published readings are append only: the notary hash chains "
             "every reading so editing any past day breaks every link after "
             "it, and the attest layer makes a rewrite attributable "
             "(signature) and undatable (Bitcoin anchor). The referee, "
             "methodology and skeptic pages carry dated changelogs, and the "
             "PROOF page's stated contract is that unimpressive numbers "
             "publish anyway."),
    ]
    reading = (
        "Four rows PASS, four are PARTIAL with the gaps named: no as "
        "published vintage replay, a still short attested live record, "
        "thresholds measured but not derived, and a design that saw its own "
        "evaluation sample. Each PARTIAL is locked by a regression test and "
        "flips only when the missing evidence ships."
    )
    return _case("self", "Seiche itself: the PROOF scoreboard and the composite",
                 rows, reading)


# ------------------------------------------- case 2: the GL referee verdicts

def _fmt_corr(c: dict | None) -> str | None:
    if not c:
        return None
    lo, hi = c["ci95"]
    return f"{c['corr']:+.2f} in [{lo:+.2f}, {hi:+.2f}], n {c['n']}"


def grade_gli(blk: dict | None) -> dict:
    """The GL Indexes referee case under the same rubric. The grades are
    facts about the firm's published record, established by the honesty
    rails of engines/refereegli.py; the evidence rides live numbers whenever
    the block is lit and degrades honestly when it is dark."""
    blk = blk if isinstance(blk, dict) and blk.get("ok") else None
    c1 = (blk or {}).get("claim1") or {}
    c2 = (blk or {}).get("claim2") or {}
    c3 = (blk or {}).get("claim3") or {}
    oos = c1.get("walkforward_oos")

    pit_ev = "The claims ship with no stated look ahead protections. "
    if oos:
        lo, hi = oos["spread_ci95"]
        pit_ev += (
            "Rebuilt with them (trailing median signal, no look ahead), the "
            f"public layer's 6 month spread is {oos['spread_6m_logret']:+.4f} "
            f"with a 95 percent interval of [{lo:+.4f}, {hi:+.4f}]."
        )
    else:
        pit_ev += (
            "The public layer walk forward test is dark on today's inputs; "
            "the absence of protections in the published record stands "
            "either way."
        )

    held_ev = (
        "Nothing is held out because nothing is released to hold out. The "
        "referee's own held out test on the public layer refuses to certify "
        "the asset price lead"
    )
    at13 = _fmt_corr(c2.get("corr_at_claimed_13m"))
    held_ev += (
        f"; at the claimed 13 month business cycle lead the correlation "
        f"reads {at13}." if at13 else "."
    )

    if blk:
        w0, w1 = blk["window"]
        uni_ev = (
            "The largest agreed component is public and is reconstructed "
            f"here: G3 central bank assets at monthly average spot, "
            f"{blk['n_months']} months from {w0} to {w1}. "
        )
    else:
        uni_ev = (
            "The largest agreed component is public and is reconstructed "
            "here monthly from 2003: G3 central bank assets at monthly "
            "average spot. "
        )
    uni_ev += (
        "The remaining layers, private credit and cross border flows across "
        "some 80 countries back to 1974, are defined in prose and "
        "reproducible by nobody outside the firm."
    )

    thr_ev = (
        "The claimed windows, 3 to 6 months, 12 to 15 months and a 65 month "
        "cycle, arrive with no stated derivation."
    )
    pk, res = c2.get("peak_lead_months"), c3.get("spectral_resolution_at_65m_pm_months")
    if pk is not None:
        thr_ev += (
            f" Tested on the public layer, the business cycle correlation "
            f"peaks at a {pk} month lead."
        )
    if res is not None:
        thr_ev += (
            f" The sample's spectral resolution at 65 months is plus or "
            f"minus {res:g} months: too coarse to certify the cycle either way."
        )

    rows = [
        _row("point_in_time_controls", FAIL, pit_ev),
        _row("split_transparency", FAIL,
             "No design or evaluation split ships with the claims: no error "
             "bars, no out of sample tests, no design sample named. The "
             "referee page states that absence above the fold."),
        _row("held_out_evaluation", FAIL, held_ev),
        _row("universe_definition", PARTIAL, uni_ev),
        _row("artifact_release", FAIL,
             "The index is proprietary: no series, no code, no error "
             "distribution. The referee page commits to rerunning every test "
             "the day an error distribution is released."),
        _row("vintage_handling", FAIL,
             "No revision record ships with the claims, so as published "
             "values cannot even be distinguished from today's history. An "
             "index family whose vintages are unpublished is ungradeable on "
             "this row, and ungradeable by choice is a FAIL, not an N/A."),
        _row("threshold_provenance", FAIL, thr_ev),
        _row("verdict_revision_policy", FAIL,
             "The claims are undated and carry no changelog and no stated "
             "policy for revising or retiring a claim that fails. A claim "
             "that cannot fail in public is copy, not a forecast; the "
             "referee page's dated changelog is what the alternative looks "
             "like."),
    ]
    reading = (
        "Seven rows FAIL and one is PARTIAL, all from one root cause: "
        "nothing is released that would let an outsider grade the claims "
        "any better. The rubric grades the published record, and the "
        "published record is assertion."
    )
    return _case("external", "the global liquidity index family's three headline claims",
                 rows, reading)


# ------------------------------------------------------------------ entry

def build(refereegli_blk: dict | None = None) -> dict:
    """Both matrices, self first, validated before they ship."""
    fields = []
    for key, label in FIELDS:
        f = {"field": key, "label": label}
        if key in RECODED_FROM:
            f["recoded_from"] = RECODED_FROM[key]
        fields.append(f)
    block = {
        "ok": True,
        "kernel": KERNEL,
        "ordering_rule": (
            "the terminal grades itself before it grades anyone else: the "
            "self grade publishes first, on the same rows, with its PARTIAL "
            "rows named and locked by regression test"
        ),
        "fields": fields,
        "cases": [self_grade(), grade_gli(refereegli_blk)],
        "method": (
            f"Adapted from the coded execution assumption matrix of {KERNEL}. "
            "The paper's seven fields grade backtested trading claims; two of "
            "them, cost and turnover treatment and execution semantics, are "
            "vacuous for a terminal that trades nothing, so they are re coded "
            "as vintage and revision handling and threshold provenance, and a "
            "verdict revision policy row is added. Grades are PASS, PARTIAL, "
            "FAIL or NOT_APPLICABLE, N/A only with a stated reason, and every "
            "row carries evidence a reader can check. The self grade is built "
            "from repo facts (unit tests by name, config constants, the "
            "attested PIT ledger); the external case is populated from what "
            "engines/refereegli.py actually established, with live numbers "
            "when the block is lit."
        ),
    }
    issues = validate_block(block)
    if issues:
        raise RuntimeError("rubric failed validation: " + "; ".join(issues))
    return block
