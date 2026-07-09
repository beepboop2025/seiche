import { useEffect, useState } from "react";
import { renderMarkdown } from "../md";
import { API_BASE } from "../apiBase";
import { authHeaders, getToken } from "../auth";

type Index = { slug: string; title: string; date: string; summary: string; tag?: string }[];

const HAS_PAID = "<!--HAS-PAID-->";

function slugFromHash(): string | null {
  const h = decodeURIComponent(window.location.hash.replace("#", ""));
  const m = h.match(/^dispatches\/(.+)$/i);
  return m ? m[1] : null;
}

export default function Dispatches() {
  const [index, setIndex] = useState<Index | null>(null);
  const [slug, setSlug] = useState<string | null>(slugFromHash());
  const [body, setBody] = useState<string | null>(null);
  const [paid, setPaid] = useState<string | null>(null);
  const signedIn = getToken() !== null;

  useEffect(() => {
    fetch("dispatches/index.json").then((r) => r.json()).then(setIndex).catch(() => setIndex([]));
    const onHash = () => setSlug(slugFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!slug) { setBody(null); setPaid(null); return; }
    setBody(null); setPaid(null);
    fetch(`dispatches/${slug}.md`).then((r) => (r.ok ? r.text() : Promise.reject())).then(setBody).catch(() => setBody(""));
    if (signedIn) {
      fetch(`${API_BASE}/api/dispatch/${slug}`, { headers: authHeaders() })
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((j) => setPaid(j.paid ?? null))
        .catch(() => setPaid(null));
    }
  }, [slug, signedIn]);

  // ---- single dispatch ----
  if (slug) {
    const meta = index?.find((d) => d.slug === slug);
    if (body === null) return <div className="loading" style={{ padding: 60 }}>loading dispatch…</div>;
    if (body === "") return (
      <div className="card span12" style={{ marginTop: 18 }}>
        <div className="faults">no dispatch at that address.</div>
        <a className="dispatch-back" href="#dispatches">← all dispatches</a>
      </div>
    );
    const hasPaid = body.includes(HAS_PAID);
    const free = body.replace(HAS_PAID, "").trim();
    return (
      <div className="dispatch" style={{ marginTop: 18 }}>
        <a className="dispatch-back" href="#dispatches">← all dispatches</a>
        {meta && (
          <div className="dispatch-head">
            <div className="dispatch-date">{meta.date}{meta.tag ? ` · ${meta.tag}` : ""}</div>
            <h1 className="dispatch-title">{meta.title}</h1>
          </div>
        )}
        <div className="dispatch-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(free) }} />
        {hasPaid && (signedIn && paid ? (
          <div className="dispatch-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(paid) }} />
        ) : (
          <div className="paywall">
            <div className="paywall-lock">◆ THE DESK'S READ · SUBSCRIBERS</div>
            <p>The rest of this dispatch — the forward read and the dates the desk is watching — is for subscribers. The board and the honest record stay free forever; the interpretation is the paid layer.</p>
            <div className="paywall-actions">
              <a className="paywall-cta" href="#timemachine">sign in</a>
              <a className="paywall-alt" href="mailto:desk@seiche.info?subject=Seiche%20subscription">request access · desk@seiche.info</a>
            </div>
          </div>
        ))}
      </div>
    );
  }

  // ---- list ----
  return (
    <div className="dispatch-list" style={{ marginTop: 18 }}>
      <div className="dispatch-intro">
        <h1>Dispatches</h1>
        <p>What the plumbing did, and what it means — written from the same free public data the board runs on. Every claim traces to a number you can check. The summaries are free; the desk's forward read is for subscribers.</p>
      </div>
      {!index ? (
        <div className="loading" style={{ padding: 40 }}>loading…</div>
      ) : index.length === 0 ? (
        <div className="card span12"><div className="sub">no dispatches yet.</div></div>
      ) : (
        index.map((d) => (
          <a className="dispatch-card" key={d.slug} href={`#dispatches/${d.slug}`}>
            <div className="dispatch-date">{d.date}{d.tag ? ` · ${d.tag}` : ""}</div>
            <div className="dispatch-card-title">{d.title}</div>
            <div className="dispatch-card-sum">{d.summary}</div>
            <div className="dispatch-read">read →</div>
          </a>
        ))
      )}
    </div>
  );
}
