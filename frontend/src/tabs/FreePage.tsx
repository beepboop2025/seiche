import { useEffect, useState } from "react";
import { API_BASE } from "../apiBase";
import { renderMarkdown } from "../md";
import { login } from "../auth";

type Any = Record<string, any>;

export default function FreePage({ onSignedIn }: { onSignedIn: () => void }) {
  const [pub, setPub] = useState<Any | null>(null);
  const [dispatch, setDispatch] = useState<{ meta: Any; free: string } | null>(null);
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    // conclusion + proof, live-first then static fallback
    fetch(`${API_BASE}/api/public`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .catch(() => fetch("data/public.json").then((r) => r.json()))
      .then(setPub)
      .catch(() => {});
    // latest dispatch free conclusion
    fetch("dispatches/index.json")
      .then((r) => r.json())
      .then((idx: Any[]) => {
        if (!idx.length) return;
        const meta = idx[0];
        return fetch(`dispatches/${meta.slug}.md`)
          .then((r) => r.text())
          .then((body) => setDispatch({ meta, free: body.replace("<!--HAS-PAID-->", "").trim() }));
      })
      .catch(() => {});
  }, []);

  const doLogin = () => {
    setErr(null);
    login(user.trim(), pw).then((res) => {
      if (res.ok) onSignedIn();
      else setErr(res.error);
    });
  };

  const c = pub?.conclusion ?? {};
  const p = pub?.proof ?? {};

  return (
    <div className="freewrap">
      <div className="masthead">
        <div className="wordmark">SEI<span>CHE</span></div>
        <div className="tagline">the funding-stress terminal · free public data only</div>
      </div>

      {/* today's conclusion — the free daily reading */}
      <div className="free-hero">
        {c.regime && (
          <div className="free-dial">
            <div className="value">{c.value != null ? Math.round(c.value) : "—"}</div>
            <div>
              <div className={`regime ${c.regime}`}>{c.regime}</div>
              <div className="coverage" style={{ marginTop: 6 }}>
                {pub?.generated_at ? `as of ${pub.generated_at.slice(0, 16).replace("T", " ")}Z` : ""}
                {c.coverage_pct != null ? ` · coverage ${Math.round(c.coverage_pct)}%` : ""}
              </div>
            </div>
          </div>
        )}
        <div className="free-conclusion">
          <div className="free-kicker">TODAY'S CONCLUSION</div>
          {dispatch ? (
            <>
              <h1>{dispatch.meta.title}</h1>
              <div className="dispatch-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(dispatch.free) }} />
            </>
          ) : (
            <p className="free-line">{c.line ?? "loading the reading…"}</p>
          )}
        </div>
      </div>

      {/* PROOF stays free — the honest scoreboard with its misses */}
      {p.recall != null && (
        <div className="free-proof">
          <div className="free-kicker">THE RECORD · nothing hidden</div>
          <div className="free-proof-stats">
            <div><b>{Math.round(p.recall * 100)}%</b><span>events caught (recall)</span></div>
            {p.median_lead_d != null && <div><b>{p.median_lead_d}d</b><span>median lead</span></div>}
            {p.precision_runs != null && <div><b>{Math.round(p.precision_runs * 100)}%</b><span>run precision</span></div>}
            {p.base_rate != null && <div><b>{(p.base_rate * 100).toFixed(1)}%</b><span>base rate</span></div>}
          </div>
          <table className="mini" style={{ maxWidth: 620 }}>
            <thead><tr><th>episode</th><th>date</th><th>window</th></tr></thead>
            <tbody>
              {(p.episodes ?? []).map((e: Any) => (
                <tr key={e.date}>
                  <td>{e.episode}</td>
                  <td className="num">{e.date}</td>
                  <td>{e.in_sample ? "in-sample" : "out-of-sample"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="caveat">The board publishes what it missed next to what it caught. Recall, precision and lead times carry Wilson confidence bands. Method is printed under every engine — for subscribers.</div>
        </div>
      )}

      {/* the wall */}
      <div className="paywall" style={{ marginTop: 26 }}>
        <div className="paywall-lock">◆ THE TERMINAL · SUBSCRIBERS</div>
        <p>The live board, the physics layer, positioning, the Time Machine replay and the desk's forward read are for subscribers. The conclusion and the record above stay free.</p>
        <div className="free-login">
          <input type="text" placeholder="username" value={user} autoComplete="username"
                 onChange={(e) => setUser(e.target.value)} />
          <input type="password" placeholder="password" value={pw} autoComplete="current-password"
                 onChange={(e) => setPw(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && doLogin()} />
          <button onClick={doLogin} disabled={!user || !pw}>sign in</button>
        </div>
        {err && <div className="dimsmall" style={{ color: "var(--stress)", marginTop: 8 }}>{err}</div>}
        <a className="paywall-alt" href="mailto:desk@seiche.info?subject=Seiche%20subscription" style={{ display: "inline-block", marginTop: 10 }}>
          no account? request access · desk@seiche.info
        </a>
        <a className="paywall-alt" href="/support.html" style={{ display: "inline-block", marginTop: 6 }}>
          subscribe with crypto · BTC / ETH / SOL / TRON →
        </a>
      </div>

      <div className="footer">
        SEICHE — a standing wave in an enclosed basin, invisible until it sloshes over the edge. · Not investment advice. ·{" "}
        <a href="mailto:desk@seiche.info" style={{ color: "var(--dim)" }}>desk@seiche.info</a> ·{" "}
        <a href="/support.html" style={{ color: "var(--dim)" }}>subscribe</a> ·{" "}
        <a href="/terms.html" style={{ color: "var(--faint)" }}>terms</a> ·{" "}
        <a href="/privacy.html" style={{ color: "var(--faint)" }}>privacy</a>
      </div>
    </div>
  );
}
