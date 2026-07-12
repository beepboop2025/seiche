import { useEffect, useState } from "react";
import { renderMarkdown } from "../md";
import { API_BASE } from "../apiBase";

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
  const [deskRead, setDeskRead] = useState<string | null>(null);

  useEffect(() => {
    fetch("dispatches/index.json").then((r) => r.json()).then(setIndex).catch(() => setIndex([]));
    const onHash = () => setSlug(slugFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!slug) { setBody(null); setDeskRead(null); return; }
    setBody(null); setDeskRead(null);
    fetch(`dispatches/${slug}.md`).then((r) => (r.ok ? r.text() : Promise.reject())).then(setBody).catch(() => setBody(""));
    // The desk's-read continuation is free like everything else (the API's
    // "paid" key is a historical name kept for compatibility).
    fetch(`${API_BASE}/api/dispatch/${slug}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => setDeskRead(j.paid ?? null))
      .catch(() => setDeskRead(null));
  }, [slug]);

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
        {hasPaid && deskRead && (
          <div className="dispatch-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(deskRead) }} />
        )}
        {hasPaid && !deskRead && (
          <div className="dimsmall" style={{ marginTop: 12 }}>loading the desk's forward read…</div>
        )}
      </div>
    );
  }

  // ---- list ----
  return (
    <div className="dispatch-list" style={{ marginTop: 18 }}>
      <div className="dispatch-intro">
        <h1>Dispatches</h1>
        <p>What the plumbing did, and what it means — written from the same free public data the board runs on. Every claim traces to a number you can check. All of it is free, including the desk's forward read; if it earns its keep, <a href="/support.html">support keeps it running</a>.</p>
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
