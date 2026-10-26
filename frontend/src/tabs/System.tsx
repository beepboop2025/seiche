import { Any, fmt } from "../lib";

function clock(value: unknown): string {
  return typeof value === "string" && value ? value.slice(0, 16).replace("T", " ") + " UTC" : "—";
}

function Provenance({ prov }: { prov: Any[] }) {
  return (
    <div className="card span12">
      <h2>Provenance</h2>
      <div className="sub">Observation dates and retrieval times are different clocks. Daily, weekly and monthly sources retain their publication schedules.</div>
      <table className="mini">
        <thead><tr><th>series</th><th>source</th><th>label</th><th>as-of</th><th>fetched</th><th>availability</th><th>freshness</th></tr></thead>
        <tbody>{prov.map((p: Any) => {
          const table = !p.asof && p.availability === "available" && p.fetched_at;
          const freshness = p.staleness ?? "unknown";
          return <tr key={p.mnemonic}>
            <td>{p.mnemonic}</td><td>{p.source}</td><td>{p.label}</td>
            <td className="num">{p.asof ?? (table ? "Multiple source dates" : "—")}</td>
            <td className="num">{clock(p.fetched_at)}</td>
            <td>{p.availability ?? "unknown"}</td>
            <td title={p.freshness_basis ?? p.unavailable_reason ?? ""}>
              <span className={`chip ${freshness}`}>{table ? "Fetch clock only" : freshness}</span>
              {p.unavailable_reason && <div className="sub">{String(p.unavailable_reason).replaceAll("_", " ")}</div>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

export default function System({ snap, live }: { snap: Any; live: boolean }) {
  const system = snap.system ?? {};
  const log = system.alert_log;
  const config = system.editorial_config;
  const alerts: Any[] = log?.alerts ?? [];
  const editorial = snap.editorial;

  return (
    <div className="grid">
      {snap.faults?.length > 0 && (
        <div className="card span12">
          <h2>Faults</h2>
          {snap.faults.map((f: Any, i: number) => (
            <div className="crunch" key={i}>
              <b>{f.source}{f.series ? ` / ${f.series}` : ""}</b> — {String(f.detail ?? f.reason ?? "Collection failed").slice(0, 160)}
              {f.cache_used === true && <span> · Cached observations retained with their original dates.</span>}
              {f.cache_used === false && <span> · No usable cached observations.</span>}
            </div>
          ))}
        </div>
      )}

      <div className="card span6">
        <h2>Alert log</h2>
        <div className="sub">Recorded automatically from completed board snapshots. Each rule and state is recorded once; this log does not send notifications.</div>
        {log?.status !== "ready" ? (
          <div className="sub">{log?.reason ?? "Alert history is unavailable in this snapshot."}</div>
        ) : <>
          <div className="sub">Rules checked: {clock(log.evaluated_at)}{!live ? " · Saved snapshot" : ""}</div>
          {alerts.length === 0 ? <div className="allclear">No alert rules triggered in the recorded snapshots.</div> :
            <table className="mini">
              <thead><tr><th>first recorded</th><th>rule</th><th>message</th></tr></thead>
              <tbody>{alerts.map((a: Any) => (
                <tr key={`${a.rule}:${a.state}`}>
                  <td className="num">{clock(a.fired_at)}</td><td>{a.rule}</td><td>{a.message}</td>
                </tr>
              ))}</tbody>
            </table>}
        </>}
      </div>

      <div className="card span6">
        <h2>Editorial voice</h2>
        <div className="sub">The published weights and thresholds behind this board. These are editorial choices, not measured probabilities.</div>
        {config ? <>
          <table className="mini">
            <thead><tr><th>composite weight</th><th>value</th></tr></thead>
            <tbody>{Object.entries<number>(config.composite_weights ?? {}).map(([k, w]) => (
              <tr key={k}><td>{k}</td><td className="num">{fmt(w, 2)}</td></tr>
            ))}</tbody>
          </table>
          <div className="sub" style={{ marginTop: 8 }}>
            regimes: {(config.regimes ?? []).map((r: Any) => `${r.name}<${r.below}`).join(" · ")}
          </div>
        </> : <>
          <div className="sub">Weights and thresholds are unavailable in this snapshot.</div>
          {editorial?.thesis && <p>{editorial.thesis}</p>}
        </>}
      </div>
      <Provenance prov={snap.provenance ?? []} />
    </div>
  );
}
