import { useMemo, useState } from "react";
import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Fault, Method, Roll } from "../lib";
import "../styles-fx.css";

const BUCKET_COLORS: Record<string, string> = {
  year_turn: P.stress,
  quarter_turn: P.strain,
  month_end: P.accent,
  tax_date: P.gold,
  mid_month: P.accentSoft,
  plain: P.ghost,
};

function RiptideCard({ r }: { r: Any }) {
  if (!r?.ok) return <Fault name="Riptide" reason={r?.reason} span={12} />;
  const lv = r.live;
  const v = r.validation?.sticky ?? {};
  return (
    <div className="card span12">
      <h2>Riptide ★</h2>
      <div className="sub">
        the pop prognosis — the morning the spread pops: chop (calendar mechanics) or current
        (genuine scarcity)? discriminators: RRP co-sign, calendar, damping state
      </div>
      {lv ? (
        <div className="tellhero">
          <div className={`tellvalue ${(lv.p_sticky ?? 0) >= 0.5 ? "hot" : ""}`}>
            {lv.p_sticky != null ? `${fmt(lv.p_sticky * 100, 0)}%` : "—"}
          </div>
          <div>
            <div className="tellreading">{lv.verdict}</div>
            <div className="coverage">
              {lv.pop_bp}bp pop on {lv.date} ({lv.bucket?.replace("_", "-")}, {lv.age_bd}bd ago) ·
              P(escalates to ≥10bp) {lv.p_escalates != null ? `${fmt(lv.p_escalates * 100, 0)}%` : "—"} ·
              RRP co-sign <b style={{ color: lv.rrp_cosigned ? undefined : P.stress }}>
                {lv.rrp_cosigned ? "present (choreography)" : "ABSENT (scarcity)"}</b>
            </div>
          </div>
        </div>
      ) : (
        <div className="allclear">▮ flat water — no live pop in the last {5} business days; that IS the reading</div>
      )}
      <table className="mini">
        <thead><tr><th>pop</th><th>size</th><th>bucket</th><th>RRP co-z</th><th>stuck</th><th>escalated</th></tr></thead>
        <tbody>
          {(r.receipts ?? []).map((p: Any) => (
            <tr key={p.date}>
              <td>{p.date}</td>
              <td className="num">{fmt(p.pop_bp, 1)}bp</td>
              <td>{p.bucket?.replace("_", "-")}</td>
              <td className="num">{fmt(p.rrp_co_z, 1)}</td>
              <td style={{ color: p.stuck ? P.stress : undefined }}>{p.stuck ? "yes" : "no"}</td>
              <td style={{ color: p.escalated ? P.stress : undefined }}>{p.escalated == null ? "—" : p.escalated ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="coverage">
        sticky base rate {fmt((r.sticky_base?.rate ?? 0) * 100, 0)}%
        {r.sticky_base?.ci95 ? ` (CI ${fmt(r.sticky_base.ci95[0] * 100, 0)}–${fmt(r.sticky_base.ci95[1] * 100, 0)}%)` : ""} over {r.n_resolved} resolved pops ·
        walk-forward: AUROC {fmt(v.auroc, 2)} · Brier {fmt(v.brier, 4)} vs base {fmt(v.brier_base, 4)}
      </div>
      <Method>{(r.caveats ?? []).join(" · ")} · {r.method}</Method>
    </div>
  );
}

function BreakwaterCard({ b }: { b: Any }) {
  if (!b?.ok) return <Fault name="The Breakwater" reason={b?.reason} span={12} />;
  const t = b.revealed_threshold ?? {};
  return (
    <div className="card span12">
      <h2>The Breakwater ★</h2>
      <div className="sub">
        the rescuer modeled — every Fed intervention is a confession of where its pain threshold sat;
        nobody else instruments the goalie
      </div>
      <div className="tellhero">
        <div className={`tellvalue ${(b.rescue_proximity ?? 0) >= 90 ? "hot" : ""}`}>{fmt(b.rescue_proximity, 0)}%</div>
        <div>
          <div className="tellreading">
            rescue proximity — board at the {fmt(b.current?.spread_pctl, 0)}th pctl vs revealed threshold
            median {fmt(t.median_pctl, 0)}th (range {fmt(t.min_pctl, 0)}–{fmt(t.max_pctl, 0)}, n={t.n})
          </div>
          <div className="coverage">{b.reading} · {b.posture}</div>
        </div>
      </div>
      <table className="mini">
        <thead><tr><th>intervention</th><th>kind</th><th>board pctl day before</th><th>20d max spread</th><th>20d max SRF</th></tr></thead>
        <tbody>
          {(b.interventions ?? []).filter((r: Any) => r.in_sample).map((r: Any) => (
            <tr key={r.date}>
              <td>{r.date} — {r.label}{r.dating && r.dating !== "public record" && <span className="dimsmall"> †{r.dating}</span>}</td>
              <td>{r.kind}</td>
              <td className="num">{fmt(r.spread_pctl_before, 0)}th</td>
              <td className="num">{fmt(r.spread_max20_bp, 1)}bp</td>
              <td className="num">{r.srf_max20_b != null ? `$${fmt(r.srf_max20_b, 1)}B` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{(b.caveats ?? []).join(" · ")} · {b.method}</Method>
    </div>
  );
}

function SwellCard({ s }: { s: Any }) {
  if (!s?.ok) return <Fault name="Swell Forecast" reason={s?.reason} span={12} />;
  const hz = s.event_by_horizon ?? {};
  const v = s.validation ?? {};
  // must match the backend's own verdict condition (Brier AND AUROC) — never
  // paint a self-demoting verdict green
  const beats = v.ok && v.brier < v.brier_climatology && (v.auroc ?? 0) > 0.55;
  const state = s.state ?? {};
  const rows: (string | number | null)[][] = (s.curve ?? []).map((r: Any) => [
    r.date,
    r.p10 * 100,
    r.cum10 * 100,
    r.p2 * 100,
  ]);
  const settleDates = (s.curve ?? []).filter((r: Any) => r.settle_b >= s.settlement?.flag_b).map((r: Any) => r.date);
  return (
    <div className="card span12">
      <h2>Swell Forecast ★</h2>
      <div className="sub">
        the funding-stress forward curve — P(SOFR−IORB pop ≥ x bp) for each of the next {s.horizon_bd} business
        days, from the public forcing calendar + the basin's damping state · a marine forecast for the plumbing
      </div>
      <div className="tellhero">
        <div className={`tellvalue ${(s.p_event_5bd ?? 0) >= 0.5 ? "hot" : ""}`}>{fmt((s.p_event_5bd ?? 0) * 100, 0)}%</div>
        <div>
          <div className="tellreading">
            P(funding event within 5bd) · 10bd {fmt((hz.h10 ?? 0) * 100, 0)}% · 21bd {fmt((hz.h21 ?? 0) * 100, 0)}% ·
            {" "}{s.horizon_bd}bd {fmt((hz[`h${s.horizon_bd}`] ?? 0) * 100, 0)}%
            <span className="dimsmall"> (h≥10 assume day-independence — upper bounds; only 5bd is validated)</span>
          </div>
          <div className="coverage">
            peak day <b style={{ color: P.strain }}>{s.peak?.date}</b> ({s.peak?.bucket?.replace("_", "-")},
            P(≥10bp) {fmt((s.peak?.p10 ?? 0) * 100, 0)}%)
            {state.available && (
              <> · damping state {state.hot
                ? <b style={{ color: P.stress }}>HOT · lift {fmt(state.lift_10bp, 1)}×</b>
                : <span className="dimsmall">calm</span>}</>
            )}
            {" "}· asof {s.asof}
          </div>
          <div className="coverage" style={{ color: beats ? P.calm : P.erosion }}>
            {v.ok
              ? `walk-forward: AUROC ${fmt(v.auroc, 2)} · Brier ${fmt(v.brier, 4)} vs climatology ${fmt(v.brier_climatology, 4)} — ${v.verdict}`
              : v.reason ?? "validation not run"}
          </div>
        </div>
      </div>
      <Chart
        rows={rows}
        series={[
          { label: "P(≥10bp) per day", color: P.strain },
          { label: "P(event by date)", color: P.calm },
          { label: "P(≥2bp) per day", color: P.ghost, dash: [2, 3] },
        ]}
        yLabel="%"
        vlines={settleDates.length ? { dates: settleDates, color: P.accentSoft } : null}
      />
      <table className="mini">
        <thead>
          <tr>
            <th>forcing bucket</th><th>n days</th><th>P(≥2bp)</th><th>P(≥5bp)</th>
            <th>P(≥10bp)</th><th>95% CI</th><th>P(≥20bp)</th>
          </tr>
        </thead>
        <tbody>
          {(s.buckets ?? []).map((b: Any) => (
            <tr key={b.bucket}>
              <td>
                <span style={{ color: BUCKET_COLORS[b.bucket] }}>▮</span> {b.label}
                {b.low_n && <span className="dimsmall" title="few observations — rate shrunk toward its parent bucket's evidence"> †low-n</span>}
              </td>
              <td className="num">{b.n}</td>
              <td className="num">{fmt(b.p2 * 100, 1)}%</td>
              <td className="num">{fmt(b.p5 * 100, 1)}%</td>
              <td className="num" style={{ color: b.p10 >= 0.1 ? P.stress : undefined }}>{fmt(b.p10 * 100, 1)}%</td>
              <td className="num dimsmall">{b.ci95_10bp ? `${fmt(b.ci95_10bp[0] * 100, 0)}–${fmt(b.ci95_10bp[1] * 100, 0)}%` : "—"}</td>
              <td className="num">{fmt(b.p20 * 100, 1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(v.reliability ?? []).length > 0 && (
        <table className="mini">
          <thead>
            <tr><th>predicted bin</th><th>mean pred</th><th>realized</th><th>realized CI</th><th>n</th></tr>
          </thead>
          <tbody>
            {v.reliability.map((r: Any) => (
              <tr key={r.bin}>
                <td>{r.bin}</td>
                <td className="num">{fmt(r.mean_pred * 100, 1)}%</td>
                <td className="num">{fmt(r.realized * 100, 1)}%</td>
                <td className="num dimsmall">{r.realized_ci95 ? `${fmt(r.realized_ci95[0] * 100, 0)}–${fmt(r.realized_ci95[1] * 100, 0)}%` : "—"}</td>
                <td className="num dimsmall">{r.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Method>{(s.caveats ?? []).join(" · ")} · {s.method}</Method>
    </div>
  );
}

function PotentialSVG({ floor, popNow }: { floor: Any; popNow: number | null }) {
  const curve: number[][] = (floor?.curve ?? []) as number[][];
  if (!curve.length) return null;
  const W = 600, H = 150, PAD = 8;
  const xs = curve.map((r) => r[0]);
  const vs = curve.map((r) => r[1]);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const vmax = Math.max(...vs, 1e-9);
  const px = (x: number) => PAD + ((x - xmin) / (xmax - xmin)) * (W - 2 * PAD);
  const py = (v: number) => H - PAD - (v / vmax) * (H - 2 * PAD);
  const pts = curve.map((r) => `${px(r[0]).toFixed(1)},${py(r[1]).toFixed(1)}`).join(" ");
  // interpolate V at today's state for the ball marker
  let ball: { x: number; y: number } | null = null;
  if (popNow != null) {
    const cx = Math.min(Math.max(popNow, xmin), xmax);
    let k = 0;
    while (k < curve.length - 2 && curve[k + 1][0] < cx) k++;
    const [x0, v0] = curve[k], [x1, v1] = curve[k + 1];
    const v = x1 === x0 ? v0 : v0 + ((cx - x0) / (x1 - x0)) * (v1 - v0);
    ball = { x: px(cx), y: py(v) };
  }
  const evX = px(10.0 > xmax ? xmax : 10.0);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "150px", display: "block" }}>
      <line x1={evX} y1={PAD} x2={evX} y2={H - PAD} stroke={P.stress} strokeDasharray="3 4" strokeWidth={1} />
      <text x={evX - 4} y={PAD + 10} fill={P.stress} fontSize={10} textAnchor="end" fontFamily="Inter, sans-serif">
        event ≥10bp
      </text>
      <polyline points={pts} fill="none" stroke={P.slate} strokeWidth={1.6} />
      {ball && <circle cx={ball.x} cy={ball.y} r={4.5} fill={P.erosion} stroke={P.bg} strokeWidth={1} />}
      <text x={PAD + 2} y={H - PAD - 2} fill={P.faint} fontSize={10} fontFamily="Inter, sans-serif">
        V(x) — the basin floor · ● today
      </text>
    </svg>
  );
}

function BathymetryCard({ b }: { b: Any }) {
  if (!b?.ok) return <Fault name="Bathymetry" reason={b?.reason} span={12} />;
  const fl = b.floor ?? {}, sp = b.spectrum ?? {}, ar = b.arrow ?? {}, v = b.validation ?? {};
  const hz = b.p_by_horizon ?? {};
  const beats = v.ok && v.brier < v.brier_climatology && (v.auroc ?? 0) > 0.55;
  const rows: (string | number | null)[][] = (b.series ?? []).map((r: Any) => [r[0], r[1], r[2]]);
  const mfpt = b.mfpt_bd;
  return (
    <div className="card span12">
      <h2>Bathymetry ★</h2>
      <div className="sub">
        the basin floor mapped from the water's motion — empirical Langevin potential, the
        Fokker–Planck↔Schrödinger energy spectrum, entropy production (the arrow of time), and the
        Kramers escape problem solved as a forecast
      </div>
      <div className="tellhero">
        <div className={`tellvalue ${(b.p_event_5bd ?? 0) >= 0.5 ? "hot" : ""}`}>
          {b.p_event_5bd != null ? `${fmt(b.p_event_5bd * 100, 0)}%` : "—"}
        </div>
        <div>
          <div className="tellreading">
            first-passage P(funding event within 5bd) · 1bd {fmt((hz.h1 ?? 0) * 100, 0)}% ·
            10bd {fmt((hz.h10 ?? 0) * 100, 0)}% ·
            {" "}expected first passage {b.state_now?.in_event_bin
              ? <b style={{ color: P.stress }}>state already in the event bin</b>
              : mfpt != null
                ? <b>{fmt(mfpt, 0)}bd</b>
                : <span className="dimsmall">beyond {b.mfpt_cap_bd}bd — the well holds</span>}
          </div>
          <div className="coverage">
            state x = {fmt(b.state_now?.pop_bp, 1)}bp · well at {fmt(fl.well_bp, 1)}bp ·
            stiffness {fmt(fl.stiffness, 2)}/bd · escape barrier{" "}
            <b style={{ color: (fl.barrier_kt ?? 99) < 2 ? P.stress : undefined }}>{fmt(fl.barrier_kt, 1)} k<sub>B</sub>T</b> ·
            τ (slowest relaxation) {fmt(sp.tau_bd, 1)}bd
            {sp.tau_pctl != null && <b style={{ color: sp.tau_pctl >= 80 ? P.stress : undefined }}> ({fmt(sp.tau_pctl, 0)}th pctl)</b>} ·
            entropy production {fmt(ar.sigma_nats_bd, 3)} nats/bd
            {ar.pctl != null && <b style={{ color: ar.pctl >= 80 ? P.stress : undefined }}> ({fmt(ar.pctl, 0)}th pctl)</b>}
          </div>
          <div className="coverage" style={{ color: beats ? P.calm : P.erosion }}>
            {v.ok
              ? `walk-forward: AUROC ${fmt(v.auroc, 2)} · Brier ${fmt(v.brier, 4)} vs climatology ${fmt(v.brier_climatology, 4)} — ${v.verdict}`
              : v.reason ?? "validation not run"}
          </div>
        </div>
      </div>
      <PotentialSVG floor={fl} popNow={b.state_now?.pop_bp ?? null} />
      <Chart
        rows={rows}
        series={[
          { label: "τ relaxation (bd)", color: P.accentSoft },
          { label: "entropy production (nats/bd)", color: P.strain, dash: [4, 3] },
        ]}
      />
      <div className="coverage">
        energy levels E₁..E₄ = −ln|λ| per bd: {(sp.energy_levels ?? []).map((e: number) => fmt(e, 2)).join(" · ")} ·
        spectral gap {fmt(sp.gap, 3)} · {b.n_transitions} transitions learned
      </div>
      <Method>{(b.caveats ?? []).join(" · ")} · {b.method}</Method>
    </div>
  );
}

export function TideTablesCard({ t }: { t: Any }) {
  if (!t?.ok) return <Fault name="Tide Tables" reason={t?.reason} span={12} />;
  const odds = t.event_odds ?? {}, nov = t.novelty ?? {}, skill = t.skill ?? {};
  const uncharted = nov.verdict === "uncharted";
  const beats = skill.ok && skill.brier < skill.brier_climatology;
  // one connected chart: trailing actual spread, then the analog fan.
  const rows: (string | number | null)[][] = (t.recent_spread ?? []).map(
    (r: Any) => [r[0], r[1], null, null, null, null, null]
  );
  if (rows.length) {
    const now = t.spread_now_bp;
    rows[rows.length - 1] = [rows[rows.length - 1][0], now, now, now, now, now, now];
  }
  for (const f of t.fan ?? []) rows.push([f.date, null, f.p10, f.p25, f.median, f.p75, f.p90]);
  return (
    <div className="card span12">
      <h2>Tide Tables ★</h2>
      <div className="sub">
        what happened next, the last {odds.n} times the water looked like this —
        {t.k} nearest analogs over all history (window {t.window_d}bd), no labels, no look-ahead
      </div>
      <div className="tellhero">
        <div className={`tellvalue ${odds.p >= 0.5 ? "hot" : ""}`}>{fmt(odds.p * 100, 0)}%</div>
        <div>
          <div className="tellreading">
            of nearest analogs saw a funding event within 5bd
            {odds.ci95 ? ` (CI ${fmt(odds.ci95[0] * 100, 0)}–${fmt(odds.ci95[1] * 100, 0)}%)` : ""}
          </div>
          <div className="coverage">
            base rate {fmt(odds.base_rate * 100, 0)}% · lift {fmt(odds.lift, 1)}× ·
            water <b style={{ color: uncharted ? P.stress : undefined }}>{nov.verdict}</b>
            {nov.pctl != null ? ` (NN-distance ${fmt(nov.pctl, 0)}th pctl)` : ""} · asof {t.asof}
          </div>
          <div className="coverage" style={{ color: beats ? P.calm : P.stress }}>
            hindcast: {skill.ok
              ? `Brier ${fmt(skill.brier, 3)} vs climatology ${fmt(skill.brier_climatology, 3)} · AUROC ${fmt(skill.auroc, 2)} — ${skill.verdict}`
              : skill.reason ?? "not run"}
          </div>
        </div>
      </div>
      <Chart
        rows={rows}
        series={[
          { label: "SOFR−IORB", color: P.slate },
          { label: "p10", color: P.ghost, dash: [2, 3] },
          { label: "p25", color: P.accentSoft, dash: [4, 3] },
          { label: "analog median", color: P.erosion },
          { label: "p75", color: P.accentSoft, dash: [4, 3] },
          { label: "p90", color: P.ghost, dash: [2, 3] },
        ]}
        yLabel="bp"
        refLine={{ value: 0, color: P.ghost, label: "" }}
      />
      <table className="mini">
        <thead>
          <tr><th>analog ends</th><th>distance</th><th>max move next 5bd</th><th>event ≤5bd</th><th>run-up to</th></tr>
        </thead>
        <tbody>
          {(t.analogs ?? []).slice(0, 8).map((a: Any) => (
            <tr key={a.end_date}>
              <td>{a.end_date}</td>
              <td className="num dimsmall">{fmt(a.distance, 2)}</td>
              <td className="num" style={{ color: a.max_move_5bd_bp > 5 ? P.stress : undefined }}>
                {a.max_move_5bd_bp > 0 ? "+" : ""}{fmt(a.max_move_5bd_bp, 1)}bp
              </td>
              <td>{a.event_within_5bd ? <b style={{ color: P.stress }}>yes</b> : <span className="dimsmall">no</span>}</td>
              <td className="dimsmall">{a.episode ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{(t.caveats ?? []).join(" · ")} · {t.method}</Method>
    </div>
  );
}

function SeaStateCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Sea State" reason={e?.reason} span={6} />;
  const rough = (e.p_rough_now ?? 0) >= 0.5;
  const v = e.validation ?? {};
  return (
    <div className="card span6">
      <h2>Sea State</h2>
      <div className="sub">
        the regime estimated, not asserted — a 2-state hidden Markov model (Hamilton filter, strictly
        causal) on the spread residual publishes FILTERED P(rough water); the composite's regime words
        are editorial, this is their statistical counterpart
      </div>
      <div className="kv">
        <div className="item"><div className="k">P(rough) now</div>
          <div className={`v ${rough ? "bad" : ""}`}>{fmt(e.p_rough_now, 2)}</div></div>
        <div className="item"><div className="k">calm water</div>
          <div className="v">σ {fmt(e.states?.calm?.sigma_bp, 1)}bp
            <span className="dimsmall"> · ~{fmt(e.states?.calm?.expected_duration_bd, 0)}bd spells</span></div></div>
        <div className="item"><div className="k">rough water</div>
          <div className="v">σ {fmt(e.states?.rough?.sigma_bp, 1)}bp
            <span className="dimsmall"> · ~{fmt(e.states?.rough?.expected_duration_bd, 0)}bd spells</span></div></div>
      </div>
      <Chart
        rows={e.rows ?? []}
        series={[{ label: "filtered P(rough water)", color: P.strain }]}
        yLabel="P"
      />
      <div className="dimsmall">{e.reading}</div>
      {v.ok && (
        <div className="dimsmall">
          walk-forward: AUROC {fmt(v.auroc_p_rough, 2)} vs climatology {fmt(v.auroc_climatology, 2)}
          {" "}({v.n_scored} scored) — {v.verdict}
        </div>
      )}
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

function SeaRoomCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Sea Room" reason={e?.reason} span={6} />;
  const t = e.today ?? {};
  const cov = e.coverage ?? {};
  const alarm = t.set === "event" || t.set === "empty";
  return (
    <div className="card span6">
      <h2>Sea Room</h2>
      <div className="sub">
        guaranteed coverage for the fleet's probability — adaptive conformal sets over
        {" {event, no-event}"} whose long-run coverage tracks {fmt((cov.target ?? 0.9) * 100, 0)}% even
        under regime drift (Gibbs–Candès ACI; label feedback honestly delayed 5bd)
      </div>
      <div className="kv">
        <div className="item"><div className="k">today's set</div>
          <div className={`v ${alarm ? "bad" : ""}`}>
            {t.set === "no_event" ? "{no event}" : t.set === "event" ? "{event}" :
             t.set === "both" ? "{event, no event}" : t.set === "empty" ? "∅ (nonconforming)" : "—"}</div></div>
        <div className="item"><div className="k">realized coverage</div>
          <div className="v">{fmt((cov.realized ?? 0) * 100, 1)}%
            <span className="dimsmall"> vs {fmt((cov.target ?? 0) * 100, 0)}% target · {cov.n_resolved_sets} resolved</span></div></div>
        <div className="item"><div className="k">informative days</div>
          <div className="v">{fmt((e.informative_rate ?? 0) * 100, 0)}%
            <span className="dimsmall"> ({fmt((e.informative_rate_250d ?? 0) * 100, 0)}% last 250d)</span></div></div>
        <div className="item"><div className="k">working α</div>
          <div className="v">{fmt(t.alpha_working, 3)}</div></div>
      </div>
      <div className="dimsmall">{t.reading}</div>
      <div className="dimsmall">{e.verdict}</div>
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   SwellCurveCard — the forward curve drawn as a curve, not a table.
   Main line = the engine's published P(≥10bp event by date) accumulating over
   the horizon; band = a ≥5bp…≥20bp severity envelope recomputed from the
   published per-day p5/p20 under the engine's own day-independence assumption
   (the same one it flags as an upper bound on h≥10). The line draws itself in
   via the house `svg path.draw` idiom; the band breathes and carries a light
   sweep (CSS, no-preference only); forcing days and heavy settlements are
   marked on the axis; hover reads any day off the curve.
   ------------------------------------------------------------------------- */
function SwellCurveCard({ s }: { s: Any }) {
  const [hover, setHover] = useState<number | null>(null);
  const curve: Any[] = s?.ok ? s.curve ?? [] : [];
  const m = useMemo(() => {
    if (!curve.length) return null;
    const n = curve.length;
    let c5 = 0, c20 = 0;
    const upper: number[] = [], lower: number[] = [], main: number[] = [];
    for (const r of curve) {
      const p5 = Math.min(Math.max(r.p5 ?? 0, 0), 1);
      const p20 = Math.min(Math.max(r.p20 ?? 0, 0), 1);
      c5 = 1 - (1 - c5) * (1 - p5);
      c20 = 1 - (1 - c20) * (1 - p20);
      upper.push(c5 * 100);
      lower.push(c20 * 100);
      main.push((r.cum10 ?? 0) * 100);
    }
    const W = 920, H = 260, PL = 46, PR = 16, PT = 16, PB = 30;
    const yMax = Math.max(...upper, 4) * 1.12;
    const x = (i: number) => PL + (i / Math.max(n - 1, 1)) * (W - PL - PR);
    const y = (v: number) => PT + (1 - v / yMax) * (H - PT - PB);
    const lineD = main.map((v, i) => `${i ? "L" : "M"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    const bandD =
      upper.map((v, i) => `${i ? "L" : "M"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ") +
      " " +
      lower.map((_, i) => `L ${x(n - 1 - i).toFixed(1)} ${y(lower[n - 1 - i]).toFixed(1)}`).join(" ") +
      " Z";
    return { n, W, H, PL, PR, PT, PB, yMax, x, y, lineD, bandD, main, upper, lower };
  }, [curve]);
  if (!s?.ok || !m) return null; // SwellCard above already carries the Fault

  const hz = s.event_by_horizon ?? {};
  const flagB = s.settlement?.flag_b ?? Infinity;
  const axisY = m.H - m.PB;
  const anchors = [5, 10, 21, 42].filter((h) => h <= m.n && hz[`h${h}`] != null);
  const hv = hover != null ? curve[hover] : null;

  return (
    <div className="card span12">
      <h2>Swell Forward Curve</h2>
      <div className="sub">
        P(funding event ≥10bp by date) accumulating across the next {s.horizon_bd} business days ·
        band = the ≥5bp…≥20bp severity envelope · colored ticks = forcing days, diamonds = heavy
        settlements (≥${fmt(flagB, 0)}B) · anchors = the engine's event_by_horizon checkpoints
      </div>
      <svg viewBox={`0 0 ${m.W} ${m.H}`} style={{ display: "block", width: "100%", height: "auto" }}
           role="img" aria-label="forward curve of funding-event probability by horizon">
        <defs>
          <linearGradient id="fxSwellShine" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#fff" stopOpacity="0" />
            <stop offset="0.5" stopColor="#fff" stopOpacity="0.10" />
            <stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
          <clipPath id="fxSwellBand"><path d={m.bandD} /></clipPath>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <g key={f}>
            <line x1={m.PL} x2={m.W - m.PR} y1={m.y(m.yMax * f)} y2={m.y(m.yMax * f)} stroke={P.grid} strokeWidth={1} />
            <text x={m.PL - 6} y={m.y(m.yMax * f) + 3} textAnchor="end" className="fx-axis">
              {fmt(m.yMax * f, 0)}%
            </text>
          </g>
        ))}
        <line x1={m.PL} x2={m.W - m.PR} y1={axisY} y2={axisY} stroke={P.grid} strokeWidth={1} />
        <path d={m.bandD} className="fx-band" stroke="none" />
        <g clipPath="url(#fxSwellBand)">
          <rect className="fx-shine" x={-180} y={0} width={150} height={m.H} fill="url(#fxSwellShine)" />
        </g>
        {curve.map((r, i) => {
          const bc = BUCKET_COLORS[r.bucket];
          const forcing = r.bucket && r.bucket !== "plain";
          const heavy = (r.settle_b ?? 0) >= flagB;
          return (
            <g key={r.date}>
              {forcing && (
                <line x1={m.x(i)} x2={m.x(i)} y1={axisY} y2={axisY - (r.bucket === "mid_month" ? 4 : 7)}
                      stroke={bc} strokeWidth={2} strokeOpacity={r.bucket === "mid_month" ? 0.55 : 0.9}>
                  <title>{`${r.date} · ${r.bucket.replace("_", "-")}`}</title>
                </line>
              )}
              {heavy && (
                <path d={`M ${m.x(i)} ${axisY + 4} l 3.6 3.6 l -3.6 3.6 l -3.6 -3.6 Z`} fill={P.erosion} fillOpacity={0.9}>
                  <title>{`${r.date} · $${fmt(r.settle_b, 0)}B settles (heavy day)`}</title>
                </path>
              )}
            </g>
          );
        })}
        {curve.map((r, i) =>
          i % 5 === 4 || i === m.n - 1 ? (
            <text key={`xl${i}`} x={m.x(i)} y={axisY + 22} textAnchor="middle" className="fx-axis">
              {String(r.date).slice(5)}
            </text>
          ) : null,
        )}
        <path d={m.lineD} pathLength={1} className="draw" fill="none" stroke={P.calm} strokeWidth={1.8} />
        {anchors.map((h) => (
          <g key={h}>
            <circle cx={m.x(h - 1)} cy={m.y(m.main[h - 1])} r={3} fill="#000" stroke={P.accentBright} strokeWidth={1.4} />
            <text x={m.x(h - 1)} y={m.y(m.main[h - 1]) - 8} textAnchor={h >= m.n ? "end" : "middle"}
                  className="fx-axis" fill={P.accentSoft}>
              {`h${h} ${fmt((hz[`h${h}`] ?? 0) * 100, 0)}%`}
            </text>
          </g>
        ))}
        {hover != null && (
          <g className="fx-cross">
            <line x1={m.x(hover)} x2={m.x(hover)} y1={m.PT} y2={axisY} stroke={P.ghost} strokeWidth={1} strokeDasharray="2 3" />
            <circle cx={m.x(hover)} cy={m.y(m.main[hover])} r={3.2} fill={P.calm} stroke="#000" strokeWidth={1} />
          </g>
        )}
        <rect x={m.PL} y={m.PT} width={m.W - m.PL - m.PR} height={m.H - m.PT - m.PB}
              fill="transparent"
              onMouseMove={(ev) => {
                const svg = ev.currentTarget.ownerSVGElement;
                if (!svg) return;
                const rect = svg.getBoundingClientRect();
                const vx = ((ev.clientX - rect.left) / rect.width) * m.W;
                const i = Math.round(((vx - m.PL) / (m.W - m.PL - m.PR)) * (m.n - 1));
                setHover(Math.max(0, Math.min(m.n - 1, i)));
              }}
              onMouseLeave={() => setHover(null)} />
      </svg>
      <div className="fx-readout">
        {hv ? (
          <>
            h+{hover! + 1}bd · <b>{hv.date}</b> · {String(hv.bucket).replace("_", "-")} ·
            P(≥10bp by then) <b>{fmt(m.main[hover!], 1)}%</b> · that day alone p10 {fmt((hv.p10 ?? 0) * 100, 1)}% ·
            envelope {fmt(m.lower[hover!], 1)}–{fmt(m.upper[hover!], 1)}%
            {(hv.settle_b ?? 0) > 0 && <> · ${fmt(hv.settle_b, 0)}B settles</>}
          </>
        ) : (
          <>hover the curve — per-day readout · band = ≥5bp…≥20bp cumulative envelope (day-independence, the engine's own h≥10 caveat — an upper bound)</>
        )}
      </div>
      <Method>
        cumulative line as published by the engine; envelope recomputed from its per-day p5/p20 under the
        same independence assumption · {s.method}
      </Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   QuantOddsStrip — three independent estimators of the same stress tail, side
   by side: regime counting (markov p_reach_stress), the analytic OU+jump
   endpoint (oujump p_above_stress), and the simulated path-max (montecarlo
   p_touch_stress). Numbers roll up on first paint via the shared Roll
   primitive (which itself honors reduced-motion).
   ------------------------------------------------------------------------- */
function QuantOddsStrip({ deep }: { deep: Any }) {
  const mk = deep.markov, ou = deep.oujump, mc = deep.montecarlo;
  const pct = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${fmt(v * 100, d)}%`);
  const tone = (v: number) =>
    v >= 0.5 ? P.stress : v >= 0.2 ? P.strain : v >= 0.05 ? P.erosion : undefined;
  const chips: { k: string; v: number | null; s: string }[] = [];
  if (mk?.ok) {
    const p = mk.p_reach_stress ?? {};
    chips.push({
      k: "markov · P(STRESS regime ≤ 21bd)",
      v: p.h21 ?? null,
      s: `5bd ${pct(p.h5)} · 10bd ${pct(p.h10)} · from ${mk.current_regime} · regime counting, STRESS absorbing`,
    });
  }
  if (ou?.ok) {
    const h21 = (ou.horizons ?? []).find((r: Any) => r.h === 21);
    chips.push({
      k: "ou+jump · P(above stress line @21bd)",
      v: h21?.p_above_stress ?? null,
      s: `endpoint, not path-max · jump share of tail ${pct(h21?.jump_share_of_tail, 0)} · τ½ ${fmt(ou.fit?.half_life_bd, 0)}bd`,
    });
  }
  if (mc?.ok) {
    const p = mc.p_touch_stress ?? {};
    chips.push({
      k: "montecarlo · P(touch stress ≤ 21bd)",
      v: p.h21 ?? null,
      s: `path-max (any crossing) · ${(mc.n_paths ?? 0).toLocaleString("en-US")} paths · P(back to calm) ${pct(mc.p_back_to_calm?.h21)}`,
    });
  }
  if (!chips.length) return null;
  return (
    <div className="card span12">
      <h2>Quant Odds — one tail, three estimators</h2>
      <div className="sub">
        the stress tail read three independent ways — they answer slightly different questions (regime
        entry · level at the endpoint · any touch along the path), so honest disagreement is the point
      </div>
      <div className="fx-chips">
        {chips.map((c, i) => (
          <div className="fx-chip" key={c.k} style={{ animationDelay: `${i * 0.12}s` }}>
            <div className="k">{c.k}</div>
            <div className="v" style={{ color: c.v != null ? tone(c.v) : undefined }}>
              <Roll v={c.v == null ? null : c.v * 100} d={1} unit="%" />
            </div>
            <div className="s">{c.s}</div>
          </div>
        ))}
      </div>
      <Method>
        markov: empirical transition counts · oujump: analytic OU+jump fit · montecarlo: seeded path
        simulation — all from the deep layer's published blocks, no recomputation
      </Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   CaesarCard — tomorrow's tail, estimated from the tail's own dynamics
   (engines.caesar): joint (VaR, ES) bands for the next business day's pop at
   95/99, walk-forward skill vs climatology stated as a loss ratio, and the
   verdict — which SELF-DEMOTES to "use climatology" when the model stops
   beating the unconditional band (never paint a demoted verdict green).
   ------------------------------------------------------------------------- */
function CaesarCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="CAESar" reason={e?.reason ?? "not in this snapshot"} span={12} />;
  const lv = e.levels ?? {};
  const demoted = e.verdict !== "use caesar";
  const bandTone = (v: number | null | undefined) =>
    (v ?? 0) >= 10 ? "bad" : (v ?? 0) >= 5 ? "warn" : "";
  return (
    <div className="card span12">
      <h2>CAESar</h2>
      <div className="sub">
        tomorrow's tail from the tail's own dynamics — CAViaR extended to a joint (VaR, ES) estimator;
        bands for the next business day's pop (SOFR−IORB vs its 5bd median) · {e.n_origins} scored
        origins · asof {e.asof}
      </div>
      <div className="kv">
        <div className="item"><div className="k">VaR 95 · next bd</div>
          <div className={`v ${bandTone(e.var95_bp)}`}>{fmt(e.var95_bp, 1)}bp</div></div>
        <div className="item"><div className="k">ES 95 · next bd</div>
          <div className={`v ${bandTone(e.es95_bp)}`}>{fmt(e.es95_bp, 1)}bp</div></div>
        <div className="item"><div className="k">VaR 99 · next bd</div>
          <div className={`v ${bandTone(e.var99_bp)}`}>{fmt(e.var99_bp, 1)}bp</div></div>
        <div className="item"><div className="k">ES 99 · next bd</div>
          <div className={`v ${bandTone(e.es99_bp)}`}>{fmt(e.es99_bp, 1)}bp</div></div>
        <div className="item"><div className="k">loss ratio vs climatology</div>
          <div className="v" style={{ fontSize: 13 }}>
            q95 {lv.q95?.loss_ratio_vs_climatology != null ? fmt(lv.q95.loss_ratio_vs_climatology, 3) : "n/a"}
            {" · "}q99 {lv.q99?.loss_ratio_vs_climatology != null ? fmt(lv.q99.loss_ratio_vs_climatology, 3) : "n/a"}
            <span className="dimsmall"> (below 1 beats the unconditional band)</span>
          </div></div>
        <div className="item"><div className="k">verdict</div>
          <div className="v" style={{ fontSize: 13, color: demoted ? P.erosion : P.calm }}>{e.verdict}</div></div>
      </div>
      <div className="dimsmall" style={{ marginTop: 4 }}>{e.verdict_detail}</div>
      {(e.reliability ?? []).length > 0 && (
        <table className="mini">
          <thead><tr><th>level</th><th>nominal tail</th><th>exceedance rate</th><th>Wilson 95% CI</th><th>origins</th></tr></thead>
          <tbody>
            {(e.reliability ?? []).map((r: Any) => (
              <tr key={r.level}>
                <td>{r.level}</td>
                <td className="num">{fmt(r.nominal, 2)}</td>
                <td className="num" style={{ color: r.exceedance_rate > (r.nominal ?? 0) * 1.6 ? P.stress : undefined }}>
                  {fmt(r.exceedance_rate, 3)}
                </td>
                <td className="num dimsmall">{r.wilson95 ? `${fmt(r.wilson95[0], 3)}–${fmt(r.wilson95[1], 3)}` : "—"}</td>
                <td className="num dimsmall">{r.n_origins}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

export default function Forecast({ snap }: { snap: Any }) {
  const deep = snap.deep ?? {};
  return (
    <div className="grid">
      <RiptideCard r={deep.riptide} />
      <SwellCard s={deep.swell} />
      <SwellCurveCard s={deep.swell} />
      <QuantOddsStrip deep={deep} />
      <CaesarCard e={snap.engines?.caesar} />
      <BathymetryCard b={deep.bathymetry} />
      <SeaStateCard e={deep.seastate} />
      <SeaRoomCard e={deep.searoom} />
      <TideTablesCard t={deep.tidetables} />
      <BreakwaterCard b={snap.engines?.breakwater} />
    </div>
  );
}