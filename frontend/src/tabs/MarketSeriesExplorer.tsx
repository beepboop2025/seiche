import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { API_BASE } from "../apiBase";
import {
  atlasStateTone,
  assertCompatibleMarketSeriesPage,
  buildPlotModel,
  canonicalUnitLabel,
  filterInstruments,
  filterObservations,
  mergeObservationPages,
  normalizeMarketCatalog,
  normalizeMarketSeries,
  numericSeriesForInstrument,
  roleLabel,
  safePublicSourceUrl,
  type MarketCatalog,
  type MarketCatalogItem,
  type MarketInstrument,
  type MarketObservation,
  type MarketSeries,
} from "../marketAtlas";
import "../styles-market-atlas.css";

const SERIES_PAGE_SIZE = 200;
const REQUEST_TIMEOUT_MS = 12_000;

type Resource<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

type PaginationState = "idle" | "loading" | "error";

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "The market API returned an unknown error.";
}

async function fetchApiJson(path: string, parentSignal: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  const onParentAbort = () => controller.abort(parentSignal.reason);
  if (parentSignal.aborted) onParentAbort();
  else parentSignal.addEventListener("abort", onParentAbort, { once: true });
  const timeout = globalThis.setTimeout(
    () => controller.abort(new DOMException("Market API request timed out.", "TimeoutError")),
    REQUEST_TIMEOUT_MS,
  );

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`Market API returned HTTP ${response.status} for ${path}.`);
    }
    return await response.json();
  } catch (error) {
    if (controller.signal.reason instanceof DOMException
      && controller.signal.reason.name === "TimeoutError") {
      throw new Error(`Market API did not answer within ${REQUEST_TIMEOUT_MS / 1000} seconds.`);
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
    parentSignal.removeEventListener("abort", onParentAbort);
  }
}

