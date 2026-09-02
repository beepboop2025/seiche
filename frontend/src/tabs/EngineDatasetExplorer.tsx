import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { CORPUS_API_BASE } from "../apiBase";
import {
  acceptEngineDetail,
  filterEngineDatasets,
  mergeEnginePages,
  normalizeEngineDetail,
  normalizeEnginePage,
  type AcquiredObject,
  type EngineDataset,
  type EngineDatasetDetail,
  type EngineDatasetPage,
  type EngineRights,
  type RestrictedCollection,
  type StructuralHeader,
  type StructuralProfile,
} from "../engineCorpus";

const PAGE_SIZE = 24;

type Resource<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

interface Props {
  dataClasses: string[];
  groups: string[];
  engines: string[];
  reload: number;
}

async function fetchJson(path: string, parentSignal: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => controller.abort(parentSignal.reason);
  if (parentSignal.aborted) abort();
  else parentSignal.addEventListener("abort", abort, { once: true });
  const timeout = globalThis.setTimeout(abort, 10_000);
  try {
    const response = await fetch(`${CORPUS_API_BASE}${path}`, {
      signal: controller.signal,
      credentials: "omit",
      headers: { Accept: "application/json" },
    });
    if (!response.ok || !(response.headers.get("content-type") ?? "").includes("json")) {
      throw new Error(`corpus returned HTTP ${response.status}`);
    }
    return await response.json();
  } finally {
    globalThis.clearTimeout(timeout);
    parentSignal.removeEventListener("abort", abort);
  }
}

function messageFor(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === "AbortError") return "request timed out";
  return reason instanceof Error ? reason.message : "dataset registry is temporarily unreachable";
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

function dataClassBoundary(value: string): string {
  switch (value) {
    case "train_candidate": return "candidate only — not training approval";
    case "evaluation_only": return "evaluation use only — never training input";
    case "research_only": return "research only — never execution input";
    case "context_feature": return "context candidate — acceptance still required";
    case "outcome_label": return "label candidate — temporal review still required";
    case "entity_reference": return "reference data — no signal permission";
    default: return "classification is descriptive, not permission";
  }
}

function RightsLattice({ rights }: { rights: EngineRights }) {
  const fields: Array<[string, boolean]> = [
    ["metadata", rights.public_metadata],
    ["schema", rights.public_schema],
    ["preview", rights.public_preview_values],
    ["raw", rights.public_raw_download],
  ];
  return (
    <div className="engine-rights" aria-label={`Publication state ${rights.publication_state}`}>
      <strong>{rights.publication_state.replaceAll("_", " ")}</strong>
      <div>{fields.map(([label, allowed]) => (
        <span className={allowed ? "is-allowed" : "is-withheld"} key={label}>
          {label} {allowed ? "yes" : "no"}
        </span>
      ))}</div>
      <small>review: {rights.acquisition_review}</small>
    </div>
  );
}

function HeaderProfile({ title, header }: { title: string; header: StructuralHeader }) {
  return (
    <div className="engine-profile__header">
      <span>{title}</span>
      <strong>{header.column_count.toLocaleString()} columns</strong>
      {header.delimiter && <code>delimiter {JSON.stringify(header.delimiter)}</code>}
      <div>{header.columns_sample.map((column) => <code key={column}>{column}</code>)}</div>
    </div>
  );
}

function StructuralSounding({ profile }: { profile: StructuralProfile }) {
  return (
    <section className="engine-profile" aria-labelledby="engine-profile-title">
      <header><span>STRUCTURAL SOUNDING</span><h4 id="engine-profile-title">Shape, never sample values</h4></header>
      <dl>
        <div><dt>format</dt><dd>{profile.format}</dd></div>
        {profile.inner_table && <div><dt>inner table</dt><dd>{profile.inner_table}</dd></div>}
        {profile.archive_entry_count !== undefined && <div><dt>archive entries</dt><dd>{profile.archive_entry_count.toLocaleString()}</dd></div>}
        {profile.top_level_type && <div><dt>top-level type</dt><dd>{profile.top_level_type}</dd></div>}
        {profile.list_length !== undefined && <div><dt>list length</dt><dd>{profile.list_length.toLocaleString()}</dd></div>}
        {profile.magic && <div><dt>magic</dt><dd>{profile.magic}</dd></div>}
        {profile.boundary_magic && <div><dt>boundary</dt><dd>{profile.boundary_magic}</dd></div>}
      </dl>
      {profile.header && <HeaderProfile title="outer header" header={profile.header} />}
      {profile.inner_header && <HeaderProfile title="inner header" header={profile.inner_header} />}
      {profile.archive_entries_sample && (
        <div className="engine-profile__sample"><span>archive member names</span>{profile.archive_entries_sample.map((item) => <code key={item}>{item}</code>)}</div>
      )}
      {profile.keys_sample && (
        <div className="engine-profile__sample"><span>JSON keys</span>{profile.keys_sample.map((item) => <code key={item}>{item}</code>)}</div>
      )}
    </section>
  );
}

