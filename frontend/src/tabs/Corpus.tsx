import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CORPUS_API_BASE } from "../apiBase";
import { safeCorpusExportHref } from "../engineCorpus";
import BisFlowExplorer from "./BisFlowExplorer";
import EngineDatasetExplorer from "./EngineDatasetExplorer";
import MarketSeriesExplorer from "./MarketSeriesExplorer";
import "../styles-corpus.css";

type Resource<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

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
      attempts?: number;
      objects?: number;
      verified_objects?: number;
      published_objects?: number;
      withheld_objects?: number;
      recovered_objects?: number;
      unresolved_objects?: number;
      structurally_profiled_objects?: number;
      bis_linked_objects?: number;
      restricted?: {
        collections?: number;
        acquired_internal_research_only?: number;
        not_acquired_restricted_recipes?: number;
        object_count?: number;
        row_count?: number;
        total_bytes?: number;
      };
      data_classes?: Record<string, number>;
      groups?: Record<string, number>;
      engines?: Record<string, number>;
      acquisition_reviews?: Record<string, number>;
      collection_kinds?: Record<string, number>;
      publication_states?: Record<string, number>;
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
  flows: Resource<BisFlows>;
  markets: Resource<SeicheMarkets>;
  exports: Resource<CorpusExports>;
}

function loadingResources(): CorpusResources {
  return {
    catalog: { status: "loading" },
    flows: { status: "loading" },
    markets: { status: "idle" },
    exports: { status: "loading" },
  };
}

