import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

const BUCKET_COLORS: Record<string, string> = {
  year_turn: "#e5484d",
  quarter_turn: "#e88a3a",
  month_end: "#4cc3ff",
  tax_date: "#d9b23a",
  mid_month: "#8a63d2",
  plain: "#3d4654",
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
              RRP co-sign <b style={{ color: lv.rrp_cosigned ? undefined : "#e5484d" }}>
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
              <td style={{ color: p.stuck ? "#e5484d" : undefined }}>{p.stuck ? "yes" : "no"}</td>
              <td style={{ color: p.escalated ? "#e5484d" : undefined }}>{p.escalated == null ? "—" : p.escalated ? "yes" : "no"}</td>
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
            peak day <b style={{ color: "#e88a3a" }}>{s.peak?.date}</b> ({s.peak?.bucket?.replace("_", "-")},
            P(≥10bp) {fmt((s.peak?.p10 ?? 0) * 100, 0)}%)
            {state.available && (
              <> · damping state {state.hot
                ? <b style={{ color: "#e5484d" }}>HOT · lift {fmt(state.lift_10bp, 1)}×</b>
                : <span className="dimsmall">calm</span>}</>
            )}
            {" "}· asof {s.asof}
          </div>
          <div className="coverage" style={{ color: beats ? "#37c88b" : "#e8b64c" }}>
            {v.ok
              ? `walk-forward: AUROC ${fmt(v.auroc, 2)} · Brier ${fmt(v.brier, 4)} vs climatology ${fmt(v.brier_climatology, 4)} — ${v.verdict}`
              : v.reason ?? "validation not run"}
          </div>
        </div>
      </div>
      <Chart
        rows={rows}
        series={[
          { label: "P(≥10bp) per day", color: "#e88a3a" },
          { label: "P(event by date)", color: "#37c88b" },
          { label: "P(≥2bp) per day", color: "#3d4654", dash: [2, 3] },
        ]}
        yLabel="%"
        vlines={settleDates.length ? { dates: settleDates, color: "#8a63d2" } : null}
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
              <td className="num" style={{ color: b.p10 >= 0.1 ? "#e5484d" : undefined }}>{fmt(b.p10 * 100, 1)}%</td>
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
      <line x1={evX} y1={PAD} x2={evX} y2={H - PAD} stroke="#e5484d" strokeDasharray="3 4" strokeWidth={1} />
      <text x={evX - 4} y={PAD + 10} fill="#e5484d" fontSize={10} textAnchor="end" fontFamily="SF Mono, monospace">
        event ≥10bp
      </text>
      <polyline points={pts} fill="none" stroke="#5aa9e6" strokeWidth={1.6} />
      {ball && <circle cx={ball.x} cy={ball.y} r={4.5} fill="#e8b64c" stroke="#0b0f14" strokeWidth={1} />}
      <text x={PAD + 2} y={H - PAD - 2} fill="#6b7686" fontSize={10} fontFamily="SF Mono, monospace">
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
              ? <b style={{ color: "#e5484d" }}>state already in the event bin</b>
              : mfpt != null
                ? <b>{fmt(mfpt, 0)}bd</b>
                : <span className="dimsmall">beyond {b.mfpt_cap_bd}bd — the well holds</span>}
          </div>
          <div className="coverage">
            state x = {fmt(b.state_now?.pop_bp, 1)}bp · well at {fmt(fl.well_bp, 1)}bp ·
            stiffness {fmt(fl.stiffness, 2)}/bd · escape barrier{" "}
            <b style={{ color: (fl.barrier_kt ?? 99) < 2 ? "#e5484d" : undefined }}>{fmt(fl.barrier_kt, 1)} k<sub>B</sub>T</b> ·
            τ (slowest relaxation) {fmt(sp.tau_bd, 1)}bd
            {sp.tau_pctl != null && <b style={{ color: sp.tau_pctl >= 80 ? "#e5484d" : undefined }}> ({fmt(sp.tau_pctl, 0)}th pctl)</b>} ·
            entropy production {fmt(ar.sigma_nats_bd, 3)} nats/bd
            {ar.pctl != null && <b style={{ color: ar.pctl >= 80 ? "#e5484d" : undefined }}> ({fmt(ar.pctl, 0)}th pctl)</b>}
          </div>
          <div className="coverage" style={{ color: beats ? "#37c88b" : "#e8b64c" }}>
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
          { label: "τ relaxation (bd)", color: "#8a63d2" },
          { label: "entropy production (nats/bd)", color: "#e88a3a", dash: [4, 3] },
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
            water <b style={{ color: uncharted ? "#e5484d" : undefined }}>{nov.verdict}</b>
            {nov.pctl != null ? ` (NN-distance ${fmt(nov.pctl, 0)}th pctl)` : ""} · asof {t.asof}
          </div>
          <div className="coverage" style={{ color: beats ? "#37c88b" : "#e5484d" }}>
            hindcast: {skill.ok
              ? `Brier ${fmt(skill.brier, 3)} vs climatology ${fmt(skill.brier_climatology, 3)} · AUROC ${fmt(skill.auroc, 2)} — ${skill.verdict}`
              : skill.reason ?? "not run"}
          </div>
        </div>
      </div>
      <Chart
        rows={rows}
        series={[
          { label: "SOFR−IORB", color: "#5aa9e6" },
          { label: "p10", color: "#3d4654", dash: [2, 3] },
          { label: "p25", color: "#8a63d2", dash: [4, 3] },
          { label: "analog median", color: "#e8b64c" },
          { label: "p75", color: "#8a63d2", dash: [4, 3] },
          { label: "p90", color: "#3d4654", dash: [2, 3] },
        ]}
        yLabel="bp"
        refLine={{ value: 0, color: "#3d4654", label: "" }}
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
              <td className="num" style={{ color: a.max_move_5bd_bp > 5 ? "#e5484d" : undefined }}>
                {a.max_move_5bd_bp > 0 ? "+" : ""}{fmt(a.max_move_5bd_bp, 1)}bp
              </td>
              <td>{a.event_within_5bd ? <b style={{ color: "#e5484d" }}>yes</b> : <span className="dimsmall">no</span>}</td>
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
        series={[{ label: "filtered P(rough water)", color: "#e88a3a" }]}
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

export default function Forecast({ snap }: { snap: Any }) {
  const deep = snap.deep ?? {};
  return (
    <div className="grid">
      <RiptideCard r={deep.riptide} />
      <SwellCard s={deep.swell} />
      <BathymetryCard b={deep.bathymetry} />
      <SeaStateCard e={deep.seastate} />
      <SeaRoomCard e={deep.searoom} />
      <TideTablesCard t={deep.tidetables} />
      <BreakwaterCard b={snap.engines?.breakwater} />
    </div>
  );
}
