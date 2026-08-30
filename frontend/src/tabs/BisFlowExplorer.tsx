import { useEffect, useMemo, useRef, useState } from "react";
import { CORPUS_API_BASE } from "../apiBase";
import {
  bisAttributePairs,
  bisDimensionPairs,
  bisDomain,
  bisValue,
  filterBisFlows,
  filterBisRecords,
  mergeBisPages,
  normalizeBisPage,
  type BisDomain,
  type BisEvidenceClass,
  type BisFlowRecord,
  type BisPage,
  type BisRecord,
} from "../bisAtlas";

const PAGE_SIZE = 75;
const DOMAINS: ReadonlyArray<[BisDomain | "all", string]> = [
  ["all", "All strata"],
  ["funding", "Funding"],
  ["fx", "FX"],
  ["credit", "Credit"],
  ["markets", "Capital markets"],
  ["payments", "Payments"],
  ["macro", "Macro"],
];

interface RevisionRun {
  capture_id?: string;
  row_count?: number;
  insert_count?: number;
  revision_count?: number;
  unchanged_count?: number;
  deletion_count?: number;
  completed_at?: string;
  complete_snapshot?: boolean;
  normalized_sha256?: string;
}

interface RevisionPage {
  generated_at?: string;
  runs: RevisionRun[];
}

type LoadState =
  | { status: "idle" | "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; page: BisPage; revisions: RevisionPage | null };

interface Props {
  flows: BisFlowRecord[];
  knowledgeTime?: string;
  generatedAt?: string;
}

async function fetchJson(path: string, parentSignal: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  parentSignal.addEventListener("abort", abort, { once: true });
  const timeout = globalThis.setTimeout(abort, 12_000);
  try {
    const response = await fetch(`${CORPUS_API_BASE}${path}`, {
      signal: controller.signal,
      credentials: "omit",
      headers: { Accept: "application/json" },
    });
    const mediaType = response.headers.get("content-type") ?? "";
    if (!response.ok || !mediaType.includes("json")) {
      throw new Error(`corpus returned HTTP ${response.status}`);
    }
    return await response.json();
  } finally {
    globalThis.clearTimeout(timeout);
    parentSignal.removeEventListener("abort", abort);
  }
}

function errorMessage(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === "AbortError") return "request timed out";
  return reason instanceof Error ? reason.message : "BIS evidence is temporarily unavailable";
}

function shortClock(value: string | null | undefined): string {
  return value ? value.replace("T", " ").replace(/(\.\d+)?(Z|\+00:00)$/, "Z") : "not reported";
}

