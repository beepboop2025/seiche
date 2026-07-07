import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

function TellCard({ t }: { t: Any }) {
  if (!t?.ok) return <Fault name="The Tell" reason={t?.reason} span={12} />;
  const hot = t.tell >= 15, cold = t.tell <= -15;
  return (
    <div className="card span12">
      <h2>The Tell</h2>
      <div className="sub">plumbing percentile − market-priced-stress percentile · the gap is the signal</div>
      <div className="tellhero">
        <div className={`tellvalue ${hot ? "hot" : cold ? "cold" : ""}`}>
          {t.tell > 0 ? "+" : ""}{fmt(t.tell, 0)}
        </div>
        <div>
          <div className="tellreading">{t.reading}</div>
          <div className="coverage">plumbing {fmt(t.plumbing_pctl, 0)}th pctl · market {fmt(t.market_pctl, 0)}th pctl · asof {t.asof}</div>
        </div>
        <div className="kv" style={{ marginLeft: "auto" }}>
          {Object.entries<Any>(t.components ?? {}).map(([k, c]) => (
            <div className="item" key={k}>
              <div className="k">{c.label}</div>
              <div className="v">{fmt(c.last, 2)} <span className="dimsmall">({fmt(c.pctl, 0)}th)</span></div>
            </div>
          ))}
        </div>
      </div>
      <Chart rows={t.series} series={[{ label: "tell", color: "#8a63d2" }]} refLine={{ value: 0, color: "#3d4654", label: "" }} />
      <Method>{t.method}</Method>
    </div>
  );
}

function TideTablesCard({ t }: { t: Any }) {
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

function PlaybookCard({ p }: { p: Any }) {
  if (!p?.ok) return <Fault name="Playbook" reason={p?.reason} span={12} />;
  const horizons = ["5d", "20d"];
  return (
    <div className="card span12">
      <h2>Playbook</h2>
      <div className="sub">
        what happened the last {p.state?.n_matching_days} times the board read{" "}
        <b>{p.state?.regime} × {p.state?.tell_bucket}</b> — native units, n shown, not advice
      </div>
      <table className="mini">
        <thead>
          <tr>
            <th>outcome</th>
            {horizons.map((h) => (
              <th key={h} colSpan={3}>next {h}</th>
            ))}
          </tr>
          <tr>
            <th></th>
            {horizons.map((h) => (
              <>
                <th key={h + "m"}>median</th>
                <th key={h + "i"}>p25 / p75</th>
                <th key={h + "n"}>%+ · n</th>
              </>
            ))}
          </tr>
        </thead>
        <tbody>
          {(p.tables ?? []).map((row: Any) => (
            <tr key={row.mnemonic}>
              <td>{row.outcome}</td>
              {horizons.map((h) => {
                const c = row.horizons?.[h];
                if (!c || c.insufficient)
                  return <td key={h} colSpan={3} className="dimsmall">n/a (n={c?.n_days ?? 0})</td>;
                const dim = c.low_confidence ? { opacity: 0.45 } : undefined;
                return (
                  <>
                    <td key={h + "m"} className="num" style={{ ...dim, color: c.median > 0 ? "#37c88b" : c.median < 0 ? "#e5484d" : undefined }}
                        title={c.low_confidence ? "fewer than 8 non-overlapping windows — an anecdote, not a distribution" : undefined}>
                      {c.median > 0 ? "+" : ""}{fmt(c.median, 2)}
                    </td>
                    <td key={h + "i"} className="num dimsmall" style={dim}>{fmt(c.p25, 1)} / {fmt(c.p75, 1)}</td>
                    <td key={h + "n"} className="num dimsmall" style={dim}>{fmt(c.pct_positive, 0)}% · {c.n_days}({c.n_independent})</td>
                  </>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{p.caveat} · {p.method}</Method>
    </div>
  );
}

export default function Market({ snap }: { snap: Any }) {
  const deep = snap.deep ?? {};
  return (
    <div className="grid">
      <TellCard t={deep.tell} />
      <TideTablesCard t={deep.tidetables} />
      <PlaybookCard p={deep.playbook} />
    </div>
  );
}
