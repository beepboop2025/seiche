"""E-Detector — the changepoint tripwire with a false-alarm warranty.

Every other engine reads the water; this one stands watch for the moment the
water CHANGES. The instrument is the e-detector of Shin, Ramdas & Rinaldo
(arXiv:2203.03532): a mixture of Shiryaev–Roberts e-processes restarted at
consecutive times, thresholded at 1/alpha, which carries a NONASYMPTOTIC
Frequentist warranty — under any pre-change distribution in the calibrated
class, the expected waiting time to a false alarm is at least 1/alpha days.
Not a heuristic, not a backtested threshold: a proof.

Construction (the paper's sub-Gaussian instantiation, Sec. 2–4): the first
EDETECT_BASELINE_BD observations calibrate the pre-change class — a mean
bound padded EDETECT_MU_PAD_SE standard errors beyond the baseline mean
(per direction) and a scale bound sigma-hat = baseline sd inflated by
EDETECT_SIGMA_INFL. Each mixture component k then multiplies the baseline
increment

    L_n(k) = exp( lam_k * (X_n - mu_k)/sigma_hat - lam_k^2 / 2 ),

where component k's null boundary carries its OWN analytic margin,
mu_k = mu0 +/- (pad + |lam_k| sigma_hat / 2). That margin makes validity
exact, not approximate: whenever the true pre-change stream is
sigma-sub-Gaussian with sigma <= sigma_hat and mean inside the padded
bound, E[L_n(k) | F_{n-1}] <= exp(-lam_k^2 / 2) < 1 (the margin absorbs the
quadratic term at the worst case sigma = sigma_hat). Each component runs
the Shiryaev–Roberts recursion M_n = L_n * (M_{n-1} + 1) — the sum of
e-processes started at consecutive times — and the published statistic is
the uniform mixture over the FIXED a-priori lambda grid (a mixture of
e-detectors is an e-detector, Prop. 2.3). Alarm when the mixture reaches
1/alpha (Thm. 2.4: ARL >= 1/alpha). The change-date estimate is the SR
structure's own: the restart index whose e-process dominates at the alarm —
argmax_j Lambda^{(j)} — read off the dominant component's cumsum minimum.
After each alarm the accumulator restarts from zero so a further change can
be flagged; the FIRST alarm of each run is the one the warranty speaks for
cleanly.

Placement among the siblings:
  - Turn, Tidetables and the Stack forecast stress LEVELS ahead; the
    E-Detector testifies that the regime itself is no longer the one the
    baseline knew. A level can be forecastable inside a regime that has
    already changed — different question, orthogonal answer.
  - Undertow watches damping drift within a regime; the E-Detector rules on
    the regime break itself, with a stated price for crying wolf.
  - Markov's regimes are probabilistic states; a detection here is a
    WARRANTY-BACKED statement, not a state probability.

Honesty notes:
  - expanding only, zero look-ahead: calibration is a fixed PREFIX of the
    given series, the recursion is strictly causal, and resets depend only on
    the past — analyze(s[:k]) reproduces the full run restricted to k
    (unit-tested house invariant);
  - the warranty is exact for streams inside the calibrated class
    (sigma-sub-Gaussian, sigma <= sigma_hat, mean within the padded bound).
    Calibration can still miss: under Gaussian baseline noise the 3-se mean
    pad fails ~0.3% of windows per side and the 1.5x scale inflation fails
    only if the baseline sd underestimates by >33% (~1% at n=40) — the
    residual calibration risk is stated here, not hidden;
  - two independent streams are monitored (SOFR-IORB spread and the SOFR
    tail detachment); by the union bound the system-level warranty halves to
    1/(2*alpha) days — stated, not hidden;
  - sensitivity is the price of the warranty: shifts below ~0.5 sigma-hat
    accrue evidence slowly (expected delay is published in `method`); this
    detector rules on regime breaks, it does not scan for drifts;
  - increments are clipped at EDETECT_Z_CLIP sigma-hat so one corrupted print
    cannot manufacture or overflow evidence (conservative under the null,
    negligible under any plausible alternative);
  - NO composite score: a detection is a testimony with a warranty, but the
    e-value path itself is context about the basin, not evidence of stress
    today (doctrine).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --- Fixed a priori (published, never tuned on the stream) -------------------
EDETECT_ALPHA = 1e-3            # alarm threshold is 1/alpha; ARL >= 1/alpha days
EDETECT_MIN_HISTORY_D = 60      # below this there is no honest calibration
EDETECT_BASELINE_BD = 40        # fixed prefix that DEFINES the pre-change class
EDETECT_LAMBDAS = (-2.0, -1.0, -0.5, -0.25, 0.25, 0.5, 1.0, 2.0)
EDETECT_MU_PAD_SE = 3.0         # baseline-mean padding (standard errors, per direction)
EDETECT_SIGMA_INFL = 1.5        # baseline-scale inflation (warranty needs sigma_true <= sigma_hat)
EDETECT_Z_CLIP = 12.0           # standardized-increment clip (corruption guard)

_THRESHOLD = 1.0 / EDETECT_ALPHA
_WARRANTY = f"1 false alarm per {int(round(_THRESHOLD))} days in expectation"


def _run_detector(x: np.ndarray, idx: pd.DatetimeIndex) -> dict:
    """Mixture SR e-detector on one stream. x is NaN-free, idx its dates.

    Returns the per-stream block (JSON-safe) plus the private log10 mixture
    path. Pure forward recursion on the prefix — truncation equality holds
    bitwise."""
    n = int(x.size)
    base = x[:EDETECT_BASELINE_BD]
    sd = float(base.std(ddof=1)) if base.size > 1 else 0.0
    sigma = max(EDETECT_SIGMA_INFL * sd, 1e-9)
    se = float(sd / np.sqrt(base.size)) if base.size > 1 else 0.0
    mu_hat = float(base.mean())
    pad = EDETECT_MU_PAD_SE * se

    lams = np.asarray(EDETECT_LAMBDAS, dtype=float)
    # Per-component null boundary with its OWN analytic margin: for lam > 0,
    # mu_k = mu0 + pad + lam*sigma/2 — then for ANY pre-change stream that is
    # sigma_t-sub-Gaussian with sigma_t <= sigma and mean <= mu0 + pad,
    #   E[L(k)] <= exp( lam(mu0+pad-mu_k)/sigma + lam^2 sigma_t^2/(2 sigma^2) - lam^2/2 )
    #           <= exp( -lam^2/2 ) < 1,
    # the lam*sigma/2 margin absorbing the quadratic term at the worst case
    # sigma_t = sigma. Symmetric for lam < 0. Validity is exact inside the
    # calibrated class; only calibration error can void it (see caveats).
    mu_k = mu_hat + np.where(lams > 0.0, pad + lams * sigma / 2.0,
                                           -pad + lams * sigma / 2.0)  # lams<0: -pad - |lam| sigma/2
    z_mat = np.clip((x[None, :] - mu_k[:, None]) / sigma, -EDETECT_Z_CLIP, EDETECT_Z_CLIP)
    ell_mat = lams[:, None] * z_mat - 0.5 * (lams ** 2)[:, None]

    k_n = lams.size
    m = np.zeros(k_n)                     # per-component SR accumulators
    run_start = EDETECT_BASELINE_BD       # first obs of the current run
    log_path = np.full(n, np.nan)         # log10 mixture, detector phase only
    detections: list[dict] = []

    for i in range(EDETECT_BASELINE_BD, n):
        m = np.exp(ell_mat[:, i]) * (m + 1.0)  # SR recursion: restarts at consecutive times
        mix = float(m.mean())             # uniform mixture — still an e-detector
        log_path[i] = np.log10(max(mix, 1e-300))
        if mix >= _THRESHOLD:
            k_star = int(np.argmax(m))
            lam_star = float(lams[k_star])
            # change-date estimate: argmax_j Lambda_i^{(j)} on the dominant
            # component == 1 + argmin of its run cumsum over u in
            # [run_start-1, i-1] (C_i itself would be the empty product j=i+1)
            ell_seg = ell_mat[k_star, run_start : i + 1]
            c = np.concatenate(([0.0], np.cumsum(ell_seg)))  # C_u, u = run_start-1..i
            change_pos = run_start + int(np.argmin(c[:-1]))  # j* = argmin C_{j-1} + 1
            detections.append({
                "pos": i,
                "date": idx[i].date().isoformat(),
                "change_pos": int(change_pos),
                "change_date": idx[change_pos].date().isoformat(),
                "direction": "up" if lam_star > 0.0 else "down",
                "lambda_star": lam_star,
                "delay_bd": int(i - change_pos + 1),
                "e_value": round(mix, 1),
                "log10_e_value": round(float(log_path[i]), 3),
            })
            m[:] = 0.0                    # restart the watch; the warranty resets with it
            run_start = i + 1

    det_phase = log_path[EDETECT_BASELINE_BD:]
    m_now = mix  # the final day's mixture BEFORE any same-day reset (loop always runs: n > baseline)
    last = detections[-1] if detections else None
    block = {
        "ok": True,
        "n_days": n,
        "baseline": {
            "bd": EDETECT_BASELINE_BD,
            "mu0_bp": round(mu_hat, 3),
            "sigma_hat_bp": round(sigma, 3),
            "mean_pad_bp": round(pad, 3),
            "null_class": (
                f"sigma-sub-Gaussian, sigma <= {sigma:.3g}bp, mean within "
                f"mu0 +/- ({pad:.3g} + |lam|*{sigma:.3g}/2) bp per component"
            ),
        },
        "lambda_grid": [float(v) for v in EDETECT_LAMBDAS],
        "alpha": EDETECT_ALPHA,
        "threshold": _THRESHOLD,
        "arl_warranty": _WARRANTY,
        "e_value": round(m_now, 4),
        "log10_e_value": round(float(np.log10(max(m_now, 1e-300))), 3),
        "alarm_now": bool(m_now >= _THRESHOLD),
        "detected": bool(detections),
        "n_detections": int(len(detections)),
        "days_since_last_detection": int(n - 1 - last["pos"]) if last else None,
        "change_date": last["change_date"] if last else None,
        "change_pos": last["change_pos"] if last else None,
        "last_detection": last,
        "detections": detections,
        "max_log10_e_value": round(float(np.max(det_phase)), 3),
    }
    series = pd.Series(det_phase, index=idx[EDETECT_BASELINE_BD:], dtype=float)
    return {"block": block, "series": series}


def analyze(spread_bp: pd.Series, tail_bp: pd.Series | None = None) -> dict:
    """Mixture SR e-detectors on the two funding streams. Inputs: SOFR-IORB
    spread in bp (daily DatetimeIndex) and, optionally, the SOFR tail
    detachment (P99-P50) in bp; each stream gets its own independent
    detector on the same fixed lambda grid. Publishes current e-values,
    detection flags and change-date estimates under a nonasymptotic ARL
    warranty. No composite score — context, not evidence."""
    spread = spread_bp.dropna()
    n = int(len(spread))
    if n < EDETECT_MIN_HISTORY_D:
        return {"ok": False, "reason": f"insufficient history ({n}d < {EDETECT_MIN_HISTORY_D}d)"}

    s_res = _run_detector(spread.to_numpy(dtype=float), spread.index)

    tail_block, tail_series = None, None
    if tail_bp is None:
        tail_note = "tail stream not provided — the spread detector alone stands watch"
    else:
        tail = tail_bp.dropna()
        if len(tail) < EDETECT_MIN_HISTORY_D:
            tail_block = {"ok": False, "reason": f"insufficient tail history ({len(tail)}d < {EDETECT_MIN_HISTORY_D}d)"}
            tail_note = "tail stream too thin to calibrate — refused rather than guessed"
        else:
            t_res = _run_detector(tail.to_numpy(dtype=float), tail.index)
            tail_block, tail_series = t_res["block"], t_res["series"]
            tail_note = None

    caveats = [
        f"the pre-change class is calibrated on the FIRST {EDETECT_BASELINE_BD} days only "
        f"(mean bound padded {EDETECT_MU_PAD_SE:g} se, scale inflated {EDETECT_SIGMA_INFL:g}x, "
        f"plus a per-component |lam|*sigma/2 margin): the ARL warranty is exact INSIDE that "
        f"calibrated class — under Gaussian baseline noise the padding fails only for a "
        f">3-sigma calibration draw (~0.3% per side) or a >33% scale underestimate (~1% at "
        f"n={EDETECT_BASELINE_BD}), and heavier-than-Gaussian pre-change tails can void it "
        f"entirely; the guarantee is exactly as good as this calibration",
        "sensitivity is the price of the warranty: shifts below ~0.5 sigma-hat accrue evidence "
        "too slowly to alarm quickly — this tripwire rules on regime breaks, it does not scan "
        "for drifts",
        "two independent detectors run (spread + tail); by the union bound the SYSTEM-level "
        f"warranty halves to 1 false alarm per {int(round(_THRESHOLD / 2))} days in expectation",
        "after each alarm the accumulator restarts from zero: the FIRST alarm of a run carries "
        "the clean warranty; subsequent alarms are conditional statements about further changes",
        f"increments are clipped at ±{EDETECT_Z_CLIP:g} sigma-hat so one corrupted print cannot "
        "manufacture evidence or overflow the accumulator",
        "a detection says the regime CHANGED relative to the baseline window — including a "
        "slow permanent drift away from it; read the change-date and direction, not just the flag",
        "no composite score: the e-value path is context about the basin, not evidence of "
        "stress today (doctrine)",
    ]
    if tail_note:
        caveats.append(tail_note)

    k = len(EDETECT_LAMBDAS)
    method = (
        f"Shin–Ramdas–Rinaldo e-detector (arXiv:2203.03532), sub-Gaussian instantiation, per "
        f"stream. Pre-change class calibrated on the first {EDETECT_BASELINE_BD} obs: mean bound "
        f"mu0 +/- {EDETECT_MU_PAD_SE:g} se, scale sigma_hat = {EDETECT_SIGMA_INFL:g}x baseline sd. "
        f"Baseline increments L_n(k) = exp(lam_k*(X_n - mu_k)/sigma_hat - lam_k^2/2) on the FIXED "
        f"a-priori grid lam in {list(EDETECT_LAMBDAS)} (K={k}, uniform mixture — Prop. 2.3), each "
        f"component's null boundary mu_k = mu0 +/- (pad + |lam| sigma_hat/2) so validity is exact "
        f"inside the class (E[L] <= exp(-lam^2/2)). Each component runs Shiryaev–Roberts "
        f"M_n = L_n*(M_{{n-1}} + 1), the sum of e-processes restarted at consecutive times. Alarm "
        f"when the mixture reaches 1/alpha = {_THRESHOLD:.0f} (Thm. 2.4: E[N*] >= 1/alpha = "
        f"{_THRESHOLD:.0f} days, nonasymptotic). Change-date = argmax restart index of the "
        f"dominant component's e-process at the alarm. Expected delay for a shift of D sigma-hat: "
        f"~ (ln(1/alpha) + ln K) / max_lam(lam*D - lam^2/2) days. Accumulator restarts from zero "
        f"after each alarm."
    )

    return {
        "ok": True,
        "asof": spread.index[-1].date().isoformat(),
        "alpha": EDETECT_ALPHA,
        "threshold": _THRESHOLD,
        "lambda_grid": [float(v) for v in EDETECT_LAMBDAS],
        "baseline_bd": EDETECT_BASELINE_BD,
        "arl_warranty": _WARRANTY,
        "warranty_note": (
            "The threshold is not backtested or tuned: for ANY pre-change distribution in the "
            "calibrated class, thresholding an e-detector at 1/alpha yields E[N*] >= 1/alpha — a "
            "nonasymptotic Frequentist average-run-length guarantee (arXiv:2203.03532, Thm. 2.4), "
            "valid at every sample size with no asymptotic regime and no multiple-comparison "
            "penalty for continuous monitoring. The warranty prices false alarms; it says "
            "nothing about the cause of a true one."
        ),
        "streams": {"spread": s_res["block"], "tail": tail_block},
        "caveats": caveats,
        "method": method,
        "_log_m": {"spread": s_res["series"], "tail": tail_series},
    }
