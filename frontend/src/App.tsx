import { useEffect, useState } from "react";
import Chart from "./Chart";

type Any = Record<string, any>;

const fmt = (v: number | null | undefined, d = 1, unit = "") =>
  v == null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}${unit}`;

function Stat({ k, blk, unit, d = 2 }: { k: string; blk: Any; unit: string; d?: number }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{blk ? fmt(blk.value, d, unit) : "—"}</div>
      <div className="asof">{blk?.asof ?? "no data"}</div>
    </div>
  );
}

function Decomp({ composite }: { composite: Any }) {
  return (
    <div className="decomp">
      {(composite.decomposition ?? []).map((d: Any) => (
        <div className="row" key={d.component}>
          <span className={d.status === "DEAD" ? "dead" : ""}>{d.component}</span>
          <div className="bar">
            <div style={{ width: `${d.score ?? 0}%`, background: d.status === "DEAD" ? "#e5484d" : undefined }} />
          </div>
          <span className={d.status === "DEAD" ? "dead" : ""}>
            {d.status === "DEAD" ? "DEAD" : fmt(d.score, 0)}
          </span>
        </div>
      ))}
    </div>
  );
}

function KinkCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Kink Engine" reason={e?.reason} />;
  const risky = e.distance_b < 300;
  return (
    <div className="card span6">
      <h2>Kink Engine</h2>
      <div className="sub">live reserve-demand curve · where does scarcity start?</div>
      <div className="kv">
        <div className="item"><div className="k">est. kink</div><div className="v">${fmt(e.kink_reserves_b, 0)}B</div></div>
        <div className="item"><div className="k">reserves</div><div className="v">${fmt(e.current_reserves_b, 0)}B</div></div>
        <div className="item"><div className="k">distance</div><div className={`v ${risky ? "bad" : ""}`}>${fmt(e.distance_b, 0)}B</div></div>
        <div className="item"><div className="k">drain /bday</div><div className="v">{fmt(e.drain_per_bday_b, 1)}B</div></div>
        <div className="item"><div className="k">days to kink</div><div className={`v ${e.days_to_kink && e.days_to_kink < 60 ? "bad" : ""}`}>{e.days_to_kink ?? "n/a"}</div></div>
        <div className="item"><div className="k">fit R²</div><div className="v">{fmt(e.r2, 2)}</div></div>
      </div>
      <div className="method">{e.method} · asof {e.asof}</div>
    </div>
  );
}

function WeatherCard({ e, kinkB }: { e: Any; kinkB: number | null }) {
  if (!e?.ok) return <Fault name="Liquidity Weather" reason={e?.reason} />;
  return (
    <div className="card span6">
      <h2>Liquidity Weather</h2>
      <div className="sub">6-week forward reserve path · TGA seasonal + Fed drift · 20/80% bands</div>
      <Chart
        rows={e.path}
        series={[
          { label: "forecast $B", color: "#4cc3ff" },
          { label: "low", color: "#3d4654", dash: [4, 4] },
          { label: "high", color: "#3d4654", dash: [4, 4] },
        ]}
        refLine={kinkB ? { value: kinkB, color: "#e5484d", label: `kink ~$${Math.round(kinkB)}B` } : null}
        yLabel="$B"
      />
      {e.crunch_windows.length === 0 ? (
        <div className="allclear">▮ no crunch windows inside the horizon</div>
      ) : (
        e.crunch_windows.slice(0, 5).map((c: Any) => (
          <div className="crunch" key={c.date}>
            <b>{c.date}</b> — worst-case ${fmt(c.worst_case_b, 0)}B ({c.reason})
          </div>
        ))
      )}
      <div className="method">{e.method}</div>
    </div>
  );
}

function TailsCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Tail Seismograph" reason={e?.reason} />;
  return (
    <div className="card span6">
      <h2>Tail Seismograph</h2>
      <div className="sub">P99 − P50 of secured rates — the tails detach before the median moves</div>
      <div className="kv">
        <div className="item"><div className="k">tail index (z)</div>
          <div className={`v ${e.tail_index_z > 1.5 ? "bad" : e.tail_index_z > 0.7 ? "warn" : ""}`}>{fmt(e.tail_index_z, 2)}</div></div>
        {Object.entries<Any>(e.per_rate ?? {}).map(([r, d]) => (
          <div className="item" key={r}><div className="k">{r} tail</div><div className="v">{fmt(d.tail_bp, 0)}bp</div></div>
        ))}
        <div className="item"><div className="k">SOFR−IORB</div>
          <div className={`v ${(e.spread?.sofr_iorb_bp ?? 0) > 5 ? "bad" : ""}`}>{fmt(e.spread?.sofr_iorb_bp, 0)}bp</div></div>
      </div>
      <Chart rows={e.index_series} series={[{ label: "tail index z", color: "#e88a3a" }]} />
      <div className="method">{e.method}</div>
    </div>
  );
}

function EchoCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Echo Engine" reason={e?.reason} />;
  return (
    <div className="card span6">
      <h2>Echo Engine</h2>
      <div className="sub">does today's trajectory rhyme with any pre-stress run-up?</div>
      <table className="mini">
        <thead><tr><th>episode</th><th>match window</th><th>similarity</th><th></th></tr></thead>
        <tbody>
          {e.matches.map((m: Any) => (
            <tr key={m.date}>
              <td>{m.episode}</td>
              <td className="num">T−{m.lead_days}d</td>
              <td className="num">{fmt(m.similarity, 3)}</td>
              <td><div className="simbar"><div style={{ width: `${m.similarity * 100}%` }} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="method">{e.method} · resemblance is context, not evidence — not weighted into the index</div>
    </div>
  );
}

function RvCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="RV X-Ray" reason={e?.reason} />;
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
      <div className="method">{e.method}</div>
    </div>
  );
}

function AuctionsCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Auction Digestion" reason={e?.reason} />;
  return (
    <div className="card span4">
      <h2>Auction Digestion</h2>
      <div className="sub">is the market choking on supply?</div>
      <div className="metric">{fmt(e.digestion_index, 2)}</div>
      <Chart rows={e.index_series} series={[{ label: "digestion idx", color: "#d9b23a" }]} height={120} />
      <table className="mini">
        <thead><tr><th>date</th><th>tenor</th><th>b/c</th><th>score</th></tr></thead>
        <tbody>
          {e.recent_auctions.slice(-6).reverse().map((a: Any, i: number) => (
            <tr key={i}>
              <td>{a.date}</td><td>{a.tenor.replace("Bill ", "B ").replace("Note ", "N ").replace("Bond ", "Bd ")}</td>
              <td className="num">{fmt(a.btc, 2)}</td>
              <td className="num" style={{ color: a.score > 0.8 ? "#e5484d" : undefined }}>{fmt(a.score, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="method">{e.method}</div>
    </div>
  );
}

function Fault({ name, reason }: { name: string; reason?: string }) {
  return (
    <div className="card span6">
      <h2>{name}</h2>
      <div className="faults">ENGINE DOWN — {reason ?? "unknown"}</div>
    </div>
  );
}

function Provenance({ prov }: { prov: Any[] }) {
  return (
    <div className="card span12">
      <h2>Provenance</h2>
      <div className="sub">no naked numbers — every input with its source, as-of and staleness</div>
      <table className="mini">
        <thead><tr><th>series</th><th>source</th><th>label</th><th>as-of</th><th>fetched</th><th>staleness</th></tr></thead>
        <tbody>
          {prov.map((p: Any) => (
            <tr key={p.mnemonic}>
              <td>{p.mnemonic}</td><td>{p.source}</td><td>{p.label}</td>
              <td className="num">{p.asof ?? "—"}</td>
              <td className="num">{(p.fetched_at ?? "").slice(0, 16).replace("T", " ")}</td>
              <td><span className={`chip ${p.staleness ?? "fresh"}`}>{p.staleness ?? "fresh"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [snap, setSnap] = useState<Any | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    fetch("/api/overview")
      .then((r) => r.json())
      .then(setSnap)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => clearInterval(t);
  }, []);

  if (err) return <div className="app"><div className="faults">API unreachable: {err}</div></div>;
  if (!snap) return <div className="app"><div className="loading">SEICHE · sounding the basin…</div></div>;

  const c = snap.engines.composite ?? {};
  const kinkB = snap.engines.kink?.ok ? snap.engines.kink.kink_reserves_b : null;

  return (
    <div className="app">
      <div className="masthead">
        <div className="wordmark">SEI<span>CHE</span></div>
        <div className="tagline">funding-stress &amp; leveraged-positioning early warning · free public data only</div>
        <div className="right">
          generated {snap.generated_at?.slice(0, 16).replace("T", " ")}Z<br />
          sources: FRED · NY Fed · OFR · FiscalData · CFTC
        </div>
      </div>

      {snap.faults?.length > 0 && (
        <div className="faults">
          {snap.faults.length} source fault(s): {snap.faults.map((f: Any) => f.source).join(", ")} — affected inputs degraded or dead, composite coverage reduced accordingly
        </div>
      )}

      <div className="hero">
        <div className="dial">
          <div className="value">{fmt(c.value, 0)}</div>
          <div>
            <div className={`regime ${c.regime}`}>{c.regime ?? "?"}</div>
            <div className="coverage" style={{ marginTop: 6 }}>Seiche Index · coverage {fmt(c.coverage_pct, 0)}%</div>
          </div>
        </div>
        <Decomp composite={c} />
      </div>

      <div className="strip">
        <Stat k="SOFR" blk={snap.headline.sofr_pct} unit="%" />
        <Stat k="EFFR" blk={snap.headline.effr_pct} unit="%" />
        <Stat k="IORB" blk={snap.headline.iorb_pct} unit="%" />
        <Stat k="Reserves" blk={snap.headline.reserves_b} unit="B" d={0} />
        <Stat k="ON RRP" blk={snap.headline.rrp_b} unit="B" d={0} />
        <Stat k="TGA" blk={snap.headline.tga_b} unit="B" d={0} />
        <Stat k="SRF take-up" blk={snap.headline.srf_accepted_b} unit="B" />
      </div>

      <div className="grid">
        <WeatherCard e={snap.engines.weather} kinkB={kinkB} />
        <KinkCard e={snap.engines.kink} />
        <TailsCard e={snap.engines.tails} />
        <EchoCard e={snap.engines.echo} />
        <RvCard e={snap.engines.rvxray} />
        <AuctionsCard e={snap.engines.auctions} />
        <Provenance prov={snap.provenance ?? []} />
      </div>

      <div className="footer">
        SEICHE — a standing wave in an enclosed basin, invisible until it sloshes over the edge. ·
        Not investment advice. All data from free public APIs with their native lags (COT is T+3 by construction; that lag is shown, never hidden). ·
        Composite weights are editorial and live in backend/seiche/config.py.
      </div>
    </div>
  );
}
