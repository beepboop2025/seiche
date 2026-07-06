import { useEffect, useState } from "react";
import { Any, fmt } from "../lib";

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

export default function System({ snap, live }: { snap: Any; live: boolean }) {
  const [alerts, setAlerts] = useState<Any[]>([]);
  const [config, setConfig] = useState<Any | null>(null);

  useEffect(() => {
    if (!live) return;
    fetch("/api/alerts").then((r) => r.json()).then((j) => setAlerts(j.alerts ?? [])).catch(() => {});
    fetch("/api/config").then((r) => r.json()).then(setConfig).catch(() => {});
  }, [live]);

  return (
    <div className="grid">
      {snap.faults?.length > 0 && (
        <div className="card span12">
          <h2>Faults</h2>
          {snap.faults.map((f: Any, i: number) => (
            <div className="crunch" key={i}><b>{f.source}</b> — {String(f.detail).slice(0, 160)}</div>
          ))}
        </div>
      )}

      <div className="card span6">
        <h2>Alert log</h2>
        <div className="sub">written by `seiche alert` / `seiche watch` — deduped per state</div>
        {!live ? (
          <div className="sub">requires live backend</div>
        ) : alerts.length === 0 ? (
          <div className="allclear">▮ no alerts recorded yet — run `seiche alert`</div>
        ) : (
          <table className="mini">
            <thead><tr><th>fired</th><th>rule</th><th>message</th></tr></thead>
            <tbody>
              {alerts.map((a: Any, i: number) => (
                <tr key={i}>
                  <td className="num">{(a.fired_at ?? "").slice(0, 16).replace("T", " ")}</td>
                  <td>{a.rule}</td><td>{a.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card span6">
        <h2>Editorial voice</h2>
        <div className="sub">every judgment call lives in backend/seiche/config.py — tune it, own it</div>
        {config ? (
          <>
            <table className="mini">
              <thead><tr><th>composite weight</th><th>value</th></tr></thead>
              <tbody>
                {Object.entries<number>(config.composite_weights ?? {}).map(([k, w]) => (
                  <tr key={k}><td>{k}</td><td className="num">{fmt(w, 2)}</td></tr>
                ))}
              </tbody>
            </table>
            <div className="sub" style={{ marginTop: 8 }}>
              regimes: {(config.regimes ?? []).map((r: Any) => `${r.name}<${r.below}`).join(" · ")}
            </div>
          </>
        ) : (
          <div className="sub">{live ? "loading…" : "requires live backend"}</div>
        )}
      </div>

      <Provenance prov={snap.provenance ?? []} />
    </div>
  );
}
