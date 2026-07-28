import { Fragment } from "react";
import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Method, Roll } from "../lib";

/* ---------------------------------------------------------------------------
   SUPPLY: who is funding the Treasury, and at what cost.

   Four cards, four engines, one question each. supplydesk answers "how much
   cash does the calendar pull out of reserves and when". reportcard answers
   "the auction printed fine, but what did the plumbing do about it". officialbid
   answers "is the foreign official bid rotating its parking or actually
   leaving". stigma answers "is the SRF ceiling holding, or is repo clearing
   above it while the window sits empty".

   Every card degrades to the engine's own reason rather than to silence, and
   nothing on this page is computed here: the numbers are the engines' numbers,
   the thresholds quoted in the copy are the engines' thresholds.
   ------------------------------------------------------------------------- */

function Dark({ title, span, reason }: { title: string; span: number; reason?: string }) {
  return (
    <div className={`card span${span}`}>
      <h2>{title}</h2>
      <div className="faults">unavailable: {reason ?? "engine dark"}</div>
    </div>
  );
}

const signed = (v: number | null | undefined, d = 1) =>
  v == null ? "n/a" : `${v > 0 ? "+" : ""}${fmt(v, d)}`;

/* ---------------------------------------------------------------------------
   1. The Supply Desk, the forward net-new-cash table.

   Net new cash is the number the desk trades off: issuance settling minus
   paper maturing, so a positive row is cash leaving the private sector into
   the TGA. Rows past announced_through are the engine's own carry-forward
   projection, and a row flagged issuance_incomplete refuses to print a net at
   all: its maturities are known while its refunding issuance is deliberately
   not projected, so the "drain" would be an artifact of what the projector
   declines to guess. That row shows n/a and says why.
   ------------------------------------------------------------------------- */
const WARN_NET_B = 50;
const BAD_NET_B = 100;

function rowStatus(r: Any): { label: string; why: string } {
  if (r.issuance_incomplete)
    return {
      label: "refunding not yet announced",
      why: "maturities known, refunding issuance not projected (quarterly originals have no short cadence to extrapolate), so this row's net is an artifact and is withheld",
    };
  if (r.projected)
    return { label: "projected", why: "house projection: each tenor's last size carried forward at its observed cadence" };
  if (r.amount_estimated)
    return { label: "est. size", why: "announced offering with the amount still TBA, filled with the tenor's last realized size" };
  return { label: "announced", why: "announced offering with a published amount" };
}

