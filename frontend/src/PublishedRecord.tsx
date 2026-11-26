import { useEffect, useState } from "react";
import { latestArticles, latestInvestigations, latestLetters, type RecordLink } from "./recordLinks";

type PublishedLinks = { articles: RecordLink[]; letters: RecordLink[]; investigations: RecordLink[] };

const ARCHIVES = [
  { key: "articles", title: "Articles", href: "/articles/", index: "/articles/index.json", read: latestArticles },
  { key: "letters", title: "Dated letters", href: "/dispatches/", index: "/dispatches/index.json", read: latestLetters },
  { key: "investigations", title: "Investigations", href: "/investigations/", index: "/investigations/index.json", read: latestInvestigations },
] as const;

async function readIndex(path: string, signal: AbortSignal): Promise<unknown> {
  try {
    const response = await fetch(path, { signal, credentials: "omit", headers: { Accept: "application/json" } });
    return response.ok && (response.headers.get("content-type") || "").includes("json") ? await response.json() : null;
  } catch {
    return null;
  }
}

/**
 * Plain links into the published record, rendered by the page itself rather
 * than inside <noscript>, so a crawler that runs the page sees them. The hub
 * links render at once; the newest pieces come from the archive indexes.
 */
export default function PublishedRecord() {
  const [record, setRecord] = useState<PublishedLinks | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    Promise.all(ARCHIVES.map(async (archive) => [archive.key, archive.read(await readIndex(archive.index, controller.signal))] as const))
      .then((entries) => setRecord(Object.fromEntries(entries) as PublishedLinks))
      .finally(() => window.clearTimeout(timeout));
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, []);
  return <section className="rw-follow audience-record" aria-labelledby="published-record-title" aria-busy={record === null}>
    <div className="rw-section-title"><h2 id="published-record-title">The published record</h2><p>Articles, dated letters and investigations, each kept with the date it was published.</p></div>
    <div className="rw-follow-grid">
      {ARCHIVES.map((archive) => <article key={archive.key}>
        <h3><a href={archive.href}>{archive.title}</a></h3>
        {record && record[archive.key].length > 0 && <ul>{record[archive.key].map((item) => <li key={item.href}><a href={item.href}>{item.title}</a> <time dateTime={item.date}>{item.date}</time></li>)}</ul>}
      </article>)}
    </div>
    <p><a href="/money-markets/">Money-market evidence map →</a> · <a href="/markets/">World markets atlas</a></p>
  </section>;
}
