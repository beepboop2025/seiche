import { useEffect, useMemo, useState } from "react";
import Chart from "../Chart";
import LiveMarket from "../LiveMarket";
import { API_BASE } from "../apiBase";
import { Any, fmt } from "../lib";
import { P } from "../palette";
import "../styles-editorial.css";

type Dispatch = { slug: string; title: string; date: string; summary: string; tag?: string };

const display = (value: string | undefined) => (value || "unknown").replaceAll("_", " ");

function fallbackEditorial(snap: Any): Any {
  const engines = snap.engines ?? {};
  const composite = engines.composite ?? {};
  const tell = snap.deep?.tell ?? {};
  const top = (composite.decomposition ?? [])[0] ?? {};
  const spread = engines.kink?.observed_spread_now_bp;
  const thesis = top.component === "weather" && top.saturated && spread < 0
    ? "The calendar is carrying the strain call; the price of overnight cash still says abundance."
    : tell.tell >= 25
      ? "The plumbing looks tighter than markets price, but divergence is a warning to investigate, not proof of a squeeze."
      : `${display(top.component)} is the largest reason the board is not calm; the rest of the tape is mixed.`;
  const evidence = [
    engines.ledger?.ok && { label: "Balance-sheet identity", claim: engines.ledger.letter_line, asof: engines.ledger.asof, source: "Federal Reserve H.4.1" },
    engines.officialbid?.ok && { label: "Official-sector footprint", claim: engines.officialbid.letter_line, asof: engines.officialbid.asof, source: "Federal Reserve custody and foreign RRP" },
    engines.kink?.ok && {
      label: "Reserve-demand curve",
      claim: `The fitted kink is $${fmt(engines.kink.kink_reserves_b, 0)}B; observed SOFR minus IORB is ${engines.kink.observed_spread_now_bp > 0 ? "+" : ""}${fmt(engines.kink.observed_spread_now_bp)}bp.`,
      asof: engines.kink.asof,
      source: "FRED and NY Fed secured rates",
    },
  ].filter(Boolean);
  return {
    thesis,
    standfirst: `The board reads ${fmt(composite.value, 0)} out of 100, ${composite.regime}; ${display(top.component)} contributes ${fmt(top.contribution)} points.`,
    confidence: snap.faults?.length ? "low" : "guarded",
    confidence_note: "The structural read is useful, but current-market confirmation is not yet broad.",
    dominant_driver: { engine: top.component, label: display(top.component), ...top },
    evidence,
    countercase: spread < 0 ? [{ claim: `SOFR remains ${fmt(Math.abs(spread))}bp below IORB, a present-tense abundance signal.`, asof: engines.kink?.asof }] : [],
    watch: (snap.calendar?.crunch_windows ?? []).slice(0, 4).map((row: Any) => ({ date: row.date, label: row.reason, ...row })),
  };
}

function Evidence({ rows }: { rows: Any[] }) {
  return (
    <div className="today-evidence">
      {rows.map((row, index) => (
        <article className="today-evidence__row" key={`${row.engine ?? row.label}-${index}`}>
          <div className="today-evidence__no">0{index + 1}</div>
          <div>
            <h3>{row.label}</h3>
            <p>{row.claim}</p>
            <div className="today-source">{row.source ?? "Seiche point-in-time record"}{row.asof ? ` · as of ${row.asof}` : ""}</div>
          </div>
        </article>
      ))}
    </div>
  );
}