function SupplyDesk({ e }: { e: Any }) {
  if (!e?.ok) return <Dark title="The Supply Desk" span={12} reason={e?.reason} />;
  const rows: Any[] = e.rows ?? [];
  const t = e.totals ?? {};
  const heavy = e.heaviest_day ?? {};
  const through: string | null = e.announced_through ?? null;
  const nIncomplete = rows.filter((r) => r.issuance_incomplete).length;
  const lastAnnounced = through
    ? rows.reduce((acc, r, i) => (String(r.date) <= through ? i : acc), -1)
    : -1;

  return (
    <div className="card span12">
      <h2>The Supply Desk</h2>
      <div className="sub">
        the forward cash table: what settles, what matures, and the net new cash the calendar pulls
        out of reserves on each settlement date through {e.horizon_end}
      </div>
      <div className="kv">
        <div className="item">
          <div className="k">net new cash, window</div>
          <div className={`v ${(t.net_new_cash_b ?? 0) >= 250 ? "bad" : (t.net_new_cash_b ?? 0) >= 100 ? "warn" : ""}`}>
            $<Roll v={t.net_new_cash_b} d={1} />B
          </div>
        </div>
        <div className="item"><div className="k">gross settling</div><div className="v">${fmt(t.gross_b, 1)}B</div></div>
        <div className="item"><div className="k">maturing</div><div className="v">${fmt(t.maturing_b, 1)}B</div></div>
        <div className="item"><div className="k">bills net / coupons net</div>
          <div className="v" style={{ fontSize: 13 }}>${fmt(t.bills_net_b, 1)}B / ${fmt(t.coupons_net_b, 1)}B</div></div>
        <div className="item"><div className="k">heaviest day</div>
          <div className={`v ${(heavy.net_new_cash_b ?? 0) >= BAD_NET_B ? "bad" : (heavy.net_new_cash_b ?? 0) >= WARN_NET_B ? "warn" : ""}`}
               style={{ fontSize: 13 }}>
            {heavy.date ?? "n/a"} · ${fmt(heavy.net_new_cash_b, 1)}B
          </div></div>
        <div className="item"><div className="k">announced through</div>
          <div className="v" style={{ fontSize: 13 }}>{through ?? "n/a"}</div></div>
      </div>
      <table className="mini">
        <thead>
          <tr>
            <th>settlement</th>
            <th>bills gross $B</th>
            <th>coupons gross $B</th>
            <th>maturing $B</th>
            <th style={{ color: "var(--text)" }}>net new cash $B</th>
            <th>status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: Any, i: number) => {
            const st = rowStatus(r);
            const inc = !!r.issuance_incomplete;
            const net = r.net_new_cash_b as number | null | undefined;
            const isHeavy = heavy.date != null && r.date === heavy.date;
            const netColor = inc || net == null
              ? P.faint
              : net >= BAD_NET_B ? P.stress : net >= WARN_NET_B ? P.erosion : net < 0 ? P.calm : undefined;
            return (
              <Fragment key={String(r.date)}>
                <tr>
                  <td className="num">{r.date}{isHeavy ? " ◂ heaviest" : ""}</td>
                  <td className="num">{fmt(r.bills_gross_b, 1)}</td>
                  <td className="num">{fmt(r.coupons_gross_b, 1)}</td>
                  <td className="num">{fmt(r.maturing_b, 1)}</td>
                  <td className="num" style={{ color: netColor, fontWeight: 600, fontSize: 12 }}
                      title={inc ? st.why : "issuance settling minus paper maturing"}>
                    {inc ? "n/a" : signed(net, 1)}
                  </td>
                  <td className={r.projected || inc ? "dimsmall" : undefined} title={st.why}>{st.label}</td>
                </tr>
                {i === lastAnnounced && i < rows.length - 1 && (
                  <tr>
                    <td className="dimsmall" colSpan={6}>
                      ▸ announcement ends here ({through}) · everything below is house projection
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {nIncomplete > 0 && (
        <div className="crunch">
          <b>{nIncomplete} row{nIncomplete > 1 ? "s" : ""} withheld a net</b>: settlement dates past the
          announced horizon whose maturities are known while their refunding issuance carries no short
          cadence to project, so the drain would be an artifact of what the projector declines to guess.
          The totals above sum every row as the engine publishes them, so they still carry that gap.
        </div>
      )}
      {(e.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>
        {e.method} · net shaded amber at ${WARN_NET_B}B and red at ${BAD_NET_B}B settling in one day
        (amber at $100B and red at $250B for the window total), green when the day returns cash
      </Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   2. Auction Report Cards, the same-day event study behind each grade.

   auctions.py says whether demand was normal. This says what the funding
   market did over the five business days that followed, which nobody else
   publishes auction by auction. An unfinished window prints "open", never a
   zero: an observed zero and a missing mark are different facts.
   ------------------------------------------------------------------------- */
const gradeColor = (g: string | null | undefined) =>
  g === "A" || g === "B" ? P.calm : g === "D" || g === "F" ? P.stress : undefined;

function Chg({ h }: { h: Any }) {
  if (!h || h.status !== "printed" || h.change_bp == null)
    return <span className="dimsmall">{h?.status === "open" ? "open" : "n/a"}</span>;
  const c = Number(h.change_bp);
  return (
    <span style={{ color: c >= 3 ? P.stress : c <= -3 ? P.calm : undefined }} title={`${h.h} on ${h.date}, level ${fmt(h.spread_bp, 2)}bp`}>
      {signed(c, 1)}
    </span>
  );
}

function ReportCards({ e }: { e: Any }) {
  if (!e?.ok) return <Dark title="Auction Report Cards" span={12} reason={e?.reason} />;
  const cards: Any[] = e.cards ?? [];
  const hz: number[] = e.window_bd ?? [0, 1, 3, 5];
  const fatigue: Any[] = e.demand_fatigue ?? [];

  return (
    <div className="card span12">
      <h2>Auction Report Cards</h2>
      <div className="sub">
        the auction printed, then what? each recent auction gets a letter off the digestion engine's
        composite z, plus the event study nobody else runs: SOFR minus IORB at T+0, T+1, T+3 and T+5
        against the last print before the auction, SRF take-up inside the same window, and the reserve
        change across the settlement week
      </div>
      {e.letter_line && <div className="crunch">{e.letter_line}</div>}
      <div className="kv">
        <div className="item"><div className="k">cards on the record</div><div className="v">{e.n_cards ?? cards.length}</div></div>
        <div className="item"><div className="k">latest auction</div><div className="v" style={{ fontSize: 13 }}>{e.asof ?? "n/a"}</div></div>
        <div className="item"><div className="k">funding tape through</div><div className="v" style={{ fontSize: 13 }}>{e.funding_asof ?? "n/a"}</div></div>
        <div className="item"><div className="k">window</div>
          <div className="v" style={{ fontSize: 13 }}>{hz.map((h) => `T+${h}`).join(" · ")}</div></div>
      </div>
      <table className="mini">
        <thead>
          <tr>
            <th colSpan={4}>auction</th>
            <th colSpan={4}>demand (z, lower is stronger)</th>
            <th colSpan={hz.length}>SOFR minus IORB, bp vs the last print before</th>
            <th colSpan={2}>what the plumbing did</th>
          </tr>
          <tr>
            <th>date</th><th>tenor</th><th>grade</th><th>size $B</th>
            <th>bid/cover</th><th>btc z</th><th>dealer z</th><th>indirect z</th>
            {hz.map((h) => <th key={h}>T+{h}</th>)}
            <th>SRF peak $B</th><th>reserves Δ $B</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((c: Any) => {
            const g = c.grades ?? {};
            const es = c.event_study ?? {};
            const srf = es.srf ?? {};
            const res = es.reserves ?? {};
            const marks: Any[] = es.horizons ?? [];
            const byH: Record<string, Any> = {};
            for (const m of marks) byH[String(m.h)] = m;
            return (
              <tr key={c.slug ?? `${c.auction_date}-${c.tenor}`} title={c.verdict || undefined}>
                <td className="num">{c.auction_date}</td>
                <td>{c.display ?? c.tenor}</td>
                <td style={{ color: gradeColor(g.grade), fontWeight: 600 }}
                    title={g.composite_z != null ? `composite z ${signed(g.composite_z, 2)}` : undefined}>
                  {g.grade ?? "n/a"}
                </td>
                <td className="num">{c.size_b == null ? "n/a" : fmt(c.size_b, 1)}</td>
                <td className="num">{g.bid_to_cover == null ? "n/a" : fmt(g.bid_to_cover, 2)}</td>
                <td className="num" style={{ color: (g.btc_z ?? 0) <= -1 ? P.stress : (g.btc_z ?? 0) >= 1 ? P.calm : undefined }}>
                  {g.btc_z == null ? "n/a" : fmt(g.btc_z, 2)}
                </td>
                <td className="num" style={{ color: (g.pd_share_z ?? 0) >= 1 ? P.stress : undefined }}>
                  {g.pd_share_z == null ? "n/a" : fmt(g.pd_share_z, 2)}
                </td>
                <td className="num" style={{ color: (g.indirect_z ?? 0) <= -1 ? P.stress : undefined }}>
                  {g.indirect_z == null ? "n/a" : fmt(g.indirect_z, 2)}
                </td>
                {hz.map((h) => (
                  <td className="num" key={h}><Chg h={byH[`T+${h}`]} /></td>
                ))}
                <td className="num">
                  {srf.status === "complete"
                    ? <span style={{ color: (srf.max_b ?? 0) >= 1 ? P.stress : undefined }}>{fmt(srf.max_b, 2)}</span>
                    : <span className="dimsmall">{srf.status === "open" ? "open" : "n/a"}</span>}
                </td>
                <td className="num">
                  {res.status === "complete"
                    ? <span style={{ color: (res.change_b ?? 0) <= -50 ? P.strain : undefined }}>{signed(res.change_b, 0)}</span>
                    : <span className="dimsmall">{res.status === "open" ? "open" : "n/a"}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {fatigue.length > 0 && (
        <>
          <div className="sub" style={{ marginTop: 10 }}>
            demand fatigue: one soft auction is a story, the same tenor softening across a run is the
            dataset · trailing indirect share by tenor
          </div>
          <table className="mini">
            <thead>
              <tr><th>tenor</th><th>auctions</th><th>latest indirect %</th><th>prior mean %</th><th>trend pp/auction</th><th>verdict</th></tr>
            </thead>
            <tbody>
              {fatigue.map((f: Any) => (
                <tr key={f.tenor}>
                  <td>{f.display ?? f.tenor}</td>
                  <td className="num">{f.auctions}</td>
                  <td className="num">{fmt(f.latest_indirect_pct, 1)}</td>
                  <td className="num">{fmt(f.prior_mean_indirect_pct, 1)}</td>
                  <td className="num">{signed(f.trend_pp_per_auction, 2)}</td>
                  <td style={{ color: f.verdict === "softening" ? P.stress : f.verdict === "firming" ? P.calm : undefined }}>
                    {f.verdict}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {(e.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{e.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   3. The Foreign Official Bid, rotation or retreat.

   A falling custody book alone is ambiguous: officials may be selling to
   defend a currency, or shifting the same dollars into cash at the Fed.
   Reading custody against foreign RRP disambiguates; FIMA repo drawn is the
   stress tell that outranks the rotation reading, because borrowing dollars
   against collateral rather than selling it is what a squeezed official sector
   does before it capitulates.
   ------------------------------------------------------------------------- */
const BID_RULES: { key: string; when: string }[] = [
  { key: "rotation", when: "custody down, foreign RRP up or flat" },
  { key: "retreat", when: "custody down, foreign RRP down" },
  { key: "build", when: "custody up (classic build has both pools rising)" },
  { key: "steady", when: "both 13w changes inside the $5B flat band" },
];

function OfficialBid({ e }: { e: Any }) {
  if (!e?.ok) return <Dark title="The Foreign Official Bid" span={6} reason={e?.reason} />;
  const cls = String(e.classification ?? "");
  const pools = [
    { name: "custody Treasuries", level: e.custody_b, c4: e.custody_chg_4w_b, c13: e.custody_chg_13w_b },
    { name: "foreign RRP", level: e.foreign_rrp_b, c4: e.foreign_rrp_chg_4w_b, c13: e.foreign_rrp_chg_13w_b },
    { name: "FIMA repo", level: e.fima_repo_b, c4: e.fima_repo_chg_4w_b, c13: e.fima_repo_chg_13w_b },
    { name: "footprint (custody + RRP)", level: e.footprint_b, c4: e.footprint_chg_4w_b, c13: e.footprint_chg_13w_b },
  ];
  return (
    <div className="card span6">
      <h2>The Foreign Official Bid</h2>
      <div className="sub">
        is the official sector rotating its parking or actually leaving? custody read against the
        foreign RRP, with FIMA repo as the stress tell on top
      </div>
      {e.letter_line && <div className="crunch">{e.letter_line}</div>}
      {e.fima_drawn && (
        <div className="faults">
          FIMA repo DRAWN: ${fmt(e.fima_repo_b, 1)}B · officials are borrowing dollars against their
          custody collateral instead of selling it, which outranks whatever the rotation line says
        </div>
      )}
      <div className="kv">
        <div className="item"><div className="k">classification</div>
          <div className={`v ${cls === "retreat" ? "bad" : cls === "rotation" ? "warn" : ""}`}>{cls || "n/a"}</div></div>
        <div className="item"><div className="k">footprint</div><div className="v">$<Roll v={e.footprint_b} d={0} />B</div></div>
        <div className="item"><div className="k">footprint 13w</div>
          <div className={`v ${(e.footprint_chg_13w_b ?? 0) <= -50 ? "warn" : ""}`} style={{ fontSize: 14 }}>
            {signed(e.footprint_chg_13w_b, 1)}B
          </div></div>
        <div className="item"><div className="k">FIMA repo</div>
          <div className={`v ${e.fima_drawn ? "bad" : ""}`} style={{ fontSize: 14 }}>
            ${fmt(e.fima_repo_b, 1)}B {e.fima_drawn ? "drawn" : "undrawn"}
          </div></div>
      </div>
      <table className="mini">
        <thead><tr><th>pool</th><th>level $B</th><th>4w Δ $B</th><th>13w Δ $B</th></tr></thead>
        <tbody>
          {pools.map((p) => (
            <tr key={p.name}>
              <td>{p.name}</td>
              <td className="num">{fmt(p.level, 1)}</td>
              <td className="num" style={{ color: (p.c4 ?? 0) < 0 ? P.strain : (p.c4 ?? 0) > 0 ? P.calm : undefined }}>{signed(p.c4, 1)}</td>
              <td className="num" style={{ color: (p.c13 ?? 0) < 0 ? P.strain : (p.c13 ?? 0) > 0 ? P.calm : undefined }}>{signed(p.c13, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="mini">
        <thead><tr><th>the stated rule, 13w changes on the H.4.1 grid</th><th>reads as</th></tr></thead>
        <tbody>
          {BID_RULES.map((r) => (
            <tr key={r.key} style={r.key === cls ? { fontWeight: 600 } : undefined}>
              <td className={r.key === cls ? undefined : "dimsmall"}>{r.when}</td>
              <td style={{ color: r.key === cls ? P.accentSoft : undefined }}>
                {r.key}{r.key === cls ? " ◂ current" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(e.series ?? []).length > 1 && (
        <Chart
          rows={e.series}
          series={[
            { label: "custody $B", color: P.slate },
            { label: "foreign RRP $B", color: P.gold },
            { label: "footprint $B", color: P.ink },
          ]}
          height={140}
        />
      )}
      {(e.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{e.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   4. Facility Tripwires, the SRF ceiling leak.

   The ceiling is supposed to hold by arbitrage. Repo printing above it while
   take-up sits near zero is the September 2019 signature: desks paying up
   rather than be seen at the window. The card names the ceiling it used,
   because the IORB fallback sits at or below the SRF rate and therefore
   overstates the leak.
   ------------------------------------------------------------------------- */
function Tripwires({ e }: { e: Any }) {
  if (!e?.ok) return <Dark title="Facility Tripwires" span={6} reason={e?.reason} />;
  const c = e.ceiling ?? {};
  const bp = e.bp_days_above_ceiling ?? {};
  const tk = e.takeup ?? {};
  const proxy = c.source === "iorb_proxy";
  const score = e.stigma_score as number | null | undefined;
  const p75: Record<string, number> = {};
  for (const r of (e.series_p75_sum20 ?? []) as Any[]) p75[String(r[0])] = Number(r[1]);
  const rows = ((e.series_sum20 ?? []) as Any[]).map(
    (r) => [String(r[0]), Number(r[1]), p75[String(r[0])] ?? null],
  );

  return (
    <div className="card span6">
      <h2>Facility Tripwires</h2>
      <div className="sub">
        the SRF is meant to cap secured funding by arbitrage · repo clearing above the ceiling while
        the window sits empty is the stigma signature, and a used facility is pressure, not stigma
      </div>
      <div className={proxy ? "crunch" : "sub"}>
        {proxy ? (
          <>
            <b>ceiling = IORB proxy</b> at {fmt(c.latest_pct, 3)}%, no SRF offering rate series was
            available. IORB sits at or below the SRF rate, so every leak number on this card
            overstates the true above-ceiling mass.
          </>
        ) : (
          <>ceiling = the SRF offering rate at {fmt(c.latest_pct, 3)}%, the real cap, no proxy</>
        )}
      </div>
      <div className="kv">
        <div className="item"><div className="k">stigma score</div>
          <div className={`v ${(score ?? 0) >= 60 ? "bad" : (score ?? 0) >= 25 ? "warn" : ""}`}>
            <Roll v={score} d={1} /> <span className="dimsmall">of 100</span>
          </div></div>
        <div className="item"><div className="k">P99 leak, 20d</div>
          <div className={`v ${(bp.p99_sum20_bp_days ?? 0) >= 40 ? "bad" : (bp.p99_sum20_bp_days ?? 0) > 0 ? "warn" : ""}`}>
            {fmt(bp.p99_sum20_bp_days, 1)} <span className="dimsmall">bp days</span>
          </div></div>
        <div className="item"><div className="k">P75 leak, 20d</div>
          <div className={`v ${(bp.p75_sum20_bp_days ?? 0) > 0 ? "bad" : ""}`}>
            {fmt(bp.p75_sum20_bp_days, 1)} <span className="dimsmall">bp days</span>
          </div></div>
        <div className="item"><div className="k">sessions above, of 20</div>
          <div className={`v ${(bp.days_above_20d ?? 0) >= 10 ? "bad" : (bp.days_above_20d ?? 0) > 0 ? "warn" : ""}`}>
            {bp.days_above_20d ?? "n/a"}
          </div></div>
      </div>
      <table className="mini">
        <thead><tr><th>SRF take-up</th><th>$B</th><th>reading</th></tr></thead>
        <tbody>
          <tr>
            <td>latest ({tk.asof ?? "n/a"})</td>
            <td className="num">{fmt(tk.latest_b, 2)}</td>
            <td className={tk.classification === "material" ? undefined : "dimsmall"}
                style={{ color: tk.classification === "material" ? P.stress : undefined }}>
              {String(tk.classification ?? "n/a").replace("_", " ")}
            </td>
          </tr>
          <tr>
            <td>20d max</td>
            <td className="num">{fmt(tk.max20_b, 2)}</td>
            <td className="dimsmall">the absence term: a facility in real use zeroes the score</td>
          </tr>
          <tr>
            <td>share of facility</td>
            <td className="num">{fmt(tk.share_of_facility_pct, 3)}%</td>
            <td className="dimsmall">of the ${fmt(tk.facility_scale_b, 0)}B aggregate limit</td>
          </tr>
          <tr>
            <td>latest breach, P99 / P75</td>
            <td className="num">{fmt(bp.p99_last_bp, 1)} / {fmt(bp.p75_last_bp, 1)}</td>
            <td className="dimsmall">bp above the ceiling on the last print</td>
          </tr>
        </tbody>
      </table>
      <div className="crunch"><b>verdict</b> · {e.verdict}</div>
      <div className="dimsmall">
        scale: 0 = ceiling holding · above 25 = leak forming · above 60 = stigma signature live ·
        heavy take-up zeroes the score by construction
      </div>
      {rows.length > 1 && (
        <Chart
          rows={rows}
          series={[
            { label: "P99 leak, 20d bp days (at least 1% of volume paid up)", color: P.slate },
            { label: "P75 leak, 20d bp days (at least 25% did)", color: P.stress },
          ]}
          height={140}
        />
      )}
      {(e.caveats ?? []).map((cv: string, i: number) => <div className="caveat" key={i}>▸ {cv}</div>)}
      <Method>{e.method}</Method>
    </div>
  );
}

export default function Supply({ snap }: { snap: Any }) {
  const eng = snap.engines ?? {};
  return (
    <div className="grid">
      <SupplyDesk e={eng.supplydesk} />
      <ReportCards e={eng.reportcard} />
      <OfficialBid e={eng.officialbid} />
      <Tripwires e={eng.stigma} />
    </div>
  );
}
