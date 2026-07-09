import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

const MODE_COLORS: Record<string, string> = {
  quarter_end: "#e88a3a",
  year_end: "#e5484d",
  month_end: "#4cc3ff",
  mid_month: "#3d4654",
  tax_date: "#d9b23a",
};

function ResonanceCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Resonance Engine" reason={e?.reason} span={12} />;
  const modes = Object.entries<Any>(e.modes ?? {}).filter(([, d]) => d.ok);
  // Split the scatter into one series per mode for coloring.
  const modeKeys = Object.keys(MODE_COLORS).filter((m) => (e.events_scatter ?? []).some((r: Any) => r[2] === m));
  const rows = (e.events_scatter ?? []).map((r: Any) => [
    r[0],
    ...modeKeys.map((m) => (r[2] === m ? r[1] : null)),
  ]);
  return (
    <div className="card span12">
      <h2>Resonance Engine</h2>
      <div className="sub">
        the seiche made literal — does the same calendar forcing produce a bigger slosh than it used to?
        · score {fmt(e.score, 0)} · loudest: {e.worst_mode?.label} at {fmt(e.worst_mode?.amplification, 2)}×
      </div>
      <Chart
        rows={rows}
        series={modeKeys.map((m) => ({ label: m.replace("_", "-"), color: MODE_COLORS[m], pointsOnly: true }))}
        height={190}
        yLabel="slosh bp"
      />
      <table className="mini">
        <thead>
          <tr>
            <th>mode</th><th>n</th><th>last slosh</th><th>recent med</th><th>prior med</th>
            <th>amplification</th><th>ex-max</th><th>decay (prior→recent)</th><th>score</th>
          </tr>
        </thead>
        <tbody>
          {modes.map(([m, d]) => (
            <tr key={m}>
              <td>{d.label}{d.low_n && <span className="dimsmall" title="fewer than 10 events"> †low-n</span>}</td>
              <td className="num">{d.n}</td>
              <td className="num">{fmt(d.last?.slosh_bp, 1)}bp ({d.last?.date})</td>
              <td className="num">{fmt(d.recent_median_bp, 1)}bp</td>
              <td className="num">{fmt(d.prior_median_bp, 1)}bp</td>
              <td className="num" style={{ color: d.amplification >= 2 ? "#e5484d" : d.amplification >= 1.3 ? "#d9b23a" : undefined }}>
                {fmt(d.amplification, 2)}×
              </td>
              <td className="num dimsmall" title="amplification with the largest recent slosh removed — one-event sensitivity">
                {d.amplification_ex_max == null ? "—" : `${fmt(d.amplification_ex_max, 2)}×`}
              </td>
              <td className="num">{fmt(d.decay_prior_d, 1)}d → {fmt(d.decay_recent_d, 1)}d</td>
              <td className="num">{fmt(d.score, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{e.method}</Method>
    </div>
  );
}

function UndertowCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Undertow" reason={e?.reason} span={12} />;
  const sp = e.per_series?.spread ?? {};
  const tl = e.per_series?.tail ?? {};
  const rec = sp.recovery ?? {};
  return (
    <div className="card span12">
      <h2>Undertow</h2>
      <div className="sub">
        critical slowing down — the free-decay half of the physics: a basin losing damping forgets
        perturbations slowly (rising AC1 + variance, stretching recovery) even while levels look calm
        · score {fmt(e.score, 0)}
      </div>
      <div className="kv">
        <div className="item"><div className="k">spread AC1</div>
          <div className={`v ${sp.ac1_pctl >= 85 ? "bad" : ""}`}>{fmt(sp.ac1, 2)} <span className="dimsmall">({fmt(sp.ac1_pctl, 0)}th)</span></div></div>
        <div className="item"><div className="k">relaxation τ</div>
          <div className="v">{sp.tau_bd != null ? `${fmt(sp.tau_bd, 1)}d` : "—"}</div></div>
        <div className="item"><div className="k">spread var pctl</div>
          <div className={`v ${sp.var_pctl >= 85 ? "bad" : ""}`}>{fmt(sp.var_pctl, 0)}th</div></div>
        <div className="item"><div className="k">tail AC1 pctl</div>
          <div className={`v ${tl.ac1_pctl >= 85 ? "bad" : ""}`}>{tl.ac1_pctl != null ? `${fmt(tl.ac1_pctl, 0)}th` : "—"}</div></div>
        <div className="item"><div className="k">recovery half-life</div>
          <div className={`v ${rec.stretch >= 1.5 ? "warn" : ""}`}>
            {rec.halflife_prior_d != null ? `${fmt(rec.halflife_prior_d, 1)}d → ${fmt(rec.halflife_recent_d, 1)}d` : "—"}
            {rec.stretch != null && ` (${fmt(rec.stretch, 2)}×)`}
            {rec.low_n && <span className="dimsmall" title="too few pops in one of the eras"> †low-n</span>}
          </div></div>
        <div className="item"><div className="k">mechanism</div>
          <div className={`v ${sp.mechanism?.startsWith("both") || sp.mechanism?.startsWith("absorbers") ? "bad" : sp.mechanism?.startsWith("louder") ? "warn" : ""}`}
               title="fluctuation-dissipation split: noise power D = Var·(1−AC1²) vs damping — louder shocks or weaker absorbers (diagnostic, not scored)">
            {sp.mechanism ?? "—"}
            {sp.noise_pctl != null && <span className="dimsmall"> (D {fmt(sp.noise_pctl, 0)}th)</span>}
          </div></div>
      </div>
      <Chart
        rows={e.ac1_rows ?? []}
        series={[
          { label: "AC1 · SOFR−IORB", color: "#5aa9e6" },
          { label: "AC1 · tail", color: "#8a63d2", dash: [4, 3] },
        ]}
        yLabel="lag-1 autocorr"
      />
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

function HydrophoneCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Hydrophone Array" reason={e?.reason} span={7} />;
  return (
    <div className="card span7">
      <h2>Hydrophone Array</h2>
      <div className="sub">absorption ratio — decoupled pipes absorb shocks; a densifying network transmits them</div>
      <div className="kv">
        <div className="item"><div className="k">absorption</div><div className="v">{fmt(e.absorption, 3)}</div></div>
        <div className="item"><div className="k">percentile</div>
          <div className={`v ${e.absorption_pctl >= 80 ? "bad" : ""}`}>{fmt(e.absorption_pctl, 0)}th</div></div>
        <div className="item"><div className="k">Δ 60d</div><div className={`v ${e.trend_60d > 0.05 ? "warn" : ""}`}>{e.trend_60d > 0 ? "+" : ""}{fmt(e.trend_60d, 3)}</div></div>
        <div className="item"><div className="k">series in panel</div><div className="v">{e.n_series}</div></div>
      </div>
      <Chart rows={e.series} series={[{ label: "absorption (top-2 PC share)", color: "#37c88b" }]} />
      <Method>{e.method}</Method>
    </div>
  );
}

function EdgesCard({ e }: { e: Any }) {
  if (!e?.ok) return null;
  return (
    <div className="card span5">
      <h2>Lead-Lag Map</h2>
      <div className="sub">which pipe is upstream right now — the map reorganizing is itself a signal</div>
      <table className="mini">
        <thead><tr><th>leads</th><th></th><th>follows</th><th>lag</th><th>r</th></tr></thead>
        <tbody>
          {(e.edges ?? []).map((ed: Any, i: number) => (
            <tr key={i}>
              <td>{ed.lead}</td><td>→</td><td>{ed.follows}</td>
              <td className="num">{ed.lag_d}d</td>
              <td className="num" style={{ color: Math.abs(ed.corr) >= 0.45 ? "#e88a3a" : undefined }}>{fmt(ed.corr, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(e.edges ?? []).length === 0 && <div className="allclear">▮ no edges above threshold — pipes decoupled</div>}
    </div>
  );
}

function SonarCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="SONAR" reason={"sweep unavailable"} span={12} />;
  return (
    <div className="card span12">
      <h2>SONAR</h2>
      <div className="sub">daily anomaly sweep across every series — {e.n_flagged} of {e.n_scanned} flagged beyond ±2.5 robust z</div>
      <table className="mini">
        <thead><tr><th>series</th><th>last</th><th>Δ 1d</th><th>level z</th><th>change z</th><th>asof</th></tr></thead>
        <tbody>
          {(e.movers ?? []).map((m: Any) => (
            <tr key={m.name} style={{ opacity: m.flag ? 1 : 0.55 }}>
              <td>{m.label}</td>
              <td className="num">{fmt(m.last, 2)} {m.unit}</td>
              <td className="num">{m.chg_1d == null ? "—" : `${m.chg_1d > 0 ? "+" : ""}${fmt(m.chg_1d, 2)}`}</td>
              <td className="num" style={{ color: Math.abs(m.level_z ?? 0) >= 2.5 ? "#e5484d" : undefined }}>{fmt(m.level_z, 2)}</td>
              <td className="num" style={{ color: Math.abs(m.change_z ?? 0) >= 2.5 ? "#e5484d" : undefined }}>{fmt(m.change_z, 2)}</td>
              <td className="num">{m.asof}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{e.method}</Method>
    </div>
  );
}

function StationKeepingCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Station-Keeping" reason={e?.reason} span={12} />;
  return (
    <div className="card span12">
      <h2>Station-Keeping</h2>
      <div className="sub">
        orbit-determination transfer: propagate the reserve system's expected state, watch the
        residuals — a persistent innovation is a burn the model didn't know about
        {e.any_active && <span style={{ color: "#e88a3a" }}> · BURN IN PROGRESS</span>}
      </div>
      <div className="kv">
        {Object.entries<Any>(e.channels ?? {}).map(([ch, c]) => (
          <div className="item" key={ch}>
            <div className="k">{ch} {c.active ? "· active" : ""}</div>
            <div className={`v ${c.active ? "warn" : ""}`}>S⁺{fmt(c.s_pos, 1)} / S⁻{fmt(c.s_neg, 1)}</div>
          </div>
        ))}
      </div>
      <table className="mini">
        <thead><tr><th>alarm</th><th>channel</th><th>run start</th><th>direction</th><th>size</th></tr></thead>
        <tbody>
          {(e.recent_maneuvers ?? []).map((m: Any, i: number) => (
            <tr key={i}>
              <td className="num">{m.date}</td>
              <td>{m.channel}</td>
              <td className="num">{m.start}</td>
              <td style={{ color: m.direction === "drain" ? "#e5484d" : "#37c88b" }}>{m.direction}</td>
              <td className="num">${fmt(Math.abs(m.cum_b), 0)}B</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{e.method}</Method>
    </div>
  );
}

export default function Resonance({ snap }: { snap: Any }) {
  return (
    <div className="grid">
      <ResonanceCard e={snap.engines.resonance} />
      <UndertowCard e={snap.engines.undertow} />
      <HydrophoneCard e={snap.engines.hydrophone} />
      <EdgesCard e={snap.engines.hydrophone} />
      <StationKeepingCard e={snap.engines.stationkeeping} />
      <SonarCard e={snap.engines.sonar} />
    </div>
  );
}
