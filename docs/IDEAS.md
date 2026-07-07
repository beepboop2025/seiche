# Seiche — pattern-first improvement roadmap

The premise of this list: **funding markets rhyme**. The same calendar forces the
basin every month; the same escalation grammar plays out in every squeeze; the
same divergences open before every break. Each idea below turns one *recurring
pattern* into a tool, ranked roughly by (evidence the pattern is real) ×
(nobody else ships it) ÷ (build cost). Everything must clear the house bar:
free keyless data, no look-ahead, fail-loud, benchmarked against a naive
baseline, honest about itself.

---

## 1. Tide Tables — analog forecasting ✅ *shipped in this branch*

**Pattern:** history's nearest neighbors are the best local forecast (Lorenz's
analog method — the oldest trick in operational weather prediction). Echo
already proves the board rhymes with labeled episodes; the predictive question
is what happened next *every* time it rhymed, labeled or not.

**Tool:** k-nearest analogs of today's trailing 20bd state trajectory over all
history (expanding-z, de-clustered, no shared days, forward outcomes closed) →
forward SOFR−IORB fan, funding-event odds vs climatology with Wilson CI, a
novelty gauge that flags the fan as extrapolation when today's water is
uncharted, and a walk-forward hindcast (Brier/AUROC vs climatology) printed
next to the forecast. Lives on the MARKET tab; `analog_event_odds` alert rule.

## 2. Undertow — the damping gauge (critical slowing down)

**Pattern:** systems approaching a regime shift relax more slowly — rising
lag-1 autocorrelation and variance of the *detrended* state are early-warning
signals across ecology, climate and finance (Scheffer et al.). Resonance
measures decay half-life only *around calendar events*; nothing measures the
basin's damping continuously.

**Tool:** rolling AR(1) coefficient + variance trend of detrended SOFR−IORB
and the tail series → an implied relaxation time in days. A basin that takes
longer and longer to flatten after every ripple is losing damping even while
levels are calm. Completes the seiche physics: Resonance = forced response,
Undertow = free decay. Backtest against EPISODES with the PROOF harness.

## 3. Cascade Sequencer — the escalation grammar

**Pattern:** squeezes escalate in a characteristic ORDER (tails detach →
GC/tri-party spreads widen → volumes shift → SRF/discount window confession →
swap lines). Hydrophone finds who leads whom on average; nobody tracks *where
in the canonical sequence we currently are*.

**Tool:** learn the median activation order of engine subscores across the six
labeled episodes; report "stage k of N, historically T−x days from the break,"
with the stages that fired out of order flagged (out-of-order cascades were
false alarms historically — that's the discriminator).

## 4. Fleet of Forecasts — disagreement as a signal

**Pattern:** the rule index, ML Lab and Tide Tables now emit three independent
P(event)-shaped views. Ensembles beat components, and *forecast dispersion*
itself spikes when the regime is genuinely ambiguous — which is when operators
most need to know.

**Tool:** a stacked probability (logistic blend, walk-forward) plus a
disagreement meter. When the three views diverge, say so loudly instead of
averaging silently.

## 5. Weekend Canary formalized

**Pattern:** crypto is the only dollar market open when funding markets sleep;
USDT peg pressure and BTC weekend moves have led Monday funding prints.
Moorings shows the levels; nothing tests the lead.

**Tool:** event-study of Monday SOFR−IORB conditioned on weekend peg/BTC
moves, published with n and CI like the Playbook; alert on qualifying weekends
Sunday night, before the NY open.

## 6. Counterfactual Weather — the what-if throttle

**Pattern:** the reserve path is driven by three levers (TGA rebuild pace, QT
runoff, RRP drain) whose plausible ranges are known. Operators ask "what if
Treasury rebuilds $200B faster?" and today have to guess.

**Tool:** slider-parameterized re-run of the Weather model (it's already a
pure function) → crunch windows under user-chosen lever paths, with the
default path always shown as the anchor. Zero new data.

## 7. Auction Autopsy — the demand-composition tell

**Pattern:** before digestion breaks, the *composition* of auction demand
rotates first (dealers absorb what indirects refuse — visible in FiscalData's
allotment fields well before tails blow out).

**Tool:** per-tenor indirect/direct/dealer takedown trends with expanding
percentiles; wire a composition term into the Auction Digestion engine and
re-run PROOF to see if lead time improves. Publish either way.

## 8. Narrative Diff — the morning brief that only says what changed

**Pattern:** operators don't re-read dashboards; they ask "what's different
since yesterday?" The PIT record (`pit:*` blobs) already stores each day's
as-published board.

**Tool:** `seiche diff` + brief section: engine-by-engine deltas vs yesterday
and vs last week, sorted by |Δ|, rendered in words ("confession +12 on a $6B
SRF print; novelty jumped to uncharted"). Cheap, high daily-retention value.

## 9. Alert Utility Tuner

**Pattern:** PROOF already computes recall/precision at the fixed 80th-pctl
alert line; different operators have different false-alarm costs, and the
optimal threshold is regime-dependent.

**Tool:** publish the full precision-recall curve from the backtest and let
config express alert appetite as a cost ratio ("1 missed event = 20 false
alarms") → thresholds derived, not hand-picked, and re-derived as the sample
grows.

## 10. Basin Tomography — transfer entropy across basins

**Pattern:** cross-basin coupling today is lagged correlation (linear, single
lag). Stress transmission is nonlinear and asymmetric — offshore dollar strain
(€STR, SONIA, INR, USDT) leads onshore differently in EROSION than in CALM.

**Tool:** regime-conditioned transfer-entropy map between basins, replacing
the correlation edges in Global Basin Coupling; alert when the offshore→onshore
direction strengthens (the 2019 and Mar-2020 signature).

---

*Naming note: engines keep the hydrology metaphor — the pattern layer is now
Echo (rhyme), Resonance (forced response), Tide Tables (analog prediction);
Undertow and Cascade would extend it.*
