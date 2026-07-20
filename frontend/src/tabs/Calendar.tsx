import { useMemo, useState } from "react";
import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Fault, Method, Roll } from "../lib";
import "../styles-fx.css";

function TurnCard({ t }: { t: Any }) {
  const nt = t?.next_turn;
  if (!t?.ok || !nt) {
    return (
      <div className="card span6">
        <h2>Turn Barometer</h2>
        <div className="faults">unavailable — {t?.reason ?? "no forecast"}</div>
      </div>
    );
  }
  const v = t.validation ?? {};
  return (
    <div className="card span6">
      <h2>Turn Barometer</h2>
      <div className="sub">the next calendar turn: the date is known, only the amplitude is uncertain</div>
      <div className="kv">
        <div className="item"><div className="k">next turn</div><div className="v">{nt.date}</div></div>
        <div className="item"><div className="k">mode</div><div className="v">{nt.mode?.replace("_", "-")}</div></div>
        <div className="item"><div className="k">forecast slosh ({nt.published})</div>
          <div className={`v ${nt.severity >= 4 ? "bad" : nt.severity >= 3 ? "warn" : ""}`}>
            {nt.forecast_bp > 0 ? "+" : ""}{fmt(nt.forecast_bp, 1)}bp
          </div></div>
        <div className="item"><div className="k">model / naive</div>
          <div className="v dimsmall" style={{ fontSize: 13 }}>{fmt(nt.forecast_model_bp, 1)} / {fmt(nt.forecast_naive_bp, 1)}bp</div></div>
        <div className="item"><div className="k">20/80 band</div><div className="v">{fmt(nt.band_bp?.[0], 1)} … {fmt(nt.band_bp?.[1], 1)}</div></div>
        <div className="item"><div className="k">severity</div>
          <div className={`v ${nt.severity >= 4 ? "bad" : nt.severity >= 3 ? "warn" : ""}`}>{nt.severity}/5</div></div>
      </div>
      <div className="sub" style={{ marginTop: 8 }}>
        validation: n={v.n_turns} turns · LOO MAE {fmt(v.loo_mae_bp, 2)}bp vs naive {fmt(v.naive_mae_bp, 2)}bp ·
        skill {fmt(v.skill_vs_naive, 3)} {v.model_used ? "· model used" : ""}
      </div>
      {v.note && <div className="crunch"><b>honesty note</b> — {v.note}</div>}
      <table className="mini">
        <thead><tr><th>recent turns</th><th>mode</th><th>slosh</th></tr></thead>
        <tbody>
          {(t.recent_turns ?? []).slice(-6).reverse().map((r: Any) => (
            <tr key={r.date}><td>{r.date}</td><td>{r.mode?.replace("_", "-")}</td><td className="num">{fmt(r.slosh_bp, 1)}bp</td></tr>
          ))}
        </tbody>
      </table>
      <Method>{t.method}</Method>
    </div>
  );
}

function EventList({ cal }: { cal: Any }) {
  return (
    <div className="card span6">
      <h2>Event Horizon</h2>
      <div className="sub">everything dated inside ~90 days, one list</div>
      {(cal.fomc_next_90d ?? []).map((f: Any) => (
        <div className="evrow" key={f.date}><span className="evtag fomc">FOMC</span><b>{f.date}</b> decision day (in {f.days_until}d)</div>
      ))}
      {(cal.crunch_windows ?? []).slice(0, 6).map((c: Any) => (
        <div className="evrow" key={"c" + c.date}><span className="evtag crunch">CRUNCH</span><b>{c.date}</b> {c.reason}</div>
      ))}
      {(cal.upcoming_settlements ?? []).map((s: Any) => (
        <div className="evrow" key={"s" + s.date}><span className="evtag settle">SETTLE</span><b>{s.date}</b> ${fmt(s.amount_b, 0)}B auction settlement</div>
      ))}
      {(cal.corporate_tax_next_90d ?? []).map((t: Any) => (
        <div className="evrow" key={"t" + t.date}><span className="evtag tax">TAX</span><b>{t.date}</b> corporate tax date (in {t.days_until}d)</div>
      ))}
      {cal.next_turn && (
        <div className="evrow"><span className="evtag turn">TURN</span><b>{cal.next_turn.date}</b> {cal.next_turn.mode?.replace("_", "-")} · forecast {cal.next_turn.forecast_bp > 0 ? "+" : ""}{fmt(cal.next_turn.forecast_bp, 1)}bp (sev {cal.next_turn.severity}/5)</div>
      )}
    </div>
  );
}

