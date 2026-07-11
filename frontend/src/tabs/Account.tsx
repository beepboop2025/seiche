import { useEffect, useState } from "react";
import { API_BASE } from "../apiBase";
import { authHeaders, clearToken, getToken } from "../auth";

export default function Account() {
  const [email, setEmail] = useState("");
  const [on, setOn] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [me, setMe] = useState<{ username?: string; tier?: string }>({});
  const token = getToken() ?? "";

  useEffect(() => {
    fetch(`${API_BASE}/api/me`, { headers: authHeaders() }).then((r) => r.json()).then(setMe).catch(() => {});
    fetch(`${API_BASE}/api/alerts/prefs`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((p) => { setEmail(p.email ?? ""); setOn(!!p.alerts_on); })
      .catch(() => {});
  }, []);

  const save = () => {
    setSaved(null);
    fetch(`${API_BASE}/api/alerts/prefs`, {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ email: email.trim(), alerts_on: on }),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json())?.detail ?? "failed"); return r.json(); })
      .then((p) => { setEmail(p.email); setOn(p.alerts_on); setSaved("saved"); })
      .catch((e) => setSaved(String(e.message ?? e)));
  };

  return (
    <div className="grid" style={{ marginTop: 18 }}>
      <div className="card span6">
        <h2>Email alerts</h2>
        <div className="sub">
          The box checks the board six times a day and emails you when the regime changes, the Tell or a
          crunch window crosses its threshold, or a composite input goes dead. Off by default.
        </div>
        <div className="tmcontrols" style={{ flexDirection: "column", alignItems: "stretch", gap: 10, maxWidth: 380 }}>
          <input type="email" placeholder="you@example.com" value={email} autoComplete="email"
                 onChange={(e) => setEmail(e.target.value)} />
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "var(--text)" }}>
            <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
            email me when an alert fires
          </label>
          <button onClick={save} disabled={on && !email.trim()}>save</button>
          {saved && <span className="dimsmall" style={{ color: saved === "saved" ? "var(--calm)" : "var(--stress)" }}>{saved}</span>}
        </div>
        <div className="method">Delivery is best-effort from desk@seiche.info. Turn alerts off here anytime, or reply to any alert to reach the desk.</div>
      </div>

      <div className="card span6">
        <h2>API access</h2>
        <div className="sub">
          Your bearer token is your API key. It carries your access — the full board, the Time
          Machine replay and the dispatch reads are all reachable programmatically. Keep it secret; it lasts 30 days.
        </div>
        <div className="kv" style={{ flexDirection: "column", gap: 8 }}>
          <div className="item"><div className="k">account</div><div className="v">{me.username ?? "—"} · {me.tier ?? ""}</div></div>
          <div className="item" style={{ width: "100%" }}>
            <div className="k">API key</div>
            <textarea readOnly value={token} onClick={(e) => (e.target as HTMLTextAreaElement).select()}
              style={{ width: "100%", height: 54, background: "var(--panel-2)", border: "1px solid var(--panel-edge-2)", color: "var(--dim)", fontFamily: "var(--mono)", fontSize: 10.5, borderRadius: 8, padding: 8, marginTop: 4, resize: "none" }} />
          </div>
        </div>
        <pre style={{ background: "var(--panel-2)", border: "1px solid var(--panel-edge)", borderRadius: 8, padding: 12, overflowX: "auto", fontSize: 11, marginTop: 6 }}>
{`curl https://api.seiche.info/api/overview \\
  -H "Authorization: Bearer $SEICHE_KEY"

# free surface, no key needed:
curl https://api.seiche.info/api/public`}
        </pre>
        <div className="method">
          Endpoints: /api/overview (full board), /api/asof/&#123;date&#125; (Time Machine),
          /api/dispatch/&#123;slug&#125; (desk reads), /api/public (free conclusion + record).
        </div>
      </div>

      <div className="card span12">
        <div className="dimsmall">
          signed in as {me.username} ·{" "}
          <a href="#" style={{ color: "var(--faint)" }} onClick={(e) => { e.preventDefault(); clearToken(); location.reload(); }}>sign out</a>
        </div>
      </div>
    </div>
  );
}
