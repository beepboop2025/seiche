import type { CSSProperties } from "react";
import Chart, { type ChartSeries } from "../Chart";
import { Any, fmt } from "../lib";
import "../styles-oil-structure.css";

const C = {
  crude: "#d7a85e",
  dollar: "#879ed8",
  refinery: "#6fb7ac",
  inr: "#c2a5f4",
  muted: "#787f95",
};

const CUSHING_SERIES: ChartSeries[] = [
  { label: "Cushing commercial crude stocks", color: C.crude, fill: "rgba(215,168,94,.08)" },
];
const SPREAD_SERIES: ChartSeries[] = [
  { label: "Brent − WTI", color: C.dollar },
  { label: "5-observation average", color: C.crude, dash: [5, 4] },
];
const ZERO_LINE = { value: 0, color: C.muted, label: "WTI = Brent" };

const SOURCES = {
  cushing: "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=W_EPC0_SAX_YCUOK_MBBL&f=W",
  capacity: "https://www.eia.gov/petroleum/storagecapacity/",
  tankBottoms: "https://www.eia.gov/todayinenergy/detail.php?id=67866",
  wti: "https://www.cmegroup.com/education/courses/introduction-to-crude-oil/crude-oil-fundamentals/delivery-of-wti-futures",
  brent: "https://www.ice.com/products/219",
  brentBasket: "https://www.ice.com/publicdocs/ICE_MIdland_WTI_AGC_HOU_Presentation_COQA.pdf",
  security: "https://www.eia.gov/outlooks/steo/report/energysecurity/article.php",
  india: "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2238525&lang=1&reg=3",
  indiaDependence: "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202675912001.pdf",
  isprl: "https://www.isprlindia.com/downloads/annual-reports/Annual_Report_Final_2025_Revised_English.pdf",
};

