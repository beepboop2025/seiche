import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CORPUS_API_BASE } from "../apiBase";
import "../styles-corpus.css";

const DATASET_PAGE_SIZE = 24;

type Resource<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

interface CountBucket {
  datasets?: number;
  bytes?: number;
}

interface CorpusDataset {
  schema_version?: string;
  dataset_id: string;
  group?: string;
  bytes?: number;
  sha256?: string;
  data_class?: string;
  engines?: string[];
  acquired_date?: string;
  started_at?: string;
  finished_at?: string;
  status?: string;
  license_name?: string;
  license_review?: string;
  license_url?: string;
  source_page?: string;
  url?: string;
  split_policy?: string;
  notes?: string;
  evidence_class?: string;
  rights?: Record<string, unknown>;
  download?: string | null;
}

interface DatasetPage {
  schema_version?: string;
  generated_at?: string;
  count?: number;
  next_cursor?: string | null;
  filters?: Record<string, string>;
  datasets: CorpusDataset[];
}

interface BisFlow {
  agency_id?: string;
  flow_id: string;
  version?: string;
  name?: string;
  topic?: string;
  availability?: string;
  priority_tier?: number;
  product_scores?: Record<string, number>;
  joins?: string[];
  cautions?: string[];
  data_url?: string;
  structure_urn?: string;
}

interface BisFlows {
  schema_version?: string;
  generated_at?: string;
  knowledge_time?: string;
  count?: number;
  flows: BisFlow[];
}

interface MarketSource {
  source: string;
  files?: number;
  bytes?: number;
}

interface CorpusMarket {
  market: string;
  sources: MarketSource[];
}

interface SeicheMarkets {
  status?: string;
  market_count?: number;
  source_count?: number;
  evidence_class?: string;
  markets: CorpusMarket[];
}

interface CorpusExport {
  product: string;
  name?: string;
  evidence_class?: string;
  detail?: string;
  bytes?: number;
  modified_at?: string;
  download?: string | null;
}

interface CorpusExports {
  schema_version?: string;
  generated_at?: string;
  count?: number;
  exports: CorpusExport[];
}

interface CorpusCatalog {
  schema_version?: string;
  service?: string;
  generated_at?: string;
  knowledge_time?: string;
  evidence_classes?: string[];
  corpora?: {
    liquilens_engine?: {
      datasets?: number;
      bytes?: number;
      data_classes?: Record<string, CountBucket>;
      license_reviews?: Record<string, CountBucket>;
    };
    bis?: {
      flows?: number;
      bulk_flat?: number;
      api_only?: number;
      registry_only?: number;
    };
    seiche?: SeicheMarkets;
  };
}

interface CorpusResources {
  catalog: Resource<CorpusCatalog>;
  datasets: Resource<DatasetPage>;
  flows: Resource<BisFlows>;
  exports: Resource<CorpusExports>;
}

function loadingResources(): CorpusResources {
  return {
    catalog: { status: "loading" },
    datasets: { status: "loading" },
    flows: { status: "loading" },
    exports: { status: "loading" },
  };
}

