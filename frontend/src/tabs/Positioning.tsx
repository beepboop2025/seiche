import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

function RvCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="RV X-Ray" reason={e?.reason} span={8} />;
  return (
    <div className="card span8">
      <h2>RV X-Ray</h2>
      <div className="sub">Treasury RV complex — CFTC leveraged-fund shorts × repo funding (T+3 by nature)</div>
      <div className="kv">
        <div className="item"><div className="k">pair proxy</div><div className="v">${fmt(e.pair_proxy_b, 0)}B</div></div>
        <div className="item"><div className="k">gross lev short</div><div className="v">${fmt(e.gross_short_b, 0)}B</div></div>
        <div className="item"><div className="k">DV01</div><div className="v">${fmt(e.dv01_m_per_bp, 0)}M/bp</div></div>
        <div className="item"><div className="k">Δ 13w</div><div className={`v ${(e.pair_change_13w_b ?? 0) > 50 ? "warn" : ""}`}>{fmt(e.pair_change_13w_b, 0)}B</div></div>
        <div className="item"><div className="k">size z</div><div className="v">{fmt(e.size_z, 2)}</div></div>
        <div className="item"><div className="k">DVP volume</div><div className="v">${fmt(e.dvp_volume_b, 0)}B/d</div></div>
      </div>
      <Chart
        rows={e.series}
        series={[
          { label: "pair proxy $B", color: "#4cc3ff" },
          { label: "gross short $B", color: "#8a63d2" },
        ]}
      />
      <table className="mini">
        <thead><tr><th>shock</th><th>MTM loss</th><th>assumed unwind (10%)</th><th>days of DVP volume</th></tr></thead>
        <tbody>
          {e.scenarios.map((s: Any) => (
            <tr key={s.shock_bp}>
              <td>{s.shock_bp}bp</td>
              <td className="num">${fmt(s.mtm_loss_b, 1)}B</td>
              <td className="num">${fmt(s.assumed_unwind_b, 0)}B</td>
              <td className="num">{fmt(s.unwind_days_of_dvp, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{e.method}</Method>
    </div>
  );
}

function CrowdingCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Crowding" reason={e?.reason} span={4} />;
  return (
    <div className="card span4">
      <h2>Crowding</h2>
      <div className="sub">leveraged-fund net / open interest — extremes precede unwinds</div>
      <table className="mini">
        <thead><tr><th>contract</th><th>net/OI</th><th>z</th><th>pctl</th></tr></thead>
        <tbody>
          {(e.rows ?? []).map((r: Any) => (
            <tr key={r.contract}>
              <td>{r.contract}</td>
              <td className="num">{r.lev_net_share_oi > 0 ? "+" : ""}{fmt(r.lev_net_share_oi, 2)}</td>
              <td className="num" style={{ color: Math.abs(r.z) >= 2 ? "#e5484d" : Math.abs(r.z) >= 1.3 ? "#d9b23a" : undefined }}>{fmt(r.z, 2)}</td>
              <td className="num">{fmt(r.pctl, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{e.method}</Method>
    </div>
  );
}

function WarehouseCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Warehouse" reason={e?.reason} span={12} />;
  return (
    <div className="card span12">
      <h2>Dealer Warehouse</h2>
      <div className="sub">primary-dealer net UST inventory — a full warehouse is a spent shock absorber (weekly, T+9)</div>
      <div className="kv">
        <div className="item"><div className="k">net inventory</div><div className="v">${fmt(e.total_net_b, 0)}B</div></div>
        <div className="item"><div className="k">saturation</div>
          <div className={`v ${e.total_pctl >= 90 ? "bad" : e.total_pctl >= 70 ? "warn" : ""}`}>{fmt(e.total_pctl, 0)}th pctl</div></div>
        <div className="item"><div className="k">Δ 13w</div><div className="v">{fmt(e.chg_13w_b, 0)}B</div></div>
        <div className="item"><div className="k">long-end share</div><div className="v">{fmt(e.long_end_share_pct, 0)}%</div></div>
      </div>
      <div className="warehouse-row">
        <div className="warehouse-chart">
          <Chart rows={e.series} series={[{ label: "dealer net UST $B", color: "#d9b23a" }]} height={150} />
        </div>
        <table className="mini" style={{ maxWidth: 380 }}>
          <thead><tr><th>bucket</th><th>net $B</th><th>pctl</th></tr></thead>
          <tbody>
            {(e.buckets ?? []).map((b: Any) => (
              <tr key={b.bucket}>
                <td>{b.bucket}</td>
                <td className="num">{fmt(b.net_b, 0)}</td>
                <td className="num" style={{ color: b.pctl >= 95 ? "#e5484d" : undefined }}>{fmt(b.pctl, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Method>{e.method}</Method>
    </div>
  );
}

export default function Positioning({ snap }: { snap: Any }) {
  return (
    <div className="grid">
      <RvCard e={snap.engines.rvxray} />
      <CrowdingCard e={snap.engines.crowding} />
      <WarehouseCard e={snap.engines.warehouse} />
    </div>
  );
}