function signed(value: unknown, digits = 1, suffix = ""): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number > 0 ? "+" : ""}${fmt(number, digits)}${suffix}`;
}

function Evidence({ children, tone = "observed" }: { children: React.ReactNode; tone?: string }) {
  return <span className={`os-evidence os-evidence--${tone}`}>{children}</span>;
}

function SourceLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <a className="os-source-link" href={href} target="_blank" rel="noreferrer">{children}<span aria-hidden="true">↗</span></a>;
}

function TankGeometry({ structure, live }: { structure: Any; live: Any }) {
  const working = Number(structure?.working_capacity_m_bbl);
  const stocks = Number(live?.stocks_m_bbl);
  const reference = Number(structure?.stress_reference_m_bbl);
  const fill = Number.isFinite(stocks) && Number.isFinite(working) && working > 0
    ? Math.max(0, Math.min(100, stocks / working * 100))
    : 0;
  const stress = Number.isFinite(reference) && Number.isFinite(working) && working > 0
    ? Math.max(0, Math.min(100, reference / working * 100))
    : 0;
  const tankStyle = { "--tank-fill": `${fill}%`, "--tank-stress": `${stress}%` } as CSSProperties;

  return (
    <div className="os-tank-scene">
      <div className="os-tank" style={tankStyle} aria-label={`Cushing stocks fill ${fmt(fill, 1)} percent of the last official working-capacity reference`}>
        <div className="os-tank__ribs" aria-hidden="true" />
        <div className="os-tank__liquid" aria-hidden="true" />
        <div className="os-tank__stress"><span>20m reference</span></div>
      </div>
      <div className="os-tank-scale" aria-hidden="true">
        <span>{fmt(working, 1)}m</span><i /><i /><i /><span>0</span>
      </div>
      <div className="os-tank-reading">
        <strong>{fmt(stocks, 3)}m</strong>
        <span>barrels · {fmt(live?.fill_of_last_working_capacity_pct, 1)}% of last working capacity</span>
        <small>weekly observation through {live?.asof ?? "—"}</small>
      </div>
    </div>
  );
}

function FleetGeometry() {
  return (
    <div className="os-fleet" role="img" aria-label="A cargo-based benchmark connected to multiple seaborne loading programmes and routes">
      {["BFOET", "WTI MIDLAND", "OPEN WATER"].map((label, index) => (
        <div className="os-fleet__lane" key={label}>
          <span>{label}</span>
          <i className={`os-ship os-ship--${index + 1}`} aria-hidden="true"><b /></i>
        </div>
      ))}
      <div className="os-fleet__reading">
        <strong>cargo pool</strong>
        <span>loading programmes + the ocean</span>
        <small>no single equivalent weekly hub-stock print</small>
      </div>
    </div>
  );
}

function DeliveryGeometry({ structure, live }: { structure: Any; live: Any }) {
  const architecture = structure?.benchmark_architecture ?? [];
  const wti = architecture.find((item: Any) => item.benchmark === "WTI") ?? {};
  const brent = architecture.find((item: Any) => item.benchmark === "Brent") ?? {};
  return (
    <div className="os-geometry">
      <article className="os-geometry__side os-geometry__side--wti">
        <div className="os-geometry__head">
          <div><span>WTI / INLAND DELIVERY</span><h3>A claim on a place</h3></div>
          <Evidence>WEEKLY OBSERVED</Evidence>
        </div>
        <TankGeometry structure={structure?.cushing} live={live?.cushing} />
        <dl>
          <div><dt>contract</dt><dd>{wti.settlement}</dd></div>
          <div><dt>release valve</dt><dd>{wti.release_valve}</dd></div>
        </dl>
        <SourceLink href={SOURCES.wti}>CME delivery mechanics</SourceLink>
      </article>

      <div className="os-geometry__hinge" aria-hidden="true">
        <span>DELIVERY<br />GEOMETRY</span><i>≠</i>
      </div>

      <article className="os-geometry__side os-geometry__side--brent">
        <div className="os-geometry__head">
          <div><span>BRENT / WATERBORNE COMPLEX</span><h3>A claim on a cargo market</h3></div>
          <Evidence tone="reference">CONTRACT DESIGN</Evidence>
        </div>
        <FleetGeometry />
        <dl>
          <div><dt>contract</dt><dd>{brent.settlement}</dd></div>
          <div><dt>basket</dt><dd>{brent.basket}</dd></div>
        </dl>
        <div className="os-link-pair"><SourceLink href={SOURCES.brent}>ICE contract</SourceLink><SourceLink href={SOURCES.brentBasket}>2023 basket addition</SourceLink></div>
      </article>
    </div>
  );
}

function ChokepointBars({ data }: { data: Any }) {
  const rows = (data?.rows ?? []) as Any[];
  const max = Math.max(1, ...rows.flatMap((row) => [Number(row.q4_2025_mbd) || 0, Number(row.q1_2026_mbd) || 0]));
  return (
    <article className="os-chokepoints">
      <div className="os-panel-head">
        <div>
          <span>PHYSICAL FLOW CONTROL</span>
          <h3>Where barrels had to pass</h3>
        </div>
        <div className="os-panel-head__right"><Evidence>QUARTERLY ESTIMATE</Evidence><small>released {data?.release_date}</small></div>
      </div>
      <div className="os-choke-legend"><span><i className="prior" />4Q25</span><span><i className="current" />1Q26</span><b>million bbl / day</b></div>
      <div className="os-choke-chart" role="img" aria-label="EIA estimated oil flows through world chokepoints in fourth-quarter 2025 and first-quarter 2026">
        {rows.map((row) => (
          <div className="os-choke-row" key={row.name}>
            <div className="os-choke-row__label"><strong>{row.name}</strong><span>{row.kind}</span></div>
            <div className="os-choke-row__bars">
              <div><i className="prior" style={{ width: `${Number(row.q4_2025_mbd) / max * 100}%` }} /><span>{fmt(row.q4_2025_mbd, 1)}</span></div>
              <div><i className="current" style={{ width: `${Number(row.q1_2026_mbd) / max * 100}%` }} /><span>{fmt(row.q1_2026_mbd, 1)}</span></div>
            </div>
          </div>
        ))}
      </div>
      <div className="os-uncertainty">
        <Evidence tone="warning">NO LIVE TRANSIT CLAIM</Evidence>
        <p>{data?.quality_note}. Values can overlap across routes, so they must not be added together.</p>
      </div>
      <details className="os-data-table">
        <summary>Inspect chokepoint data</summary>
        <table className="mini"><thead><tr><th>route</th><th>4Q25</th><th>1Q26</th></tr></thead><tbody>
          {rows.map((row) => <tr key={row.name}><td>{row.name}</td><td className="num">{fmt(row.q4_2025_mbd, 1)}</td><td className="num">{fmt(row.q1_2026_mbd, 1)}</td></tr>)}
        </tbody></table>
      </details>
      <SourceLink href={SOURCES.security}>EIA Global Energy Security Data</SourceLink>
    </article>
  );
}

function Transmission({ items }: { items: string[] }) {
  return (
    <div className="os-transmission">
      <div className="os-transmission__label"><span>TRANSMISSION ORDER</span><strong>Constraint becomes price</strong></div>
      <ol>{items.map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item}</strong>{index < items.length - 1 && <i aria-hidden="true">→</i>}</li>)}</ol>
    </div>
  );
}

function ControlStack({ rows }: { rows: Any[] }) {
  return (
    <article className="os-control-stack">
      <div className="os-panel-head"><div><span>CONTROL STACK</span><h3>Different clocks, different power</h3></div><Evidence tone="interpretive">ANALYTICAL ORDER</Evidence></div>
      <ol>{rows.map((row, index) => <li key={row.layer}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{row.layer}</strong><p>{row.nodes}</p></div><small>{row.status}</small></li>)}</ol>
      <p className="os-panel-note">This is a transmission taxonomy, not a measured league table. Cushing can dominate WTI basis while remaining downstream of a global freight shock.</p>
    </article>
  );
}

function HubTaxonomy({ rows }: { rows: Any[] }) {
  return (
    <article className="os-hubs">
      <div className="os-panel-head"><div><span>COMPARABLE HUBS</span><h3>Classify by constraint</h3></div><Evidence tone="interpretive">STRUCTURE</Evidence></div>
      <div className="os-hub-list">{rows.map((row, index) => <div key={row.type}>
        <span>{String(index + 1).padStart(2, "0")}</span><h4>{row.type}</h4><strong>{row.examples.join(" · ")}</strong><p>{row.mechanism}</p>
      </div>)}</div>
      <p className="os-panel-note">Waterborne benchmarks are not squeeze-proof; their release valve is broader. Inland delivery concentrates the constraint in fewer tanks and pipes.</p>
    </article>
  );
}

function IndiaLedger({ data }: { data: Any }) {
  const current = Number(data?.non_hormuz_crude_routing_pct) || 0;
  const prior = Number(data?.prior_non_hormuz_crude_routing_pct) || 0;
  return (
    <article className="os-india">
      <div className="os-panel-head"><div><span>INDIA / EXPOSURE LEDGER</span><h3>Time bought, dependence retained</h3></div><Evidence tone="reference">OFFICIAL RELEASES</Evidence></div>
      <div className="os-india-route">
        <div><span>crude routed outside Hormuz</span><strong>{fmt(prior, 0)}% <i>→</i> {fmt(current, 0)}%</strong></div>
        <div className="os-india-route__track" aria-label={`Non-Hormuz crude routing increased from ${prior} to ${current} percent`}><i style={{ width: `${prior}%` }} /><b style={{ width: `${current}%` }} /></div>
      </div>
      <div className="os-india-metrics">
        <div><strong>{fmt(data?.crude_import_dependence_pct, 1)}%</strong><span>crude import dependence</span></div>
        <div><strong>{fmt(data?.lpg_imports_via_hormuz_pct, 0)}%</strong><span>of LPG imports via Hormuz</span></div>
        <div><strong>{fmt(data?.strategic_inventory_m_bbl, 0)}m</strong><span>estimated strategic barrels</span><small>{data?.strategic_inventory_period}</small></div>
        <div><strong>₹{fmt(data?.excise_cut_inr_per_litre, 0)}</strong><span>excise cut / litre</span></div>
      </div>
      <div className="os-india-project"><span>CAPACITY IN THE PIPELINE</span><strong>{fmt(data?.mangaluru_expansion_mmt, 2)} MMT</strong><p>{data?.mangaluru_expansion_status}</p></div>
      <blockquote>{data?.verdict}</blockquote>
      <div className="os-link-pair"><SourceLink href={SOURCES.india}>PIB route response</SourceLink><SourceLink href={SOURCES.indiaDependence}>PIB import dependence</SourceLink><SourceLink href={SOURCES.isprl}>ISPRL annual report</SourceLink></div>
    </article>
  );
}

function Principles({ rows }: { rows: string[] }) {
  return <div className="os-principles"><span>THREE THINGS TO CARRY FORWARD</span><ol>{rows.map((row, index) => <li key={row}><b>{index + 1}</b><p>{row}</p></li>)}</ol></div>;
}

export default function OilStructure({ engine }: { engine: Any }) {
  const structure = engine?.market_structure;
  if (!structure) return null;
  const charts = engine?.charts ?? {};
  const live = engine?.live ?? {};
  const cushing = live.cushing ?? {};
  const spread = live.brent_wti_spread ?? {};

  return (
    <section className="oil-structure" aria-labelledby="oil-structure-title">
      <header className="os-header">
        <div><span className="oil-kicker oil-kicker--observed">MARKET STRUCTURE · EVIDENCE-GRADED</span><h2 id="oil-structure-title">The barrel has an address.<br /><em>The benchmark has an architecture.</em></h2></div>
        <p>Cushing explains WTI deliverability. Chokepoints, freight and cargo programmes explain how a physical disruption reaches the world price—and then the money market.</p>
      </header>

      <DeliveryGeometry structure={structure} live={live} />

      <div className="os-chart-grid">
        <article className="os-chart-panel">
          <div className="os-panel-head"><div><span>CUSHING / WEEKLY</span><h3>Stocks against a stress reference</h3></div><div className="os-panel-head__metric"><strong>{fmt(cushing.stocks_m_bbl, 3)}m bbl</strong><small>{signed(cushing.change_8w_m_bbl, 3, "m")} over 8 weeks</small></div></div>
          <Chart rows={charts.cushing_inventory?.rows ?? []} series={CUSHING_SERIES} height={250} yLabel="commercial crude stocks · million barrels" refLine={{ value: structure.cushing?.stress_reference_m_bbl ?? 20, color: C.refinery, label: "20m reference" }} source="U.S. EIA · W_EPC0_SAX_YCUOK_MBBL" asOf={cushing.asof} note="20m is a stress reference, not a universal pumpability floor. EIA says tank-bottom operability varies by facility and pipeline system." />
          <div className="os-chart-foot"><Evidence>OBSERVED</Evidence><Evidence tone="reference">CAPACITY AS OF 2024-03-31</Evidence><SourceLink href={SOURCES.tankBottoms}>EIA tank-bottom note</SourceLink></div>
        </article>
        <article className="os-chart-panel">
          <div className="os-panel-head"><div><span>BENCHMARK BASIS / DAILY</span><h3>When WTI outruns Brent</h3></div><div className="os-panel-head__metric"><strong>{signed(spread.brent_minus_wti_usd_per_bbl, 2)}</strong><small>Brent − WTI · USD/bbl</small></div></div>
          <Chart rows={charts.brent_wti_spread?.rows ?? []} series={SPREAD_SERIES} height={250} yLabel="Brent minus WTI · USD per barrel" refLine={ZERO_LINE} source="EIA spot benchmarks via FRED · DCOILBRENTEU − DCOILWTICO" asOf={spread.asof} note="Below zero means WTI traded above Brent on the same observation date. This is spot basis, not a futures time-spread." />
          <div className="os-spread-read"><strong>{spread.negative_days_last_60_observations ?? "—"}</strong><span>negative observations in the latest 60</span><small>5-observation average {signed(spread.average_5d_usd_per_bbl, 2)}</small></div>
        </article>
      </div>

      <Transmission items={structure.transmission_order ?? []} />

      <div className="os-structure-grid">
        <ChokepointBars data={structure.chokepoints} />
        <ControlStack rows={structure.control_stack ?? []} />
        <HubTaxonomy rows={structure.hub_taxonomy ?? []} />
        <IndiaLedger data={structure.india} />
      </div>

      <div className="os-capacity-ledger">
        <div><Evidence tone="reference">LAST OFFICIAL</Evidence><strong>{fmt(structure.cushing?.working_capacity_m_bbl, 3)}m bbl</strong><span>Cushing working capacity · {structure.cushing?.capacity_asof}</span></div>
        <div><Evidence tone="reference">LAST OFFICIAL</Evidence><strong>{fmt(structure.cushing?.net_available_shell_capacity_m_bbl, 3)}m bbl</strong><span>net available shell capacity · report discontinued</span></div>
        <div><Evidence tone="warning">REFERENCE</Evidence><strong>{fmt(structure.cushing?.stress_reference_m_bbl, 0)}m bbl</strong><span>visible stress line · not a universal tank bottom</span></div>
        <SourceLink href={SOURCES.capacity}>EIA capacity workbook</SourceLink>
      </div>

      <Principles rows={structure.principles ?? []} />
    </section>
  );
}