async function fetchCorpusJson<T>(path: string, parentSignal: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  parentSignal.addEventListener("abort", abort, { once: true });
  const timeout = globalThis.setTimeout(abort, 10_000);
  try {
    const response = await fetch(`${CORPUS_API_BASE}${path}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
      credentials: "omit",
    });
    const mediaType = response.headers.get("content-type") ?? "";
    if (!response.ok || !mediaType.includes("json")) {
      throw new Error(`corpus returned HTTP ${response.status}`);
    }
    return await response.json() as T;
  } finally {
    globalThis.clearTimeout(timeout);
    parentSignal.removeEventListener("abort", abort);
  }
}

function messageFor(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === "AbortError") {
    return "request timed out";
  }
  if (reason instanceof Error) return reason.message;
  return "structured corpus is temporarily unreachable";
}

function formatBytes(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "not reported";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let scaled = value;
  let index = 0;
  while (scaled >= 1000 && index < units.length - 1) {
    scaled /= 1000;
    index += 1;
  }
  return `${scaled >= 100 || index === 0 ? scaled.toFixed(0) : scaled.toFixed(1)} ${units[index]}`;
}

function Clock({ value }: { value?: string }) {
  return value
    ? <time dateTime={value}>{value}</time>
    : <span className="corpus-muted">not reported</span>;
}

function safeHttps(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const resolved = new URL(value, `${CORPUS_API_BASE}/`);
    return resolved.protocol === "https:" ? resolved.toString() : null;
  } catch {
    return null;
  }
}

function dataClassBoundary(value: string | undefined): string {
  switch (value) {
    case "train_candidate":
      return "candidate only — not training approval";
    case "evaluation_only":
      return "evaluation use only — never training input";
    case "research_only":
      return "research only — never execution input";
    case "context_feature":
      return "context candidate — acceptance still required";
    case "outcome_label":
      return "label candidate — temporal review still required";
    case "entity_reference":
      return "reference data — no signal permission";
    default:
      return "classification is descriptive, not permission";
  }
}

function rightsEntries(value: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!value) return [];
  return Object.entries(value)
    .filter((entry): entry is [string, string | number | boolean | null] => {
      const field = entry[1];
      return field === null || ["string", "number", "boolean"].includes(typeof field);
    })
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, field]) => [key, field === null ? "null" : String(field)]);
}

function Status({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  const safeTone = /^[a-z]+$/.test(tone) ? tone : "neutral";
  return <span className={`corpus-status corpus-status--${safeTone}`}>{children}</span>;
}

function Fault({ label, resource }: { label: string; resource: Resource<unknown> }) {
  if (resource.status !== "error") return null;
  return <li><b>{label}</b>: {resource.message}. This section is unavailable; no zero or healthy value was substituted.</li>;
}

export default function Corpus() {
  const [resources, setResources] = useState<CorpusResources>(loadingResources);
  const [dataClass, setDataClass] = useState("");
  const [reload, setReload] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [paginationError, setPaginationError] = useState<string | null>(null);
  const paginationAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setResources((current) => ({
      ...current,
      catalog: { status: "loading" },
      flows: { status: "loading" },
      exports: { status: "loading" },
    }));

    const load = async <T,>(
      path: string,
      publish: (resource: Resource<T>) => void,
    ) => {
      try {
        const data = await fetchCorpusJson<T>(path, controller.signal);
        if (!disposed) publish({ status: "ready", data });
      } catch (reason) {
        if (!disposed) publish({ status: "error", message: messageFor(reason) });
      }
    };

    void load<CorpusCatalog>("/v1/catalog", (catalog) => {
      setResources((current) => ({ ...current, catalog }));
    });
    void load<BisFlows>("/v1/bis/flows?product=seiche", (flows) => {
      setResources((current) => ({ ...current, flows }));
    });
    void load<CorpusExports>("/v1/seiche/exports", (exports) => {
      setResources((current) => ({ ...current, exports }));
    });

    return () => {
      disposed = true;
      controller.abort();
    };
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    paginationAbort.current?.abort();
    setLoadingMore(false);
    setPaginationError(null);
    setResources((current) => ({ ...current, datasets: { status: "loading" } }));

    const query = new URLSearchParams({ limit: String(DATASET_PAGE_SIZE) });
    if (dataClass) query.set("data_class", dataClass);
    void fetchCorpusJson<DatasetPage>(`/v1/datasets?${query.toString()}`, controller.signal)
      .then((data) => {
        if (!disposed) setResources((current) => ({ ...current, datasets: { status: "ready", data } }));
      })
      .catch((reason: unknown) => {
        if (!disposed) setResources((current) => ({ ...current, datasets: { status: "error", message: messageFor(reason) } }));
      });

    return () => {
      disposed = true;
      controller.abort();
      paginationAbort.current?.abort();
    };
  }, [dataClass, reload]);

  const classBuckets = resources.catalog.status === "ready"
    ? resources.catalog.data.corpora?.liquilens_engine?.data_classes ?? {}
    : {};
  const classes = useMemo(() => Object.keys(classBuckets).sort(), [classBuckets]);
  const catalog = resources.catalog.status === "ready" ? resources.catalog.data : null;
  const engine = catalog?.corpora?.liquilens_engine;
  const bis = catalog?.corpora?.bis;
  const seiche = catalog?.corpora?.seiche;
  const faults = Object.values(resources).filter((resource) => resource.status === "error").length;

  const loadMore = async () => {
    if (resources.datasets.status !== "ready" || !resources.datasets.data.next_cursor || loadingMore) return;
    const controller = new AbortController();
    paginationAbort.current?.abort();
    paginationAbort.current = controller;
    setLoadingMore(true);
    setPaginationError(null);
    const query = new URLSearchParams({
      limit: String(DATASET_PAGE_SIZE),
      cursor: resources.datasets.data.next_cursor,
    });
    if (dataClass) query.set("data_class", dataClass);
    try {
      const page = await fetchCorpusJson<DatasetPage>(`/v1/datasets?${query.toString()}`, controller.signal);
      setResources((current) => {
        if (current.datasets.status !== "ready") return current;
        const seen = new Set(current.datasets.data.datasets.map((row) => row.dataset_id));
        const appended = page.datasets.filter((row) => !seen.has(row.dataset_id));
        return {
          ...current,
          datasets: {
            status: "ready",
            data: {
              ...page,
              count: current.datasets.data.datasets.length + appended.length,
              datasets: [...current.datasets.data.datasets, ...appended],
            },
          },
        };
      });
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setPaginationError(`Next page failed: ${messageFor(reason)}. Previously loaded receipts remain valid.`);
      }
    } finally {
      if (paginationAbort.current === controller) paginationAbort.current = null;
      setLoadingMore(false);
    }
  };

  return (
    <section className="corpus-shell" aria-labelledby="corpus-title">
      <header className="corpus-hero">
        <div className="corpus-hero__copy">
          <span>STRUCTURED EVIDENCE / PUBLIC DISCOVERY</span>
          <h1 id="corpus-title">The corpus, with its boundaries intact.</h1>
          <p>
            Browse Seiche’s money- and capital-market source spine as structured records.
            Rights, evidence class, status and clocks travel with every row; nothing on this
            page is silently admitted to an analytic, model, score or execution system.
          </p>
          <div className="corpus-actions">
            <a href={`${CORPUS_API_BASE}/v1/catalog`}>Open JSON catalog</a>
            <a href={CORPUS_API_BASE}>API + MCP index</a>
            <button type="button" onClick={() => setReload((value) => value + 1)}>Refresh</button>
          </div>
        </div>
        <div className="corpus-hero__clock">
          <span>CATALOG GENERATED</span>
          <Clock value={catalog?.generated_at} />
          <span>EVIDENCE KNOWLEDGE TIME</span>
          <Clock value={catalog?.knowledge_time} />
          <small>These clocks are distinct. A page refresh never rewrites source time.</small>
        </div>
      </header>

      <aside className="corpus-boundary" aria-label="Corpus permission boundary">
        <b>READ THIS AS A CATALOG, NOT AN APPROVAL.</b>
        <span>
          <code>train_candidate</code>, <code>evaluation_only</code>, <code>research_only</code>
          and every other data class remain descriptive classifications. Model, training,
          scoring, redistribution and execution permission require separate acceptance.
        </span>
      </aside>

      {faults > 0 && (
        <div className="corpus-faults" role="status">
          <strong>{faults} corpus section{faults === 1 ? " is" : "s are"} unavailable</strong>
          <ul>
            <Fault label="Catalog" resource={resources.catalog} />
            <Fault label="Datasets" resource={resources.datasets} />
            <Fault label="BIS flows" resource={resources.flows} />
            <Fault label="Exports" resource={resources.exports} />
          </ul>
        </div>
      )}

      <div className="corpus-kpis" aria-label="Corpus coverage summary">
        <article><span>REGISTERED DATASETS</span><strong>{engine?.datasets ?? "—"}</strong><small>{formatBytes(engine?.bytes)}</small></article>
        <article><span>BIS FLOWS</span><strong>{bis?.flows ?? "—"}</strong><small>{bis ? `${bis.bulk_flat ?? 0} bulk · ${bis.api_only ?? 0} API · ${bis.registry_only ?? 0} registry` : "not reported"}</small></article>
        <article><span>SEICHE MARKETS</span><strong>{seiche?.market_count ?? "—"}</strong><small>{seiche?.source_count === undefined ? "not reported" : `${seiche.source_count} official source families`}</small></article>
        <article><span>EVIDENCE VOCABULARY</span><strong>{catalog?.evidence_classes?.length ?? "—"}</strong><small>{catalog?.evidence_classes?.join(" · ") || "not reported"}</small></article>
      </div>

      <section className="corpus-panel" aria-labelledby="corpus-datasets-title">
        <header className="corpus-panel__head">
          <div><span>VERSIONED RECEIPTS</span><h2 id="corpus-datasets-title">Dataset registry</h2></div>
          <label>
            Data class
            <select value={dataClass} onChange={(event) => setDataClass(event.target.value)} disabled={resources.catalog.status === "loading"}>
              <option value="">all classes</option>
              {classes.map((value) => <option value={value} key={value}>{value} ({classBuckets[value]?.datasets ?? 0})</option>)}
            </select>
          </label>
        </header>
        {resources.datasets.status === "loading" && <p className="corpus-loading">Loading versioned receipts…</p>}
        {resources.datasets.status === "ready" && (
          <>
            <div className="corpus-table-wrap">
              <table className="corpus-table">
                <caption>Structured receipt fields are projected as published; upstream URLs are provenance, not corpus download grants.</caption>
                <thead><tr><th>Dataset</th><th>Status / class</th><th>Rights</th><th>Clocks / provenance</th></tr></thead>
                <tbody>
                  {resources.datasets.data.datasets.map((row) => {
                    const licenseUrl = safeHttps(row.license_url);
                    const sourcePage = safeHttps(row.source_page);
                    const download = safeHttps(row.download);
                    const rights = rightsEntries(row.rights);
                    return (
                      <tr key={row.dataset_id}>
                        <th scope="row">
                          <code>{row.dataset_id}</code>
                          <span>{row.group || "group not reported"} · {formatBytes(row.bytes)}</span>
                          {row.sha256 && <small title={row.sha256}>sha256 {row.sha256.slice(0, 12)}…</small>}
                        </th>
                        <td>
                          <Status tone={row.status === "downloaded" ? "observed" : "neutral"}>{row.status || "unknown"}</Status>
                          <code>{row.data_class || "unclassified"}</code>
                          <small>{dataClassBoundary(row.data_class)}</small>
                        </td>
                        <td>
                          <Status tone={row.license_review === "approved_public" ? "approved" : "restricted"}>{row.license_review || "unreviewed"}</Status>
                          {row.evidence_class && <Status tone={row.evidence_class === "restricted" ? "restricted" : "neutral"}>{row.evidence_class}</Status>}
                          <span>{row.license_name || "license not reported"}</span>
                          {licenseUrl && <a href={licenseUrl} rel="noreferrer">Terms</a>}
                          {download && <a href={download}>Reviewed corpus download</a>}
                          {row.download === null && <code>download: null</code>}
                          {rights.length > 0 && <dl className="corpus-rights">{rights.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
                        </td>
                        <td>
                          <span>acquired <Clock value={row.acquired_date} /></span>
                          <span>finished <Clock value={row.finished_at} /></span>
                          {sourcePage && <a href={sourcePage} rel="noreferrer">Source page</a>}
                          {(row.split_policy || row.notes) && <details><summary>Receipt notes</summary><p>{row.split_policy}</p><p>{row.notes}</p></details>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <footer className="corpus-pagination">
              <span>{resources.datasets.data.datasets.length} rows shown · generated <Clock value={resources.datasets.data.generated_at} /></span>
              {resources.datasets.data.next_cursor
                ? <button type="button" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load next page"}</button>
                : <Status tone="approved">end of filtered registry</Status>}
            </footer>
            {paginationError && <p className="corpus-page-error" role="status">{paginationError}</p>}
          </>
        )}
      </section>

      <section className="corpus-panel" aria-labelledby="corpus-flows-title">
        <header className="corpus-panel__head">
          <div><span>SEICHE-RELEVANT / PRODUCT SCORE &gt; 0</span><h2 id="corpus-flows-title">BIS flow map</h2></div>
          <a href={`${CORPUS_API_BASE}/v1/bis/flows?product=seiche`}>Full JSON</a>
        </header>
        {resources.flows.status === "loading" && <p className="corpus-loading">Loading BIS flow registry…</p>}
        {resources.flows.status === "ready" && (
          <>
            <p className="corpus-clockline">Knowledge time <Clock value={resources.flows.data.knowledge_time} /> · response generated <Clock value={resources.flows.data.generated_at} /></p>
            <div className="corpus-flow-grid">
              {resources.flows.data.flows.map((flow) => (
                <article key={flow.flow_id}>
                  <div><code>{flow.flow_id}</code><Status tone="neutral">tier {String(flow.priority_tier ?? "—")}</Status></div>
                  <h3>{flow.name || flow.topic || "Unnamed BIS flow"}</h3>
                  <p>{flow.topic}</p>
                  <dl><div><dt>availability</dt><dd>{flow.availability || "unavailable"}</dd></div><div><dt>Seiche fit</dt><dd>{flow.product_scores?.seiche ?? "—"}/100</dd></div></dl>
                  {(flow.cautions ?? []).map((caution) => <small key={caution}>Caution: {caution}</small>)}
                </article>
              ))}
            </div>
          </>
        )}
      </section>

      <div className="corpus-split">
        <section className="corpus-panel" aria-labelledby="corpus-markets-title">
          <header className="corpus-panel__head"><div><span>OBSERVED PARTITIONS</span><h2 id="corpus-markets-title">Seiche market lake</h2></div><a href={`${CORPUS_API_BASE}/v1/seiche/markets`}>Detailed JSON</a></header>
          {resources.catalog.status === "loading" && <p className="corpus-loading">Loading bounded market catalog…</p>}
          {seiche && (
            <div className="corpus-market-grid">
              {seiche.markets.map((market) => (
                <article key={market.market}>
                  <div><strong>{market.market}</strong><Status tone="observed">{seiche.evidence_class || "unknown"}</Status></div>
                  <ul>{market.sources.map((source) => <li key={source.source}><code>{source.source}</code><span>{source.files === undefined && source.bytes === undefined ? "official source family" : `${source.files?.toLocaleString() ?? "—"} files · ${formatBytes(source.bytes)}`}</span></li>)}</ul>
                </article>
              ))}
            </div>
          )}
          {resources.catalog.status === "error" && <p className="corpus-loading">Market catalog unavailable; no empty-lake claim was substituted.</p>}
        </section>

        <section className="corpus-panel corpus-exports" aria-labelledby="corpus-exports-title">
          <header className="corpus-panel__head"><div><span>PUBLICATION GATE</span><h2 id="corpus-exports-title">Product exports</h2></div></header>
          {resources.exports.status === "loading" && <p className="corpus-loading">Checking export grants…</p>}
          {resources.exports.status === "ready" && resources.exports.data.exports.map((entry) => {
            const download = safeHttps(entry.download);
            return (
              <article key={`${entry.product}:${entry.name || "registered"}`}>
                <div><code>{entry.product}</code><Status tone={entry.evidence_class === "restricted" ? "restricted" : "approved"}>{entry.evidence_class || "unknown"}</Status></div>
                <p>{entry.detail || "A reviewed public export grant is registered."}</p>
                {download
                  ? <a href={download}>Download reviewed export</a>
                  : <strong>download: null · raw protected export is not public</strong>}
                {entry.modified_at && <small>modified <Clock value={entry.modified_at} /></small>}
              </article>
            );
          })}
          {resources.exports.status === "ready" && resources.exports.data.exports.length === 0 && <p>No product export is registered. This is not evidence that underlying data is empty.</p>}
        </section>
      </div>

      <footer className="corpus-footer">
        <span>CANONICAL CONTRACT <code>{CORPUS_API_BASE}</code> · MCP <code>{CORPUS_API_BASE}/mcp</code></span>
        <p>Public research data and provenance, not investment advice or an executable quote.</p>
      </footer>
    </section>
  );
}
