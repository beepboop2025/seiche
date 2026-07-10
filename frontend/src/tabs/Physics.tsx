import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

/** Minimal XY (non-time) SVG chart: the potential landscape, decay curves —
 *  things uPlot's date axis can't draw. Self-contained, theme-matched. */
function XYChart({
  xs, ys, height = 180, yLabel, vline, markers, color = "#5aa9e6",
}: {
  xs: number[]; ys: number[]; height?: number; yLabel?: string;
  vline?: { x: number; color: string; label: string } | null;
  markers?: { x: number; y: number; color: string; label: string }[];
  color?: string;
}) {
  if (!xs?.length || xs.length !== ys.length) return null;
  const W = 640, H = height, padL = 44, padR = 12, padT = 10, padB = 26;
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const finite = ys.filter((v) => Number.isFinite(v));
  const ymin = Math.min(...finite), ymax = Math.max(...finite);
  const yspan = ymax - ymin || 1, xspan = xmax - xmin || 1;
  const X = (x: number) => padL + ((x - xmin) / xspan) * (W - padL - padR);
  const Y = (y: number) => padT + (1 - (y - ymin) / yspan) * (H - padT - padB);
  const path = xs
    .map((x, i) => (Number.isFinite(ys[i]) ? `${i === 0 ? "M" : "L"}${X(x).toFixed(1)},${Y(ys[i]).toFixed(1)}` : ""))
    .join(" ");
  const xticks = [xmin, xmin + xspan / 2, xmax];
  const yticks = [ymin, ymin + yspan / 2, ymax];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {yticks.map((t, i) => (
        <g key={`y${i}`}>
          <line x1={padL} x2={W - padR} y1={Y(t)} y2={Y(t)} stroke="rgba(28,36,48,0.6)" />
          <text x={padL - 6} y={Y(t) + 3} fill="#6b7686" fontSize={10} textAnchor="end"
            fontFamily="SF Mono, monospace">{t.toFixed(Math.abs(yspan) < 5 ? 1 : 0)}</text>
        </g>
      ))}
      {xticks.map((t, i) => (
        <text key={`x${i}`} x={X(t)} y={H - 8} fill="#6b7686" fontSize={10} textAnchor="middle"
          fontFamily="SF Mono, monospace">{t.toFixed(Math.abs(xspan) < 5 ? 1 : 0)}</text>
      ))}
      {vline && vline.x >= xmin && vline.x <= xmax && (
        <g>
          <line x1={X(vline.x)} x2={X(vline.x)} y1={padT} y2={H - padB} stroke={vline.color} strokeDasharray="4 4" />
          <text x={X(vline.x) + 4} y={padT + 10} fill={vline.color} fontSize={10}
            fontFamily="SF Mono, monospace">{vline.label}</text>
        </g>
      )}
      <path d={path} fill="none" stroke={color} strokeWidth={1.6} />
      {(markers ?? []).map((m, i) => (
        <g key={`m${i}`}>
          <circle cx={X(m.x)} cy={Y(m.y)} r={4} fill={m.color} />
          <text x={X(m.x) + 6} y={Y(m.y) - 6} fill={m.color} fontSize={10}
            fontFamily="SF Mono, monospace">{m.label}</text>
        </g>
      ))}
      {yLabel && (
        <text x={12} y={padT + 10} fill="#6b7686" fontSize={10} fontFamily="SF Mono, monospace">{yLabel}</text>
      )}
    </svg>
  );
}

function BathymetryCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Bathymetry" reason={e?.reason} span={7} />;
  const fl = e.floor ?? {};
  const spec = e.spectrum ?? {};
  const arrow = e.arrow ?? {};
  const v = e.validation ?? {};
  const curve = fl.curve ?? []; // rows [x_bp, V, d1, d2, n]
  const wellMarker =
    fl.well_bp != null && curve.length
      ? [{
          x: fl.well_bp,
          y: Math.min(...curve.map((r: Any) => r[1])),
          color: "#37c88b", label: "well",
        }]
      : [];
  return (
    <div className="card span7">
      <h2>Bathymetry</h2>
      <div className="sub">
        the basin floor, measured from the water's motion — empirical Langevin potential of the pop
        statistic, the quantum-dual relaxation spectrum, the entropy arrow, and the exact
        first-passage forecast (a Stack member with its own record)
      </div>
      <XYChart
        xs={curve.map((r: Any) => r[0])} ys={curve.map((r: Any) => r[1])} yLabel="V(x)"
        vline={{ x: 10, color: "#e5484d", label: "event bin ≥10bp" }}
        markers={wellMarker}
      />
      <div className="kv">
        <div className="item"><div className="k">P(event, 5bd)</div>
          <div className={`v ${(e.p_event_5bd ?? 0) >= 0.35 ? "bad" : ""}`}>
            {e.p_event_5bd != null ? `${fmt(e.p_event_5bd * 100, 1)}%` : "—"}</div></div>
        <div className="item"><div className="k">days to next event</div>
          <div className="v">{e.mfpt_bd != null ? `~${fmt(e.mfpt_bd, 0)}bd` : e.mfpt_capped ? `>${e.mfpt_cap_bd}bd` : "—"}</div></div>
        <div className="item"><div className="k">relaxation τ (gap)</div>
          <div className={`v ${spec.tau_pctl >= 85 ? "bad" : ""}`}>{fmt(spec.tau_bd, 1)}d
            <span className="dimsmall"> ({fmt(spec.tau_pctl, 0)}th)</span></div></div>
        <div className="item"><div className="k">entropy σ (arrow)</div>
          <div className={`v ${arrow.pctl >= 85 ? "warn" : ""}`}>{fmt(arrow.sigma_nats_bd, 3)}
            <span className="dimsmall"> nats/bd ({fmt(arrow.pctl, 0)}th)</span></div></div>
        <div className="item"><div className="k">well / barrier</div>
          <div className="v">{fmt(fl.well_bp, 1)}bp · {fmt(fl.barrier_kt, 1)} k<sub>B</sub>T</div></div>
      </div>
      <Chart
        rows={(e.series ?? []).map((r: Any) => [r[0], r[1], r[2]])}
        series={[
          { label: "relaxation τ (bd)", color: "#8a63d2" },
          { label: "entropy σ (nats/bd)", color: "#e88a3a", dash: [4, 3] },
        ]}
      />
      {v.ok && (
        <div className="dimsmall">
          walk-forward: AUROC {fmt(v.auroc, 2)} · Brier {fmt(v.brier, 4)} vs climatology {fmt(v.brier_climatology, 4)} ({v.n_scored} scored) — {v.verdict}
        </div>
      )}
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

function MerianCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Merian Modes" reason={e?.reason} span={5} />;
  const inst = e.instability ?? {};
  const hot = (inst.g_now ?? 0) > 0 && (inst.pctl ?? 0) >= 90;
  return (
    <div className="card span5">
      <h2>Merian Modes</h2>
      <div className="sub">
        the seiche eigenmodes — Hankel-DMD (a Koopman-operator estimate) reads the basin's standing
        waves out of the plumbing panel; a growing mode is instability before levels move
      </div>
      <div className="kv">
        <div className="item"><div className="k">instability g*</div>
          <div className={`v ${hot ? "bad" : ""}`}>{inst.g_now != null ? `${inst.g_now > 0 ? "+" : ""}${fmt(inst.g_now, 4)}/bd` : "—"}
            <span className="dimsmall"> ({fmt(inst.pctl, 0)}th)</span></div></div>
        <div className="item"><div className="k">panel</div>
          <div className="v">{e.n_series} pipes · rank {e.rank}</div></div>
      </div>
      <table className="mini">
        <thead><tr><th>period</th><th>direction</th><th>e-fold</th><th>amplitude</th><th></th></tr></thead>
        <tbody>
          {(e.modes ?? []).map((m: Any, i: number) => (
            <tr key={i}>
              <td className="num">{m.period_bd != null ? `${fmt(m.period_bd, 0)}bd` : "non-osc"}</td>
              <td style={{ color: m.direction === "growing" ? "#e5484d" : undefined }}>{m.direction}</td>
              <td className="num">{m.efold_bd != null ? `${fmt(m.efold_bd, 0)}bd` : "—"}</td>
              <td className="num">{fmt((m.amp_share ?? 0) * 100, 0)}%</td>
              <td className="dimsmall">{m.label ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Chart
        rows={e.rows ?? []}
        series={[{ label: "dominant growth rate g*", color: "#e88a3a" }]}
        yLabel="ln|λ| /bd"
      />
      <div className="dimsmall">{(e.forecast_skill ?? {}).verdict}</div>
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

function GyreCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="The Gyre" reason={e?.reason} span={7} />;
  const det = e.determinism ?? {};
  const nl = e.nonlinearity ?? {};
  const st = e.stability ?? {};
  const fc = e.forecast ?? {};
  const decay = e.decay ?? [];
  return (
    <div className="card span7">
      <h2>The Gyre</h2>
      <div className="sub">
        Takens embedding + empirical dynamic modeling — is the basin deterministic enough to predict
        at all, how fast does that skill decay, and does the water obey different rules at different states?
      </div>
      <div className="kv">
        <div className="item"><div className="k">embedding E</div><div className="v">{e.embedding?.E}</div></div>
        <div className="item"><div className="k">determinism</div>
          <div className={`v ${String(det.verdict ?? "").startsWith("deterministic") ? "warn" : ""}`}>
            ρ {fmt(det.rho, 2)} vs surrogate {fmt(det.surrogate_p95, 2)}</div></div>
        <div className="item"><div className="k">nonlinearity Δρ</div>
          <div className="v">{fmt(nl.delta_rho, 3)} (θ* {fmt(nl.theta_best, 1)})</div></div>
        <div className="item"><div className="k">local λ</div>
          <div className={`v ${st.lambda_now > 1 ? "bad" : ""}`}>{fmt(st.lambda_now, 2)}
            <span className="dimsmall"> ({fmt(st.pctl, 0)}th)</span></div></div>
      </div>
      <XYChart
        xs={decay.map((d: Any) => d.h)} ys={decay.map((d: Any) => d.rho)}
        yLabel="skill ρ by horizon (bd)" color="#4cc3ff" height={140}
      />
      <table className="mini">
        <thead><tr><th>h (bd)</th>{decay.map((d: Any) => <th key={d.h} className="num">{d.h}</th>)}</tr></thead>
        <tbody>
          <tr><td>ρ</td>{decay.map((d: Any) => <td key={d.h} className="num">{fmt(d.rho, 2)}</td>)}</tr>
          <tr><td>MAE vs persistence</td>{decay.map((d: Any) => (
            <td key={d.h} className="num" style={{ color: d.mae_ratio < 1 ? "#37c88b" : undefined }}>{fmt(d.mae_ratio, 2)}</td>
          ))}</tr>
        </tbody>
      </table>
      <div className="dimsmall">{det.verdict} · {nl.verdict}</div>
      <Chart
        rows={e.stability_rows ?? []}
        series={[{ label: "local expansion multiplier |λ|", color: "#d9b23a" }]}
        refLine={{ value: 1.0, color: "#e5484d", label: "expanding" }}
      />
      {fc.point_bp != null && (
        <div className="dimsmall">
          5bd simplex forecast: {fmt(fc.point_bp, 1)}bp [{fmt(fc.p25_bp, 1)}, {fmt(fc.p75_bp, 1)}] — {fc.verdict}
        </div>
      )}
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

function RogueWaveCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Rogue Wave" reason={e?.reason} span={5} />;
  const fit = e.fit ?? {};
  const ci = fit.xi_ci95 ?? [];
  return (
    <div className="card span5">
      <h2>Rogue Wave</h2>
      <div className="sub">
        the tail law — POT/GPD on the shared pop statistic: the honest instrument for the wave
        that is not in the sample yet · {e.n_clusters} declustered waves over {e.threshold_bp}bp
      </div>
      <div className="kv">
        <div className="item"><div className="k">tail shape ξ</div>
          <div className={`v ${fit.xi > 0.05 ? "bad" : ""}`}>{fmt(fit.xi, 2)}
            <span className="dimsmall"> [{fmt(ci[0], 2)}, {fmt(ci[1], 2)}]</span></div></div>
        <div className="item"><div className="k">sample max</div>
          <div className="v">{fmt(e.sample_max_bp, 1)}bp</div></div>
      </div>
      <table className="mini">
        <thead><tr><th>return period</th><th>wave size</th><th>95% CI</th></tr></thead>
        <tbody>
          {(e.return_levels ?? []).map((r: Any) => (
            <tr key={r.years}>
              <td>{r.years}y</td>
              <td className="num" style={{ color: r.bp > (e.sample_max_bp ?? Infinity) ? "#e88a3a" : undefined }}>
                {fmt(r.bp, 0)}bp</td>
              <td className="num dimsmall">{r.ci95 ? `${fmt(r.ci95[0], 0)}–${fmt(r.ci95[1], 0)}` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="mini">
        <thead><tr><th>P(pop ≥ x)</th><th>5bd</th><th>21bd</th><th>63bd</th><th></th></tr></thead>
        <tbody>
          {(e.p_exceed ?? []).map((p: Any) => (
            <tr key={p.x_bp}>
              <td className="num">{fmt(p.x_bp, 0)}bp</td>
              <td className="num">{fmt((p.h5 ?? 0) * 100, 1)}%</td>
              <td className="num">{fmt((p.h21 ?? 0) * 100, 1)}%</td>
              <td className="num">{fmt((p.h63 ?? 0) * 100, 1)}%</td>
              <td className="dimsmall">{p.basis}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Chart
        rows={e.xi_rows ?? []}
        series={[{ label: "tail shape ξ (annual expanding refits)", color: "#e5484d" }]}
      />
      <div className="dimsmall">{e.tail_verdict}</div>
      <details>
        <summary className="dimsmall">threshold sensitivity</summary>
        <table className="mini">
          <thead><tr><th>threshold pctl</th><th>u (bp)</th><th>n</th><th>ξ</th></tr></thead>
          <tbody>
            {(e.sensitivity ?? []).map((s: Any) => (
              <tr key={s.pctl}>
                <td className="num">{s.pctl}</td><td className="num">{fmt(s.threshold_bp, 1)}</td>
                <td className="num">{s.n}</td><td className="num">{fmt(s.xi, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

function MicroseismCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Microseism" reason={e?.reason} span={5} />;
  const fit = e.fit ?? {};
  const lr = e.lr_test ?? {};
  const wf = e.walkforward ?? {};
  const nearCritical = lr.identified && (fit.branching ?? 0) >= 0.7;
  return (
    <div className="card span5">
      <h2>Microseism</h2>
      <div className="sub">
        the shock catalog nobody kept — a calendar-gated Hawkes process on every ≥{fmt(e.threshold_bp, 1)}bp
        tremor asks whether shocks BREED shocks beyond the calendar's forcing; the branching ratio n is the
        basin's distance to criticality (at n=1 the chain reaction is self-sustaining)
      </div>
      <div className="kv">
        <div className="item"><div className="k">branching n</div>
          <div className={`v ${nearCritical ? "bad" : lr.identified && (fit.branching ?? 0) >= 0.4 ? "warn" : ""}`}>
            {fmt(fit.branching, 2)}<span className="dimsmall"> aftershocks/shock</span></div></div>
        <div className="item"><div className="k">aftershock half-life</div>
          <div className="v">{fmt(fit.half_life_bd, 1)}bd</div></div>
        <div className="item"><div className="k">intensity that is echo</div>
          <div className={`v ${(fit.excitation_share_now ?? 0) >= 0.6 ? "warn" : ""}`}>
            {fmt((fit.excitation_share_now ?? 0) * 100, 0)}%</div></div>
        <div className="item"><div className="k">vs calendar null</div>
          <div className="v">{lr.identified ? "identified" : "NOT identified"}
            <span className="dimsmall"> (LR p={fmt(lr.p, 4)})</span></div></div>
      </div>
      <Chart
        rows={e.branching_rows ?? []}
        series={[{ label: "branching ratio n (criticality at 1.0)", color: "#e5484d" }]}
        yLabel="n"
      />
      <div className="dimsmall">{e.reading}</div>
      {wf.ok && (
        <div className="dimsmall">
          walk-forward: Brier {fmt(wf.brier_hawkes, 4)} vs calendar {fmt(wf.brier_calendar, 4)} ·
          AUROC {fmt(wf.auroc_hawkes, 2)} vs {fmt(wf.auroc_calendar, 2)} ({wf.n_scored} scored) — {wf.verdict}
        </div>
      )}
      <table className="mini">
        <thead><tr><th>catalog thr</th><th>shocks</th><th>n</th><th>half-life</th></tr></thead>
        <tbody>
          {(e.sensitivity ?? []).map((r: Any, i: number) => (
            <tr key={i}>
              <td className="num">{fmt(r.thr_bp, 1)}bp</td>
              <td className="num">{r.n_shocks}</td>
              <td className="num">{r.branching != null ? fmt(r.branching, 2) : "—"}</td>
              <td className="num">{r.half_life_bd != null ? `${fmt(r.half_life_bd, 1)}bd` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

export default function Physics({ snap }: { snap: Any }) {
  const eng = snap.engines ?? {};
  const deep = snap.deep ?? {};
  return (
    <div className="grid">
      <div className="card span12">
        <h2>The Physics Package</h2>
        <div className="sub">
          the basin treated as the dynamical system it is named after — its <b>floor</b> (Bathymetry:
          empirical Langevin potential, the Fokker–Planck↔Schrödinger spectrum, the entropy arrow,
          exact first-passage forecast), its <b>modes</b> (Merian: Koopman/DMD eigenmodes), its{" "}
          <b>determinism</b> (Gyre: Takens embedding — is prediction possible at all?), its{" "}
          <b>tail law</b> (Rogue Wave: extreme value theory), and its <b>clustering law</b>{" "}
          (Microseism: a calendar-gated Hawkes process — do shocks breed shocks?). Together with
          Resonance (forced response) and Undertow (free decay) on the RESONANCE tab, this is the full
          physical examination. Every claim ships with walk-forward evidence or expanding percentiles
          vs its own past — the formalism is quantum-mechanical where that is honest (Koopman
          operators, the Fokker–Planck↔Schrödinger duality) and nowhere else.
        </div>
      </div>
      <BathymetryCard e={deep.bathymetry} />
      <MerianCard e={eng.merian} />
      <GyreCard e={deep.gyre} />
      <RogueWaveCard e={eng.roguewave} />
      <MicroseismCard e={deep.microseism} />
    </div>
  );
}
