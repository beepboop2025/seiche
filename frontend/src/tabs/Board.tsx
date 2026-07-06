import { useState } from "react";
import Chart from "../Chart";
import { Any, fmt, Fault, Method, Stat, Decomp } from "../lib";

function AskCard({ live }: { live: boolean }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Any | null>(null);
  if (!live) return null;
  const go = () => {
    if (!q.trim()) return;
    setBusy(true);
    fetch(`/api/ask?q=${encodeURIComponent(q.trim())}`)
      .then((r) => r.json())
      .then(setRes)
      .catch((e) => setRes({ ok: false, reason: String(e) }))
      .finally(() => setBusy(false));
  };
  return (
    <div className="card span12">
      <h2>Ask the desk</h2>
      <div className="sub">answers are restricted to the live board (every number cited to its engine) — not advice</div>
      <div className="tmcontrols">
        <input
          type="text" value={q} placeholder='e.g. "why is the index elevated?" · "what should I watch this week?"'
          style={{ flex: 1, minWidth: 280, background: "var(--bg)", border: "1px solid var(--panel-edge)",
                   color: "var(--text)", fontFamily: "var(--mono)", fontSize: 12, padding: "8px 10px", borderRadius: 4 }}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && go()}
        />
        <button onClick={go} disabled={busy || !q.trim()}>{busy ? "reading the board…" : "ask"}</button>
      </div>
      {res && (res.ok ? (
        <div className="askanswer">
          {res.answer}
          <div className="method" style={{ marginTop: 8 }}>[{res.route}] {res.grounding}</div>
        </div>
      ) : (
        <div className="faults">{res.reason}</div>
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
        <div className="item"><div className="k">days to kink</div><div className={`v ${e.days_to_kink && e.days_to_kink < 60 ? "bad" : ""}`}>{e.distance_b < 0 ? "below kink" : e.days_to_kink ?? "n/a"}</div></div>
        <div className="item"><div className="k">fit R²</div><div className="v">{fmt(e.r2, 2)}</div></div>
        <div className="item"><div className="k">model vs mkt</div><div className={`v ${e.consistency < 0.7 ? "warn" : ""}`}>{fmt(e.predicted_spread_now_bp, 0)}bp / {fmt(e.observed_spread_now_bp, 0)}bp</div></div>
      </div>
      <Method>{e.method} · asof {e.asof}</Method>
    </div>
  );
}

function WeatherCard({ e, kinkB }: { e: Any; kinkB: number | null }) {
  if (!e?.ok) return <Fault name="Liquidity Weather" reason={e?.reason} />;
  return (
    <div className="card span6">
      <h2>Liquidity Weather</h2>
      <div className="sub">6-week forward reserve path · TGA seasonal + Fed drift + settlements · 20/80% bands</div>
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
      <Method>{e.method}</Method>
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
      <Method>{e.method}</Method>
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
      <Method>{e.method} · resemblance is context, not evidence — not weighted into the index</Method>
    </div>
  );
}

export default function Board({ snap, live }: { snap: Any; live: boolean }) {
  const c = snap.engines.composite ?? {};
  const kinkB = snap.engines.kink?.ok ? snap.engines.kink.kink_reserves_b : null;
  const tell = snap.deep?.tell ?? {};
  const ml = snap.deep?.ml ?? {};

  return (
    <>
      <div className="hero">
        <div className="dial">
          <div className="value">{fmt(c.value, 0)}</div>
          <div>
            <div className={`regime ${c.regime}`}>{c.regime ?? "?"}</div>
            <div className="coverage" style={{ marginTop: 6 }}>Seiche Index · coverage {fmt(c.coverage_pct, 0)}%</div>
            {tell.ok && (
              <div className={`tellchip ${tell.tell >= 15 ? "hot" : tell.tell <= -15 ? "cold" : ""}`}>
                TELL {tell.tell > 0 ? "+" : ""}{fmt(tell.tell, 0)} · {tell.reading}
              </div>
            )}
            {ml.ok && (
              <div className={`tellchip ${ml.p_event_5bd >= 0.5 ? "hot" : ""}`} style={{ marginLeft: 6 }}>
                ML P(event 5bd) {fmt(ml.p_event_5bd * 100, 1)}%
              </div>
            )}
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
        <Stat k="SRF" blk={snap.headline.srf_accepted_b} unit="B" />
        <Stat k="Disc. window" blk={snap.headline.dw_b} unit="B" />
        <Stat k="VIX" blk={snap.headline.vix} unit="" />
        <Stat k="HY OAS" blk={snap.headline.hy_oas_pct} unit="%" />
      </div>

      <div className="grid">
        <WeatherCard e={snap.engines.weather} kinkB={kinkB} />
        <KinkCard e={snap.engines.kink} />
        <TailsCard e={snap.engines.tails} />
        <EchoCard e={snap.engines.echo} />
        <AskCard live={live} />
      </div>
    </>
  );
}
