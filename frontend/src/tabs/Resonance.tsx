import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Fault, Method, Roll } from "../lib";
import "../styles-fx.css";

/* ---------------------------------------------------------------------------
   Local motion utilities (deliberately NOT imported from motion/): a reduced-
   motion check plus an rAF loop that only runs while its element is onscreen
   and the tab is visible. Every perpetual animation in this file gates on
   these; CSS-side perpetuals gate on `prefers-reduced-motion: no-preference`
   in styles-fx.css, so the static render is the reduced-motion fallback.
   ------------------------------------------------------------------------- */
export const reducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function usePrefersReducedMotion(): boolean {
  const [rm, setRm] = useState(reducedMotion);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const fn = () => setRm(mq.matches);
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, []);
  return rm;
}

/** Calls cb(timestamp) on every animation frame, but only while `active`,
    the observed element intersects the viewport, and document.hidden is false. */
export function useAnimFrame(
  cb: (t: number) => void,
  active: boolean,
  ref: RefObject<HTMLElement | null>,
) {
  const cbRef = useRef(cb);
  cbRef.current = cb;
  useEffect(() => {
    if (!active) return;
    let raf = 0;
    let onscreen = true;
    const tick = (t: number) => {
      cbRef.current(t);
      raf = requestAnimationFrame(tick);
    };
    const start = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const stop = () => cancelAnimationFrame(raf);
    const io = new IntersectionObserver(
      ([en]) => {
        onscreen = en.isIntersecting;
        if (onscreen && !document.hidden) start();
        else stop();
      },
      { threshold: 0.05 },
    );
    if (ref.current) io.observe(ref.current);
    const vis = () => {
      if (!document.hidden && onscreen) start();
      else stop();
    };
    document.addEventListener("visibilitychange", vis);
    start();
    return () => {
      stop();
      io.disconnect();
      document.removeEventListener("visibilitychange", vis);
    };
  }, [active, ref]);
}

const MODE_COLORS: Record<string, string> = {
  quarter_end: P.strain,
  year_end: P.stress,
  month_end: P.accent,
  mid_month: P.ghost,
  tax_date: P.gold,
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
              <td className="num" style={{ color: d.amplification >= 2 ? P.stress : d.amplification >= 1.3 ? P.gold : undefined }}>
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
          { label: "AC1 · SOFR−IORB", color: P.slate },
          { label: "AC1 · tail", color: P.accentSoft, dash: [4, 3] },
        ]}
        yLabel="lag-1 autocorr"
      />
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   EdetectCard — the changepoint tripwire (engines.edetect). Per stream the
   card prints the current e-value against its alarm threshold, the ARL
   warranty (a proof, not a backtested threshold), and days since the last
   detection with its estimated change-date and direction. No score: a
   detection is testimony about a regime break, context not evidence.
   ------------------------------------------------------------------------- */
function EdetectCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="E-Detector" reason={e?.reason ?? "not in this snapshot"} span={12} />;
  const streams: [string, Any | null][] = [
    ["SOFR−IORB spread", e.streams?.spread],
    ["SOFR tail detachment", e.streams?.tail],
  ];
  return (
    <div className="card span12">
      <h2>E-Detector</h2>
      <div className="sub">
        the changepoint tripwire — a Shiryaev–Roberts mixture e-process per stream, alarming at 1/α
        with a nonasymptotic warranty: {e.arl_warranty} · asof {e.asof}
      </div>
      <div className="kv">
        {streams.map(([label, s]) =>
          s == null ? (
            <div className="item" key={label}>
              <div className="k">{label}</div>
              <div className="v dimsmall">stream not provided — detector not running</div>
            </div>
          ) : !s.ok ? (
            <div className="item" key={label}>
              <div className="k">{label}</div>
              <div className="v dimsmall">{s.reason ?? "detector down"}</div>
            </div>
          ) : (
            <div className="item" key={label}>
              <div className="k">
                {label}
                {s.alarm_now && <span style={{ color: P.stress }}> · ALARM</span>}
              </div>
              <div className={`v ${s.alarm_now ? "bad" : ""}`}>
                e = {fmt(s.e_value, 1)}
                <span className="dimsmall">
                  {" "}(log10 {fmt(s.log10_e_value, 2)} · alarm at {fmt(e.threshold, 0)})
                </span>
              </div>
              <div className="dimsmall" style={{ marginTop: 3 }}>
                {s.days_since_last_detection != null
                  ? `last detection ${s.days_since_last_detection}d ago · change ${s.change_date} (${s.last_detection?.direction ?? "?"}) · ${s.n_detections} on record`
                  : "no detection on record"}
                {" · "}σ̂ {fmt(s.baseline?.sigma_hat_bp, 1)}bp · peak log10 {fmt(s.max_log10_e_value, 2)}
              </div>
            </div>
          ),
        )}
        <div className="item">
          <div className="k">false-alarm warranty</div>
          <div className="v" style={{ fontSize: 13 }}>{e.arl_warranty}</div>
          <div className="dimsmall" style={{ marginTop: 3 }}>
            α {e.alpha} · baseline {e.baseline_bd}bd · λ grid {(e.lambda_grid ?? []).length} components ·
            two streams halve the system warranty (union bound, in caveats)
          </div>
        </div>
      </div>
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
      <Chart rows={e.series} series={[{ label: "absorption (top-2 PC share)", color: P.calm }]} />
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
              <td className="num" style={{ color: Math.abs(ed.corr) >= 0.45 ? P.strain : undefined }}>{fmt(ed.corr, 2)}</td>
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
              <td className="num" style={{ color: Math.abs(m.level_z ?? 0) >= 2.5 ? P.stress : undefined }}>{fmt(m.level_z, 2)}</td>
              <td className="num" style={{ color: Math.abs(m.change_z ?? 0) >= 2.5 ? P.stress : undefined }}>{fmt(m.change_z, 2)}</td>
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
        {e.any_active && <span style={{ color: P.strain }}> · BURN IN PROGRESS</span>}
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
              <td style={{ color: m.direction === "drain" ? P.stress : P.calm }}>{m.direction}</td>
              <td className="num">${fmt(Math.abs(m.cum_b), 0)}B</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{e.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   HydrophoneMap — the lead-lag graph drawn as the plumbing it describes.
   Nodes = the hydrophone panel's pipes (the same list Merian publishes as
   series_used, falling back to the panel mirror in the assembler), arranged
   on a circle; edges = the measured lead-lag pairs; pulse dots ride each edge
   leader → follower with a traversal time scaled to the measured lag
   (lag_d × 1.4s). Edge opacity = |corr|; the ring around a node = its current
   coupling (Σ|corr| of edges touching it); node size = current Δz where SONAR
   scans the pipe's own series (exact-underlying matches only — derived panel
   spreads are never mapped onto raw-series z's).
   ------------------------------------------------------------------------- */
const PIPE_SONAR: Record<string, string> = { SRF: "SRF", RRP: "RRP", TGA: "WTREGEN" };
// mirrors the panel built in backend assemble.py (_panel) — last-resort node list
const STATIC_PANEL = [
  "SOFR-IORB", "EFFR-IORB", "BGCR-SOFR", "TGCR-SOFR", "DVP-TRI rate",
  "SOFR tail", "SRF", "RRP", "TGA", "DVP vol", "TRI vol",
];

function HydrophoneMap({ e, m, sonar }: { e: Any; m: Any; sonar: Any }) {
  const rm = usePrefersReducedMotion();
  const wrapRef = useRef<HTMLDivElement>(null);
  const pathRefs = useRef<(SVGPathElement | null)[]>([]);
  const pulseRefs = useRef<(SVGCircleElement | null)[]>([]);
  const lenRef = useRef<number[]>([]);
  const [readout, setReadout] = useState<string>("");

  const edges: Any[] = useMemo(
    () =>
      (e?.edges ?? []).filter(
        (ed: Any) => ed?.lead && ed?.follows && typeof ed?.corr === "number",
      ),
    [e],
  );
  const nodes: string[] = useMemo(() => {
    const base: string[] =
      m?.ok && Array.isArray(m.series_used) && m.series_used.length
        ? m.series_used
        : STATIC_PANEL;
    const set = new Set<string>(base);
    for (const ed of edges) {
      set.add(ed.lead);
      set.add(ed.follows);
    }
    return [...set];
  }, [m, edges]);

  // current z per pipe — SONAR's own change-z (the same quantity the
  // hydrophone z-scores: daily changes), level-z as fallback, exact series only
  const zByPipe = useMemo(() => {
    const out: Record<string, { z: number; kind: string }> = {};
    const movers: Any[] = sonar?.movers ?? [];
    for (const [pipe, mnemonic] of Object.entries(PIPE_SONAR)) {
      const mv = movers.find((r: Any) => r.name === mnemonic);
      const z = mv?.change_z ?? mv?.level_z;
      if (typeof z === "number")
        out[pipe] = { z, kind: mv?.change_z != null ? "Δz" : "level z" };
    }
    return out;
  }, [sonar]);

  // geometry — circle layout, edges bowed toward the center
  const W = 760, H = 420, CX = W / 2, CY = H / 2, R = 148;
  const pos = useMemo(
    () =>
      nodes.map((name, i) => {
        const a = -Math.PI / 2 + (i * 2 * Math.PI) / nodes.length;
        return { name, x: CX + R * Math.cos(a), y: CY + R * Math.sin(a), a };
      }),
    [nodes],
  );
  const idx = useMemo(
    () => Object.fromEntries(nodes.map((n, i) => [n, i])) as Record<string, number>,
    [nodes],
  );
  const edgeGeom = useMemo(
    () => {
      // reciprocal pairs (A→B and B→A both present) get a perpendicular bow so
      // they read as two directions instead of one overpainted path
      const keys = new Set(edges.map((ed) => `${ed.lead}→${ed.follows}`));
      return edges
        .filter((ed) => idx[ed.lead] != null && idx[ed.follows] != null)
        .map((ed) => {
          const a = pos[idx[ed.lead]], b = pos[idx[ed.follows]];
          const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
          let qx = mx + (CX - mx) * 0.32, qy = my + (CY - my) * 0.32;
          if (keys.has(`${ed.follows}→${ed.lead}`)) {
            const dx = b.x - a.x, dy = b.y - a.y;
            const len = Math.hypot(dx, dy) || 1;
            const side = ed.lead < ed.follows ? 1 : -1;
            qx += (dy / len) * 14 * side;
            qy -= (dx / len) * 14 * side;
          }
          return { ed, d: `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} Q ${qx.toFixed(1)} ${qy.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}` };
        });
    },
    [edges, pos, idx],
  );
  const coupling = useMemo(
    () =>
      nodes.map((n) =>
        edges.reduce(
          (s, ed) => s + (ed.lead === n || ed.follows === n ? Math.abs(ed.corr) : 0),
          0,
        ),
      ),
    [nodes, edges],
  );
  const maxCoupling = Math.max(...coupling, 1e-9);

  // pulse loop — direct DOM mutation, one dot per edge, traversal = lag × 1.4s
  useAnimFrame(
    (t) => {
      for (let i = 0; i < edgeGeom.length; i++) {
        const path = pathRefs.current[i], dot = pulseRefs.current[i];
        if (!path || !dot) continue;
        if (lenRef.current[i] == null) lenRef.current[i] = path.getTotalLength();
        const lag = Math.max(1, Number(edgeGeom[i].ed.lag_d) || 1);
        const dur = lag * 1400;
        const p = ((t + i * 617) % dur) / dur; // desync phases per edge
        const pt = path.getPointAtLength(p * lenRef.current[i]);
        dot.setAttribute("cx", String(pt.x));
        dot.setAttribute("cy", String(pt.y));
      }
    },
    !rm && edgeGeom.length > 0,
    wrapRef,
  );

  return (
    <div ref={wrapRef}>
      <svg viewBox={`0 0 ${W} ${H}`} className="fx-hydromap" role="img"
           aria-label="lead-lag map of the funding pipes">
        {edgeGeom.map(({ ed, d }, i) => (
          <path
            key={`e${i}`}
            ref={(el) => {
              pathRefs.current[i] = el;
            }}
            d={d}
            className="fx-edge"
            fill="none"
            stroke={ed.corr >= 0 ? P.accentSoft : P.slate}
            strokeOpacity={0.18 + 0.62 * Math.min(Math.abs(ed.corr) / 0.6, 1)}
            strokeWidth={1.2}
            strokeDasharray={ed.corr >= 0 ? undefined : "3 3"}
            onMouseEnter={() =>
              setReadout(`${ed.lead} → ${ed.follows} · lag ${ed.lag_d}d · r ${fmt(ed.corr, 2)}`)
            }
            onMouseLeave={() => setReadout("")}
          >
            <title>{`${ed.lead} leads ${ed.follows} by ${ed.lag_d}d (r ${fmt(ed.corr, 2)})`}</title>
          </path>
        ))}
        {!rm &&
          edgeGeom.map(({ ed }, i) => (
            <circle
              key={`p${i}`}
              ref={(el) => {
                pulseRefs.current[i] = el;
              }}
              className="fx-pulse"
              r={2.4}
              fill={ed.corr >= 0 ? P.accentBright : P.slate}
              opacity={0.9}
            />
          ))}
        {pos.map((p, i) => {
          const zz = zByPipe[p.name];
          const az = zz ? Math.abs(zz.z) : 0;
          const r = zz ? 6 + Math.min(az, 8) * 1.25 : 6;
          const stroke = az >= 2.5 ? P.stress : az >= 1.5 ? P.erosion : P.ghost;
          const cos = Math.cos(p.a);
          const lx = CX + (R + r + 14) * Math.cos(p.a);
          const ly = CY + (R + r + 14) * Math.sin(p.a);
          return (
            <g
              key={p.name}
              className="fx-node"
              onMouseEnter={() =>
                setReadout(
                  `${p.name} · ${zz ? `current ${zz.kind} ${fmt(zz.z, 2)} (SONAR)` : "no direct SONAR scan — base size"} · coupling ${fmt(coupling[i], 2)}`,
                )
              }
              onMouseLeave={() => setReadout("")}
            >
              <circle
                cx={p.x}
                cy={p.y}
                r={r + 3.5}
                fill="none"
                stroke={P.accentSoft}
                strokeWidth={1}
                strokeOpacity={0.10 + 0.5 * (coupling[i] / maxCoupling)}
              />
              <circle cx={p.x} cy={p.y} r={r} fill="#11131d" stroke={stroke} strokeWidth={1.4} />
              <text
                x={lx}
                y={ly}
                className="fx-nodelabel"
                textAnchor={cos > 0.3 ? "start" : cos < -0.3 ? "end" : "middle"}
                dominantBaseline="middle"
              >
                {p.name}
              </text>
              <title>{p.name}</title>
            </g>
          );
        })}
      </svg>
      <div className="fx-readout">
        {readout || (
          <>
            ring = current coupling · node size = |Δz| where SONAR scans the pipe · solid edge = positive r,
            dashed = negative · one pulse traversal = lag × 1.4s{rm ? " · motion off (reduced-motion)" : ""}
          </>
        )}
      </div>
      {edges.length === 0 && <div className="allclear">▮ no edges above threshold — pipes decoupled</div>}
    </div>
  );
}

function HydrophoneMapCard({ e, m, sonar }: { e: Any; m: Any; sonar: Any }) {
  if (!e?.ok) return null; // HydrophoneCard already carries the Fault
  return (
    <div className="card span7">
      <h2>Hydrophone Map</h2>
      <div className="sub">
        the lead-lag table drawn as the network it is — pulses ride each edge from the pipe that leads
        to the pipe that follows, at a speed scaled to the measured lag; the map reorganizing is itself a signal
      </div>
      <HydrophoneMap e={e} m={m} sonar={sonar} />
      <Method>{e.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   SpilloverCard — Diebold-Yilmaz directional connectedness across the harbors.
   The gauge is the total index (0-100); the bars are each node's NET
   (TO − FROM) diverging from the center axis — right = net stress source,
   left = net sink. Honesty rails ride in the method line: connectedness is a
   statistical lead, not a proven channel, and the graph is blind to exogenous
   shocks (that is the physics engines' job).
   ------------------------------------------------------------------------- */
function SpilloverCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Spillover" reason={e?.reason ?? "not in this snapshot"} span={5} />;
  const dir: Any[] = e.directional ?? [];
  const total = typeof e.total_connectedness === "number" ? e.total_connectedness : 0;
  const maxNet = Math.max(...dir.map((d) => Math.abs(d.net ?? 0)), 1e-9);
  const tone = total >= 70 ? P.stress : total >= 50 ? P.strain : total >= 30 ? P.erosion : P.calm;
  // semicircular gauge: 180° arc, value sweeps from the left stop
  const GX = 90, GY = 84, GR = 68;
  const frac = Math.min(Math.max(total / 100, 0), 1);
  const endA = Math.PI * (1 - frac);
  const ex = GX + GR * Math.cos(endA), ey = GY - GR * Math.sin(endA);
  const arcD = `M ${GX - GR} ${GY} A ${GR} ${GR} 0 ${frac > 0.5 ? 1 : 0} 1 ${ex.toFixed(1)} ${ey.toFixed(1)}`;
  return (
    <div className="card span5">
      <h2>Spillover</h2>
      <div className="sub">
        when one harbor's funding tightens, where does the stress go — directional connectedness of the
        harbor panel · structural, not causal
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ position: "relative", width: 180, height: 98 }}>
          <svg width="180" height="98" viewBox="0 0 180 98" role="img" aria-label={`total connectedness ${fmt(total, 0)} of 100`}>
            <path d={`M ${GX - GR} ${GY} A ${GR} ${GR} 0 0 1 ${GX + GR} ${GY}`}
                  fill="none" stroke="#11131d" strokeWidth={7} strokeLinecap="round" />
            {frac > 0.004 && (
              <path d={arcD} pathLength={1} className="draw"
                    fill="none" stroke={tone} strokeWidth={7} strokeLinecap="round" />
            )}
            <text x={GX - GR} y={GY + 13} textAnchor="middle" className="fx-gauge-cap">0</text>
            <text x={GX + GR} y={GY + 13} textAnchor="middle" className="fx-gauge-cap">100</text>
          </svg>
          <div style={{ position: "absolute", left: 0, right: 0, top: 46, textAlign: "center", pointerEvents: "none" }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 26, fontWeight: 500, color: tone, fontVariantNumeric: "tabular-nums" }}>
              <Roll v={total} d={0} />
            </div>
            <div style={{ fontSize: 9, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ghost)" }}>
              total / 100
            </div>
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 170 }}>
          <div className="dimsmall" style={{ lineHeight: 1.6 }}>{e.verdict}</div>
          <div className="dimsmall" style={{ marginTop: 5 }}>
            source <b style={{ color: P.strain }}>{e.source ?? "—"}</b> · sink{" "}
            <b style={{ color: P.slate }}>{e.sink ?? "—"}</b> · {e.n_obs} obs · VAR({e.lag}), H={e.horizon}
          </div>
        </div>
      </div>
      <div className="fx-bars">
        <div className="fx-barrow" style={{ color: "var(--ghost)" }}>
          <span className="node" style={{ color: "var(--ghost)" }}>node</span>
          <span style={{ fontSize: 9, textAlign: "center" }}>← sink · NET · source →</span>
          <span className="nums">net · to/from</span>
        </div>
        {dir.map((d) => {
          const net = d.net ?? 0;
          const w = (Math.abs(net) / maxNet) * 50;
          return (
            <div className="fx-barrow" key={d.node}>
              <span className="node" title={d.node}>{d.node}</span>
              <div className="track">
                <div
                  className="fill"
                  style={{
                    left: `${net >= 0 ? 50 : 50 - w}%`,
                    width: `${w}%`,
                    background: net >= 0 ? P.strain : P.slate,
                    transformOrigin: net >= 0 ? "left" : "right",
                  }}
                />
              </div>
              <span className="nums">
                {net > 0 ? "+" : ""}
                {fmt(net, 0)} · {fmt(d.to, 0)}/{fmt(d.from, 0)}
              </span>
            </div>
          );
        })}
      </div>
      <Method>{(e.caveats ?? []).join(" · ")} · {e.method}</Method>
    </div>
  );
}

export default function Resonance({ snap }: { snap: Any }) {
  return (
    <div className="grid">
      <ResonanceCard e={snap.engines.resonance} />
      <UndertowCard e={snap.engines.undertow} />
      <EdetectCard e={snap.engines.edetect} />
      <HydrophoneCard e={snap.engines.hydrophone} />
      <EdgesCard e={snap.engines.hydrophone} />
      <HydrophoneMapCard e={snap.engines.hydrophone} m={snap.engines.merian} sonar={snap.engines.sonar} />
      <SpilloverCard e={snap.engines.spillover} />
      <StationKeepingCard e={snap.engines.stationkeeping} />
      <SonarCard e={snap.engines.sonar} />
    </div>
  );
}