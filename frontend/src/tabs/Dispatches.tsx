import { useEffect, useState } from "react";
import { renderMarkdown } from "../md";
import { API_BASE } from "../apiBase";
import ShareBar from "../ShareBar";
import Subscribe from "../Subscribe";
import { composeTextCard } from "../share";
import "../styles-editorial.css";

type Index = { slug: string; title: string; date: string; summary: string; tag?: string }[];

// New letters mark their continuation HAS-DESK; the published archive still
// carries HAS-PAID from before the open-access rename (it gated nothing —
// everything on Seiche is free). Accept both.
const DESK_MARKERS = ["<!--HAS-DESK-->", "<!--HAS-PAID-->"];

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
    const hasDesk = DESK_MARKERS.some((m) => body.includes(m));
    const free = DESK_MARKERS.reduce((md, m) => md.replace(m, ""), body).trim();
    const complete = `${free}\n${deskRead ?? ""}`;
    const readMinutes = Math.max(1, Math.ceil(complete.trim().split(/\s+/).length / 220));
    const sourceClocks = (complete.match(/\bas of\b/gi) ?? []).length;
    const sections = (complete.match(/^#{2,3}\s+/gm) ?? []).length;
    return (
      <div className="dispatch dispatch--editorial" style={{ marginTop: 18 }}>
        <a className="dispatch-back" href="#dispatches">← all dispatches</a>
        {meta && (
          <div className="dispatch-head">
            <div className="dispatch-date">{meta.date}{meta.tag ? ` · ${meta.tag}` : ""}</div>
            <h1 className="dispatch-title">{meta.title}</h1>
            <ShareBar
              compose={() => Promise.resolve(composeTextCard({
                title: meta.title,
                kicker: `${meta.date}${meta.tag ? ` · ${meta.tag}` : ""} · dispatch`,
                body: meta.summary,
              }))}
              title={() => meta.title}
            />
          </div>
        )}
        <div className="dispatch-layout">
          <aside className="dispatch-dossier" aria-label="Dispatch evidence contract">
            <div className="dispatch-dossier__kicker">ISSUE CONTRACT</div>
            <dl>
              <div><dt>status</dt><dd>frozen point-in-time</dd></div>
              <div><dt>reading time</dt><dd>{readMinutes} min</dd></div>
              <div><dt>sections</dt><dd>{sections || "—"}</dd></div>
              <div><dt>dated source clocks</dt><dd>{sourceClocks}</dd></div>
            </dl>
            <p>The live board can change. This letter cannot. Its argument, countercase and misses stay attached to the date.</p>
            <a href="/methodology.html">methods + source register →</a>
          </aside>
          <article className="dispatch-copy">
            <div className="dispatch-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(free) }} />
            {hasDesk && deskRead && (
              <div className="dispatch-body dispatch-body--desk" dangerouslySetInnerHTML={{ __html: renderMarkdown(deskRead) }} />
            )}
            {hasDesk && !deskRead && (
              <div className="dimsmall" style={{ marginTop: 12 }}>loading the desk's forward read…</div>
            )}
          </article>
        </div>
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
      {/* The field is here and on the board, and it gates nothing in either
          place: the whole archive below renders identically whether or not an
          address was ever typed. */}
      <Subscribe />
      {!index ? (
        <div className="loading" style={{ padding: 40 }}>loading…</div>
      ) : index.length === 0 ? (
        <div className="card span12"><div className="sub">no dispatches yet.</div></div>
      ) : (
        <>
          <a className="dispatch-card dispatch-card--lead" href={`#dispatches/${index[0].slug}`}>
            <div className="dispatch-card__eyebrow">LATEST DISPATCH</div>
            <div className="dispatch-date">{index[0].date}{index[0].tag ? ` · ${index[0].tag}` : ""}</div>
            <div className="dispatch-card-title">{index[0].title}</div>
            <div className="dispatch-card-sum">{index[0].summary}</div>
            <div className="dispatch-read">read the point-in-time issue →</div>
          </a>
          <div className="dispatch-archive-label">THE RECORD · NEWEST FIRST</div>
          {index.slice(1).map((d) => (
          <a className="dispatch-card dispatch-card--archive" key={d.slug} href={`#dispatches/${d.slug}`}>
            <div className="dispatch-date">{d.date}{d.tag ? ` · ${d.tag}` : ""}</div>
            <div className="dispatch-card-title">{d.title}</div>
            <div className="dispatch-card-sum">{d.summary}</div>
            <div className="dispatch-read">read →</div>
          </a>
          ))}
        </>
      )}
    </div>
  );
}
