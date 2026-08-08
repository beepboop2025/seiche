"""The free public surface: today's argument + the historical diagnostic.

Everything else — the live board, the physics, positioning, the Time Machine,
the desk's forward read — is subscriber-gated. But two things stay free
forever, because the whole business is a derivative of trust in the record:
  * the argument (thesis, evidence, countercase and confidence),
  * the conclusion (what the plumbing is doing today, one reading),
  * the data-quality contract (which clock each layer really runs on), and
  * PROOF (the construction-PIT diagnostic WITH its published misses and
    explicit claim-eligibility flags).

`public_payload` slices a full snapshot down to exactly that surface, so a
non-subscriber can never pull the gated data through this path.
"""

from __future__ import annotations

from seiche.evidence_boundary import historical_evidence


def _regime_line(composite: dict, tell: dict) -> str:
    reg = composite.get("regime", "?")
    val = composite.get("value")
    t = tell.get("tell")
    reading = tell.get("reading", "")
    bits = [f"The board reads {reg}"]
    if val is not None:
        bits[0] += f" ({val:.0f}/100)"
    if t is not None:
        bits.append(f"The Tell is {t:+.0f}, {reading}")
    return ". ".join(bits) + "."


def public_payload(snap: dict) -> dict:
    engines = snap.get("engines", {})
    deep = snap.get("deep", {})
    composite = engines.get("composite", {})
    tell = deep.get("tell", {})
    bt = deep.get("backtest", {})
    ec = bt.get("event_capture", {})
    evidence = historical_evidence(snap)

    return {
        "schema": "seiche.public.v2",
        "generated_at": snap.get("generated_at"),
        "conclusion": {
            "regime": composite.get("regime"),
            "value": composite.get("value"),
            "coverage_pct": composite.get("coverage_pct"),
            "tell": tell.get("tell"),
            "tell_reading": tell.get("reading"),
            "line": _regime_line(composite, tell),
        },
        # PROOF stays free: the diagnostic, misses, and claim boundary travel
        # together so a client cannot accidentally detach a rate from status.
        "proof": {
            "recall": ec.get("recall"),
            "recall_ci95": ec.get("recall_ci95"),
            "precision_runs": ec.get("precision_runs"),
            "base_rate": ec.get("base_rate"),
            "n_events": ec.get("n_events"),
            "median_lead_d": ec.get("median_lead_d"),
            "episodes": [
                {"episode": e.get("episode"), "date": e.get("date"),
                 "in_sample": e.get("in_sample")}
                for e in bt.get("episodes", [])
            ],
            "caveats": bt.get("caveats", []),
            "historical_evidence": evidence,
        },
        # The argument, evidence and countercase travel together. These are
        # derived slices, not the underlying engine payloads.
        "editorial": snap.get("editorial"),
        "data_quality": snap.get("data_quality"),
    }
