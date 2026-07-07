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

export default function Forecast({ snap }: { snap: Any }) {
  const deep = snap.deep ?? {};
  return (
    <div className="grid">
      <SwellCard s={deep.swell} />
      <TideTablesCard t={deep.tidetables} />
    </div>
  );
}
