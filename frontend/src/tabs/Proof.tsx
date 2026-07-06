import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

export default function Proof({ snap }: { snap: Any }) {
  const bt = snap.deep?.backtest ?? {};
  const hist = snap.deep?.history ?? {};
  if (!bt.ok) return <div className="grid"><Fault name="PROOF" reason={bt.reason} span={12} /></div>;
  const cap = bt.event_capture ?? {};
  const s = bt.sample ?? {};

  return (
    <div className="grid">
      <div className="card span12">
        <h2>PROOF — the page that earns the right to be believed</h2>
        <div className="sub">
          Seiche-lite index rebuilt with expanding-window statistics only (no look-ahead), tested against
          {" "}{s.n_events} funding events over {s.start} → {s.end}. If the numbers were unimpressive, they'd publish anyway.
        </div>
        <div className="kv">
          <div className="item"><div className="k">recall</div><div className="v">{fmt((cap.recall ?? 0) * 100, 0)}%</div></div>
          <div className="item"><div className="k">precision</div><div className="v">{fmt((cap.precision ?? 0) * 100, 0)}%</div></div>
          <div className="item"><div className="k">base rate</div><div className="v">{fmt((cap.base_rate ?? 0) * 100, 0)}%</div></div>
          <div className="item"><div className="k">precision lift</div>
            <div className="v">{cap.base_rate ? fmt(cap.precision / cap.base_rate, 1) : "—"}×</div></div>
          <div className="item"><div className="k">median alert run-up</div><div className="v">{fmt(cap.median_lead_d, 0)}d</div></div>
          <div className="item"><div className="k">alert line</div><div className="v">≥{fmt(cap.alert_pctl, 0)}th pctl</div></div>
          <div className="item"><div className="k">event def</div><div className="v">+{fmt(cap.spike_def_bp, 0)}bp spike</div></div>
        </div>
        <Chart
          rows={bt.signal_series}
          series={[{ label: "Seiche-lite expanding pctl", color: "#4cc3ff" }]}
          refLine={{ value: cap.alert_pctl ?? 80, color: "#e5484d", label: "alert line" }}
          vlines={{ dates: s.event_dates ?? [], color: "rgba(229,72,77,.5)" }}
          height={200}
        />
        <Method>red verticals = funding events · {bt.method}</Method>
      </div>

      <div className="card span7">
        <h2>Episode ledger</h2>
        <div className="sub">the six labeled breaks — including the ones it did NOT catch</div>
        <table className="mini">
          <thead><tr><th>episode</th><th>date</th><th>max pctl (T−30…T−1)</th><th>first alert</th></tr></thead>
          <tbody>
            {(bt.episodes ?? []).map((ep: Any) => (
              <tr key={ep.date}>
                <td>{ep.episode}</td>
                <td className="num">{ep.date}</td>
                <td className="num">{ep.in_sample ? fmt(ep.max_pctl_30d_before, 0) : "out of sample"}</td>
                <td className="num" style={{ color: ep.in_sample && !ep.first_alert_lead_d ? "#e5484d" : "#37c88b" }}>
                  {!ep.in_sample ? "—" : ep.first_alert_lead_d ? `${ep.first_alert_lead_d}d early` : "not alerted"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Method>
          Mar-2020 and Apr-2025 were exogenous shocks, not plumbing events — an honest funding gauge
          should NOT claim them. Catching the mechanical squeezes (Sep/Dec-2025, SVB) with weeks of
          run-up is the actual claim.
        </Method>
      </div>

      <div className="card span5">
        <h2>Reconstruction contract</h2>
        <div className="sub">what the backtest index is — and is not</div>
        <div className="kv" style={{ marginBottom: 8 }}>
          <div className="item"><div className="k">live index now</div><div className="v">{fmt(snap.engines?.composite?.value, 1)}</div></div>
          <div className="item"><div className="k">lite index now</div><div className="v">{fmt(hist.current?.value, 1)}</div></div>
          <div className="item"><div className="k">lite pctl</div><div className="v">{fmt(hist.current?.pctl, 0)}th</div></div>
        </div>
        <table className="mini">
          <thead><tr><th>lite component</th><th>weight</th></tr></thead>
          <tbody>
            {Object.entries<number>(hist.weights ?? {}).map(([k, w]) => (
              <tr key={k}><td>{k}</td><td className="num">{fmt(w, 3)}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="sub" style={{ marginTop: 6 }}>excluded (live-only): {(hist.excluded ?? []).join(", ")}</div>
        {(bt.caveats ?? []).map((c: string, i: number) => (
          <div className="caveat" key={i}>▸ {c}</div>
        ))}
      </div>

      {(bt.outcome_tables ?? []).length > 0 && (
        <div className="card span12">
          <h2>Market outcomes by signal bucket</h2>
          <div className="sub">forward moves conditioned on the index percentile — The Tell's evidence base</div>
          <div className="outgrid">
            {bt.outcome_tables.map((t: Any, i: number) => (
              <table className="mini" key={i}>
                <thead>
                  <tr><th colSpan={4}>{t.outcome} · {t.horizon_bd}d fwd</th></tr>
                  <tr><th>pctl bucket</th><th>median</th><th>%+</th><th>n(indep)</th></tr>
                </thead>
                <tbody>
                  {t.buckets.map((b: Any) => (
                    <tr key={b.bucket}>
                      <td>{b.bucket}</td>
                      <td className="num" style={{ color: (b.median ?? 0) > 0 ? "#37c88b" : (b.median ?? 0) < 0 ? "#e5484d" : undefined }}>
                        {b.median == null ? "—" : `${b.median > 0 ? "+" : ""}${fmt(b.median, 2)}`}
                      </td>
                      <td className="num">{fmt(b.pct_positive, 0)}%</td>
                      <td className="num">{b.n_days}({b.n_independent})</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