function Identity({ row }: { row: EngineDataset }) {
  return (
    <dl className="engine-identity">
      <div><dt>artifact</dt><dd><code>{row.artifact_id}</code></dd></div>
      {row.collection_kind === "acquired_object" && <div><dt>content sha256</dt><dd><code>{row.content_sha256}</code></dd></div>}
      {row.collection_kind === "restricted_metadata_only" && row.manifest_sha256 && <div><dt>manifest sha256</dt><dd><code>{row.manifest_sha256}</code></dd></div>}
    </dl>
  );
}

function AcquiredDetail({ row }: { row: AcquiredObject }) {
  const normalizedHref = row.normalized_records
    ? `${CORPUS_API_BASE}${row.normalized_records.href}`
    : null;
  return (
    <>
      <div className="engine-detail-grid">
        <div><span>GROUP / CLASS</span><strong>{row.group}</strong><small>{row.data_class}</small></div>
        <div><span>OBJECT</span><strong>{row.media_format.toUpperCase()}</strong><small>{row.attempt_count} attempt{row.attempt_count === 1 ? "" : "s"} · {row.recovered ? "recovered retry" : "first-pass verified"}</small></div>
        <div><span>ACQUIRED</span><strong><time dateTime={row.acquired_date}>{row.acquired_date}</time></strong><small>{row.engines.join(" · ") || "no engine binding"}</small></div>
      </div>
      <div className="engine-detail-links">
        {row.source?.page && <a href={row.source.page} rel="noreferrer">Publisher source</a>}
        {row.license?.url && <a href={row.license.url} rel="noreferrer">{row.license.name}</a>}
        {normalizedHref && <a href={normalizedHref}>Open {row.normalized_records?.flow_id} normalized records</a>}
      </div>
      {row.structural_profile
        ? <StructuralSounding profile={row.structural_profile} />
        : <p className="engine-detail-empty">No structural profile is published for this object. No schema was inferred.</p>}
    </>
  );
}

function RestrictedDetail({ row }: { row: RestrictedCollection }) {
  return (
    <>
      <div className="engine-detail-grid">
        <div><span>COLLECTION</span><strong>{row.object_count?.toLocaleString() ?? "—"} objects</strong><small>{formatBytes(row.total_bytes)}</small></div>
        <div><span>ROWS</span><strong>{row.row_count?.toLocaleString() ?? "not reported"}</strong><small>{row.formats?.join(" · ") || "formats not reported"}</small></div>
        <div><span>EVENT RANGE</span><strong>{row.event_from ?? "—"}</strong><small>through {row.event_to ?? "—"}</small></div>
      </div>
      <p className="engine-restricted-note">{row.notes || "Metadata is public; values, schema, previews, and downloads remain withheld."}</p>
      <div className="engine-detail-links">
        {row.source?.page && <a href={row.source.page} rel="noreferrer">Publisher source</a>}
        <strong>forward evidence: {row.forward_evidence_eligible === true ? "eligible" : "not eligible"}</strong>
      </div>
    </>
  );
}

function EvidenceSheet({ resource, close, sheetRef }: { resource: Resource<EngineDatasetDetail>; close: () => void; sheetRef: RefObject<HTMLElement> }) {
  if (resource.status === "idle") return null;
  return (
    <aside
      className="engine-evidence-sheet"
      aria-busy={resource.status === "loading"}
      aria-live="polite"
      aria-labelledby="engine-detail-title"
      onKeyDown={(event) => { if (event.key === "Escape") close(); }}
      ref={sheetRef}
      tabIndex={-1}
    >
      <header>
        <div><span>IMMUTABLE OBJECT EVIDENCE</span><h3 id="engine-detail-title">{resource.status === "ready" ? resource.data.dataset.dataset_id : "Dataset detail"}</h3></div>
        <button type="button" onClick={close} aria-label="Close dataset evidence">Close</button>
      </header>
      {resource.status === "loading" && <p className="corpus-loading">Verifying object identity and publication rights…</p>}
      {resource.status === "error" && <p className="corpus-page-error" role="status">{resource.message}. The previous registry page remains unchanged.</p>}
      {resource.status === "ready" && (
        <>
          <div className="engine-evidence-sheet__meta">
            <RightsLattice rights={resource.data.dataset.rights} />
            <div><span>INDEX VERIFIED</span><time dateTime={resource.data.verified_at}>{resource.data.verified_at}</time><code title={resource.data.index_sha256}>{resource.data.index_sha256.slice(0, 16)}…</code></div>
          </div>
          <Identity row={resource.data.dataset} />
          {resource.data.dataset.collection_kind === "acquired_object"
            ? <AcquiredDetail row={resource.data.dataset} />
            : <RestrictedDetail row={resource.data.dataset} />}
        </>
      )}
    </aside>
  );
}

