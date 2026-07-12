/**
 * BOARD — The Dive.
 *
 * The board as a water column: scroll to descend from what markets price
 * (surface) to what the floor knows (physics). The gauge on the left tracks
 * your depth; the Tell is the visible gap between the surface and everything
 * below it. Causality is ordered top-to-bottom, so the morning scan is one
 * gesture.
 */
import { useEffect, useRef, useState } from "react";
import { Any, fmt } from "../lib";

/* ---------- tiny SVG line helper (ports the exploration's path scaler) ---- */

type Box = { x0: number; x1: number; y0: number; y1: number };

function scalePaths(rows: (string | number | null)[][], nSeries: number, box: Box) {
  const { x0, x1, y0, y1 } = box;
  const N = rows.length;
  let vmin = Infinity, vmax = -Infinity;
  for (const r of rows)
    for (let i = 1; i <= nSeries; i++) {
      const v = r[i];
      if (v != null) { vmin = Math.min(vmin, v as number); vmax = Math.max(vmax, v as number); }
    }
  if (!isFinite(vmin)) { vmin = 0; vmax = 1; }
  const span = vmax - vmin || 1;
  const X = (k: number) => x0 + (k / (N - 1 || 1)) * (x1 - x0);
  const Y = (v: number) => y1 - ((v - vmin) / span) * (y1 - y0);
  const paths: string[] = [];
  for (let i = 1; i <= nSeries; i++) {
    let d = "", pen = false;
    rows.forEach((r, k) => {
      if (r[i] == null) { pen = false; return; }
      d += (pen ? "L" : "M") + X(k).toFixed(1) + "," + Y(r[i] as number).toFixed(1);
      pen = true;
    });
    paths.push(d);
  }
  return { paths, vmin, vmax, X, Y };
}

const ord = (n: number | null | undefined) => {
  if (n == null) return "—";
  const v = Math.round(n);
  const s = ["th", "st", "nd", "rd"], m = v % 100;
  return `${v}${s[(m - 20) % 10] ?? s[m] ?? s[0]}`;
};

const suffix = (n: number | null | undefined) => (n == null ? "" : ord(n).replace(/^-?\d+/, ""));

