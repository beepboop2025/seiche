import { useEffect, useState } from "react";
import { API_BASE } from "../apiBase";

const TOPICS = {
  all: "All Palimpsest evidence", china: "China economy", regions: "Regional research and BRI",
  information_controls: "Information controls", model_evaluations: "Model evaluations",
  funding: "Funding transmission", institutions: "Institution research",
  liquidity: "Market liquidity", global_data: "Global data library",
} as const;
type Topic = keyof typeof TOPICS;
type Dataset = {
  id: string; title: string | null; description: string | null; evidence_state: string;
  observed_at: string | null; cadence: string | null; sources: string[];
  url: string | null; data_url: string | null; rights: string | null;
};
type Step = { product: string; question: string; url: string; mcp: string | null; telegram: string | null };
type Network = {
  schema: string; status: string; datasets: Dataset[]; next_steps: Step[];
  catalog_total: number | null; matched_total: number; returned: number; next_offset: number | null;
  source: { generated_at: string | null; retrieved_at: string | null; sha256: string | null };
  funding_context: { status: string; as_of?: string | null; summary?: { domains?: { id: string; status: string; as_of: string | null; reading?: string | null }[] } };
  boundary: string;
};

function selection(): { topic: Topic; offset: number } {
  const [, topic, offset] = window.location.hash.slice(1).split("/");
  return { topic: Object.hasOwn(TOPICS, topic || "") ? topic as Topic : "all",
    offset: /^\d{1,4}$/.test(offset || "") && Number(offset) <= 1000 ? Number(offset) : 0 };
}

function safeLink(url: string | null): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    const hosts = ["palimpsest.info", "www.palimpsest.info", "seiche.info", "api.seiche.info", "liquilens.in", "liquilens-undertow.com", "narcoscope.com", "beepboop2025.github.io", "t.me"];
    return parsed.protocol === "https:" && hosts.includes(parsed.hostname) && !parsed.username && !parsed.password && !parsed.port ? url : undefined;
  } catch { return undefined; }
}

export default function Research() {
  const [query, setQuery] = useState(selection);
  const [data, setData] = useState<Network | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const changed = () => setQuery(selection());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setData(null); setError(false); setLoading(true);
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    fetch(`${API_BASE}/api/v2/research-network?topic=${query.topic}&offset=${query.offset}&limit=12`,
      { signal: controller.signal, credentials: "omit" })
      .then(async response => {
        if (!response.ok) throw new Error("Research catalog unavailable");
        const body = await response.json();
        if (body?.schema !== "seiche.research-network.v1" || !Array.isArray(body.datasets) || !Array.isArray(body.next_steps)
          || body.datasets.length > 25 || !body.source || body.selection?.topic !== query.topic || body.selection?.offset !== query.offset) {
          throw new Error("Unexpected research catalog");
        }
        if (active) setData(body);
      }).catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); window.clearTimeout(timeout); });
    return () => { active = false; controller.abort(); window.clearTimeout(timeout); };
  }, [query.topic, query.offset]);
  const move = (topic: Topic, offset = 0) => { window.location.hash = `RESEARCH/${topic}/${offset}`; };

  return <div className="grid" aria-busy={loading}>
    <section className="card span12">
      <div className="sub">CONNECTED RESEARCH</div>
      <h1>Follow the evidence.</h1>
      <p>Start with Palimpsest’s source record. Examine funding conditions in Seiche, then follow the relevant institution, liquidity or global-data research.</p>
      <label htmlFor="research-topic">Research topic </label>
      <select id="research-topic" value={query.topic} onChange={event => move(event.target.value as Topic)}>
        {Object.entries(TOPICS).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
      </select>
      <p className="sub">The address preserves this topic and page. Each dataset keeps its own sources, dates and reuse terms.</p>
      <p role="status">{loading ? "Loading the published research catalog…" : error ? "The research service is unavailable. Open the source catalog below." :
        `${data?.matched_total ?? 0} matching datasets · ${data?.catalog_total ?? "Unknown"} in the full catalog · ${data?.status ?? "unavailable"}`}</p>
      <p className="sub">This index describes registered datasets. “Unknown” means their public availability and freshness have not been established here. Inspect each source before using observations.</p>
      <a href="https://palimpsest.info/data.html">Open Palimpsest’s full source catalog</a>
    </section>
    {data && <section className="card span12">
      <h2>Funding and market context</h2>
      <p>Seiche’s completed snapshot · {data.funding_context?.status ?? "unavailable"} · as of {data.funding_context?.as_of ?? "unavailable"}</p>
      {data.funding_context?.summary?.domains?.map(domain => <p key={domain.id}>
        <strong>{domain.id.replaceAll("_", " ")}</strong>: {domain.status} · {domain.as_of ?? "undated"}{domain.reading ? ` — ${domain.reading}` : ""}
      </p>)}
      <a href="#MONEY%20MARKETS">Inspect the funding evidence and source clocks →</a>
    </section>}
    {data?.datasets.map(row => <article className="card span6" key={row.id}>
      <div className="sub">{row.evidence_state} · {row.cadence ?? "Cadence unspecified"}</div>
      <h2>{safeLink(row.url) ? <a href={safeLink(row.url)}>{row.title || row.id}</a> : row.title || row.id}</h2>
      <p>{row.description}</p>
      <p className="sub">Source observation: {row.observed_at ?? "Unavailable"}</p>
      <p>{row.sources?.join(" · ")}</p>
      <p className="sub">{row.rights ?? "Check the source’s reuse terms."}</p>
      {safeLink(row.data_url) && <a href={safeLink(row.data_url)}>Inspect the published evidence and its access state →</a>}
    </article>)}
    {data && <section className="card span12">
      <nav aria-label="Research catalog pages">
        <button disabled={query.offset === 0} onClick={() => move(query.topic, Math.max(0, query.offset - 12))}>Previous page</button>{" "}
        <span>Page {Math.floor(query.offset / 12) + 1}</span>{" "}
        <button disabled={data.next_offset === null} onClick={() => { if (data.next_offset !== null) move(query.topic, data.next_offset); }}>Next page</button>
      </nav>
      <p className="sub">Catalog published: {data.source.generated_at ?? "Unavailable"}. Retrieved: {data.source.retrieved_at ?? "Unavailable"}.</p>
    </section>}
    {data?.next_steps.map(step => <article className="card span4" key={step.product}>
      <h2>{step.product}</h2><p>{step.question}</p>
      {safeLink(step.url) && <a href={safeLink(step.url)}>Continue the research →</a>}
      {safeLink(step.telegram) && <p><a href={safeLink(step.telegram)}>Open the Telegram desk</a></p>}
    </article>)}
    <aside className="card span12"><p>{data?.boundary ?? "These are separate research records. Shared topics and dates do not establish causality or change a score. Missing evidence remains unavailable."}</p></aside>
  </div>;
}