export default function EngineDatasetExplorer({ dataClasses, groups, engines, reload }: Props) {
  const [resource, setResource] = useState<Resource<EngineDatasetPage>>({ status: "loading" });
  const [detail, setDetail] = useState<Resource<EngineDatasetDetail>>({ status: "idle" });
  const [dataClass, setDataClass] = useState("");
  const [group, setGroup] = useState("");
  const [engine, setEngine] = useState("");
  const [collectionKind, setCollectionKind] = useState("");
  const [query, setQuery] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [paginationError, setPaginationError] = useState<string | null>(null);
  const pageGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const paginationAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const detailTrigger = useRef<HTMLButtonElement | null>(null);
  const evidenceSheet = useRef<HTMLElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    const generation = pageGeneration.current + 1;
    pageGeneration.current = generation;
    paginationAbort.current?.abort();
    detailAbort.current?.abort();
    paginationAbort.current = null;
    detailAbort.current = null;
    detailGeneration.current += 1;
    setLoadingMore(false);
    setPaginationError(null);
    setDetail({ status: "idle" });
    setResource({ status: "loading" });
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
    if (dataClass) params.set("data_class", dataClass);
    if (group) params.set("group", group);
    if (engine) params.set("engine", engine);
    if (collectionKind) params.set("collection_kind", collectionKind);
    void fetchJson(`/v1/datasets?${params.toString()}`, controller.signal)
      .then((value) => normalizeEnginePage(value))
      .then((data) => {
        if (!controller.signal.aborted && pageGeneration.current === generation) {
          setResource({ status: "ready", data });
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && pageGeneration.current === generation) {
          setResource({ status: "error", message: messageFor(reason) });
        }
      });
    return () => controller.abort();
  }, [collectionKind, dataClass, engine, group, reload]);

  useEffect(() => () => {
    paginationAbort.current?.abort();
    detailAbort.current?.abort();
  }, []);

  const visible = useMemo(
    () => resource.status === "ready" ? filterEngineDatasets(resource.data.datasets, query) : [],
    [query, resource],
  );

  useEffect(() => {
    if (detail.status !== "idle") {
      evidenceSheet.current?.focus({ preventScroll: true });
      evidenceSheet.current?.scrollIntoView({ block: "nearest" });
    }
  }, [detail.status]);

  const openDetail = async (row: EngineDataset, trigger: HTMLButtonElement) => {
    if (resource.status !== "ready") return;
    detailTrigger.current = trigger;
    detailAbort.current?.abort();
    const controller = new AbortController();
    detailAbort.current = controller;
    const generation = detailGeneration.current + 1;
    detailGeneration.current = generation;
    const page = resource.data;
    setDetail({ status: "loading" });
    try {
      const raw = await fetchJson(row.detail_href, controller.signal);
      const data = acceptEngineDetail(page, normalizeEngineDetail(raw), row.artifact_id);
      if (!controller.signal.aborted && detailGeneration.current === generation) {
        setDetail({ status: "ready", data });
      }
    } catch (reason) {
      if (!controller.signal.aborted && detailGeneration.current === generation) {
        setDetail({ status: "error", message: messageFor(reason) });
      }
    } finally {
      if (detailAbort.current === controller) detailAbort.current = null;
    }
  };

  const closeDetail = () => {
    detailGeneration.current += 1;
    detailAbort.current?.abort();
    detailAbort.current = null;
    setDetail({ status: "idle" });
    detailTrigger.current?.focus();
    detailTrigger.current = null;
  };

  const loadMore = async () => {
    if (resource.status !== "ready" || !resource.data.next_cursor || loadingMore || paginationAbort.current) return;
    const controller = new AbortController();
    paginationAbort.current = controller;
    setLoadingMore(true);
    setPaginationError(null);
    const generation = pageGeneration.current;
    const expectedCursor = resource.data.next_cursor;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), cursor: expectedCursor });
    if (dataClass) params.set("data_class", dataClass);
    if (group) params.set("group", group);
    if (engine) params.set("engine", engine);
    if (collectionKind) params.set("collection_kind", collectionKind);
    try {
      const incoming = normalizeEnginePage(await fetchJson(`/v1/datasets?${params.toString()}`, controller.signal));
      if (controller.signal.aborted || pageGeneration.current !== generation) return;
      setResource((current) => {
        if (
          pageGeneration.current !== generation
          || current.status !== "ready"
          || current.data.next_cursor !== expectedCursor
        ) return current;
        return { status: "ready", data: mergeEnginePages(current.data, incoming) };
      });
    } catch (reason) {
      if (!controller.signal.aborted && pageGeneration.current === generation) {
        setPaginationError(`Next page failed: ${messageFor(reason)}. Previously loaded objects remain valid.`);
      }
    } finally {
      if (paginationAbort.current === controller) {
        paginationAbort.current = null;
        if (pageGeneration.current === generation) setLoadingMore(false);
      }
    }
  };

  return (
    <section className="corpus-panel" aria-labelledby="corpus-datasets-title">
      <header className="corpus-panel__head">
        <div><span>ATTESTED EVIDENCE OBJECTS</span><h2 id="corpus-datasets-title">Dataset registry</h2></div>
        <div className="corpus-dataset-controls">
          <label>Search loaded objects<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="dataset, group, engine, publisher…" /></label>
          <label>Market group<select value={group} onChange={(event) => setGroup(event.target.value)}><option value="">all groups</option>{groups.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
          <label>Engine use<select value={engine} onChange={(event) => setEngine(event.target.value)}><option value="">all engines</option>{engines.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
          <label>Data class<select value={dataClass} onChange={(event) => setDataClass(event.target.value)}><option value="">all classes</option>{dataClasses.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
          <label>Collection<select value={collectionKind} onChange={(event) => setCollectionKind(event.target.value)}><option value="">all public entries</option><option value="acquired_object">verified objects</option><option value="restricted_metadata_only">restricted + supplemental</option></select></label>
        </div>
      </header>
      {resource.status === "loading" && <p className="corpus-loading">Loading the attested public index…</p>}
      {resource.status === "error" && <p className="corpus-page-error" role="status">{resource.message}. No empty registry was substituted.</p>}
      {resource.status === "ready" && (
        <>
          <div className="engine-index-strip">
            <div><span>INDEX</span><code title={resource.data.index_sha256}>{resource.data.index_sha256.slice(0, 16)}…</code></div>
            <div><span>RELEASE</span><code>{resource.data.release_id}</code></div>
            <div><span>VERIFIED</span><time dateTime={resource.data.verified_at}>{resource.data.verified_at}</time></div>
            <div><span>FILTERED OBJECTS</span><strong>{resource.data.total.toLocaleString()}</strong></div>
          </div>
          <div className="corpus-table-wrap" role="region" aria-label="Attested dataset object ledger" tabIndex={0}>
            <table className="corpus-table engine-object-table">
              <caption>Each row is bound to the displayed public-index hash. Select a row to inspect structure and exact rights; no private path or signed URL is projected.</caption>
              <thead><tr><th>Evidence object</th><th>Class / structure</th><th>Publication boundary</th><th>Acquisition / provenance</th></tr></thead>
              <tbody>{visible.map((row) => (
                <tr key={row.artifact_id}>
                  <th scope="row"><button type="button" onClick={(event) => void openDetail(row, event.currentTarget)}><code>{row.dataset_id}</code><span>{row.collection_kind.replaceAll("_", " ")}</span><small>Inspect immutable object →</small></button></th>
                  <td>{row.collection_kind === "acquired_object" ? <><code>{row.group}</code><strong>{row.data_class}</strong><small>{row.media_format.toUpperCase()} · {row.structural_profile_available ? "structure available" : "metadata only"}</small></> : <><strong>{row.title || row.dataset_id}</strong><code>{row.data_class || "restricted"}</code><small>{row.formats?.join(" · ") || "formats withheld"}</small></>}</td>
                  <td><RightsLattice rights={row.rights} /></td>
                  <td>{row.collection_kind === "acquired_object" ? <><span><time dateTime={row.acquired_date}>{row.acquired_date}</time></span><code title={row.content_sha256}>{row.content_sha256.slice(0, 12)}…</code><small>{dataClassBoundary(row.data_class)}</small></> : <><span>{row.publisher || "publisher not reported"}</span><strong>{row.row_count === undefined ? "values withheld" : `${row.row_count.toLocaleString()} rows cataloged`}</strong><small>{row.event_from || "—"} → {row.event_to || "—"}</small></>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {visible.length === 0 && <p className="engine-detail-empty">No loaded object matches this search. Clear the search or load another page.</p>}
          <footer className="corpus-pagination">
            <span>{visible.length} of {resource.data.datasets.length} loaded objects shown · {resource.data.total.toLocaleString()} in this filtered index</span>
            {resource.data.next_cursor ? <button type="button" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load next page"}</button> : <span>end of filtered registry</span>}
          </footer>
          {paginationError && <p className="corpus-page-error" role="status">{paginationError}</p>}
        </>
      )}
      <EvidenceSheet resource={detail} close={closeDetail} sheetRef={evidenceSheet} />
    </section>
  );
}
