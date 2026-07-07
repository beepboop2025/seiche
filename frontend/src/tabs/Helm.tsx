import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

const STANCE_COLOR: Record<string, string> = {
  risk_off: "#e5484d",
  risk_on: "#37c88b",
  neutral: "#6b7686",
};

function TodayCard({ t, stk }: { t: Any; stk: Any }) {
  const col = STANCE_COLOR[t.stance] ?? "#6b7686";
  return (
    <div className="card span12">
      <h2>Today's Book</h2>
      <div className="sub">
        the signal made accountable — explicit positions from a frozen rulebook · paper proxy, not advice
      </div>
      <div className="tellhero">
        <div className="tellvalue" style={{ color: col }}>{t.stance?.toUpperCase()}</div>
        <div>
          <div className="tellreading">{t.rationale}</div>
          <div className="coverage">
            ensemble P(event,5bd) {fmt(t.p_ensemble, 2)} · fleet dispersion {fmt(t.dispersion, 2)}
            {t.dispersion_gate_on ? " · GATE ON (disagreement → neutral)" : ""}
            {t.tell != null ? ` · Tell ${t.tell > 0 ? "+" : ""}${fmt(t.tell, 0)}` : ""} ·
            {t.changed_vs_prior ? " FLIPPED today" : ` unchanged (was ${t.prior_stance ?? "—"})`}
          </div>
          {stk?.ok && <div className="coverage">{stk.verdict}</div>}
        </div>
      </div>
      <table className="mini">
        <thead><tr><th>sleeve</th><th>direction</th><th>weight</th><th>vol (ann)</th><th>cost</th></tr></thead>
        <tbody>
          {(t.positions ?? []).map((p: Any) => (
            <tr key={p.sleeve}>
              <td>{p.label}</td>
              <td style={{ color: p.weight > 0 ? "#37c88b" : p.weight < 0 ? "#e5484d" : "#6b7686" }}>
                {p.direction}
              </td>
              <td className="num">{p.weight > 0 ? "+" : ""}{fmt(p.weight, 3)}</td>
              <td className="num dimsmall">{fmt(p.vol_ann_pct, 1)}%</td>
              <td className="num dimsmall">{fmt(p.tcost_bp, 0)}bp</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EnsembleCard({ s }: { s: Any }) {
  if (!s?.ok) return <Fault name="The Stack" reason={s?.reason} span={5} />;
  const v = s.validation ?? {};
  return (
    <div className="card span5">
      <h2>The Stack</h2>
      <div className="sub">every forecast Seiche makes, calibrated and blended · published = {s.published}</div>
      <div className="kv">
        <div className="item"><div className="k">P(event, 5bd)</div><div className="v">{fmt(s.p_now, 3)}
          {s.calibrated_band && <span className="dimsmall" title={s.calibrated_band.method}>
            {" "}[{fmt(s.calibrated_band.p0, 2)}–{fmt(s.calibrated_band.p1, 2)}]</span>}
        </div></div>
        <div className="item"><div className="k">dispersion</div><div className="v">{fmt(s.dispersion_now, 3)}</div></div>
        {Object.entries<number | null>(s.members_now ?? {}).map(([m, p]) => (
          <div className="item" key={m}><div className="k">{m}</div><div className="v">{p == null ? "—" : fmt(p, 3)}</div></div>
        ))}
      </div>
      <table className="mini">
        <thead><tr><th>stream</th><th>Brier</th><th>AUROC</th></tr></thead>
        <tbody>
          <tr><td><b>stack</b></td><td className="num">{fmt(v.brier_stack, 4)}</td><td className="num">{fmt(v.auroc_stack, 3)}</td></tr>
          <tr><td>equal-weight mean</td><td className="num">{fmt(v.brier_mean, 4)}</td><td className="num">{fmt(v.auroc_mean, 3)}</td></tr>
          {Object.entries<number>(v.brier_members ?? {}).map(([m, b]) => (
            <tr key={m}><td className="dimsmall">{m}</td><td className="num dimsmall">{fmt(b, 4)}</td>
              <td className="num dimsmall">{fmt(v.auroc_members?.[m], 3)}</td></tr>
          ))}
          <tr><td className="dimsmall">climatology</td><td className="num dimsmall">{fmt(v.brier_climatology, 4)}</td><td className="num dimsmall">—</td></tr>
        </tbody>
      </table>
      <div className="sub" style={{ marginTop: 6 }}>{s.verdict}</div>
      {s.series?.length > 0 && (
        <Chart rows={s.series} series={[
          { label: "P(event)", color: "#e8b64c" },
          { label: "dispersion", color: "#3d4654", dash: [3, 3] },
        ]} height={120} />
      )}
      <Method>{(s.caveats ?? []).join(" · ")} · {s.method}</Method>
    </div>
  );
}

function BacktestCard({ b }: { b: Any }) {
  const ci = b.ci95 ?? ["—", "—"];
  const bench = b.benchmarks ?? {};
  return (
    <div className="card span7">
      <h2>Walk-Forward P&L</h2>
      <div className="sub">
        {b.sample?.start} → {b.sample?.end} · {b.sample?.n_days}d · {b.sample?.n_stance_runs} stance runs
        (the independent-ish n) · costs charged, signal t → returns t+1
      </div>
      <div className="kv">
        <div className="item"><div className="k">net Sharpe</div>
          <div className="v">{fmt(b.sharpe, 2)} <span className="dimsmall">CI [{fmt(ci[0], 2)}, {fmt(ci[1], 2)}]</span></div></div>
        <div className="item"><div className="k">NW t-stat</div><div className="v">{fmt(b.nw_tstat, 2)}</div></div>
        <div className="item"><div className="k">return / vol</div><div className="v">{fmt(b.ann_return_pct, 1)}% / {fmt(b.ann_vol_pct, 1)}%</div></div>
        <div className="item"><div className="k">max drawdown</div><div className="v">{fmt(b.max_dd_pct, 1)}%</div></div>
        <div className="item"><div className="k">turnover · cost drag</div><div className="v">{fmt(b.turnover_ann, 1)}x · {fmt(b.cost_drag_bp_ann, 0)}bp/yr</div></div>
        <div className="item"><div className="k">2× costs</div>
          <div className={`v ${b.robust_to_2x_costs ? "" : "bad"}`}>{b.robust_to_2x_costs ? "survives" : "does not survive"}</div></div>
      </div>
      {b.equity?.length > 0 && (
        <Chart rows={b.equity} series={[
          { label: "the Book", color: "#e8b64c" },
          { label: "static mix", color: "#8a63d2", dash: [4, 3] },
          { label: "cash", color: "#3d4654", dash: [2, 3] },
        ]} yLabel="growth of 1" />
      )}
      <table className="mini">
        <thead><tr><th>benchmark</th><th>Sharpe</th><th>ret/yr</th><th>maxDD</th></tr></thead>
        <tbody>
          {Object.entries<Any>(bench).map(([name, m]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{fmt(m.sharpe, 2)}</td>
              <td className="num">{fmt(m.ann_return_pct, 1)}%</td>
              <td className="num">{fmt(m.max_dd_pct, 1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="sub" style={{ marginTop: 6, fontWeight: 600 }}>{b.verdict}</div>
    </div>
  );
}

function EpisodesCard({ rows }: { rows: Any[] }) {
  return (
    <div className="card span7">
      <h2>Episode Attribution</h2>
      <div className="sub">book vs static mix, T−30bd → T+10bd around each labeled stress episode</div>
      <table className="mini">
        <thead><tr><th>episode</th><th>book</th><th>static</th></tr></thead>
        <tbody>
          {(rows ?? []).map((e: Any) => (
            <tr key={e.date}>
              <td>{e.episode}</td>
              {e.in_sample ? (
                <>
                  <td className="num" style={{ color: e.book_pct > e.static_pct ? "#37c88b" : "#e5484d" }}>
                    {e.book_pct > 0 ? "+" : ""}{fmt(e.book_pct, 1)}%
                  </td>
                  <td className="num dimsmall">{e.static_pct > 0 ? "+" : ""}{fmt(e.static_pct, 1)}%</td>
                </>
              ) : (
                <td colSpan={2} className="dimsmall">out of sample</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LiveCard({ lv }: { lv: Any }) {
  return (
    <div className="card span5">
      <h2>Live Track Record</h2>
      <div className="sub">as-published positions only — hash-chained in the published site, replayed against realized returns</div>
      <div className="kv">
        <div className="item"><div className="k">days as-published</div><div className="v">{lv.n_days ?? 0}</div></div>
        {lv.since && <div className="item"><div className="k">since</div><div className="v">{lv.since}</div></div>}
        {lv.cum_return_pct != null && (
          <div className="item"><div className="k">cumulative</div>
            <div className="v" style={{ color: lv.cum_return_pct >= 0 ? "#37c88b" : "#e5484d" }}>
              {lv.cum_return_pct > 0 ? "+" : ""}{fmt(lv.cum_return_pct, 2)}%
            </div></div>
        )}
        {lv.sharpe != null && <div className="item"><div className="k">live Sharpe</div><div className="v">{fmt(lv.sharpe, 2)}</div></div>}
      </div>
      <Method>{lv.note}</Method>
    </div>
  );
}

function NavigatorCard({ n }: { n: Any }) {
  if (!n?.ok) return <Fault name="The Navigator" reason={n?.reason} span={12} />;
  const rec = n.record ?? {};
  const judged = rec.ok && rec.brier != null;
  const beats = judged && rec.brier < rec.brier_climatology;
  return (
    <div className="card span12">
      <h2>The Navigator</h2>
      <div className="sub">
        an LLM forecaster made accountable — one committed P(event, 5bd) per data-day into the
        hash-chained record; no backtest is possible for an LLM (it has read the history), so the
        forward record below is its only evidence
      </div>
      <div className="tellhero">
        <div className={`tellvalue ${n.p_event_5bd >= 0.5 ? "hot" : ""}`}>{fmt(n.p_event_5bd * 100, 0)}%</div>
        <div>
          <div className="tellreading">{n.rationale}</div>
          <div className="coverage" style={{ color: judged ? (beats ? "#37c88b" : "#e5484d") : undefined }}>
            forward record: {rec.ok
              ? judged
                ? `Brier ${fmt(rec.brier, 4)} vs climatology ${fmt(rec.brier_climatology, 4)} over ${rec.n_resolved} resolved — ${rec.verdict}`
                : rec.verdict
              : rec.reason ?? "no record yet"} · committed {n.asof}{n.cached ? " (cached — today's number is already on the record)" : ""}
          </div>
        </div>
      </div>
      <Method>{(n.caveats ?? []).join(" · ")} · {n.method}</Method>
    </div>
  );
}

export default function Helm({ snap }: { snap: Any }) {
  const deep = snap.deep ?? {};
  const bk = deep.book ?? {};
  if (!bk.ok) {
    return (
      <div className="grid">
        <Fault name="The Book" reason={bk.reason} span={12} />
        <NavigatorCard n={snap.navigator} />
      </div>
    );
  }
  return (
    <div className="grid">
      <TodayCard t={bk.today ?? {}} stk={deep.stacker} />
      <BacktestCard b={bk.backtest ?? {}} />
      <EnsembleCard s={deep.stacker} />
      <NavigatorCard n={snap.navigator} />
      <EpisodesCard rows={bk.backtest?.episodes ?? []} />
      <LiveCard lv={bk.live ?? {}} />
      <div className="card span12">
        <Method>{bk.duration_note} · {(bk.caveats ?? []).join(" · ")} · {bk.method}</Method>
      </div>
    </div>
  );
}
