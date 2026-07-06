import { Any, fmt, Method } from "../lib";

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

export default function Calendar({ snap }: { snap: Any }) {
  const cal = snap.calendar ?? {};
  return (
    <div className="grid">
      <TurnCard t={snap.deep?.turn} />
      <EventList cal={cal} />
      <BillDesk rows={cal.bill_desk} />
    </div>
  );
}