function BillDesk({ rows }: { rows: Any[] }) {
  if (!rows?.length) return null;
  return (
    <div className="card span12">
      <h2>Bill Desk</h2>
      <div className="sub">if you must park cash — latest auction stop per tenor + the next auction date</div>
      <table className="mini">
        <thead><tr><th>tenor</th><th>last high rate</th><th>last auction</th><th>next auction</th></tr></thead>
        <tbody>
          {rows.slice(0, 8).map((r: Any) => (
            <tr key={r.tenor}>
              <td>{r.tenor}</td>
              <td className="num">{fmt(r.last_high_rate_pct, 3)}%</td>
              <td className="num">{r.last_auction}</td>
              <td className="num">{r.next_auction ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>Treasury FiscalData auction results + upcoming auctions · discount-rate basis · not advice</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   EventHorizonTimeline — the forcing track, next 42 business days.
   One horizontal track from today out; every dated forcing (turn / tax /
   settlement / crunch window) lands as a marker sized by that forcing's
   historical pop severity — Resonance's per-mode recent median slosh for
   turns and tax dates, settlement size vs the swell heavy-day flag for
   settlements; FOMC shows as a ghost dot (context, not funding forcing).
   Days sharing a date merge into one glyph (the highest-priority forcing,
   radius bumped for the stack). Markers bob gently and a radar sweep scans
   the track — CSS-only perpetuals, no-preference gated; the today cursor
   breathes at bd 0. Business days skip weekends; holidays are not modeled.
   ------------------------------------------------------------------------- */
const KIND_META: Record<string, { color: string; label: string; rank: number }> = {
  settle: { color: P.erosion, label: "SETTLE", rank: 5 },
  turn: { color: P.calm, label: "TURN", rank: 4 },
  tax: { color: P.accentBright, label: "TAX", rank: 3 },
  crunch: { color: P.strain, label: "CRUNCH", rank: 2 },
  fomc: { color: P.ghost, label: "FOMC", rank: 1 },
};

const isoDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function EventHorizonTimeline({ snap }: { snap: Any }) {
  const [hover, setHover] = useState<number | null>(null);
  const model = useMemo(() => {
    const cal = snap.calendar ?? {};
    const asof = String(snap.generated_at ?? snap.deep?.turn?.asof ?? "").slice(0, 10);
    const start = new Date(`${asof}T00:00:00`);
    if (!asof || isNaN(start.getTime())) return null;
    const days: string[] = [];
    const cur = new Date(start);
    while (days.length <= 42) {
      const dow = cur.getDay();
      if (dow !== 0 && dow !== 6) days.push(isoDay(cur));
      cur.setDate(cur.getDate() + 1);
    }
    const bdOf = new Map(days.map((d, i) => [d, i]));
    const modes = snap.engines?.resonance?.modes ?? {};
    const modeMed = (mode: unknown) =>
      typeof mode === "string" ? modes?.[mode]?.recent_median_bp : undefined;
    const flagB = snap.deep?.swell?.settlement?.flag_b ?? 90;
    const sized = (bp: number) => 3.5 + 6.5 * Math.min(Math.max(bp, 0) / 20, 1);
    const byDate = new Map<string, Any[]>();
    const push = (date: unknown, ev: Any) => {
      if (typeof date !== "string" || !bdOf.has(date)) return;
      byDate.set(date, [...(byDate.get(date) ?? []), ev]);
    };
    const nt = cal.next_turn ?? snap.deep?.turn?.next_turn;
    if (nt?.date)
      push(nt.date, {
        kind: "turn",
        r: sized(modeMed(nt.mode) ?? nt.forecast_bp ?? 3),
        detail: `${String(nt.mode).replace("_", "-")} turn · forecast ${nt.forecast_bp > 0 ? "+" : ""}${fmt(nt.forecast_bp, 1)}bp · hist med ${fmt(modeMed(nt.mode), 1)}bp`,
      });
    for (const t of cal.corporate_tax_next_90d ?? [])
      push(t.date, {
        kind: "tax",
        r: sized(modeMed("tax_date") ?? 3),
        detail: `corporate tax date · hist med ${fmt(modeMed("tax_date"), 1)}bp slosh`,
      });
    for (const s of cal.upcoming_settlements ?? [])
      push(s.date, {
        kind: "settle",
        r: sized(((s.amount_b ?? 0) / (2 * flagB)) * 20),
        detail: `$${fmt(s.amount_b, 0)}B auction settlement${(s.amount_b ?? 0) >= flagB ? " — heavy day" : ""}`,
      });
    for (const c of cal.crunch_windows ?? [])
      push(c.date, { kind: "crunch", r: 5, detail: c.reason ?? "calendar crunch window" });
    for (const f of cal.fomc_next_90d ?? [])
      push(f.date, { kind: "fomc", r: 2.6, detail: "FOMC decision day (context, not funding forcing)" });
    const markers = [...byDate.entries()]
      .map(([date, evs]) => {
        const glyph = [...evs].sort((a, b) => KIND_META[b.kind].rank - KIND_META[a.kind].rank)[0];
        return { date, bd: bdOf.get(date)!, evs, kind: glyph.kind as string, r: glyph.r + (evs.length > 1 ? 1.4 : 0) };
      })
      .sort((a, b) => a.bd - b.bd);
    return { days, markers, asof, flagB };
  }, [snap]);
  if (!model) return null;

  const W = 1080, H = 150, PL = 36, PR = 20, trackY = 62;
  const x = (bd: number) => PL + (bd / 42) * (W - PL - PR);
  const hm = hover != null ? model.markers[hover] : null;

  return (
    <div className="card span12">
      <h2>Event Horizon — next 42 business days</h2>
      <div className="sub">
        where the calendar pushes on the plumbing · marker size = the forcing's historical pop severity
        (Resonance per-mode recent median slosh; settlements vs the ${fmt(model.flagB, 0)}B heavy-day flag) ·
        days sharing a date merge, radius bumped for the stack
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="fx-tl" role="img" aria-label="forcing calendar timeline">
        <defs>
          <linearGradient id="fxTlShine" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#fff" stopOpacity="0" />
            <stop offset="0.5" stopColor="#fff" stopOpacity="0.07" />
            <stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
          <clipPath id="fxTlClip"><rect x={PL} y={16} width={W - PL - PR} height={96} /></clipPath>
        </defs>
        <line x1={PL} x2={W - PR} y1={trackY} y2={trackY} className="fx-tl-track" />
        <g clipPath="url(#fxTlClip)">
          <rect className="fx-tl-sweep" x={-130} y={16} width={110} height={96} fill="url(#fxTlShine)" />
        </g>
        {model.days.map((d, i) => (
          <g key={d}>
            <line x1={x(i)} x2={x(i)} y1={trackY} y2={trackY + (i % 5 === 0 ? 6 : 3)} className="fx-tl-tick" />
            {i % 5 === 0 && (
              <text x={x(i)} y={trackY + 18} textAnchor="middle" className="fx-axis">{d.slice(5)}</text>
            )}
          </g>
        ))}
        <line x1={x(0)} x2={x(0)} y1={20} y2={104} className="fx-tl-cursor" />
        <circle cx={x(0)} cy={trackY} r={2.6} fill={P.accentBright} />
        <text x={x(0)} y={14} textAnchor="middle" className="fx-axis" fill={P.accentSoft}>today · {model.asof.slice(5)}</text>
        {model.markers.map((mk, i) => {
          const meta = KIND_META[mk.kind];
          return (
            <g
              key={mk.date}
              className="fx-bob"
              style={{ animationDelay: `${(i % 7) * 0.31}s` }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              <circle cx={x(mk.bd)} cy={trackY} r={mk.r + 5} fill="transparent" className="fx-tl-marker">
                <title>{`${mk.date} · ${mk.evs.map((e: Any) => e.detail).join(" · ")}`}</title>
              </circle>
              <circle cx={x(mk.bd)} cy={trackY} r={mk.r} fill={meta.color} fillOpacity={0.85}
                      stroke="#000" strokeWidth={1} style={{ pointerEvents: "none" }} />
            </g>
          );
        })}
      </svg>
      <div className="fx-readout">
        {hm ? (
          <>
            h+{hm.bd}bd · <b>{hm.date}</b> ·{" "}
            {hm.evs.map((e: Any, i: number) => (
              <span key={i}>
                {i > 0 && " · "}
                <b style={{ color: KIND_META[e.kind].color }}>{KIND_META[e.kind].label}</b> {e.detail}
              </span>
            ))}
          </>
        ) : model.markers.length ? (
          <>hover a marker — that day's forcing, with its size basis · business days skip weekends, holidays not modeled</>
        ) : (
          <>▮ no dated forcing inside the window — flat water ahead</>
        )}
      </div>
      <Method>
        forcing from snap.calendar (settlements, tax, crunch windows, FOMC) + the turn model ·
        severities from engines.resonance per-mode recent medians and the swell heavy-day flag
      </Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   AuctionDigestCard — the auctions engine, which renders nowhere else.
   digestion_index: per-tenor z of bid-to-cover / primary-dealer / indirect
   shares, bills damped, EWMA(20) — positive = demand absorbing supply,
   negative = the street warehousing it. lib.tsx has no shared copy affordance,
   so the recent-auctions table is a plain house mini table.
   ------------------------------------------------------------------------- */
function AuctionDigestCard({ e }: { e: Any }) {
  if (!e?.ok) return <Fault name="Auction Digestion" reason={e?.reason} span={12} />;
  const idx: (string | number)[][] = e.index_series ?? [];
  const last = idx[idx.length - 1], prev = idx[idx.length - 2];
  const di = e.digestion_index;
  const dLast = last != null && prev != null ? Number(last[1]) - Number(prev[1]) : null;
  return (
    <div className="card span12">
      <h2>Auction Digestion</h2>
      <div className="sub">
        can the street absorb the supply? bid-to-cover, dealer and indirect shares z-scored per tenor,
        bills weighted ×0.35, EWMA(20) · positive = demand ahead of supply, negative = indigestion
      </div>
      <div className="kv">
        <div className="item"><div className="k">digestion index</div>
          <div className={`v ${di <= -0.5 ? "bad" : di < 0 ? "warn" : ""}`}><Roll v={di} d={2} /></div></div>
        <div className="item"><div className="k">Δ last print</div>
          <div className="v">{dLast == null ? "—" : `${dLast > 0 ? "+" : ""}${fmt(dLast, 2)}`}</div></div>
        <div className="item"><div className="k">recent auctions</div>
          <div className="v">{(e.recent_auctions ?? []).length}</div></div>
        <div className="item"><div className="k">asof</div>
          <div className="v" style={{ fontSize: 13 }}>{e.asof}</div></div>
      </div>
      {idx.length > 1 && (
        <Chart
          rows={idx}
          series={[{ label: "digestion index", color: P.slate }]}
          height={130}
          refLine={{ value: 0, color: P.ghost, label: "" }}
        />
      )}
      <table className="mini">
        <thead>
          <tr><th>date</th><th>tenor</th><th>bid-to-cover</th><th>btc z</th><th>dealer share</th><th>pd z</th><th>score</th></tr>
        </thead>
        <tbody>
          {(e.recent_auctions ?? []).slice(0, 8).map((a: Any, i: number) => (
            <tr key={`${a.date}-${a.tenor}-${i}`}>
              <td className="num">{a.date}</td>
              <td>{a.tenor}</td>
              <td className="num">{fmt(a.btc, 2)}</td>
              <td className="num" style={{ color: (a.btc_z ?? 0) <= -1 ? P.stress : (a.btc_z ?? 0) >= 1 ? P.calm : undefined }}>
                {fmt(a.btc_z, 2)}
              </td>
              <td className="num">{a.pd_share != null ? `${fmt(a.pd_share * 100, 1)}%` : "—"}</td>
              <td className="num" style={{ color: (a.pd_share_z ?? 0) >= 1 ? P.stress : undefined }}>
                {fmt(a.pd_share_z, 2)}
              </td>
              <td className="num" style={{ color: (a.score ?? 0) <= -0.5 ? P.strain : (a.score ?? 0) >= 0.5 ? P.calm : undefined }}>
                {fmt(a.score, 2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Method>
        {e.method} · a high dealer share means the street is warehousing supply (indigestion risk);
        btc z colored red ≤ −1, green ≥ +1
      </Method>
    </div>
  );
}

export default function Calendar({ snap }: { snap: Any }) {
  const cal = snap.calendar ?? {};
  return (
    <div className="grid">
      <EventHorizonTimeline snap={snap} />
      <TurnCard t={snap.deep?.turn} />
      <EventList cal={cal} />
      <AuctionDigestCard e={snap.engines?.auctions} />
      <BillDesk rows={cal.bill_desk} />
    </div>
  );
}
