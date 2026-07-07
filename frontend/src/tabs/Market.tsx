import Chart from "../Chart";
import { Any, fmt, Fault, Method } from "../lib";

function TellCard({ t }: { t: Any }) {
  if (!t?.ok) return <Fault name="The Tell" reason={t?.reason} span={12} />;
  const hot = t.tell >= 15, cold = t.tell <= -15;
  return (
    <div className="card span12">
      <h2>The Tell</h2>
      <div className="sub">plumbing percentile − market-priced-stress percentile · the gap is the signal</div>
      <div className="tellhero">
        <div className={`tellvalue ${hot ? "hot" : cold ? "cold" : ""}`}>
          {t.tell > 0 ? "+" : ""}{fmt(t.tell, 0)}
        </div>
        <div>
          <div className="tellreading">{t.reading}</div>
          <div className="coverage">plumbing {fmt(t.plumbing_pctl, 0)}th pctl · market {fmt(t.market_pctl, 0)}th pctl · asof {t.asof}</div>
        </div>
        <div className="kv" style={{ marginLeft: "auto" }}>
          {Object.entries<Any>(t.components ?? {}).map(([k, c]) => (
            <div className="item" key={k}>
              <div className="k">{c.label}</div>
              <div className="v">{fmt(c.last, 2)} <span className="dimsmall">({fmt(c.pctl, 0)}th)</span></div>
            </div>
          ))}
        </div>
      </div>
      <Chart rows={t.series} series={[{ label: "tell", color: "#8a63d2" }]} refLine={{ value: 0, color: "#3d4654", label: "" }} />
      <Method>{t.method}</Method>
    </div>
  );
}

function PlaybookCard({ p }: { p: Any }) {
  if (!p?.ok) return <Fault name="Playbook" reason={p?.reason} span={12} />;
  const horizons = ["5d", "20d"];
  return (
    <div className="card span12">
      <h2>Playbook</h2>
      <div className="sub">
        what happened the last {p.state?.n_matching_days} times the board read{" "}
        <b>{p.state?.regime} × {p.state?.tell_bucket}</b> — native units, n shown, not advice
      </div>
      <table className="mini">
        <thead>
          <tr>
            <th>outcome</th>
            {horizons.map((h) => (
              <th key={h} colSpan={3}>next {h}</th>
            ))}
          </tr>
          <tr>
            <th></th>
            {horizons.map((h) => (
              <>
                <th key={h + "m"}>median</th>
                <th key={h + "i"}>p25 / p75</th>
                <th key={h + "n"}>%+ · n</th>
              </>
            ))}
          </tr>
        </thead>
        <tbody>
          {(p.tables ?? []).map((row: Any) => (
            <tr key={row.mnemonic}>
              <td>{row.outcome}</td>
              {horizons.map((h) => {
                const c = row.horizons?.[h];
                if (!c || c.insufficient)
                  return <td key={h} colSpan={3} className="dimsmall">n/a (n={c?.n_days ?? 0})</td>;
                const dim = c.low_confidence ? { opacity: 0.45 } : undefined;
                return (
                  <>
                    <td key={h + "m"} className="num" style={{ ...dim, color: c.median > 0 ? "#37c88b" : c.median < 0 ? "#e5484d" : undefined }}
                        title={c.low_confidence ? "fewer than 8 non-overlapping windows — an anecdote, not a distribution" : undefined}>
                      {c.median > 0 ? "+" : ""}{fmt(c.median, 2)}
                    </td>
                    <td key={h + "i"} className="num dimsmall" style={dim}>{fmt(c.p25, 1)} / {fmt(c.p75, 1)}</td>
                    <td key={h + "n"} className="num dimsmall" style={dim}>{fmt(c.pct_positive, 0)}% · {c.n_days}({c.n_independent})</td>
                  </>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <Method>{p.caveat} · {p.method}</Method>
    </div>
  );
}

export default function Market({ snap }: { snap: Any }) {
  const deep = snap.deep ?? {};
  return (
    <div className="grid">
      <TellCard t={deep.tell} />
      <PlaybookCard p={deep.playbook} />
    </div>
  );
}