function safeHttps(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function evidenceClassName(value: BisEvidenceClass): string {
  if (value === "observed") return "corpus-status--observed";
  if (value === "restricted") return "corpus-status--restricted";
  return "corpus-status--unavailable";
}

function periodLabel(row: BisRecord): string {
  for (const field of ["source_period", "label", "period", "time_period", "TIME_PERIOD", "value"]) {
    const value = row.period[field];
    if (typeof value === "string" && value) return value;
  }
  return row.event_time ? shortClock(row.event_time) : "period not reported";
}

function isRevisionPage(value: unknown): value is RevisionPage {
  return value !== null
    && typeof value === "object"
    && Array.isArray((value as { runs?: unknown }).runs);
}

export default function BisFlowExplorer({ flows, knowledgeTime, generatedAt }: Props) {
  const initial = flows.find((flow) => flow.flow_id === "WS_GLI")?.flow_id ?? flows[0]?.flow_id ?? "";
  const [selected, setSelected] = useState(initial);
  const [domain, setDomain] = useState<BisDomain | "all">("all");
  const [flowQuery, setFlowQuery] = useState("");
  const [rowQuery, setRowQuery] = useState("");
  const [state, setState] = useState<LoadState>({ status: "idle" });
  const [loadingMore, setLoadingMore] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const pagination = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);

  useEffect(() => {
    if (!selected && flows.length > 0) setSelected(initial);
  }, [flows, initial, selected]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    const expectedFlow = selected;
    pagination.current?.abort();
    pagination.current = null;
    setLoadingMore(false);
    setPageError(null);
    setRowQuery("");
    setState({ status: "loading" });
    const flow = encodeURIComponent(expectedFlow);
    void Promise.all([
      fetchJson(`/v1/bis/records?flow_id=${flow}&limit=${PAGE_SIZE}`, controller.signal)
        .then((payload) => normalizeBisPage(payload, expectedFlow)),
      fetchJson(`/v1/bis/revisions?flow_id=${flow}&limit=12`, controller.signal)
        .then((payload) => isRevisionPage(payload) ? payload : null)
        .catch(() => null),
    ]).then(
      ([page, revisions]) => {
        if (generation === requestGeneration.current) {
          setState({ status: "ready", page, revisions });
        }
      },
      (reason: unknown) => {
        if (generation === requestGeneration.current) {
          setState({ status: "error", message: errorMessage(reason) });
        }
      },
    );
    return () => controller.abort();
  }, [selected]);

  const visibleFlows = useMemo(
    () => filterBisFlows(flows, domain, flowQuery),
    [flows, domain, flowQuery],
  );
  const selectedFlow = flows.find((flow) => flow.flow_id === selected);
  const visibleRows = state.status === "ready"
    ? filterBisRecords(state.page.records, rowQuery)
    : [];

  const loadMore = async () => {
    if (state.status !== "ready" || !state.page.next_cursor || loadingMore) return;
    const controller = new AbortController();
    pagination.current?.abort();
    pagination.current = controller;
    const expectedFlow = state.page.flow_id;
    const expectedArtifact = state.page.artifact_sha256;
    const generation = requestGeneration.current;
    setLoadingMore(true);
    setPageError(null);
    const query = new URLSearchParams({
      flow_id: expectedFlow,
      limit: String(PAGE_SIZE),
      cursor: state.page.next_cursor,
    });
    try {
      const payload = await fetchJson(`/v1/bis/records?${query}`, controller.signal);
      const next = normalizeBisPage(payload, expectedFlow);
      if (generation !== requestGeneration.current || selected !== expectedFlow) return;
      setState((current) => {
        if (
          current.status !== "ready"
          || current.page.flow_id !== expectedFlow
          || current.page.artifact_sha256 !== expectedArtifact
        ) return current;
        return {
          status: "ready",
          revisions: current.revisions,
          page: mergeBisPages(current.page, next),
        };
      });
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setPageError(`Older records unavailable: ${errorMessage(reason)}. Loaded evidence remains visible.`);
      }
    } finally {
      if (pagination.current === controller) {
        pagination.current = null;
        setLoadingMore(false);
      }
    }
  };

  return (
    <section className="corpus-panel bis-explorer" aria-labelledby="bis-observations-title">
      <header className="corpus-panel__head bis-explorer__head">
        <div>
          <span>BULK CAPITAL + MONEY MARKET RECORDS</span>
          <h2 id="bis-observations-title">BIS full-record soundings</h2>
          <p>Inspect normalized series identities, dimensions, values, revisions, source attribution, and all evidence clocks.</p>
        </div>
        <div className="bis-explorer__clocks">
          <small>inventory knowledge <time>{shortClock(knowledgeTime)}</time></small>
          <small>flow map generated <time>{shortClock(generatedAt)}</time></small>
        </div>
      </header>

      <div className="bis-explorer__layout">
        <aside className="bis-flow-rail" aria-label="BIS flow selector">
          <label>
            Find a flow
            <input value={flowQuery} onChange={(event) => setFlowQuery(event.target.value)} placeholder="credit, FX, derivatives…" />
          </label>
          <div className="bis-domain-tabs" role="group" aria-label="Filter flows by market stratum">
            {DOMAINS.map(([value, label]) => (
              <button
                type="button"
                key={value}
                className={domain === value ? "active" : ""}
                aria-pressed={domain === value}
                onClick={() => setDomain(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="bis-flow-list">
            {visibleFlows.map((flow) => (
              <button
                type="button"
                key={flow.flow_id}
                className={flow.flow_id === selected ? "active" : ""}
                aria-pressed={flow.flow_id === selected}
                aria-current={flow.flow_id === selected ? "true" : undefined}
                onClick={() => setSelected(flow.flow_id)}
              >
                <span>{bisDomain(flow)}</span>
                <code>{flow.flow_id}</code>
                <strong>{flow.name ?? flow.topic ?? "Unnamed flow"}</strong>
                <small>tier {flow.priority_tier ?? "—"} · Seiche {flow.product_scores?.seiche ?? "—"}/100</small>
              </button>
            ))}
            {visibleFlows.length === 0 && <p>No registered flow matches this filter.</p>}
          </div>
        </aside>

        <div className="bis-reading-sheet">
          <div className="bis-reading-sheet__title">
            <div>
              <span>{selectedFlow ? bisDomain(selectedFlow) : "flow"}</span>
              <h3>{selectedFlow?.name ?? selectedFlow?.topic ?? (selected || "Select a flow")}</h3>
              <p>{selectedFlow?.topic}</p>
            </div>
            {selectedFlow && <a href={`${CORPUS_API_BASE}/v1/bis/records?flow_id=${encodeURIComponent(selectedFlow.flow_id)}&limit=${PAGE_SIZE}`}>Exact JSON</a>}
          </div>

          {selectedFlow?.cautions?.map((caution) => <p className="bis-caution" key={caution}>Caution: {caution}</p>)}
          {state.status === "loading" && <p className="corpus-loading">Opening immutable full-record shards…</p>}
          {state.status === "error" && <div className="bis-unavailable" role="status"><strong>UNAVAILABLE</strong><p>{state.message}. No empty or zero series was substituted.</p></div>}
          {state.status === "ready" && (
            <>
              <div className="bis-rights-strip">
                <span className={`corpus-status ${evidenceClassName(state.page.evidence_class)}`}>{state.page.evidence_class}</span>
                <code>{state.page.rights.usage_class}</code>
                <span>values {state.page.rights.public_values ? "public" : "withheld"}</span>
                <span>rights checked {shortClock(state.page.rights.knowledge_time)}</span>
                {safeHttps(state.page.rights.license_url) && <a href={safeHttps(state.page.rights.license_url)!}>Terms</a>}
              </div>

              <div className="bis-artifact-strip" aria-label="Selected immutable BIS artifact">
                <span>artifact built <time>{shortClock(state.page.artifact_generated_at)}</time></span>
                <span>artifact knowledge <time>{shortClock(state.page.artifact_knowledge_time)}</time></span>
                <span>serving shards <time>{shortClock(state.page.serving_generated_at)}</time></span>
                {state.page.artifact_sha256 && <code title={state.page.artifact_sha256}>sha256 {state.page.artifact_sha256.slice(0, 14)}…</code>}
              </div>

              {state.revisions && state.revisions.runs.length > 0 && (
                <div className="bis-revision-transect" aria-label="Recent normalization runs">
                  {state.revisions.runs.map((run, index) => (
                    <article key={run.capture_id ?? `${run.completed_at}:${index}`}>
                      <span>{index === 0 ? "latest" : `−${index}`}</span>
                      <strong>{run.row_count?.toLocaleString() ?? "—"} rows</strong>
                      <small>{run.insert_count ?? 0} new · {run.revision_count ?? 0} revised · {run.deletion_count ?? 0} deleted</small>
                      <time>{shortClock(run.completed_at)}</time>
                      {run.normalized_sha256 && <code title={run.normalized_sha256}>{run.normalized_sha256.slice(0, 10)}…</code>}
                    </article>
                  ))}
                </div>
              )}

              <label className="bis-row-search">
                Search loaded full records
                <input value={rowQuery} onChange={(event) => setRowQuery(event.target.value)} placeholder="series, country, sector, currency, value…" />
                <small>{visibleRows.length} of {state.page.records.length} loaded records shown</small>
              </label>

              <div className="corpus-table-wrap" role="region" aria-label="BIS full-record ledger" tabIndex={0}>
                <table className="corpus-table bis-observation-table bis-record-table">
                  <caption>Exact published value text is retained. Event, publication, ingestion, and revision clocks remain separate.</caption>
                  <thead><tr><th>Series + dimensions</th><th>Value + period</th><th>Evidence clocks</th><th>Revision + source</th></tr></thead>
                  <tbody>
                    {visibleRows.map((row) => {
                      const sourceUrl = safeHttps(row.source.url);
                      const attributes = bisAttributePairs(row);
                      return (
                        <tr key={row.logical_id}>
                          <th scope="row">
                            <code title={row.series_key}>{row.series_key}</code>
                            <strong>{row.flow_name}</strong>
                            <dl className="bis-dimensions">
                              {bisDimensionPairs(row).map((dimension) => (
                                <div key={dimension.code}>
                                  <dt>{dimension.code}</dt>
                                  <dd>{dimension.label}<code>{dimension.value}</code></dd>
                                </div>
                              ))}
                            </dl>
                            {attributes.length > 0 && (
                              <details className="bis-attributes">
                                <summary>{attributes.length} published attributes</summary>
                                <dl>
                                  {attributes.map((attribute) => (
                                    <div key={attribute.code}>
                                      <dt>{attribute.code}</dt>
                                      <dd>{attribute.label}<code>{attribute.value}</code></dd>
                                    </div>
                                  ))}
                                </dl>
                              </details>
                            )}
                            <code title={row.logical_id}>{row.logical_id.slice(0, 22)}…</code>
                          </th>
                          <td>
                            <strong>{bisValue(row)}</strong>
                            <span>{periodLabel(row)}</span>
                            <small>action {row.action} · {row.format} · flow v{row.flow_version}</small>
                          </td>
                          <td>
                            <small>event <time>{shortClock(row.event_time)}</time></small>
                            <small>{row.event_time_basis ?? "event basis not reported"}</small>
                            {row.scheduled_release_time && <small>scheduled <time>{shortClock(row.scheduled_release_time)}</time></small>}
                            <small>first known <time>{shortClock(row.first_knowledge_time)}</time></small>
                            <small>current knowledge <time>{shortClock(row.knowledge_time)}</time></small>
                            <small>{row.knowledge_time_basis}</small>
                            <small>source capture <time>{shortClock(row.source.capture_knowledge_time)}</time></small>
                          </td>
                          <td>
                            <span>revision {row.revision_number}</span>
                            <span className={`corpus-status ${evidenceClassName(row.evidence_class)}`}>{row.evidence_class}</span>
                            {sourceUrl
                              ? <a href={sourceUrl}>{row.source.publisher}</a>
                              : <span>{row.source.publisher}</span>}
                            <small>capture {row.source.capture_id} · row {row.source.source_row_number}</small>
                            <small>{row.as_of_rule}</small>
                            <small>historical vintage reconstructed: {row.historical_vintage_reconstructed ? "yes" : "no"}</small>
                            <code title={row.semantic_sha256}>semantic {row.semantic_sha256.slice(0, 10)}…</code>
                            <code title={row.source.capture_sha256}>capture {row.source.capture_sha256.slice(0, 10)}…</code>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {visibleRows.length === 0 && <p className="bis-empty">No loaded record matches this search. Clear the search or load another page.</p>}
              <footer className="corpus-pagination">
                <span>response generated {shortClock(state.page.generated_at)}</span>
                {state.page.next_cursor
                  ? <button type="button" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load next immutable shard page"}</button>
                  : <span>end of flow snapshot</span>}
              </footer>
              {pageError && <p className="corpus-page-error" role="status">{pageError}</p>}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