const signed = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${fmt(v, d)}`;

const fmtD = (ds?: string) => {
  if (!ds) return "";
  const d = new Date(ds);
  return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getUTCMonth()] + " " + String(d.getUTCDate()).padStart(2, "0");
};

/* ---------- layers ------------------------------------------------------- */

function Kicker({ k, sub }: { k: string; sub: string }) {
  return (
    <div className="dive-kicker">
      <span className="k">{k}</span>
      <span className="sub">{sub}</span>
    </div>
  );
}

function Surface({ tell, headline }: { tell: Any; headline: Any }) {
  const m = tell?.ok ? tell.market_pctl : null;
  const read =
    m == null ? { word: "UNKNOWN", note: "market read unavailable", color: "var(--faint)" }
    : m < 30 ? { word: "CALM", note: "nothing priced", color: "var(--calm)" }
    : m < 60 ? { word: "WATCHFUL", note: "some premium", color: "var(--erosion)" }
    : { word: "STRESSED", note: "priced in", color: "var(--stress)" };
  return (
    <div className="dive-layer">
      <Kicker k="Surface · what markets price" sub="VIX, credit, rates vol — the screens everyone watches" />
      <div style={{ display: "flex", gap: 36, marginTop: 14, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div>
          <div className="dive-lbl">market-priced stress</div>
          <div className="dive-bignum">
            {m == null ? "—" : Math.round(m)}
            <span className="unit">{suffix(m)} pctl</span>
          </div>
        </div>
        <div>
          <div className="dive-lbl">VIX</div>
          <div className="dive-mid">{fmt(headline?.vix?.value, 2)}</div>
        </div>
        <div>
          <div className="dive-lbl">HY OAS</div>
          <div className="dive-mid">{fmt(headline?.hy_oas_pct?.value, 2, "%")}</div>
        </div>
        <div style={{ marginLeft: "auto", textAlign: "right" }}>
          <div className="dive-lbl">the screens read</div>
          <div style={{ fontSize: 14, color: read.color, fontWeight: 500 }}>{read.word} · {read.note}</div>
        </div>
      </div>
    </div>
  );
}

function TellBracket({ tell }: { tell: Any }) {
  if (!tell?.ok) return null;
  const rows: (string | number | null)[][] = (tell.series ?? []).filter((_: Any, i: number) => i % 2 === 0);
  const t = rows.length > 3 ? scalePaths(rows, 1, { x0: 2, x1: 258, y0: 4, y1: 52 }) : null;
  const zeroY = t ? Math.max(4, Math.min(52, t.Y(0))) : 32;
  const startYear = rows.length ? String(rows[0][0]).slice(0, 4) : "";
  return (
    <div className="tellbracket dive-layer" style={{ animationDelay: "0.08s" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 22, flexWrap: "wrap" }}>
        <div>
          <div className="title">The Tell — surface vs. depth</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 4 }}>
            <span className="value">{signed(tell.tell)}</span>
            <span style={{ fontSize: 13, color: "#b2b6ca" }}>{tell.reading}</span>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 2 }}>
            plumbing {ord(tell.plumbing_pctl)} pctl − market {ord(tell.market_pctl)} pctl · the gap IS the thesis · asof {tell.asof}
          </div>
        </div>
        {t && (
          <svg viewBox="0 0 260 64" style={{ width: 300, height: 74, marginLeft: "auto", overflow: "visible" }}>
            <line x1={0} x2={260} y1={zeroY} y2={zeroY} stroke="rgba(233,233,237,0.14)" strokeDasharray="3 4" />
            <path d={t.paths[0]} fill="none" stroke="var(--accent)" strokeWidth={1.6}
              style={{ filter: "drop-shadow(0 0 6px rgba(145,132,217,0.5))" }} />
            <text x={0} y={62} fill="var(--ghost)" fontSize={9}>{startYear}</text>
            <text x={248} y={62} fill="var(--ghost)" fontSize={9}>now</text>
          </svg>
        )}
      </div>
    </div>
  );
}

function RateCell({ k, v, note, color }: { k: string; v: string; note: string; color?: string }) {
  return (
    <div style={{ padding: "10px 14px 10px 0" }}>
      <div style={{ fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--faint)" }}>{k}</div>
      <div style={{ fontSize: 22, fontWeight: 500, color: color ?? "var(--text)", marginTop: 2 }}>{v}</div>
      <div style={{ fontSize: 10, color: "var(--ghost)", marginTop: 1 }}>{note}</div>
    </div>
  );
}

function Rates({ headline, tails }: { headline: Any; tails: Any }) {
  const spreadBp = tails?.ok ? tails.spread?.sofr_iorb_bp : null;
  const spreadColor = spreadBp == null ? undefined
    : spreadBp <= 0 ? "var(--calm)" : spreadBp < 10 ? "var(--erosion)" : "var(--stress)";
  const series: (string | number | null)[][] = tails?.ok ? tails.spread?.series ?? [] : [];
  const sp = series.length > 3 ? scalePaths(series, 1, { x0: 0, x1: 880, y0: 6, y1: 80 }) : null;
  const zeroY = sp ? Math.max(6, Math.min(80, sp.Y(0))) : 48;
  return (
    <div className="dive-layer" style={{ animationDelay: "0.16s" }}>
      <Kicker k="Rates · the waterline" sub="the administered corridor and where secured funding clears inside it" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", marginTop: 12 }}>
        <RateCell k="SOFR" v={fmt(headline?.sofr_pct?.value, 2, "%")} note={headline?.sofr_pct?.asof?.slice(5) ?? ""} />
        <RateCell k="EFFR" v={fmt(headline?.effr_pct?.value, 2, "%")} note={headline?.effr_pct?.asof?.slice(5) ?? ""} />
        <RateCell k="IORB" v={fmt(headline?.iorb_pct?.value, 2, "%")} note={headline?.iorb_pct?.asof?.slice(5) ?? ""} />
        <RateCell k="SOFR−IORB" v={spreadBp == null ? "—" : `${signed(spreadBp)}bp`} color={spreadColor}
          note={spreadBp == null ? "" : spreadBp <= 0 ? "soft floor" : "above the floor"} />
        <RateCell k="Tail z" v={fmt(tails?.ok ? tails.tail_index_z : null, 2)} note="P99−P50 blend" />
      </div>
      {sp && (
        <svg viewBox="0 0 880 96" style={{ width: "100%", height: "auto", display: "block", marginTop: 4, overflow: "visible" }}>
          <line x1={0} x2={880} y1={zeroY} y2={zeroY} stroke="rgba(233,233,237,0.14)" strokeDasharray="3 4" />
          <path d={sp.paths[0]} fill="none" stroke="var(--dim)" strokeWidth={1.3} />
          <text x={0} y={94} fill="var(--ghost)" fontSize={9.5}>SOFR−IORB, two years · bp</text>
          <text x={880} y={94} textAnchor="end" fill="var(--ghost)" fontSize={9.5}>last {signed(spreadBp)}bp</text>
        </svg>
      )}
    </div>
  );
}

function WeatherMini({ e }: { e: Any }) {
  if (!e?.ok) return <div className="dive-lbl">Liquidity Weather unavailable — {e?.reason ?? "engine down"}</div>;
  const w = scalePaths(e.path ?? [], 3, { x0: 40, x1: 514, y0: 8, y1: 128 });
  const dates: string[] = (e.path ?? []).map((r: Any) => r[0]);
  const dot = (i: number) => (i === 0 ? "var(--strain)" : "var(--erosion)");
  return (
    <div>
      <div style={{ fontSize: 12, color: "#b2b6ca" }}>Liquidity Weather — six-week reserve path</div>
      <svg viewBox="0 0 520 148" style={{ width: "100%", height: "auto", display: "block", marginTop: 8, overflow: "visible" }}>
        {[8, 68, 128].map((y) => <line key={y} x1={40} x2={514} y1={y} y2={y} stroke="rgba(233,233,237,0.08)" />)}
        <text x={36} y={12} textAnchor="end" fill="var(--faint)" fontSize={9.5}>${Math.round(w.vmax)}B</text>
        <text x={36} y={72} textAnchor="end" fill="var(--faint)" fontSize={9.5}>${Math.round((w.vmax + w.vmin) / 2)}B</text>
        <text x={36} y={132} textAnchor="end" fill="var(--faint)" fontSize={9.5}>${Math.round(w.vmin)}B</text>
        <path d={w.paths[1]} fill="none" stroke="var(--ghost)" strokeWidth={1.1} strokeDasharray="4 4" />
        <path d={w.paths[2]} fill="none" stroke="var(--ghost)" strokeWidth={1.1} strokeDasharray="4 4" />
        <path d={w.paths[0]} fill="none" stroke="var(--accent)" strokeWidth={1.7}
          style={{ filter: "drop-shadow(0 0 6px rgba(145,132,217,0.45))" }} />
        <text x={42} y={144} fill="var(--ghost)" fontSize={9.5}>{fmtD(dates[0])}</text>
        <text x={270} y={144} textAnchor="middle" fill="var(--ghost)" fontSize={9.5}>{fmtD(dates[Math.floor(dates.length / 2)])}</text>
        <text x={512} y={144} textAnchor="end" fill="var(--ghost)" fontSize={9.5}>{fmtD(dates[dates.length - 1])}</text>
      </svg>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
        {(e.crunch_windows ?? []).slice(0, 4).map((c: Any, i: number) => (
          <div key={c.date} style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 12 }}>
            <span style={{ flex: "none", width: 7, height: 7, borderRadius: "50%", background: dot(i), alignSelf: "center" }} />
            <span style={{ color: "var(--text)", fontWeight: 500, whiteSpace: "nowrap" }}>{fmtD(c.date)}</span>
            <span style={{ color: "var(--dim)" }}>{c.reason} — worst case ${fmt(c.worst_case_b, 0)}B</span>
          </div>
        ))}
        {(e.crunch_windows ?? []).length === 0 && (
          <div style={{ fontSize: 12, color: "var(--calm)" }}>no crunch windows inside the horizon</div>
        )}
      </div>
    </div>
  );
}

function KinkMini({ e, spreadBp }: { e: Any; spreadBp: number | null }) {
  if (!e?.ok) return <div className="dive-lbl">Kink Engine unavailable — {e?.reason ?? "engine down"}</div>;
  const below = e.distance_b < 0;
  const kinkPct = 85;
  const resPct = Math.max(4, Math.min(98, (e.current_reserves_b / e.kink_reserves_b) * kinkPct));
  const closing = below
    ? `Below the kink, every settlement day is a stress test the market grades in bp. The spread at ${signed(spreadBp)}bp says the water is ${spreadBp != null && spreadBp <= 0 ? "calm" : "choppy"}; the position of the floor says it is shallow.`
    : `Reserves sit above the estimated kink — the basin has depth to spare, and settlement days should price as noise.`;
  return (
    <div>
      <div style={{ fontSize: 12, color: "#b2b6ca" }}>Kink Engine — reserve scarcity, located</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 10 }}>
        <span style={{ fontSize: 34, fontWeight: 500, letterSpacing: "-0.02em", color: below ? "var(--erosion)" : "var(--calm)" }}>
          ${fmt(Math.abs(e.distance_b), 0)}B
        </span>
        <span style={{ fontSize: 13, color: "var(--dim)" }}>{below ? "below" : "above"} the estimated kink</span>
      </div>
      <div style={{ position: "relative", height: 6, borderRadius: 3, background: "var(--panel-2)", margin: "12px 0 6px", overflow: "visible" }}>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: `${resPct}%`, borderRadius: 3, background: "linear-gradient(90deg, var(--accent-deep), var(--accent-dim))" }} />
        <div style={{ position: "absolute", left: `${kinkPct}%`, top: -4, width: 2, height: 14, background: "var(--stress)" }} />
        <div style={{ position: "absolute", left: `${resPct}%`, top: -4, width: 2, height: 14, background: "var(--text)" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--ghost)" }}>
        <span>drained</span>
        <span style={{ color: "#cfd3e5" }}>reserves ${fmt(e.current_reserves_b, 0)}B</span>
        <span style={{ color: "var(--stress)" }}>kink ${fmt(e.kink_reserves_b, 0)}B</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 18px", marginTop: 14 }}>
        <div className="dive-kv"><span className="k">drain / bday</span><span className="v">{fmt(e.drain_per_bday_b, 1)}B</span></div>
        <div className="dive-kv"><span className="k">fit R²</span><span className="v">{fmt(e.r2, 2)}</span></div>
        <div className="dive-kv"><span className="k">model / market</span><span className="v">{fmt(e.predicted_spread_now_bp, 0)} / {fmt(e.observed_spread_now_bp, 0)}bp</span></div>
        <div className="dive-kv"><span className="k">days to kink</span><span className="v">{below ? "below now" : e.days_to_kink ?? "n/a"}</span></div>
      </div>
      <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 12, lineHeight: 1.5 }}>{closing}</div>
    </div>
  );
}

function Plumbing({ weather, kink, tails }: { weather: Any; kink: Any; tails: Any }) {
  const spreadBp = tails?.ok ? tails.spread?.sofr_iorb_bp : null;
  return (
    <div className="dive-layer" style={{ animationDelay: "0.24s" }}>
      <Kicker k="Plumbing · the reserve basin" sub="where every squeeze started — weeks before the surface noticed" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 28, marginTop: 14 }}>
        <WeatherMini e={weather} />
        <KinkMini e={kink} spreadBp={spreadBp} />
      </div>
    </div>
  );
}

function BigCell({ title, big, bigColor, sub, size = 26 }: { title: string; big: any; bigColor?: string; sub: string; size?: number }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: "#b2b6ca" }}>{title}</div>
      <div style={{ fontSize: size, fontWeight: 500, color: bigColor ?? "var(--text)", marginTop: 6 }}>{big}</div>
      <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 2, lineHeight: 1.5 }}>{sub}</div>
    </div>
  );
}

function Positioning({ rv, wh, crowd }: { rv: Any; wh: Any; crowd: Any }) {
  const topBucket = wh?.ok
    ? [...(wh.buckets ?? [])].filter((b: Any) => b.bucket !== "Bills").sort((a: Any, b: Any) => (b.pctl ?? 0) - (a.pctl ?? 0))[0]
    : null;
  const worstRow = crowd?.ok
    ? [...(crowd.rows ?? [])].sort((a: Any, b: Any) => Math.abs(b.z ?? 0) - Math.abs(a.z ?? 0))[0]
    : null;
  return (
    <div className="dive-layer" style={{ animationDelay: "0.32s" }}>
      <Kicker k="Positioning · who is leaning on the water" sub="the leverage that turns a slosh into a wave · CFTC T+3, shown not hidden" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 28, marginTop: 14 }}>
        {rv?.ok ? (
          <BigCell title="RV X-Ray" big={`$${fmt(rv.pair_proxy_b, 0)}B`}
            sub={`leveraged Treasury pair proxy · z ${fmt(rv.size_z, 2)} · DV01 $${fmt(rv.dv01_m_per_bp, 0)}M/bp · Δ13w ${signed(rv.pair_change_13w_b, 0)}B`} />
        ) : <BigCell title="RV X-Ray" big="—" sub={rv?.reason ?? "engine down"} />}
        {wh?.ok ? (
          <BigCell title="Dealer Warehouse" bigColor={wh.total_pctl >= 90 ? "var(--strain)" : undefined}
            big={<>{Math.round(wh.total_pctl)}<span style={{ fontSize: 14, color: "var(--dim)" }}>{suffix(wh.total_pctl)} pctl</span></>}
            sub={`$${fmt(wh.total_net_b, 0)}B net UST inventory${wh.total_pctl >= 90 ? " — the shock absorber is nearly spent" : ""}${topBucket ? ` · ${String(topBucket.bucket).replace("Coupons ", "")} bucket at ${ord(topBucket.pctl)}` : ""}`} />
        ) : <BigCell title="Dealer Warehouse" big="—" sub={wh?.reason ?? "engine down"} />}
        {worstRow ? (
          <BigCell title="Crowding" big={`z ${fmt(worstRow.z, 2)}`}
            sub={`${worstRow.contract} leveraged net at the ${ord(worstRow.pctl)} pctl of its history${Math.abs(worstRow.z ?? 0) >= 2 ? " — an unwind waiting for a reason" : ""}`} />
        ) : <BigCell title="Crowding" big="—" sub={crowd?.reason ?? "engine down"} />}
      </div>
    </div>
  );
}

function Floor({ resonance, undertow, bathy, swell }: { resonance: Any; undertow: Any; bathy: Any; swell: Any }) {
  const worst = resonance?.ok ? resonance.worst_mode : null;
  const modeWord = worst?.mode ? String(worst.mode).replace(/_/g, "-") : "calendar";
  const ut = undertow?.ok ? undertow.per_series?.spread : null;
  const stretch = ut?.recovery?.stretch;
  const fl = bathy?.ok ? bathy.floor : null;
  return (
    <div className="dive-layer" style={{ animationDelay: "0.4s", paddingBottom: 8 }}>
      <Kicker k="The Floor · what the physics knows" sub="damping, resonance and the shape of the basin — fragility measured on days when nothing happens" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 22, marginTop: 14 }}>
        {worst ? (
          <BigCell size={24} title="Resonance" big={`${fmt(worst.amplification, 2)}×`}
            bigColor={worst.amplification >= 3 ? "var(--strain)" : undefined}
            sub={`${modeWord} forcing now sloshes ${fmt(worst.amplification, 1)}× its old amplitude`} />
        ) : <BigCell size={24} title="Resonance" big="—" sub={resonance?.reason ?? "engine down"} />}
        {ut ? (
          <BigCell size={24} title="Undertow"
            big={<>AC1 {Math.round(ut.ac1_pctl)}<span style={{ fontSize: 13, color: "var(--dim)" }}>{suffix(ut.ac1_pctl)}</span></>}
            sub={stretch != null && stretch > 1.5
              ? `recovery half-life stretched ${fmt(stretch, 1)}× — damping eroding`
              : `recovery τ ${fmt(ut?.tau_bd, 1)}bd — damping ${ut?.mechanism ?? "steady"}`} />
        ) : <BigCell size={24} title="Undertow" big="—" sub={undertow?.reason ?? "engine down"} />}
        {fl ? (
          <BigCell size={24} title="Bathymetry" big={<>{fmt(fl.barrier_kt, 2)} k<span style={{ fontSize: 13 }}>B</span>T</>}
            sub={`escape barrier from the measured floor · first passage ~${fmt(bathy.mfpt_bd, 0)}bd`} />
        ) : <BigCell size={24} title="Bathymetry" big="—" sub="dynamics unavailable" />}
        {swell?.ok ? (
          <BigCell size={24} title="Swell, 5bd" big={`${fmt((swell.event_by_horizon?.h5 ?? 0) * 100, 1)}%`}
            bigColor="var(--accent-bright)"
            sub={`P(funding event) from the forcing calendar · 42bd: ${fmt((swell.event_by_horizon?.h42 ?? 0) * 100, 0)}%`} />
        ) : <BigCell size={24} title="Swell, 5bd" big="—" sub="forecast unavailable" />}
      </div>
    </div>
  );
}

function Verdict({ composite, tell, kink }: { composite: Any; tell: Any; kink: Any }) {
  const n = (composite.decomposition ?? []).length;
  const below = kink?.ok && kink.distance_b < 0;
  const gap = tell?.ok ? tell.tell : null;
  const surface = tell?.ok && tell.market_pctl < 30 ? "The surface prices calm" : "The surface is paying attention";
  const depth = below ? `; four layers down, the basin has lost $${fmt(Math.abs(kink.distance_b), 0)}B of depth and the calendar is loaded` : "";
  const gapLine = gap != null ? ` That gap — ${signed(gap)} — is the reading.` : "";
  return (
    <div className="verdict dive-layer" style={{ animationDelay: "0.46s" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span className="num">{fmt(composite.value, 0)}</span>
          <span className={`regime ${composite.regime}`}>{composite.regime ?? "?"}</span>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--dim)", maxWidth: 560, lineHeight: 1.55 }}>
          Seiche Index, all {n} components weighted · coverage {fmt(composite.coverage_pct, 0)}%. {surface}{depth}.{gapLine}
        </div>
      </div>
    </div>
  );
}

function AskDesk({ live }: { live: boolean }) {
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
    <div className="dive-layer" style={{ marginTop: 4 }}>
      <Kicker k="Ask the desk" sub="answers are restricted to the live board (every number cited to its engine) — not advice" />
      <div className="tmcontrols" style={{ marginTop: 12 }}>
        <input
          type="text" value={q} placeholder='e.g. "why is the index elevated?" · "what should I watch this week?"'
          style={{ flex: 1, minWidth: 280 }}
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

/* ---------- the dive ------------------------------------------------------ */

const STATIONS = [
  { depth: "0 m", name: "SURFACE", hint: "what markets price" },
  { depth: "−10 m", name: "RATES", hint: "the waterline" },
  { depth: "−40 m", name: "PLUMBING", hint: "the reserve basin" },
  { depth: "−70 m", name: "POSITIONING", hint: "who leans on the water" },
  { depth: "−120 m", name: "THE FLOOR", hint: "damping · resonance · shape" },
];

export default function Board({ snap, live }: { snap: Any; live: boolean }) {
  const e = snap.engines ?? {};
  const deep = snap.deep ?? {};
  const [active, setActive] = useState(0);
  const layerRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const mid = window.innerHeight * 0.35;
        let L = 0;
        layerRefs.current.forEach((el, i) => {
          if (el && el.getBoundingClientRect().top <= mid) L = i;
        });
        setActive(L);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => { window.removeEventListener("scroll", onScroll); cancelAnimationFrame(raf); };
  }, []);

  const setRef = (i: number) => (el: HTMLDivElement | null) => { layerRefs.current[i] = el; };

  return (
    <div className="dive">
      <div className="dive-gauge">
        <div className="dive-gauge-rail">
          {STATIONS.map((s, i) => (
            <div key={s.name} className={`dive-station ${i === active ? "active" : ""}`}>
              <span className="dot" />
              <div className="depth">{s.depth}</div>
              <div className="name">{s.name}</div>
              <div className="hint">{s.hint}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="dive-col">
        <div ref={setRef(0)}>
          <Surface tell={deep.tell} headline={snap.headline} />
          <TellBracket tell={deep.tell} />
        </div>

        <div ref={setRef(1)}>
          <Rates headline={snap.headline} tails={e.tails} />
        </div>

        <div className="dive-rule" />

        <div ref={setRef(2)}>
          <Plumbing weather={e.weather} kink={e.kink} tails={e.tails} />
        </div>

        <div className="dive-rule" />

        <div ref={setRef(3)}>
          <Positioning rv={e.rvxray} wh={e.warehouse} crowd={e.crowding} />
        </div>

        <div className="dive-rule" />

        <div ref={setRef(4)}>
          <Floor resonance={e.resonance} undertow={e.undertow} bathy={deep.bathymetry} swell={deep.swell} />
          <Verdict composite={e.composite ?? {}} tell={deep.tell} kink={e.kink} />
        </div>

        <div className="dive-rule" />
        <AskDesk live={live} />
      </div>
    </div>
  );
}