function Status({ value }: { value: string }) {
  return (
    <span className={`ma-state ma-state--tone-${atlasStateTone(value)}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

function formatUtc(value: string | null): string {
  if (!value) return "unavailable";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return value;
  return `${parsed.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return value;
  return parsed.toISOString().slice(0, 10);
}

function formatNumber(value: number): string {
  const magnitude = Math.abs(value);
  return new Intl.NumberFormat("en", {
    maximumFractionDigits: magnitude < 10 ? 4 : magnitude < 1_000 ? 2 : 0,
  }).format(value);
}

function Clock({ label, value }: { label: string; value: string | null }) {
  return (
    <span className="ma-clock">
      <b>{label}</b>
      {value ? <time dateTime={value} title={value}>{formatUtc(value)}</time> : <em>unavailable</em>}
    </span>
  );
}

function selectedSummary(
  catalog: Resource<MarketCatalog>,
  marketId: string | null,
): MarketCatalogItem | null {
  if (catalog.status !== "ready" || !marketId) return null;
  return catalog.data.markets.find((market) => market.market_id === marketId) ?? null;
}

function InstrumentRegister({
  instruments,
  currency,
}: {
  instruments: MarketInstrument[];
  currency: string;
}) {
  if (instruments.length === 0) {
    return (
      <div className="ma-empty ma-empty--compact">
        <strong>No instruments match these filters.</strong>
        <span>Clear the text, role, or instrument filter to reopen the register.</span>
      </div>
    );
  }
  return (
    <div className="ma-instrument-grid">
      {instruments.map((instrument) => {
        const sourceUrl = safePublicSourceUrl(instrument.source_url);
        return (
          <article className="ma-instrument" key={instrument.instrument_id}>
            <div className="ma-instrument__head">
              <div>
                <strong>{instrument.mnemonic}</strong>
                <code>{instrument.instrument_id}</code>
              </div>
              <Status value={instrument.availability} />
            </div>
            <dl>
              <div><dt>Role</dt><dd>{roleLabel(instrument.semantic_role)}</dd></div>
              <div><dt>Unit</dt><dd>{canonicalUnitLabel(instrument.canonical_unit, currency)}</dd></div>
              <div><dt>Source</dt><dd>{instrument.source_adapter}</dd></div>
              <div><dt>Publisher</dt><dd>{instrument.publisher ?? "publisher unavailable"}</dd></div>
              <div>
                <dt>Source URL</dt>
                <dd>
                  {sourceUrl
                    ? <a href={sourceUrl} rel="noreferrer">Open official source</a>
                    : <span className="ma-source-unavailable">source URL unavailable</span>}
                </dd>
              </div>
              <div><dt>Cadence</dt><dd>{instrument.expected_cadence ?? "unavailable"}</dd></div>
              <div><dt>Connector</dt><dd>{instrument.connector_classification.replaceAll("_", " ")}</dd></div>
              <div><dt>Rights</dt><dd>{instrument.redistribution_status.replaceAll("_", " ")}</dd></div>
            </dl>
          </article>
        );
      })}
    </div>
  );
}

function SeriesPlot({
  instrument,
  observations,
  currency,
}: {
  instrument: MarketInstrument | null;
  observations: readonly MarketObservation[];
  currency: string;
}) {
  const rawId = useId();
  const id = rawId.replaceAll(":", "");
  if (!instrument) {
    return (
      <div className="ma-empty ma-empty--plot">
        <strong>No public numeric transect is available.</strong>
        <span>Restricted and unavailable instruments remain in the register, but they are never plotted as zero.</span>
      </div>
    );
  }
  const points = numericSeriesForInstrument(observations, instrument.instrument_id);
  const plot = buildPlotModel(points);
  if (!plot) {
    return (
      <div className="ma-empty ma-empty--plot">
        <strong>{instrument.mnemonic} has no numeric observations in the loaded pages.</strong>
        <span>Load an older page or choose another instrument. Rejected, redacted and non-public values are excluded by design.</span>
      </div>
    );
  }
  const midValue = (plot.maxValue + plot.minValue) / 2;
  const showEveryPoint = plot.points.length <= 80;
  const titleId = `${id}-title`;
  const descId = `${id}-desc`;
  const gradientId = `${id}-water`;
  return (
    <figure className="ma-plot">
      <svg
        viewBox="0 0 720 220"
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={titleId}>{instrument.mnemonic} canonical observation transect</title>
        <desc id={descId}>
          {plot.points.length} latest-vintage numeric observations from {formatDate(plot.firstEventTime)} to {formatDate(plot.lastEventTime)}, ranging from {formatNumber(plot.observedMinValue)} to {formatNumber(plot.observedMaxValue)} {canonicalUnitLabel(instrument.canonical_unit, currency)}.
        </desc>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--ma-glacial)" stopOpacity=".24" />
            <stop offset="1" stopColor="var(--ma-cobalt)" stopOpacity=".02" />
          </linearGradient>
        </defs>
        {[18, 102, 186].map((y) => (
          <line className="ma-plot__grid" x1="58" x2="704" y1={y} y2={y} key={y} />
        ))}
        <text className="ma-plot__axis" x="50" y="22" textAnchor="end">{formatNumber(plot.maxValue)}</text>
        <text className="ma-plot__axis" x="50" y="106" textAnchor="end">{formatNumber(midValue)}</text>
        <text className="ma-plot__axis" x="50" y="190" textAnchor="end">{formatNumber(plot.minValue)}</text>
        <path className="ma-plot__area" d={plot.areaPath} fill={`url(#${gradientId})`} />
        <path className="ma-plot__line ma-plot__line--echo" d={plot.path} />
        <path className="ma-plot__line" d={plot.path} />
        {plot.points.map((point, index) => (
          showEveryPoint || index === plot.points.length - 1
            ? (
              <circle className="ma-plot__point" cx={point.x} cy={point.y} r={index === plot.points.length - 1 ? 3.5 : 2} key={`${point.eventTime}-${index}`}>
                <title>{formatUtc(point.eventTime)} · {formatNumber(point.value)} {canonicalUnitLabel(instrument.canonical_unit, currency)}</title>
              </circle>
            )
            : null
        ))}
        <text className="ma-plot__date" x="58" y="211">{formatDate(plot.firstEventTime)}</text>
        <text className="ma-plot__date" x="704" y="211" textAnchor="end">{formatDate(plot.lastEventTime)}</text>
      </svg>
      <figcaption>
        <span>{plot.points.length} plotted events</span>
        <span>{canonicalUnitLabel(instrument.canonical_unit, currency)}</span>
        <span>latest loaded vintage per event clock</span>
      </figcaption>
    </figure>
  );
}

function ObservationTable({ observations }: { observations: MarketObservation[] }) {
  if (observations.length === 0) {
    return (
      <div className="ma-empty ma-empty--compact">
        <strong>No loaded observations match these filters.</strong>
        <span>Restricted instruments can appear in the register without exposing a value row.</span>
      </div>
    );
  }
  return (
    <div className="ma-table-wrap" tabIndex={0} role="region" aria-label="Canonical observations table">
      <table className="ma-table">
        <thead>
          <tr>
            <th scope="col">Instrument</th>
            <th scope="col">Value + unit</th>
            <th scope="col">Evidence clocks</th>
            <th scope="col">Source + revision</th>
            <th scope="col">Quality · staleness · rights</th>
          </tr>
        </thead>
        <tbody>
          {observations.map((observation) => (
            <tr key={[
              observation.instrument_id,
              observation.event_time,
              observation.knowledge_time,
              observation.source,
              observation.revision_id,
            ].join("|")}>
              <td>
                <strong>{observation.instrument_id}</strong>
                <span>{roleLabel(observation.semantic_role)}</span>
              </td>
              <td className="ma-value-cell">
                {observation.value === null
                  ? <strong className="ma-withheld">unavailable</strong>
                  : <strong>{String(observation.value)}</strong>}
                <span>{canonicalUnitLabel(observation.canonical_unit, observation.currency)}</span>
                {observation.value_status ? <small>{observation.value_status.replaceAll("_", " ")}</small> : null}
                {observation.rate_compounding || observation.day_count
                  ? <small>{[observation.rate_compounding, observation.day_count].filter(Boolean).join(" · ")}</small>
                  : null}
              </td>
              <td className="ma-clocks">
                <Clock label="Event" value={observation.event_time} />
                <Clock label="Publication" value={observation.source_publication_time} />
                <Clock label="Knowledge" value={observation.knowledge_time} />
              </td>
              <td>
                <strong>{observation.source}</strong>
                <span>revision {observation.revision_id}</span>
                {observation.evidence_hash
                  ? <code title={observation.evidence_hash}>sha256:{observation.evidence_hash.slice(0, 12)}…</code>
                  : <code>evidence hash unavailable</code>}
                <small>{observation.connector_classification.replaceAll("_", " ")}</small>
              </td>
              <td className="ma-state-stack">
                <Status value={observation.quality} />
                <Status value={observation.staleness} />
                <Status value={observation.redistribution_status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MarketSeriesExplorer() {
  const [catalog, setCatalog] = useState<Resource<MarketCatalog>>({ status: "loading" });
  const [series, setSeries] = useState<Resource<MarketSeries>>({ status: "idle" });
  const [selectedMarketId, setSelectedMarketId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<string | null>(null);
  const [instrumentFilter, setInstrumentFilter] = useState<string | null>(null);
  const [plotInstrumentId, setPlotInstrumentId] = useState<string | null>(null);
  const [catalogReload, setCatalogReload] = useState(0);
  const [seriesReload, setSeriesReload] = useState(0);
  const [pagination, setPagination] = useState<PaginationState>("idle");
  const [paginationError, setPaginationError] = useState<string | null>(null);
  const paginationAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setCatalog({ status: "loading" });
    void fetchApiJson("/api/v2/markets", controller.signal)
      .then(normalizeMarketCatalog)
      .then((data) => {
        if (controller.signal.aborted) return;
        setCatalog({ status: "ready", data });
        setSelectedMarketId((current) => (
          current && data.markets.some((market) => market.market_id === current)
            ? current
            : data.markets.find((market) => market.market_id === "US-USD")?.market_id
              ?? data.markets[0]?.market_id
              ?? null
        ));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setCatalog({ status: "error", message: messageFrom(error) });
      });
    return () => controller.abort();
  }, [catalogReload]);

  useEffect(() => {
    paginationAbort.current?.abort();
    setPagination("idle");
    setPaginationError(null);
    if (!selectedMarketId) {
      setSeries({ status: "idle" });
      return undefined;
    }
    const controller = new AbortController();
    setSeries({ status: "loading" });
    const path = `/api/v2/markets/${encodeURIComponent(selectedMarketId)}/series?n=${SERIES_PAGE_SIZE}`;
    void fetchApiJson(path, controller.signal)
      .then((payload) => normalizeMarketSeries(payload, selectedMarketId))
      .then((data) => {
        if (!controller.signal.aborted) setSeries({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setSeries({ status: "error", message: messageFrom(error) });
      });
    return () => controller.abort();
  }, [selectedMarketId, seriesReload]);

  useEffect(() => () => paginationAbort.current?.abort(), []);

  const market = selectedSummary(catalog, selectedMarketId);
  const seriesData = series.status === "ready" ? series.data : null;
  const roles = useMemo(() => (
    seriesData
      ? [...new Set(seriesData.instruments.map((instrument) => instrument.semantic_role))]
          .sort((left, right) => left.localeCompare(right))
      : []
  ), [seriesData]);
  const filteredInstruments = useMemo(() => (
    seriesData ? filterInstruments(seriesData.instruments, query, role) : []
  ), [seriesData, query, role]);
  const filteredObservations = useMemo(() => (
    seriesData
      ? filterObservations(seriesData.observations, {
          query,
          role,
          instrumentId: instrumentFilter,
        })
      : []
  ), [seriesData, query, role, instrumentFilter]);
  const numericInstruments = useMemo(() => (
    seriesData
      ? filteredInstruments.filter((instrument) => (
          numericSeriesForInstrument(seriesData.observations, instrument.instrument_id).length > 0
        ))
      : []
  ), [seriesData, filteredInstruments]);

  useEffect(() => {
    if (instrumentFilter
      && !filteredInstruments.some((instrument) => instrument.instrument_id === instrumentFilter)) {
      setInstrumentFilter(null);
    }
  }, [filteredInstruments, instrumentFilter]);
  const effectivePlotId = numericInstruments.some(
    (instrument) => instrument.instrument_id === plotInstrumentId,
  )
    ? plotInstrumentId
    : numericInstruments[0]?.instrument_id ?? null;
  const plotInstrument = numericInstruments.find(
    (instrument) => instrument.instrument_id === effectivePlotId,
  ) ?? null;
  const availabilityCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const instrument of seriesData?.instruments ?? []) {
      counts.set(instrument.availability, (counts.get(instrument.availability) ?? 0) + 1);
    }
    return counts;
  }, [seriesData]);

  const onMarketChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setSelectedMarketId(event.target.value || null);
    setRole(null);
    setInstrumentFilter(null);
    setPlotInstrumentId(null);
  };

  const loadOlder = async () => {
    if (series.status !== "ready" || !series.data.next_cursor || pagination === "loading") return;
    paginationAbort.current?.abort();
    const controller = new AbortController();
    paginationAbort.current = controller;
    setPagination("loading");
    setPaginationError(null);
    const expectedMarketId = series.data.market_id;
    const cursor = series.data.next_cursor;
    const params = new URLSearchParams({ n: String(SERIES_PAGE_SIZE), cursor });
    try {
      const payload = await fetchApiJson(
        `/api/v2/markets/${encodeURIComponent(expectedMarketId)}/series?${params.toString()}`,
        controller.signal,
      );
      const olderPage = normalizeMarketSeries(payload, expectedMarketId);
      assertCompatibleMarketSeriesPage(series.data, olderPage);
      if (controller.signal.aborted) return;
      setSeries((current) => {
        if (current.status !== "ready" || current.data.market_id !== expectedMarketId) return current;
        return {
          status: "ready",
          data: {
            ...current.data,
            observations: mergeObservationPages(
              current.data.observations,
              olderPage.observations,
            ),
            next_cursor: olderPage.next_cursor,
          },
        };
      });
      setPagination("idle");
    } catch (error) {
      if (!controller.signal.aborted) {
        setPagination("error");
        setPaginationError(messageFrom(error));
      }
    } finally {
      if (paginationAbort.current === controller) paginationAbort.current = null;
    }
  };

  return (
    <section className="ma-shell" aria-labelledby="ma-title">
      <nav className="ma-strata" aria-label="Market atlas strata">
        <div className="ma-strata__line" aria-hidden="true" />
        <a href="#ma-surface"><span>000</span>catalog</a>
        <a href="#ma-transect"><span>080</span>transect</a>
        <a href="#ma-register"><span>160</span>register</a>
        <a href="#ma-observations"><span>240</span>record</a>
        <p>{seriesData?.observations.length ?? 0}<span>loaded soundings</span></p>
      </nav>

      <div className="ma-body">
        <header className="ma-hero" id="ma-surface">
          <div className="ma-hero__copy">
            <span>Canonical market atlas · indexed observations</span>
            <h1 id="ma-title">Every market, with its evidence attached.</h1>
            <p>
              Money, capital, foreign-exchange, policy and liquidity instruments share one survey plane—without erasing native units, revision history, data rights, or the three clocks that make a value knowable.
            </p>
          </div>
          <div className="ma-hero__ledger" aria-label="Current market readout">
            <span>Survey station</span>
            <strong>{market?.display_name ?? selectedMarketId ?? "catalog loading"}</strong>
            <small>{[market?.currency, market?.policy_regime?.replaceAll("_", " ")].filter(Boolean).join(" · ") || "market identity pending"}</small>
            {seriesData ? <Status value={seriesData.status} /> : null}
          </div>
        </header>

        <div className="ma-catalog-control">
          <label>
            <span>Market station</span>
            <select
              value={selectedMarketId ?? ""}
              onChange={onMarketChange}
              disabled={catalog.status !== "ready"}
            >
              <option value="">{catalog.status === "loading" ? "Sounding market catalog…" : "Select a market"}</option>
              {catalog.status === "ready" ? catalog.data.markets.map((item) => (
                <option value={item.market_id} key={item.market_id}>
                  {item.display_name} · {item.currency} · {item.market_id}
                </option>
              )) : null}
            </select>
          </label>
          <div className="ma-catalog-note">
            <span>{catalog.status === "ready" ? `${catalog.data.count} declared markets` : "Catalog state is independent from the selected series"}</span>
            <small>{catalog.status === "ready" ? catalog.data.collection_policy ?? "collection policy unavailable" : "The series can fail without hiding the catalog error."}</small>
          </div>
        </div>

        {catalog.status === "error" ? (
          <div className="ma-notice ma-notice--error" role="alert">
            <div><strong>Market catalog unavailable</strong><span>{catalog.message}</span></div>
            <button type="button" onClick={() => setCatalogReload((value) => value + 1)}>Retry catalog</button>
          </div>
        ) : null}

        {series.status === "loading" || series.status === "idle" ? (
          <div className="ma-loading" aria-busy="true" aria-live="polite">
            <span />
            <strong>{series.status === "idle" ? "Select a market station" : "Reading the indexed observation store"}</strong>
            <small>{series.status === "idle" ? "The catalog and series have separate loading states." : `Fetching the latest ${SERIES_PAGE_SIZE} canonical rows…`}</small>
          </div>
        ) : null}

        {series.status === "error" ? (
          <div className="ma-notice ma-notice--error ma-notice--series" role="alert">
            <div><strong>Selected market series unavailable</strong><span>{series.message}</span></div>
            <button type="button" onClick={() => setSeriesReload((value) => value + 1)}>Retry series</button>
          </div>
        ) : null}

        {seriesData ? (
          <>
            <section className="ma-readout" aria-label="Evidence boundary">
              <article>
                <span>Market state</span>
                <Status value={seriesData.status} />
                <small>{seriesData.instruments.length} declared public instruments · {seriesData.observations.length} rows loaded</small>
              </article>
              <article>
                <span>Event cutoff</span>
                <strong>{formatUtc(seriesData.event_cutoff)}</strong>
                <small>The latest market event in the loaded page.</small>
              </article>
              <article>
                <span>Knowledge cutoff</span>
                <strong>{formatUtc(seriesData.knowledge_cutoff)}</strong>
                <small>The latest time Seiche could know a loaded row.</small>
              </article>
              <article>
                <span>Evidence use</span>
                <Status value={seriesData.evidence_eligibility.eligible ? "ELIGIBLE" : "DATA HOLD"} />
                <small>{seriesData.evidence_eligibility.eligible ? "Current public rows pass the API evidence gate." : seriesData.evidence_eligibility.reasons[0] ?? "Evidence eligibility was not established."}</small>
              </article>
            </section>

            <div className="ma-boundaries" aria-label="Availability boundaries">
              {(availabilityCounts.get("RESTRICTED") ?? 0) > 0 ? (
                <div className="ma-notice ma-notice--restricted">
                  <div><strong>Restricted values remain closed</strong><span>{availabilityCounts.get("RESTRICTED")} instruments disclose metadata only; no redacted level is inferred or plotted.</span></div>
                </div>
              ) : null}
              {(availabilityCounts.get("DERIVED_CONTEXT") ?? 0) > 0 ? (
                <div className="ma-notice ma-notice--restricted">
                  <div><strong>Derived context is separate</strong><span>{availabilityCounts.get("DERIVED_CONTEXT")} instruments permit derived context but not raw public levels.</span></div>
                </div>
              ) : null}
              {((availabilityCounts.get("UNAVAILABLE") ?? 0) > 0 || seriesData.status === "UNAVAILABLE") ? (
                <div className="ma-notice ma-notice--unavailable">
                  <div><strong>Unavailable is not zero</strong><span>{availabilityCounts.get("UNAVAILABLE") ?? 0} declared instruments lack a current public value.</span></div>
                </div>
              ) : null}
              {((availabilityCounts.get("STALE") ?? 0) > 0 || seriesData.stale_input_count > 0) ? (
                <div className="ma-notice ma-notice--stale">
                  <div><strong>Stale observations remain visible</strong><span>{availabilityCounts.get("STALE") ?? seriesData.stale_input_count} current instrument states require age caution.</span></div>
                </div>
              ) : null}
              {seriesData.faults.map((fault) => (
                <div
                  className="ma-notice ma-notice--error"
                  key={[fault.source, fault.category, fault.finished_at].join("|")}
                >
                  <div>
                    <strong>{fault.source} · {fault.category.replaceAll("_", " ")}</strong>
                    <span>{fault.detail}</span>
                    <small>
                      {fault.next_due
                        ? `Next collection attempt ${formatUtc(fault.next_due)}`
                        : `Finished ${formatUtc(fault.finished_at)}`}
                    </small>
                  </div>
                  <Status value={fault.status} />
                </div>
              ))}
            </div>

            <section className="ma-workbench" aria-labelledby="ma-filters-title">
              <div className="ma-section-head">
                <div><span>Survey controls</span><h2 id="ma-filters-title">Filter the instrument plane</h2></div>
                <p>Filters change the register and loaded observation record; they do not change evidence eligibility.</p>
              </div>
              <div className="ma-filters">
                <label className="ma-filter--search">
                  <span>Text</span>
                  <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="SOFR, policy target, nyfed…" type="search" />
                </label>
                <label>
                  <span>Semantic role</span>
                  <select value={role ?? ""} onChange={(event) => setRole(event.target.value || null)}>
                    <option value="">All roles</option>
                    {roles.map((item) => <option value={item} key={item}>{roleLabel(item)}</option>)}
                  </select>
                </label>
                <label>
                  <span>Observation instrument</span>
                  <select value={instrumentFilter ?? ""} onChange={(event) => setInstrumentFilter(event.target.value || null)}>
                    <option value="">All matching instruments</option>
                    {filteredInstruments.map((instrument) => (
                      <option value={instrument.instrument_id} key={instrument.instrument_id}>{instrument.mnemonic} · {instrument.instrument_id}</option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="ma-clear"
                  onClick={() => { setQuery(""); setRole(null); setInstrumentFilter(null); }}
                  disabled={!query && !role && !instrumentFilter}
                >
                  Clear filters
                </button>
              </div>
            </section>

            <section className="ma-transect" id="ma-transect" aria-labelledby="ma-transect-title">
              <div className="ma-section-head ma-section-head--chart">
                <div><span>Bathymetric transect</span><h2 id="ma-transect-title">One instrument, in its native unit</h2></div>
                <label>
                  <span>Plotted instrument</span>
                  <select
                    value={effectivePlotId ?? ""}
                    onChange={(event) => setPlotInstrumentId(event.target.value || null)}
                    disabled={numericInstruments.length === 0}
                  >
                    {numericInstruments.length === 0 ? <option value="">No numeric public series</option> : null}
                    {numericInstruments.map((instrument) => (
                      <option value={instrument.instrument_id} key={instrument.instrument_id}>{instrument.mnemonic} · {canonicalUnitLabel(instrument.canonical_unit, seriesData.currency)}</option>
                    ))}
                  </select>
                </label>
              </div>
              <SeriesPlot instrument={plotInstrument} observations={seriesData.observations} currency={seriesData.currency} />
            </section>

            <section className="ma-register" id="ma-register" aria-labelledby="ma-register-title">
              <div className="ma-section-head">
                <div><span>Declared instruments</span><h2 id="ma-register-title">Rights-aware instrument register</h2></div>
                <p>{filteredInstruments.length} of {seriesData.instruments.length} instruments shown</p>
              </div>
              <InstrumentRegister instruments={filteredInstruments} currency={seriesData.currency} />
            </section>

            <section className="ma-observations" id="ma-observations" aria-labelledby="ma-observations-title">
              <div className="ma-section-head">
                <div><span>Canonical record</span><h2 id="ma-observations-title">Observation, publication and knowledge</h2></div>
                <p>{filteredObservations.length} of {seriesData.observations.length} loaded rows shown · newest event first</p>
              </div>
              <ObservationTable observations={filteredObservations} />
              <div className="ma-pagination" aria-live="polite">
                <div>
                  <strong>{seriesData.observations.length} unique observations loaded</strong>
                  <span>{seriesData.next_cursor ? `The cursor has older rows beyond this ${SERIES_PAGE_SIZE}-row sounding.` : "The indexed series has no older page."}</span>
                  {paginationError ? <small role="alert">{paginationError}</small> : null}
                </div>
                {seriesData.next_cursor ? (
                  <button type="button" onClick={() => void loadOlder()} disabled={pagination === "loading"}>
                    {pagination === "loading" ? "Loading older…" : pagination === "error" ? "Retry older page" : "Load older"}
                  </button>
                ) : <span className="ma-seabed">Series floor reached</span>}
              </div>
            </section>
          </>
        ) : null}
      </div>
    </section>
  );
}