async function fetchCorpusJson<T>(
  path: string,
  parentSignal: AbortSignal,
  timeoutMs = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort(parentSignal.reason);
  if (parentSignal.aborted) abort();
  else parentSignal.addEventListener("abort", abort, { once: true });
  const timeout = globalThis.setTimeout(abort, timeoutMs);
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

function requireAvailableSeicheMarkets(value: SeicheMarkets): SeicheMarkets {
  if (
    value.status !== "ok"
    || value.evidence_class !== "observed"
    || !Array.isArray(value.markets)
    || !Number.isSafeInteger(value.market_count)
    || !Number.isSafeInteger(value.source_count)
    || value.market_count !== value.markets.length
  ) {
    throw new Error("market corpus reports unavailable or invalid coverage");
  }
  const sourceCount = value.markets.reduce((total, market) => {
    if (typeof market.market !== "string" || market.market.length === 0 || !Array.isArray(market.sources)) {
      throw new Error("market corpus reports an invalid market partition");
    }
    return total + market.sources.length;
  }, 0);
  if (sourceCount !== value.source_count) {
    throw new Error("market corpus source count does not match its partitions");
  }
  return value;
}

function availableSeicheMarkets(value: SeicheMarkets | undefined): SeicheMarkets | null {
  if (value === undefined) return null;
  try {
    return requireAvailableSeicheMarkets(value);
  } catch {
    return null;
  }
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

type StatusTone = "neutral" | "observed" | "reviewed" | "restricted";

function evidenceTone(value: string | undefined): StatusTone {
  switch (value?.trim().toLocaleLowerCase()) {
    case "observed":
      return "observed";
    case "derived":
      return "reviewed";
    case "restricted":
      return "restricted";
    default:
      return "neutral";
  }
}

function Status({ children, tone = "neutral" }: { children: ReactNode; tone?: StatusTone }) {
  return <span className={`corpus-status corpus-status--${tone}`}>{children}</span>;
}

function Fault({ label, resource }: { label: string; resource: Resource<unknown> }) {
  if (resource.status !== "error") return null;
  return <li><b>{label}</b>: {resource.message}. This section is unavailable; no zero or healthy value was substituted.</li>;
}

export default function Corpus() {
  const [resources, setResources] = useState<CorpusResources>(loadingResources);
  const [reload, setReload] = useState(0);
  const marketSection = useRef<HTMLElement | null>(null);
  const [marketInventoryRequested, setMarketInventoryRequested] = useState(false);

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
    if (marketInventoryRequested) return undefined;
    const section = marketSection.current;
    if (!section || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setMarketInventoryRequested(true);
        observer.disconnect();
      }
    }, { rootMargin: "600px 0px" });
    observer.observe(section);
    return () => observer.disconnect();
  }, [marketInventoryRequested]);

  useEffect(() => {
    if (!marketInventoryRequested) return undefined;
    const controller = new AbortController();
    let disposed = false;
    setResources((current) => ({ ...current, markets: { status: "loading" } }));
    void fetchCorpusJson<SeicheMarkets>("/v1/seiche/markets", controller.signal, 30_000)
      .then(requireAvailableSeicheMarkets)
      .then((data) => {
        if (!disposed) {
          setResources((current) => ({ ...current, markets: { status: "ready", data } }));
        }
      })
      .catch((reason: unknown) => {
        if (!disposed) {
          setResources((current) => ({
            ...current,
            markets: { status: "error", message: messageFor(reason) },
          }));
        }
      });
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [marketInventoryRequested, reload]);

  const classBuckets = resources.catalog.status === "ready"
    ? resources.catalog.data.corpora?.liquilens_engine?.data_classes ?? {}
    : {};
  const classes = useMemo(() => Object.keys(classBuckets).sort(), [classBuckets]);
  const groupBuckets = resources.catalog.status === "ready"
    ? resources.catalog.data.corpora?.liquilens_engine?.groups ?? {}
    : {};
  const groups = useMemo(() => Object.keys(groupBuckets).sort(), [groupBuckets]);
  const engineBuckets = resources.catalog.status === "ready"
    ? resources.catalog.data.corpora?.liquilens_engine?.engines ?? {}
    : {};
  const engines = useMemo(() => Object.keys(engineBuckets).sort(), [engineBuckets]);
  const catalog = resources.catalog.status === "ready" ? resources.catalog.data : null;
  const engine = catalog?.corpora?.liquilens_engine;
  const bis = catalog?.corpora?.bis;
  const seiche = resources.markets.status === "ready"
    ? resources.markets.data
    : availableSeicheMarkets(catalog?.corpora?.seiche);
  const faults = Object.values(resources).filter((resource) => resource.status === "error").length;

  const refreshCorpus = () => {
    setReload((value) => value + 1);
  };

  return (
    <section className="corpus-shell" aria-labelledby="corpus-title">
      <header className="corpus-hero">
        <div className="corpus-hero__copy">
          <span>SEICHE MARKET ATLAS / HETZNER EVIDENCE LAKE</span>
          <h1 id="corpus-title">Descend from the whole market to the exact row.</h1>
          <p>
            Explore Seiche’s public money, forex and capital-market evidence as one navigable
            survey: markets, instruments, observations, BIS transmission flows, revisions,
            attested objects and exports. Rights, status and every distinct clock travel with the row.
          </p>
          <div className="corpus-actions">
            <a href={`${CORPUS_API_BASE}/v1/catalog`}>Open JSON catalog</a>
            <a href={CORPUS_API_BASE}>API + MCP index</a>
            <button type="button" onClick={refreshCorpus}>Refresh</button>
          </div>
        </div>
        <div className="corpus-hero__clock">
          <span>CATALOG GENERATED</span>
          <Clock value={catalog?.generated_at} />
          <span>BIS INVENTORY KNOWLEDGE</span>
          <Clock value={catalog?.knowledge_time} />
          <small>Catalog-generation and BIS-inventory clocks are distinct; each row keeps its own evidence clocks.</small>
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
            <Fault label="BIS flows" resource={resources.flows} />
            <Fault label="Market lake" resource={resources.markets} />
            <Fault label="Exports" resource={resources.exports} />
          </ul>
        </div>
      )}

      <div className="corpus-kpis corpus-transect" aria-label="Market atlas depth transect">
        <a href="#canonical-market-series"><span>INDEXED OBSERVATIONS</span><strong>{seiche?.market_count ?? "—"} markets</strong><small>{seiche?.source_count === undefined ? "source count unavailable" : `${seiche.source_count} official source families`}</small></a>
        <a href="#bis-observations-title"><span>BULK TRANSMISSION</span><strong>{bis?.flows ?? "—"} BIS flows</strong><small>{bis ? `${bis.bulk_flat ?? 0} bulk · ${bis.api_only ?? 0} API · ${bis.registry_only ?? 0} registry` : "inventory unavailable"}</small></a>
        <a href="#corpus-datasets-title"><span>VERIFIED OBJECTS</span><strong>{engine?.verified_objects ?? engine?.objects ?? "—"} objects</strong><small>{engine?.attempts === undefined ? "attempt ledger unavailable" : `${engine.attempts.toLocaleString()} attempts · ${engine.recovered_objects ?? 0} recovered · ${engine.restricted?.collections ?? 0} restricted collections`}</small></a>
        <a href="#corpus-exports-title"><span>EVIDENCE STATES</span><strong>{catalog?.evidence_classes?.length ?? "—"} states</strong><small>{catalog?.evidence_classes?.join(" · ") || "vocabulary unavailable"}</small></a>
      </div>

      <div id="canonical-market-series"><MarketSeriesExplorer /></div>

      <EngineDatasetExplorer dataClasses={classes} groups={groups} engines={engines} reload={reload} />

      {resources.flows.status === "loading" && <section className="corpus-panel"><p className="corpus-loading">Sounding the BIS flow registry…</p></section>}
      {resources.flows.status === "ready" && (
        <BisFlowExplorer
          flows={resources.flows.data.flows}
          knowledgeTime={resources.flows.data.knowledge_time}
          generatedAt={resources.flows.data.generated_at}
        />
      )}

      <div className="corpus-split">
        <section className="corpus-panel" aria-labelledby="corpus-markets-title" ref={marketSection}>
          <header className="corpus-panel__head"><div><span>OBSERVED PARTITIONS</span><h2 id="corpus-markets-title">Seiche market lake</h2></div><a href={`${CORPUS_API_BASE}/v1/seiche/markets`}>Detailed JSON</a></header>
          {resources.markets.status === "idle" && (
            <div className="corpus-lazy-inventory">
              <p>Detailed file counts load only when this section approaches the viewport. The shallow catalog remains visible meanwhile.</p>
              <button type="button" onClick={() => setMarketInventoryRequested(true)}>Load detailed inventory</button>
            </div>
          )}
          {resources.markets.status === "loading" && <p className="corpus-loading">Loading bounded market catalog…</p>}
          {seiche && (
            <div className="corpus-market-grid">
              {seiche.markets.map((market) => (
                <article key={market.market}>
                  <div><strong>{market.market}</strong><Status tone={evidenceTone(seiche.evidence_class)}>{seiche.evidence_class || "unknown"}</Status></div>
                  <ul>{market.sources.map((source) => <li key={source.source}><code>{source.source}</code><span>{source.files === undefined && source.bytes === undefined ? "official source family" : `${source.files?.toLocaleString() ?? "—"} files · ${formatBytes(source.bytes)}`}</span></li>)}</ul>
                </article>
              ))}
            </div>
          )}
          {resources.markets.status === "error" && <p className="corpus-loading">Detailed market catalog unavailable; no empty-lake claim was substituted.</p>}
        </section>

        <section className="corpus-panel corpus-exports" aria-labelledby="corpus-exports-title">
          <header className="corpus-panel__head"><div><span>PUBLICATION GATE</span><h2 id="corpus-exports-title">Product exports</h2></div></header>
          {resources.exports.status === "loading" && <p className="corpus-loading">Checking export grants…</p>}
          {resources.exports.status === "ready" && resources.exports.data.exports.map((entry) => {
            const downloadPath = safeCorpusExportHref(entry.download);
            const download = downloadPath ? `${CORPUS_API_BASE}${downloadPath}` : null;
            return (
              <article key={`${entry.product}:${entry.name || "registered"}`}>
                <div><code>{entry.product}</code><Status tone={evidenceTone(entry.evidence_class)}>{entry.evidence_class || "unknown"}</Status></div>
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