export default function Today({ snap, live }: { snap: Any; live: boolean }) {
  const editorial = snap.editorial?.thesis ? snap.editorial : fallbackEditorial(snap);
  const [latest, setLatest] = useState<Dispatch | null>(null);
  useEffect(() => {
    let mounted = true;
    fetch("dispatches/index.json")
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((rows: Dispatch[]) => { if (mounted) setLatest(rows[0] ?? null); })
      .catch(() => undefined);
    return () => { mounted = false; };
  }, []);

  const history = useMemo(() => {
    const rows = snap.deep?.history?.ok ? snap.deep.history.series ?? [] : [];
    return rows.map((row: Any[]) => [row[0], row[1]]);
  }, [snap]);
  const comp = snap.engines?.composite ?? {};
  const quality = snap.data_quality ?? {};

  return (
    <div className="today">
      <header className="today-hero">
        <div className="today-dateline">
          <span>THE DAILY READ</span>
          <span>{String(snap.generated_at ?? "").slice(0, 10)}</span>
          <span className={live ? "today-live" : "today-snapshot"}>{live ? "LIVE API" : "PUBLISHED SNAPSHOT"}</span>
        </div>
        <div className="today-hero__grid">
          <div>
            <h1>{editorial.thesis}</h1>
            <p className="today-standfirst">{editorial.standfirst}</p>
            <a
              className="today-follow"
              href="https://t.me/seiche_desk_bot?start=seiche_home_hero"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span>Get the 11:30 UTC daily letter →</span>
              <small>Pre-US-open · sources attached · /stop any time</small>
            </a>
          </div>
          <aside className="today-reading" aria-label="Current Seiche reading">
            <div className="today-reading__value">{fmt(comp.value, 0)}</div>
            <div><span className={`regime ${comp.regime}`}>{comp.regime}</span></div>
            <div className="today-reading__meta">coverage {fmt(comp.coverage_pct, 0)}%</div>
            <div className={`conviction conviction--${editorial.confidence}`}>{editorial.confidence} conviction</div>
          </aside>
        </div>
        <div className="today-conviction"><b>Why that conviction:</b> {editorial.confidence_note}</div>
      </header>

      <section className="today-scope" aria-labelledby="today-scope-title">
        <div className="today-scope__copy">
          <span>MARKET PACKS V2 · CURRENT STATUS</span>
          <h2 id="today-scope-title">Current v2 scope</h2>
          <p>
            The primary live Seiche signal remains US dollar funding. The v2 catalog
            exposes 10 monetary-area packs. US-USD is VALIDATING; the 9 non-US packs
            are REFERENCE context only. Global Tide is currently UNAVAILABLE.
          </p>
          <div className="today-scope__links">
            <a href={`${API_BASE}/api/v2/markets`}>Open the market catalog →</a>
            <a href={`${API_BASE}/api/v2/global/tide`}>Inspect Global Tide status →</a>
          </div>
        </div>
        <dl className="today-scope__status">
          <div><dt>CATALOG</dt><dd><b>10</b><span>monetary-area packs</span></dd></div>
          <div><dt>US-USD</dt><dd><b>VALIDATING</b><span>primary USD signal</span></dd></div>
          <div><dt>NON-US</dt><dd><b>9 REFERENCE</b><span>context only</span></dd></div>
          <div><dt>GLOBAL TIDE</dt><dd><b>UNAVAILABLE</b><span>no reading published</span></dd></div>
        </dl>
      </section>

      <LiveMarket />

      <section className="today-section today-section--evidence">
        <div className="today-section__head">
          <div><span>01</span><h2>The evidence carrying the call</h2></div>
          <p>Stocks, flows, counterparties and dates. Each claim keeps its publication clock.</p>
        </div>
        <Evidence rows={editorial.evidence ?? []} />
      </section>

      {history.length > 10 && (
        <section className="today-section today-chart">
          <div className="today-section__head">
            <div><span>02</span><h2>Where today's reading sits</h2></div>
            <p>The historical light index is a comparable record, not a retrofitted copy of today's richer board.</p>
          </div>
          <Chart
            rows={history}
            series={[{ label: "Seiche-lite", color: P.accent }]}
            height={260}
            yLabel="stress index · 0–100"
            refLine={{ value: 60, color: P.strain, label: "STRESS threshold" }}
            source="Seiche point-in-time light index · public macro inputs"
            asOf={history[history.length - 1]?.[0] as string}
            note={`The history excludes live-only engines. Today's full index is ${fmt(comp.value, 1)}; the chart is used for historical context, not to imply the two measures are identical.`}
          />
        </section>
      )}

      <section className="today-columns">
        <div className="today-section today-counter">
          <div className="today-section__head">
            <div><span>03</span><h2>The case against us</h2></div>
          </div>
          {(editorial.countercase ?? []).length ? editorial.countercase.map((row: Any, index: number) => (
            <article key={index}>
              <p>{row.claim}</p>
              <div className="today-source">{row.source}{row.asof ? ` · as of ${row.asof}` : ""}</div>
            </article>
          )) : <p className="today-muted">No independent counter-signal is available. That absence lowers confidence; it does not strengthen the call.</p>}
        </div>

        <div className="today-section today-watch">
          <div className="today-section__head">
            <div><span>04</span><h2>Dates that can change the read</h2></div>
          </div>
          {(editorial.watch ?? []).map((row: Any) => (
            <div className="today-watch__row" key={`${row.date}-${row.label}`}>
              <time>{row.date}</time>
              <div>
                <p>{row.label}</p>
                {row.worst_case_reserves_b != null && <span>worst-case reserves ${fmt(row.worst_case_reserves_b, 0)}B</span>}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="today-section today-dispatch">
        <div className="today-section__head">
          <div><span>05</span><h2>From the dispatch desk</h2></div>
          <a href="#dispatches">archive →</a>
        </div>
        {latest ? (
          <a className="today-dispatch__story" href={`#dispatches/${latest.slug}`}>
            <div><span>{latest.date}</span><span>{latest.tag}</span></div>
            <h3>{latest.title}</h3>
            <p>{latest.summary}</p>
            <b>Read the frozen point-in-time letter →</b>
          </a>
        ) : <div className="today-muted">Loading the latest frozen letter…</div>}
      </section>

      <footer className="today-method">
        <div>
          <span>DATA CONTRACT</span>
          <b>{quality.source_count ?? snap.provenance?.length ?? "—"} source series</b>
          <b>{quality.fresh_share_pct != null ? `${fmt(quality.fresh_share_pct, 1)}% of dated active sources fresh` : "native lags shown"}</b>
          {quality.unclassified_source_count > 0 && <b>{quality.unclassified_source_count} table feeds carry fetch clocks only</b>}
        </div>
        <p>{quality.publication_note ?? "Official series retain their native publication lags; absence is never imputed as calm."}</p>
        <a href="/methodology">Methods, sources and changelog →</a>
      </footer>
    </div>
  );
}
