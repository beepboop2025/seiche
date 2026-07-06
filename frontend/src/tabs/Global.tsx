import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

export default function Global({ snap }: { snap: Any }) {
  const e = snap.engines?.basins ?? {};
  if (!e.ok) return <div className="grid"><Fault name="Global Basins" reason={e.reason} span={12} /></div>;
  const sw = e.swap_lines ?? {};
  const ch = e.channels ?? {};
  const tide = e.tide ?? {};

  return (
    <div className="grid">
      <div className="card span12">
        <h2>Global Basin Coupling</h2>
        <div className="sub">
          the dollar system as connected bodies of water — when the basins synchronize, one tide moves
          everything · coupling score {fmt(e.score, 0)}
        </div>
        <div className="basingrid">
          {(e.basins ?? []).map((b: Any) => (
            <div className={`basin ${Math.abs(b.z) >= 1.5 ? "hot" : ""}`} key={b.basin}>
              <div className="name">{b.basin}</div>
              <div className="rate">{fmt(b.value_bp, 1)}bp</div>
              <div className="z" style={{ color: Math.abs(b.z) >= 1.5 ? "#e88a3a" : "#6b7686" }}>
                z {fmt(b.z, 2)} · {b.anchor}
              </div>
              <div className="asof" style={{ color: "#3d4654", fontSize: 10 }}>{b.asof}</div>
            </div>
          ))}
          <div className="basin">
            <div className="name">DOLLAR (broad)</div>
            <div className="rate">{fmt(ch.dollar_idx, 1)}</div>
            <div className="z" style={{ color: Math.abs(ch.dollar_idx_z ?? 0) >= 1.5 ? "#e88a3a" : "#6b7686" }}>
              z {fmt(ch.dollar_idx_z, 2)} · DTWEXBGS
            </div>
          </div>
          <div className="basin">
            <div className="name">FOREIGN OFFICIAL RRP</div>
            <div className="rate">${fmt(ch.foreign_rrp_b, 0)}B</div>
            <div className="z" style={{ color: (ch.foreign_rrp_chg_13w_b ?? 0) < -50 ? "#e88a3a" : "#6b7686" }}>
              Δ13w {fmt(ch.foreign_rrp_chg_13w_b, 0)}B · the offshore dollar pool
            </div>
          </div>
        </div>
        <Method>{e.out_of_scope}</Method>
      </div>

      <div className="card span7">
        <h2>The Tide</h2>
        <div className="sub">
          common-component share across basins + channels — high tide = one basin, globally fragile
          · now {fmt(tide.absorption, 3)} ({fmt(tide.pctl, 0)}th pctl of own history, {tide.n_series} series)
        </div>
        {tide.series?.length ? (
          <Chart rows={tide.series} series={[{ label: "tide (top-2 PC share)", color: "#8a63d2" }]} />
        ) : (
          <div className="sub">insufficient overlapping history yet — the tide needs ~8 months of panel</div>
        )}
        <Method>{e.method}</Method>
      </div>

      <div className="card span5">
        <h2>Cross-Basin Lead-Lag</h2>
        <div className="sub">which basin is upstream this quarter</div>
        <table className="mini">
          <thead><tr><th>leads</th><th></th><th>follows</th><th>lag</th><th>r</th></tr></thead>
          <tbody>
            {(e.edges ?? []).map((ed: Any, i: number) => (
              <tr key={i}>
                <td>{ed.lead}</td><td>→</td><td>{ed.follows}</td>
                <td className="num">{ed.lag_d}d</td>
                <td className="num" style={{ color: Math.abs(ed.corr) >= 0.4 ? "#e88a3a" : undefined }}>{fmt(ed.corr, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(e.edges ?? []).length === 0 && <div className="allclear">▮ basins decoupled — no edges above threshold</div>}
      </div>

      <div className="card span12">
        <h2>Swap-Line Confession</h2>
        <div className="sub">
          a foreign central bank drawing USD means someone in its jurisdiction couldn't find dollars
          privately — small-value test operations excluded ({sw.small_value_ops_excluded} filtered)
        </div>
        <div className="kv">
          <div className="item"><div className="k">outstanding (H.4.1)</div>
            <div className={`v ${(sw.outstanding_m ?? 0) >= 5000 ? "bad" : (sw.outstanding_m ?? 0) >= 500 ? "warn" : ""}`}>
              ${fmt(sw.outstanding_m, 0)}M
            </div></div>
          <div className="item"><div className="k">ops, last 30d</div>
            <div className={`v ${(sw.ops_30d_total_m ?? 0) >= 1000 ? "bad" : ""}`}>${fmt(sw.ops_30d_total_m, 0)}M</div></div>
          {Object.entries<number>(sw.ops_30d_by_counterparty ?? {}).slice(0, 4).map(([cp, amt]) => (
            <div className="item" key={cp}><div className="k">{cp}</div><div className="v">${fmt(amt, 0)}M</div></div>
          ))}
        </div>
        <table className="mini">
          <thead><tr><th>trade date</th><th>counterparty</th><th>amount</th><th>term</th><th>rate</th></tr></thead>
          <tbody>
            {(sw.recent_ops ?? []).map((o: Any, i: number) => (
              <tr key={i}>
                <td className="num">{o.trade_date}</td>
                <td>{o.counterparty}</td>
                <td className="num">${fmt(o.amount_m, 0)}M</td>
                <td className="num">{o.term_days}d</td>
                <td className="num">{fmt(o.rate, 2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Method>NY Fed FX swap operations + H.4.1 weekly outstanding · the 2020 peak was ~$450B</Method>
      </div>
    </div>
  );
}
