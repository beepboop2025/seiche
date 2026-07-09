import { useState } from "react";
import { API_BASE } from "../apiBase";
import { Any, fmt, Decomp, Stat } from "../lib";

const PRESETS = [
  { date: "2019-09-12", label: "Sep 2019 repo spike, T−5" },
  { date: "2020-03-06", label: "Mar 2020 dash-for-cash, T−10" },
  { date: "2023-03-06", label: "SVB, T−7" },
  { date: "2025-04-02", label: "Apr 2025 basis unwind, T−7" },
  { date: "2025-09-08", label: "Sep 2025 tax squeeze, T−7" },
  { date: "2025-12-15", label: "Dec 2025 year-end, T−16" },
];

export default function TimeMachine({ live }: { live: boolean }) {
  const [date, setDate] = useState("");
  const [replay, setReplay] = useState<Any | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = (d: string) => {
    if (!d) return;
    setBusy(true);
    setErr(null);
    fetch(`${API_BASE}/api/asof/${d}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `HTTP ${r.status}`);
        return r.json();
      })
      .then(setReplay)
      .catch((e) => { setErr(String(e.message ?? e)); setReplay(null); })
      .finally(() => setBusy(false));
  };

  if (!live) {
    return (
      <div className="card span12">
        <h2>Time Machine</h2>
        <div className="faults">replay needs the live backend (static snapshot deploys can't recompute history) — run `seiche serve` locally</div>
      </div>
    );
  }

  const c = replay?.engines?.composite ?? {};
  return (
    <>
      <div className="card span12" style={{ marginTop: 18 }}>
        <h2>Time Machine</h2>
        <div className="sub">
          every engine is a pure function of its inputs — truncate the inputs, replay the board.
          Pick any date (coverage ≈ 2018-06 onward; full fidelity 2019+):
        </div>
        <div className="tmcontrols">
          <input
            type="date" value={date} min="2018-06-01"
            onChange={(e) => setDate(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load(date)}
          />
          <button onClick={() => load(date)} disabled={busy || !date}>{busy ? "replaying…" : "replay"}</button>
          {PRESETS.map((p) => (
            <button key={p.date} className="preset" onClick={() => { setDate(p.date); load(p.date); }}>
              {p.label}
            </button>
          ))}
        </div>
        {err && <div className="faults">{err}</div>}
      </div>

      {replay && (
        <>
          <div className="hero">
            <div className="dial">
              <div className="value">{fmt(c.value, 0)}</div>
              <div>
                <div className={`regime ${c.regime}`}>{c.regime ?? "?"}</div>
                <div className="coverage" style={{ marginTop: 6 }}>
                  board as of <b>{replay.asof}</b> · coverage {fmt(c.coverage_pct, 0)}%
                </div>
              </div>
            </div>
            <Decomp composite={c} />
          </div>
          <div className="strip">
            <Stat k="SOFR" blk={replay.headline.sofr_pct} unit="%" />
            <Stat k="EFFR" blk={replay.headline.effr_pct} unit="%" />
            <Stat k="Reserves" blk={replay.headline.reserves_b} unit="B" d={0} />
            <Stat k="ON RRP" blk={replay.headline.rrp_b} unit="B" d={0} />
            <Stat k="TGA" blk={replay.headline.tga_b} unit="B" d={0} />
            <Stat k="SRF" blk={replay.headline.srf_accepted_b} unit="B" />
          </div>
          <div className="grid">
            <div className="card span6">
              <h2>Crunch calls made that day</h2>
              {(replay.engines.weather?.crunch_windows ?? []).length === 0 ? (
                <div className="allclear">▮ no crunch windows were flagged</div>
              ) : (
                replay.engines.weather.crunch_windows.slice(0, 6).map((cw: Any) => (
                  <div className="crunch" key={cw.date}><b>{cw.date}</b> — {cw.reason}</div>
                ))
              )}
              <div className="method">{replay.vintage_note}</div>
            </div>
            <div className="card span6">
              <h2>Echo, as heard then</h2>
              {replay.engines.echo?.ok ? (
                <table className="mini">
                  <thead><tr><th>episode</th><th>window</th><th>similarity</th></tr></thead>
                  <tbody>
                    {replay.engines.echo.matches.slice(0, 5).map((m: Any) => (
                      <tr key={m.date}><td>{m.episode}</td><td className="num">T−{m.lead_days}d</td><td className="num">{fmt(m.similarity, 3)}</td></tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="sub">echo unavailable at this date</div>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
