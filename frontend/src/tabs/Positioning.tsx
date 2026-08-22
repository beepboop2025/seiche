import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Fault, Method, ordinal } from "../lib";
import { rvMetricQualityLabel, rvQualityLabel } from "../rvxrayQuality";

function RvMetric({ label, value, prefix = "", suffix = "", digits = 0, quality, tone = "" }: {
  label: string;
  value: number | null | undefined;
  prefix?: string;
  suffix?: string;
  digits?: number;
  quality?: string | null;
  tone?: string;
}) {
  return (
    <div className="item">
      <div className="k">{label}</div>
      <div className={`v ${tone}`}>{value == null ? "—" : `${prefix}${fmt(value, digits)}${suffix}`}</div>
      {quality ? <div className="coverage">{quality}</div> : null}
    </div>
  );
}

function RvCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="RV X-Ray" reason={e?.reason} span={8} />;
  return (
    <div className="card span8">
      <h2>RV X-Ray</h2>
      <div className="sub">Treasury RV complex — CFTC leveraged-fund shorts × repo funding (T+3 by nature)</div>
      <div className="kv">
        <RvMetric label="pair proxy" value={e.pair_proxy_b} prefix="$" suffix="B" quality={rvMetricQualityLabel(e, "pair_proxy_b")} />
        <RvMetric label="gross lev short" value={e.gross_short_b} prefix="$" suffix="B" quality={rvMetricQualityLabel(e, "gross_short_b")} />
        <RvMetric label="DV01" value={e.dv01_m_per_bp} prefix="$" suffix="M/bp" quality={rvMetricQualityLabel(e, "dv01_m_per_bp")} />
        <RvMetric label="Δ 13w" value={e.pair_change_13w_b} suffix="B" quality={rvQualityLabel(e.pair_change_13w_quality)} tone={e.pair_change_13w_b > 50 ? "warn" : ""} />
        <RvMetric label="size z" value={e.size_z} digits={2} quality={e.score_eligible === false ? "WITHHELD · pair coverage/history" : null} />
        <RvMetric label="DVP volume" value={e.dvp_volume_b} prefix="$" suffix="B/d" />
      </div>
      <Chart
        rows={e.series}
        series={[
          { label: "pair proxy $B", color: P.accent },
          { label: "gross short $B", color: P.accentSoft },
        ]}
      />
      {e.series_quality?.policy === "non_complete_aggregates_are_null"
        ? <div className="coverage">Chart gaps mark incomplete contract coverage; no partial total is plotted as a full observation.</div>
        : null}
      <table className="mini">
        <thead><tr><th>shock</th><th>MTM loss</th><th>assumed unwind (10%)</th><th>days of DVP volume</th></tr></thead>
        <tbody>
          {e.scenarios.map((s: Any) => (
            <tr key={s.shock_bp}>
              <td>{s.shock_bp}bp</td>
              <td className="num">{s.mtm_loss_b == null ? "—" : `$${fmt(s.mtm_loss_b, 1)}B`}</td>
              <td className="num">{s.assumed_unwind_b == null ? "—" : `$${fmt(s.assumed_unwind_b, 0)}B`}</td>
              <td className="num">{s.unwind_days_of_dvp == null ? "—" : fmt(s.unwind_days_of_dvp, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="coverage">Shock outputs are withheld unless every required current aggregate has complete expected-contract coverage; — means withheld or unavailable.</div>
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
              <td className="num" style={{ color: Math.abs(r.z) >= 2 ? P.stress : Math.abs(r.z) >= 1.3 ? P.gold : undefined }}>{fmt(r.z, 2)}</td>
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
          <div className={`v ${e.total_pctl >= 90 ? "bad" : e.total_pctl >= 70 ? "warn" : ""}`}>{ordinal(e.total_pctl)} pctl</div></div>
        <div className="item"><div className="k">Δ 13w</div><div className="v">{fmt(e.chg_13w_b, 0)}B</div></div>
        <div className="item"><div className="k">long-end share</div><div className="v">{fmt(e.long_end_share_pct, 0)}%</div></div>
      </div>
      <div className="warehouse-row">
        <div className="warehouse-chart">
          <Chart rows={e.series} series={[{ label: "dealer net UST $B", color: P.gold }]} height={150} />
        </div>
        <table className="mini" style={{ maxWidth: 380 }}>
          <thead><tr><th>bucket</th><th>net $B</th><th>pctl</th></tr></thead>
          <tbody>
            {(e.buckets ?? []).map((b: Any) => (
              <tr key={b.bucket}>
                <td>{b.bucket}</td>
                <td className="num">{fmt(b.net_b, 0)}</td>
                <td className="num" style={{ color: b.pctl >= 95 ? P.stress : undefined }}>{fmt(b.pctl, 0)}</td>
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
