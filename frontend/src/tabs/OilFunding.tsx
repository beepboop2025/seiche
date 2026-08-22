import { memo, useEffect, useMemo, useRef, useState } from "react";
import Chart, { type ChartSeries } from "../Chart";
import { Any, AsOf, Fault, Method, fmt, ordinal } from "../lib";
import OilStructure from "./OilStructure";
import {
  calculateScenario,
  initialScenario,
  reconcileScenarioDefaults,
  scenarioSource,
  scenarioSourceNote,
  type Scenario,
  type ScenarioField,
  type ScenarioOutputs,
  type ScenarioSource,
} from "./oilFundingScenario";
import "../styles-oil.css";

const C = {
  crude: "#d7a85e",
  dollar: "#879ed8",
  refinery: "#6fb7ac",
  inr: "#c2a5f4",
  bright: "#edeef4",
  muted: "#787f95",
  grid: "rgba(237,238,244,.10)",
};

const SPOT_SERIES: ChartSeries[] = [
  { label: "WTI spot", color: C.crude },
  { label: "Brent spot", color: C.dollar },
];
const FUNDING_SERIES: ChartSeries[] = [
  { label: "3m nonfinancial CP − bill", color: C.crude },
  { label: "3m financial CP − bill", color: C.dollar },
  { label: "SOFR − IORB", color: C.refinery, dash: [5, 4] },
];
const COUPLING_SERIES: ChartSeries[] = [
  { label: "WTI Δ vs CP-spread Δ", color: C.crude },
  { label: "WTI Δ vs SOFR−IORB Δ", color: C.dollar },
  { label: "WTI Δ vs INR return", color: C.inr },
];
const CARRY_SERIES: ChartSeries[] = [
  { label: "funding component", color: C.dollar },
  { label: "storage + insurance", color: C.refinery, dash: [5, 4] },
  { label: "required contango", color: C.crude },
];
const INFLATION_POLICY_SERIES: ChartSeries[] = [
  { label: "energy CPI · YoY", color: C.crude },
  { label: "core CPI · YoY", color: C.refinery },
  { label: "IORB", color: C.dollar, dash: [5, 4] },
];
const DOLLAR_PARKING_SERIES: ChartSeries[] = [
  { label: "Treasury custody · 52w Δ", color: C.dollar },
  { label: "foreign-official RRP · 52w Δ", color: C.inr },
];
const ZERO_LINE = { value: 0, color: C.muted, label: "zero" };

const compactUsd = (value: number): string => {
  const sign = value < 0 ? "−" : "";
  const v = Math.abs(value);
  if (v >= 1e12) return `${sign}$${fmt(v / 1e12, 2)}tn`;
  if (v >= 1e9) return `${sign}$${fmt(v / 1e9, 2)}bn`;
  if (v >= 1e6) return `${sign}$${fmt(v / 1e6, 1)}mn`;
  return `${sign}$${fmt(v, 0)}`;
};

const compactUsdMaybe = (value: unknown, scale = 1): string => {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return compactUsd(Number(value) * scale);
};

const fundingRateDisplay = (value: number | null): string =>
  value == null ? "unavailable" : `${fmt(value, 2)}%`;

const usdPerBarrelDisplay = (value: number | null): string =>
  value == null ? "unavailable" : `$${fmt(value, 2)}/bbl`;

const usdDisplay = (value: number | null): string =>
  value == null ? "unavailable" : `$${fmt(value, 2)}`;

const compactInr = (value: number): string => {
  const sign = value < 0 ? "−" : "";
  const crore = Math.abs(value) / 10_000_000;
  if (crore >= 100_000) return `${sign}₹${fmt(crore / 100_000, 2)} lakh cr`;
  return `${sign}₹${fmt(crore, 0)} cr`;
};

function LiveTile({ label, value, detail, asof, tone }: {
  label: string; value: string; detail: string; asof?: string | null; tone: string;
}) {
  return (
    <div className="oil-live__item" style={{ "--oil-tone": tone } as React.CSSProperties}>
      <div className="oil-live__label">{label}</div>
      <div className="oil-live__value">{value}</div>
      <div className="oil-live__detail">{detail}</div>
      <time>{asof ?? "date unavailable"}</time>
    </div>
  );
}

function TransmissionLoop({ s, out, live }: { s: Scenario; out: ScenarioOutputs; live: Any }) {
  const parking = live.official_dollar_parking ?? {};
  const inflation = live.inflation_policy ?? {};
  return (
    <section className="oil-loop" aria-labelledby="oil-loop-title">
      <div className="oil-section-head">
        <div>
          <span className="oil-kicker">BIDIRECTIONAL TRANSMISSION</span>
          <h2 id="oil-loop-title">The barrel has a balance sheet.</h2>
        </div>
        <p>Follow the arrows. The upper lane is the link most dashboards omit.</p>
      </div>
      <div className="oil-loop__frame">
        <div className="oil-loop__lane oil-loop__lane--reverse">
          <span className="oil-loop__lane-label">RATES → OIL</span>
          <div className="oil-node oil-node--crude">
            <span>OIL CURVE</span>
            <strong>{usdPerBarrelDisplay(out.carry.required)}</strong>
            <small>{out.carry.required == null ? "funding input unavailable" : `${fmt(s.tenorDays, 0)}d contango hurdle`}</small>
          </div>
          <i aria-hidden="true">←</i>
          <div className="oil-node">
            <span>COST OF CARRY</span>
            <strong>{usdDisplay(out.carry.financing)}</strong>
            <small>{out.carry.financing == null ? "funding input unavailable" : "funding component"}</small>
          </div>
          <i aria-hidden="true">←</i>
          <div className="oil-node oil-node--dollar">
            <span>MONEY MARKET</span>
            <strong>{fundingRateDisplay(s.fundingRate)}</strong>
            <small>{s.fundingRate == null ? "no observed rate" : "funding rate"}</small>
          </div>
        </div>
        <div className="oil-loop__turn oil-loop__turn--right" aria-hidden="true" />
        <div className="oil-loop__lane oil-loop__lane--forward">
          <span className="oil-loop__lane-label">OIL → FUNDING</span>
          <div className="oil-node oil-node--crude">
            <span>OIL PRICE</span>
            <strong>${fmt(s.oilPrice, 2)}</strong>
            <small>per barrel</small>
          </div>
          <i aria-hidden="true">→</i>
          <div className="oil-node">
            <span>CARGO + MARGIN</span>
            <strong>{compactUsd(out.margin.sameDay)}</strong>
            <small>same-day cash</small>
          </div>
          <i aria-hidden="true">→</i>
          <div className="oil-node oil-node--inr">
            <span>RBI + OMC</span>
            <strong>{compactInr(out.india.omcCp)}</strong>
            <small>scenario CP demand</small>
          </div>
          <i aria-hidden="true">→</i>
          <div className="oil-node oil-node--dollar">
            <span>MONEY MARKET</span>
            <strong>CP · REPO · CALL</strong>
            <small>liquidity landing zone</small>
          </div>
        </div>
        <div className="oil-loop__turn oil-loop__turn--left" aria-hidden="true" />
      </div>
      <div className="oil-slow-channels">
        <article>
          <span>05 · SLOW BALANCE-SHEET CHANNEL</span>
          <h3>Petrodollar recycling</h3>
          <p>Oil receipts redistribute dollars toward exporters. Free public data cannot tag those dollars by barrel, so Seiche watches broad foreign-official parking without pretending it is oil-specific.</p>
          <div>
            <strong>{compactUsdMaybe(parking.treasury_custody_change_52w_b, 1e9)}</strong>
            <small>Treasury custody · 52w Δ</small>
            <strong>{compactUsdMaybe(parking.foreign_rrp_change_52w_b, 1e9)}</strong>
            <small>foreign-official RRP · 52w Δ</small>
          </div>
        </article>
        <article>
          <span>06 · SLOW POLICY CHANNEL</span>
          <h3>Inflation → policy rate</h3>
          <p>Energy reaches headline inflation first; persistence can reach core and the reaction function later. The chart below is descriptive—there is no estimated policy coefficient hiding behind it.</p>
          <div>
            <strong>{fmt(inflation.energy_cpi_yoy_pct, 1)}%</strong><small>energy CPI YoY</small>
            <strong>{fmt(inflation.core_cpi_yoy_pct, 1)}%</strong><small>core CPI YoY</small>
            <strong>{fmt(inflation.iorb_pct, 2)}%</strong><small>IORB</small>
          </div>
        </article>
      </div>
    </section>
  );
}

function BallastSection({ ballast }: { ballast: Any }) {
  if (!ballast?.ok) {
    return (
      <section className="oil-ballast" aria-labelledby="oil-ballast-title">
        <div className="oil-section-head">
          <div>
            <span className="oil-kicker oil-kicker--scenario">BALLAST · FUTURES CASH PRESSURE</span>
            <h2 id="oil-ballast-title">The futures-cash ledger is temporarily dark.</h2>
          </div>
          <p>{ballast?.reason ?? "CFTC positioning and benchmark history are not aligned yet."}</p>
        </div>
        <div className="oil-ballast__unavailable">
          Oil × Funding remains available. Ballast refuses to infer futures cash pressure without enough public history.
        </div>
      </section>
    );
  }

  const headline = ballast.headline ?? {};
  const dominant = headline.dominant_channel ?? {};
  const fundingOverlay = headline.funding_overlay ?? {};
  const inventory = ballast.inventory ?? {};
  const funding = ballast.funding ?? {};
  const state = String(headline.state ?? "CANNOT_ASSESS");
  const stateClass = state.toLowerCase().replaceAll("_", "-");
  const boundaries = ballast.coverage?.boundaries ?? [];

  return (
    <section className="oil-ballast" aria-labelledby="oil-ballast-title">
      <div className="oil-section-head">
        <div>
          <span className="oil-kicker oil-kicker--scenario">BALLAST · OBSERVED + BOUNDED DERIVATION</span>
          <h2 id="oil-ballast-title">How much cash can the futures tape displace?</h2>
        </div>
        <p>Weekly gross scale, paying-side concentration and physical stock set the state. The price of cash remains a separate amplifier, so funding cannot manufacture a commodity alert.</p>
      </div>

      <div className="oil-ballast__headline">
        <div className={`oil-ballast__state oil-ballast__state--${stateClass}`}>
          <span>PRESSURE STATE</span><strong>{state.replaceAll("_", " ")}</strong>
          <small>context only · never composite</small>
        </div>
        <div><span>WORST COMMODITY CHANNEL</span><strong>p{fmt(headline.worst_channel_percentile, 1)}</strong><small>{dominant.label ?? "insufficient history"}</small></div>
        <div><span>DOMINANT PATH</span><strong>{String(dominant.channel ?? "—").replaceAll("_", " ")}</strong><small>own-history rank · no blended score</small></div>
        <div><span>OBSERVED COVERAGE</span><strong>{fmt(headline.coverage_pct, 1)}%</strong><small>dark fields remain explicit</small></div>
      </div>

      <div className="oil-ballast__contracts">
        {(ballast.contracts ?? []).map((contract: Any) => {
          const cash = contract.cash_transfer_scale ?? {};
          const price = contract.price_proxy ?? {};
          const oi = contract.open_interest ?? {};
          const positioning = contract.positioning ?? {};
          const priceUnit = contract.key === "HENRY_HUB" ? "$/MMBtu" : "$/bbl";
          const proxyMove = price.change_since_prior_report == null
            ? null
            : Math.abs(Number(price.change_since_prior_report));
          return (
            <article key={contract.key}>
              <div className="oil-ballast__contract-head">
                <div><span>{contract.key}</span><h3>{contract.label}</h3></div>
                <time>
                  <span>positions {contract.report_asof ?? "date unavailable"}</span>
                  <span>available {contract.available_asof ?? "date unavailable"}</span>
                </time>
              </div>
              <div className="oil-ballast__identity" aria-label={`${contract.label} gross mark displacement identity`}>
                <div><strong>{fmt(proxyMove, 2)}</strong><small>|Δ spot proxy| · {priceUnit}</small></div>
                <i aria-hidden="true">×</i>
                <div><strong>{fmt(oi.contracts, 0)}</strong><small>open contracts</small></div>
                <i aria-hidden="true">×</i>
                <div><strong>{fmt(oi.contract_multiplier, 0)}</strong><small>{oi.multiplier_unit}</small></div>
              </div>
              <div className="oil-ballast__cash">
                <strong>{compactUsdMaybe(cash.gross_mark_displacement_usd)}</strong>
                <span>gross weekly mark-displacement proxy</span>
                <small>p{fmt(cash.gross_displacement_percentile_5y, 1)} of trailing 5y</small>
              </div>
              <dl>
                <div><dt>proxy move</dt><dd>{Number(price.change_since_prior_report) > 0 ? "+" : ""}{fmt(price.change_since_prior_report, 2)} {priceUnit}</dd></div>
                <div><dt>open interest</dt><dd>{fmt(oi.contracts, 0)} contracts</dd></div>
                <div><dt>top-four paying side</dt><dd>{fmt(positioning.top4_paying_side_pct, 1)}%</dd></div>
                <div><dt>reported-side coverage</dt><dd>{fmt(cash.reported_paying_side_coverage_pct, 1)}%</dd></div>
              </dl>
              <div className="oil-ballast__guard">SPOT PROXY · GROSS SCALE · NOT AN OBSERVED MARGIN CALL</div>
            </article>
          );
        })}
      </div>

      <div className="oil-ballast__plumbing">
        <article>
          <span>PHYSICAL COLLATERAL</span>
          <h3>Commercial crude inventory</h3>
          <strong>{fmt(inventory.stocks_million_bbl, 1)}m bbl</strong>
          <p>{Number(inventory.change_1w_million_bbl) > 0 ? "+" : ""}{fmt(inventory.change_1w_million_bbl, 2)}m bbl in one week · p{fmt(inventory.absolute_weekly_change_percentile_5y, 1)} absolute move</p>
          <small>EIA period ending {inventory.asof ?? "date unavailable"} · normally available {inventory.available_asof ?? "date unavailable"} · {compactUsdMaybe(inventory.annual_sofr_carry_benchmark_usd)} annual SOFR carry benchmark—not a financed-book estimate.</small>
        </article>
        <article>
          <span>PRICE OF CASH</span>
          <h3>Funding landing zone</h3>
          <div className="oil-ballast__overlay">{String(fundingOverlay.status ?? "UNAVAILABLE").replaceAll("_", " ")} · AMPLIFIER, NOT TRIGGER</div>
          <div className="oil-ballast__funding-row"><b>{fmt(funding.sofr_iorb?.spread_bp, 1)} bp</b><small>SOFR − IORB · p{fmt(funding.sofr_iorb?.percentile_3y, 1)} · {funding.sofr_iorb?.asof ?? "date unavailable"}</small></div>
          <div className="oil-ballast__funding-row"><b>{fmt(funding.cp_nonfinancial?.spread_bp, 1)} bp</b><small>nonfinancial CP − bill · p{fmt(funding.cp_nonfinancial?.percentile_3y, 1)} · {funding.cp_nonfinancial?.asof ?? "date unavailable"}</small></div>
        </article>
        <article className="oil-ballast__coverage">
          <span>COVERAGE BOUNDARY</span>
          <h3>What is lit—and what stays dark</h3>
          <ul>{boundaries.map((row: Any) => (
            <li key={row.layer}><span>{row.layer}</span><b>{String(row.status).replaceAll("_", " ")}</b></li>
          ))}</ul>
        </article>
      </div>

      <div className="oil-ballast__handoffs">
        <div><span>→ UNDERTOW</span><strong>What will this position cost to exit?</strong><small>{ballast.handoffs?.undertow?.boundary}</small></div>
        <div><span>→ LIQUILENS</span><strong>Who has qualifying exposure—and through which funding channel?</strong><small>{ballast.handoffs?.liquilens?.boundary}</small></div>
      </div>
    </section>
  );
}

function ScatterPlot({ scatter }: { scatter: Any }) {
  const points = (scatter?.points ?? []) as [string, number, number][];
  const fit = scatter?.fit ?? {};
  const geometry = useMemo(() => {
    if (points.length < 2) return null;
    const width = 760, height = 330;
    const pad = { left: 66, right: 24, top: 22, bottom: 54 };
    const xs = points.map((p) => p[1]);
    const ys = points.map((p) => p[2]);
    let xMin = Math.min(...xs, 0), xMax = Math.max(...xs, 0);
    let yMin = Math.min(...ys, 0), yMax = Math.max(...ys, 0);
    const xPad = Math.max((xMax - xMin) * 0.08, 0.5);
    const yPad = Math.max((yMax - yMin) * 0.08, 0.5);
    xMin -= xPad; xMax += xPad; yMin -= yPad; yMax += yPad;
    const x = (value: number) => pad.left + (value - xMin) / (xMax - xMin) * (width - pad.left - pad.right);
    const y = (value: number) => height - pad.bottom - (value - yMin) / (yMax - yMin) * (height - pad.top - pad.bottom);
    const xTicks = Array.from({ length: 5 }, (_, i) => xMin + (xMax - xMin) * i / 4);
    const yTicks = Array.from({ length: 5 }, (_, i) => yMin + (yMax - yMin) * i / 4);
    const slope = Number(fit.slope_bp_per_usd);
    const intercept = Number(fit.intercept_bp);
    return { width, height, pad, x, y, xMin, xMax, xTicks, yTicks, slope, intercept };
  }, [points, fit.slope_bp_per_usd, fit.intercept_bp]);

  if (!geometry) return <div className="oil-empty">Scatter needs at least two aligned observations.</div>;
  const g = geometry;
  const hasFit = Number.isFinite(g.slope) && Number.isFinite(g.intercept);

  return (
    <>
      <div className="oil-scatter__stats" aria-label="Scatter fit statistics">
        <div><span>ρ</span><strong>{fmt(fit.correlation, 2)}</strong></div>
        <div><span>slope</span><strong>{fmt(fit.slope_bp_per_usd, 2)} bp / $</strong></div>
        <div><span>R²</span><strong>{fmt(fit.r_squared, 2)}</strong></div>
        <div><span>n</span><strong>{fit.n ?? "—"}</strong></div>
      </div>
      <div className="oil-svg-wrap">
        <svg
          className="oil-scatter"
          viewBox={`0 0 ${g.width} ${g.height}`}
          role="img"
          aria-label={`${fit.n ?? points.length} non-overlapping five-business-day observations of WTI changes against nonfinancial commercial-paper spread changes`}
        >
          <defs>
            <radialGradient id="oil-dot-glow">
              <stop offset="0" stopColor={C.crude} stopOpacity=".92" />
              <stop offset="1" stopColor={C.crude} stopOpacity=".22" />
            </radialGradient>
          </defs>
          {g.xTicks.map((tick) => (
            <g key={`x-${tick}`}>
              <line x1={g.x(tick)} x2={g.x(tick)} y1={g.pad.top} y2={g.height - g.pad.bottom} stroke={C.grid} />
              <text x={g.x(tick)} y={g.height - 28} textAnchor="middle">{fmt(tick, 1)}</text>
            </g>
          ))}
          {g.yTicks.map((tick) => (
            <g key={`y-${tick}`}>
              <line x1={g.pad.left} x2={g.width - g.pad.right} y1={g.y(tick)} y2={g.y(tick)} stroke={C.grid} />
              <text x={g.pad.left - 12} y={g.y(tick) + 3} textAnchor="end">{fmt(tick, 1)}</text>
            </g>
          ))}
          {g.xMin <= 0 && g.xMax >= 0 && <line className="oil-zero" x1={g.x(0)} x2={g.x(0)} y1={g.pad.top} y2={g.height - g.pad.bottom} />}
          {hasFit && (
            <line
              className="oil-fit"
              x1={g.x(g.xMin)} y1={g.y(g.slope * g.xMin + g.intercept)}
              x2={g.x(g.xMax)} y2={g.y(g.slope * g.xMax + g.intercept)}
            />
          )}
          {points.map(([date, xValue, yValue]) => (
            <circle key={date} cx={g.x(xValue)} cy={g.y(yValue)} r="3.4" fill="url(#oil-dot-glow)" aria-hidden="true">
              <title>{date}: WTI {xValue > 0 ? "+" : ""}{fmt(xValue, 2)} USD/bbl; CP spread {yValue > 0 ? "+" : ""}{fmt(yValue, 2)} bp</title>
            </circle>
          ))}
          <text className="oil-axis-label" x={(g.pad.left + g.width - g.pad.right) / 2} y={g.height - 5} textAnchor="middle">
            5bd WTI change · USD per barrel
          </text>
          <text className="oil-axis-label" transform={`translate(15 ${(g.pad.top + g.height - g.pad.bottom) / 2}) rotate(-90)`} textAnchor="middle">
            5bd nonfinancial CP−bill change · bp
          </text>
        </svg>
      </div>
      <details className="oil-data-details">
        <summary>Inspect the latest 12 observations</summary>
        <table className="mini">
          <thead><tr><th>period ending</th><th>WTI Δ $/bbl</th><th>CP−bill Δ bp</th></tr></thead>
          <tbody>{points.slice(-12).reverse().map((row) => (
            <tr key={row[0]}><td>{row[0]}</td><td className="num">{fmt(row[1], 2)}</td><td className="num">{fmt(row[2], 2)}</td></tr>
          ))}</tbody>
        </table>
      </details>
    </>
  );
}

const ObservedEvidence = memo(function ObservedEvidence({ engine }: { engine: Any }) {
  const charts = engine.charts ?? {};
  const scatterAsOf = charts.scatter?.points?.at(-1)?.[0] ?? engine.asof;
  return (
    <section className="oil-observed" aria-labelledby="oil-observed-title">
      <div className="oil-section-head">
        <div>
          <span className="oil-kicker oil-kicker--observed">OBSERVED · PUBLIC DATA</span>
          <h2 id="oil-observed-title">The tape, the plumbing, and their coupling</h2>
        </div>
        <p>Same-unit panels only. Correlations use changes, carry uses explicit assumptions.</p>
      </div>
      <div className="oil-chart-grid">
        <article className="oil-panel">
          <div className="oil-panel__head"><h3>Barrel benchmarks</h3><span>PRICE</span></div>
          <Chart
            rows={charts.spot?.rows ?? []}
            series={SPOT_SERIES}
            height={230}
            yLabel="spot price · USD per barrel"
            source="EIA via FRED · DCOILWTICO / DCOILBRENTEU"
            note="Spot benchmarks, not a futures strip. Negative WTI in April 2020 is retained."
          />
        </article>
        <article className="oil-panel">
          <div className="oil-panel__head"><h3>Dollar funding spreads</h3><span>PRICE OF CASH</span></div>
          <Chart
            rows={charts.funding?.rows ?? []}
            series={FUNDING_SERIES}
            height={230}
            yLabel="spread · basis points"
            refLine={ZERO_LINE}
            source="Federal Reserve + NY Fed via FRED · CP, DGS3MO, SOFR, IORB"
            note="CP spreads use the 3m Treasury on actual CP print dates; SOFR−IORB is a secured-policy spread."
          />
        </article>
        <article className="oil-panel">
          <div className="oil-panel__head"><h3>Rolling coupling</h3><span>63 OBSERVATIONS</span></div>
          <Chart
            rows={charts.coupling?.rows ?? []}
            series={COUPLING_SERIES}
            height={230}
            yLabel="rolling correlation · −1 to +1"
            refLine={ZERO_LINE}
            source="Seiche calculation from EIA / Fed / NY Fed / H.10"
            note="Associational, not causal. WTI dollar changes are used so the April 2020 negative print remains defined."
          />
        </article>
        <article className="oil-panel">
          <div className="oil-panel__head"><h3>Mechanical carry hurdle</h3><span>RATES → CURVE</span></div>
          <Chart
            rows={charts.carry_hurdle?.rows ?? []}
            series={CARRY_SERIES}
            height={230}
            yLabel="required contango · USD per barrel"
            source="Seiche identity using WTI + SOFR"
            note={charts.carry_hurdle?.assumption}
          />
        </article>
        <article className="oil-panel">
          <div className="oil-panel__head"><h3>Inflation → policy</h3><span>MONTHLY / DAILY</span></div>
          <Chart
            rows={charts.inflation_policy?.rows ?? []}
            series={INFLATION_POLICY_SERIES}
            height={230}
            yLabel="annual inflation / policy rate · percent"
            source="BLS + Federal Reserve via FRED · CPIENGSL, CPILFESL, IORB"
            note="Energy CPI is broader than crude oil. This is a descriptive transmission panel, not an estimated central-bank reaction function."
          />
        </article>
        <article className="oil-panel">
          <div className="oil-panel__head"><h3>Foreign-official dollar parking</h3><span>RECYCLING PROXY</span></div>
          <Chart
            rows={charts.official_dollar_parking?.rows ?? []}
            series={DOLLAR_PARKING_SERIES}
            height={230}
            yLabel="52-week balance change · USD billions"
            refLine={ZERO_LINE}
            source="Federal Reserve H.4.1 via FRED · WMTSECL1 / WLRRAFOIAL"
            note="Broad foreign-official balances only: this cannot identify oil exporters or prove petrodollar recycling."
          />
        </article>
        <article className="oil-panel oil-panel--wide">
          <div className="oil-panel__head">
            <div><h3>Does an oil move arrive in CP?</h3><p>Non-overlapping five-business-day changes</p></div>
            <span>ASSOCIATION</span>
          </div>
          <ScatterPlot scatter={charts.scatter} />
          <div className="oil-evidence-line">
            <span>Federal Reserve / EIA via FRED</span>
            <span>through {scatterAsOf}</span>
            <span>hover a point for its date</span>
          </div>
        </article>
      </div>
    </section>
  );
});

function Slider({ id, label, value, min, max, step, display, onChange }: {
  id: string; label: string; value: number; min: number; max: number; step: number;
  display: string; onChange: (value: number) => void;
}) {
  return (
    <label className="oil-control" htmlFor={id}>
      <span>{label}</span><output htmlFor={id}>{display}</output>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-valuetext={display}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
    </label>
  );
}

function CarryCurve({ s, out }: { s: Scenario; out: ScenarioOutputs }) {
  const fundingRate = s.fundingRate;
  const required = out.carry.required;
  if (fundingRate == null || required == null) {
    return (
      <div className="oil-empty" role="status">
        Funding rate unavailable — move the funding-rate control to set an explicit scenario assumption.
      </div>
    );
  }
  const width = 690, height = 250;
  const pad = { left: 58, right: 24, top: 24, bottom: 48 };
  const maxRate = 12;
  const rates = Array.from({ length: 49 }, (_, index) => index / 4);
  const hurdle = (rate: number) =>
    s.storagePerDay * s.tenorDays
    + s.oilPrice * ((rate + s.insuranceRate) / 100) * s.tenorDays / 365;
  const values = rates.map(hurdle);
  const yMax = Math.max(...values, s.forwardSpread, 1) * 1.12;
  const x = (rate: number) => pad.left + rate / maxRate * (width - pad.left - pad.right);
  const y = (value: number) => height - pad.bottom - Math.max(0, value) / yMax * (height - pad.top - pad.bottom);
  const path = rates.map((rate, index) => `${index ? "L" : "M"}${x(rate).toFixed(1)},${y(values[index]).toFixed(1)}`).join(" ");
  const yTicks = Array.from({ length: 4 }, (_, index) => yMax * index / 3);

  return (
    <svg
      className="oil-carry-curve"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Required ${s.tenorDays}-day contango across funding rates; at ${fundingRate}% the hurdle is ${required.toFixed(2)} dollars per barrel`}
    >
      <defs>
        <linearGradient id="oil-carry-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={C.crude} stopOpacity=".28" />
          <stop offset="1" stopColor={C.crude} stopOpacity="0" />
        </linearGradient>
      </defs>
      {yTicks.map((tick) => <g key={tick}>
        <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} stroke={C.grid} />
        <text x={pad.left - 10} y={y(tick) + 3} textAnchor="end">${fmt(tick, 1)}</text>
      </g>)}
      {[0, 2, 4, 6, 8, 10, 12].map((tick) => <g key={tick}>
        <line x1={x(tick)} x2={x(tick)} y1={pad.top} y2={height - pad.bottom} stroke={C.grid} />
        <text x={x(tick)} y={height - 24} textAnchor="middle">{tick}%</text>
      </g>)}
      {s.forwardSpread >= 0 && <>
        <line className="oil-forward-line" x1={pad.left} x2={width - pad.right} y1={y(s.forwardSpread)} y2={y(s.forwardSpread)} />
        <text className="oil-forward-label" x={width - pad.right - 4} y={y(s.forwardSpread) - 7} textAnchor="end">entered spread ${fmt(s.forwardSpread, 1)}</text>
      </>}
      <path d={`${path} L${x(maxRate)},${height - pad.bottom} L${x(0)},${height - pad.bottom} Z`} fill="url(#oil-carry-fill)" />
      <path className="oil-carry-path" d={path} />
      <line className="oil-current-guide" x1={x(fundingRate)} x2={x(fundingRate)} y1={y(required)} y2={height - pad.bottom} />
      <circle className="oil-current-dot" cx={x(fundingRate)} cy={y(required)} r="5" />
      <text className="oil-current-label" x={Math.min(x(fundingRate) + 9, width - 150)} y={Math.max(y(required) - 10, 16)}>
        now ${fmt(required, 2)}/bbl
      </text>
      <text className="oil-axis-label" x={(pad.left + width - pad.right) / 2} y={height - 3} textAnchor="middle">annual funding rate</text>
    </svg>
  );
}

function OutputMetric({ label, value, detail, tone = "" }: {
  label: string; value: string; detail: string; tone?: string;
}) {
  return <div className={`oil-output ${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function ScenarioLab({ s, source, editedFields, onFieldChange, onReset }: {
  s: Scenario;
  source: ScenarioSource;
  editedFields: ReadonlySet<ScenarioField>;
  onFieldChange: (field: ScenarioField, value: number) => void;
  onReset: () => void;
}) {
  const out = calculateScenario(s);
  const field = (key: ScenarioField) => (value: number) => onFieldChange(key, value);
  const maxMargin = Math.max(out.margin.variation, out.margin.initial, 1);
  const maxVoyage = Math.max(s.voyageDays, s.baselineVoyageDays, 1);
  const fundingUnavailable = s.fundingRate == null;
  const headroom = out.carry.headroom;

  return (
    <section className="oil-lab" aria-labelledby="oil-lab-title">
      <div className="oil-section-head">
        <div>
          <span className="oil-kicker oil-kicker--scenario">SCENARIO · NOT A LIVE FORECAST</span>
          <h2 id="oil-lab-title">Turn the barrel into a funding event</h2>
        </div>
        <p>Every output is an identity. Move one assumption and watch the cash timing change.</p>
      </div>
      <div className="oil-lab__layout">
        <aside className="oil-controls">
          <div className="oil-controls__head">
            <div><span>ASSUMPTIONS</span><strong>Transmission controls</strong></div>
            <button type="button" onClick={onReset}>RESET TO SNAPSHOT DEFAULTS</button>
          </div>
          <Slider id="oil-price" label="Oil price" value={s.oilPrice} min={20} max={200} step={1} display={`$${fmt(s.oilPrice, 0)}/bbl`} onChange={field("oilPrice")} />
          <Slider id="oil-funding" label="Funding rate" value={s.fundingRate ?? 5} min={0} max={12} step={0.05} display={fundingRateDisplay(s.fundingRate)} onChange={field("fundingRate")} />
          <Slider id="oil-forward" label="Forward spread" value={s.forwardSpread} min={-10} max={15} step={0.1} display={`${s.forwardSpread > 0 ? "+" : ""}$${fmt(s.forwardSpread, 1)}/bbl`} onChange={field("forwardSpread")} />
          <Slider id="oil-voyage" label="Voyage length" value={s.voyageDays} min={5} max={90} step={1} display={`${fmt(s.voyageDays, 0)} days`} onChange={field("voyageDays")} />
          <Slider id="oil-jump" label="Oil price jump" value={s.oilPriceChange} min={0} max={40} step={0.5} display={`+$${fmt(s.oilPriceChange, 1)}/bbl`} onChange={field("oilPriceChange")} />
          <Slider id="oil-rbi" label="RBI dollar sales" value={s.rbiUsdSalesB} min={0} max={20} step={0.25} display={`$${fmt(s.rbiUsdSalesB, 2)}bn`} onChange={field("rbiUsdSalesB")} />
          <details className="oil-advanced">
            <summary>Advanced assumptions <span>15 controls</span></summary>
            <div className="oil-advanced__grid">
              <Slider id="oil-tenor" label="Carry tenor" value={s.tenorDays} min={30} max={365} step={5} display={`${fmt(s.tenorDays, 0)}d`} onChange={field("tenorDays")} />
              <Slider id="oil-storage" label="Storage / day" value={s.storagePerDay} min={0} max={0.1} step={0.005} display={`$${fmt(s.storagePerDay, 3)}`} onChange={field("storagePerDay")} />
              <Slider id="oil-insurance" label="Insurance rate" value={s.insuranceRate} min={0} max={2} step={0.05} display={`${fmt(s.insuranceRate, 2)}%`} onChange={field("insuranceRate")} />
              <Slider id="oil-cargo" label="Cargo size" value={s.cargoBarrelsM} min={0.5} max={4} step={0.1} display={`${fmt(s.cargoBarrelsM, 1)}m bbl`} onChange={field("cargoBarrelsM")} />
              <Slider id="oil-throughput" label="Daily throughput" value={s.dailyThroughputMbd} min={0.05} max={1} step={0.05} display={`${fmt(s.dailyThroughputMbd, 2)}mb/d`} onChange={field("dailyThroughputMbd")} />
              <Slider id="oil-baseline" label="Baseline voyage" value={s.baselineVoyageDays} min={5} max={60} step={1} display={`${fmt(s.baselineVoyageDays, 0)}d`} onChange={field("baselineVoyageDays")} />
              <Slider id="oil-hedge" label="Net short hedge" value={s.hedgeBarrelsM} min={0.1} max={10} step={0.1} display={`${fmt(s.hedgeBarrelsM, 1)}m bbl`} onChange={field("hedgeBarrelsM")} />
              <Slider id="oil-im" label="Initial margin rise" value={s.initialMarginRateChange} min={0} max={30} step={0.5} display={`${fmt(s.initialMarginRateChange, 1)}%`} onChange={field("initialMarginRateChange")} />
              <Slider id="oil-import" label="India oil imports" value={s.indiaImportMbd} min={1} max={8} step={0.1} display={`${fmt(s.indiaImportMbd, 1)}mb/d`} onChange={field("indiaImportMbd")} />
              <Slider id="oil-shock" label="India oil shock" value={s.indiaOilShock} min={0} max={50} step={1} display={`$${fmt(s.indiaOilShock, 0)}/bbl`} onChange={field("indiaOilShock")} />
              <Slider id="oil-usdinr" label="USD/INR" value={s.usdInr} min={60} max={120} step={0.1} display={`₹${fmt(s.usdInr, 1)}`} onChange={field("usdInr")} />
              <Slider id="oil-replenish" label="Liquidity replenished" value={s.liquidityReplenishment} min={0} max={100} step={5} display={`${fmt(s.liquidityReplenishment, 0)}%`} onChange={field("liquidityReplenishment")} />
              <Slider id="oil-underrecovery" label="OMC under-recovery" value={s.underRecoveryCroreDay} min={0} max={2000} step={50} display={`₹${fmt(s.underRecoveryCroreDay, 0)}cr/d`} onChange={field("underRecoveryCroreDay")} />
              <Slider id="oil-lag" label="Compensation lag" value={s.compensationLagDays} min={1} max={180} step={1} display={`${fmt(s.compensationLagDays, 0)}d`} onChange={field("compensationLagDays")} />
              <Slider id="oil-cpshare" label="CP funding share" value={s.cpFundingShare} min={0} max={100} step={5} display={`${fmt(s.cpFundingShare, 0)}%`} onChange={field("cpFundingShare")} />
            </div>
          </details>
          <div className="oil-controls__note">
            {scenarioSourceNote(source, s, editedFields)}
          </div>
        </aside>

        <div className="oil-lab__results" aria-live="polite">
          <article className="oil-result oil-result--carry">
            <div className="oil-result__head"><span>01</span><div><h3>Cost of carry</h3><p>the reverse channel · rates price the curve</p></div></div>
            <div className="oil-output-grid">
              <OutputMetric label="required contango" value={usdPerBarrelDisplay(out.carry.required)} detail={fundingUnavailable ? "funding input unavailable" : `${fmt(s.tenorDays, 0)}-day hurdle`} tone="crude" />
              <OutputMetric
                label="forward headroom"
                value={headroom == null ? "unavailable" : `${headroom >= 0 ? "+" : "−"}$${fmt(Math.abs(headroom), 2)}/bbl`}
                detail={headroom == null ? "funding input unavailable" : headroom >= 0 ? "mechanically covers carry" : "below mechanical carry"}
                tone={headroom == null ? "" : headroom >= 0 ? "calm" : "stress"}
              />
            </div>
            <CarryCurve s={s} out={out} />
            <div className="oil-equation">contango = <b>${fmt(out.carry.storage, 2)}</b> storage + <b>{usdDisplay(out.carry.financing)}</b> funding + <b>${fmt(out.carry.insurance, 2)}</b> insurance</div>
          </article>

          <div className="oil-result-pair">
            <article className="oil-result">
              <div className="oil-result__head"><span>02</span><div><h3>Trade finance</h3><p>price × barrels × time</p></div></div>
              <OutputMetric label="credit per cargo" value={compactUsd(out.trade.cargoCredit)} detail={`${fmt(s.cargoBarrelsM, 1)}m barrels × $${fmt(s.oilPrice, 0)}`} tone="crude" />
              <div className="oil-waterfall">
                <div><span>baseline voyage · {fmt(s.baselineVoyageDays, 0)}d</span><i style={{ width: `${s.baselineVoyageDays / maxVoyage * 100}%` }} /></div>
                <div><span>scenario voyage · {fmt(s.voyageDays, 0)}d</span><i className="hot" style={{ width: `${s.voyageDays / maxVoyage * 100}%` }} /></div>
              </div>
              <div className="oil-output-grid oil-output-grid--compact">
                <OutputMetric label="capital in transit" value={compactUsd(out.trade.inTransit)} detail={`${fmt(out.trade.multiple, 1)}× baseline`} />
                <OutputMetric label="incremental tie-up" value={compactUsd(out.trade.incremental)} detail="vs baseline voyage" />
                <OutputMetric label="voyage interest" value={out.trade.financingCost == null ? "unavailable" : compactUsd(out.trade.financingCost)} detail={out.trade.financingCost == null ? "funding input unavailable" : "per cargo"} />
              </div>
            </article>

            <article className="oil-result">
              <div className="oil-result__head"><span>03</span><div><h3>Margin calls</h3><p>today's cash against tomorrow's barrel</p></div></div>
              <OutputMetric label="same-day liquidity" value={compactUsd(out.margin.sameDay)} detail="variation + initial margin" tone="crude" />
              <div className="oil-margin-bars">
                <div><span>variation margin <b>{compactUsd(out.margin.variation)}</b></span><i style={{ width: `${out.margin.variation / maxMargin * 100}%` }} /></div>
                <div><span>initial margin <b>{compactUsd(out.margin.initial)}</b></span><i className="dollar" style={{ width: `${out.margin.initial / maxMargin * 100}%` }} /></div>
              </div>
              <div className="oil-timing">
                <span>PRICE SHOCK</span><i>same day →</i><strong>REPO · CP · CREDIT LINE</strong><i>months →</i><span>PHYSICAL GAIN</span>
              </div>
            </article>
          </div>

          <article className="oil-result oil-result--india">
            <div className="oil-result__head"><span>04</span><div><h3>India transmission</h3><p>external oil bill → rupee liquidity → OMC commercial paper</p></div></div>
            <div className="oil-india-chain">
              <OutputMetric label="annual import-bill change" value={compactUsd(out.india.annualImportUsd)} detail={`$${fmt(s.indiaOilShock, 0)}/bbl shock at ${fmt(s.indiaImportMbd, 1)}mb/d`} tone="crude" />
              <i aria-hidden="true">→</i>
              <OutputMetric label="RBI gross absorption" value={compactInr(out.india.rbiGross)} detail={`$${fmt(s.rbiUsdSalesB, 2)}bn sold at ₹${fmt(s.usdInr, 1)}`} tone="inr" />
              <i aria-hidden="true">→</i>
              <OutputMetric label="unreplenished drain" value={compactInr(out.india.rbiUnreplenished)} detail={`${fmt(100 - s.liquidityReplenishment, 0)}% left unsterilised`} tone="inr" />
              <i aria-hidden="true">→</i>
              <OutputMetric label="OMC CP demand" value={compactInr(out.india.omcCp)} detail={`${fmt(s.cpFundingShare, 0)}% of ${compactInr(out.india.omcStock)} stock`} tone="dollar" />
            </div>
            <div className="oil-india-note">The import bill, intervention and OMC channels are separate conditional paths. They are shown in sequence for stress testing, not asserted to occur one-for-one.</div>
          </article>
        </div>
      </div>
    </section>
  );
}

function SourcesAndLimits({ engine }: { engine: Any }) {
  const links: Record<string, string> = {
    DCOILWTICO: "https://fred.stlouisfed.org/series/DCOILWTICO",
    DCOILBRENTEU: "https://fred.stlouisfed.org/series/DCOILBRENTEU",
    "DCPN3M / DCPF3M": "https://fred.stlouisfed.org/series/DCPN3M",
    DGS3MO: "https://fred.stlouisfed.org/series/DGS3MO",
    "SOFR / IORB": "https://fred.stlouisfed.org/series/SOFR",
    DEXINUS: "https://fred.stlouisfed.org/series/DEXINUS",
    "CPIENGSL / CPILFESL": "https://fred.stlouisfed.org/series/CPIENGSL",
    "WMTSECL1 / WLRRAFOIAL": "https://fred.stlouisfed.org/series/WMTSECL1",
  };
  return (
    <section className="oil-sources" aria-labelledby="oil-sources-title">
      <div>
        <span className="oil-kicker">DATA LEDGER</span>
        <h2 id="oil-sources-title">What is measured</h2>
        <ul>{(engine.sources ?? []).map((source: Any) => (
          <li key={`${source.series}-${source.id}`}>
            <span>{source.series}</span>
            <a href={source.url ?? links[source.id] ?? "https://fred.stlouisfed.org/"} target="_blank" rel="noreferrer">{source.source} · {source.id} ↗</a>
          </li>
        ))}</ul>
      </div>
      <div>
        <span className="oil-kicker oil-kicker--scenario">HONESTY LAYER</span>
        <h2>What this cannot claim</h2>
        <ol>{(engine.caveats ?? []).map((caveat: string) => <li key={caveat}>{caveat}</li>)}</ol>
      </div>
      <Method>{engine.method}</Method>
    </section>
  );
}

export default function OilFunding({ snap }: { snap: Any }) {
  const engine = snap.engines?.oilfunding ?? {};
  const base = useMemo(() => initialScenario(engine), [engine]);
  const refreshedSource = useMemo(() => scenarioSource(engine), [engine]);
  const [scenario, setScenario] = useState<Scenario>(() => base);
  const [source, setSource] = useState<ScenarioSource>(() => refreshedSource);
  const editedFields = useRef<Set<ScenarioField>>(new Set());

  useEffect(() => {
    if (!engine.ok) return;
    setScenario((current) => reconcileScenarioDefaults(current, base, editedFields.current));
    setSource(refreshedSource);
  }, [base, engine.ok, refreshedSource]);

  const updateScenarioField = (field: ScenarioField, value: number) => {
    editedFields.current.add(field);
    setScenario((current) => ({ ...current, [field]: value }));
  };
  const resetScenario = () => {
    editedFields.current.clear();
    setScenario(base);
    setSource(refreshedSource);
  };
  const outputs = calculateScenario(scenario);
  const live = engine.live ?? {};

  if (!engine.ok) {
    return <div className="grid"><Fault name="Oil × Funding" reason={engine.reason ?? "not yet present in this snapshot"} span={12} /></div>;
  }

  return (
    <div className="oil-page">
      <header className="oil-hero">
        <div className="oil-hero__copy">
          <span className="oil-kicker">SEICHE CONTEXT ENGINE · NEVER IN THE COMPOSITE</span>
          <h1>Oil is short-term<br /><em>dollar funding</em> in motion.</h1>
          <p>
            A barrel is financed while it is stored, shipped and hedged. Higher rates reshape the
            curve; higher prices enlarge the same cargo's credit need; volatility converts future
            physical gains into cash calls today.
          </p>
          <div className="oil-hero__tags">
            <span><i className="crude" /> oil → cash demand</span>
            <span><i className="dollar" /> rates → oil curve</span>
            <span><i className="inr" /> India → rupee liquidity</span>
          </div>
          <AsOf asof={engine.asof} generatedAt={snap.generated_at} />
        </div>
        <div className="oil-hero__formula" aria-label="Cash and carry identity">
          <span>THE MECHANICAL LINK</span>
          <div><b>required contango</b><i>=</i></div>
          <div><strong>storage</strong><i>+</i></div>
          <div><strong>insurance</strong><i>+</i></div>
          <div className="oil-hero__rate"><strong>interest rate</strong><i>← money market</i></div>
          <small>Convenience yield, capacity and basis sit outside this mechanical hurdle.</small>
        </div>
      </header>

      <section className="oil-live" aria-label="Latest observed oil and funding readings">
        <LiveTile
          label="WTI spot"
          value={`$${fmt(live.wti?.price_usd_per_bbl, 2)}`}
          detail={`5d ${live.wti?.change_5d_usd > 0 ? "+" : ""}${fmt(live.wti?.change_5d_usd, 2)} · 20d ${live.wti?.change_20d_pct > 0 ? "+" : ""}${fmt(live.wti?.change_20d_pct, 1)}%`}
          asof={live.wti?.asof}
          tone={C.crude}
        />
        <LiveTile
          label="Brent spot"
          value={`$${fmt(live.brent?.price_usd_per_bbl, 2)}`}
          detail={`5d ${live.brent?.change_5d_usd > 0 ? "+" : ""}${fmt(live.brent?.change_5d_usd, 2)} · 20d ${live.brent?.change_20d_pct > 0 ? "+" : ""}${fmt(live.brent?.change_20d_pct, 1)}%`}
          asof={live.brent?.asof}
          tone={C.dollar}
        />
        <LiveTile
          label="Nonfinancial CP − bill"
          value={`${fmt(live.cp_nonfinancial?.spread_bp, 1)} bp`}
          detail={`20d ${live.cp_nonfinancial?.change_20d_bp > 0 ? "+" : ""}${fmt(live.cp_nonfinancial?.change_20d_bp, 1)} · ${ordinal(live.cp_nonfinancial?.percentile_3y)} pctl`}
          asof={live.cp_nonfinancial?.asof}
          tone={C.crude}
        />
        <LiveTile
          label="SOFR − IORB"
          value={`${fmt(live.sofr_iorb?.spread_bp, 1)} bp`}
          detail={`20d ${live.sofr_iorb?.change_20d_bp > 0 ? "+" : ""}${fmt(live.sofr_iorb?.change_20d_bp, 1)} · ${ordinal(live.sofr_iorb?.percentile_3y)} pctl`}
          asof={live.sofr_iorb?.asof}
          tone={C.refinery}
        />
        <LiveTile
          label="USD / INR"
          value={`₹${fmt(live.inr?.per_usd, 2)}`}
          detail={`20d ${live.inr?.change_20d_pct > 0 ? "+" : ""}${fmt(live.inr?.change_20d_pct, 1)}% · 60d ${live.inr?.change_60d_pct > 0 ? "+" : ""}${fmt(live.inr?.change_60d_pct, 1)}%`}
          asof={live.inr?.asof}
          tone={C.inr}
        />
      </section>

      <TransmissionLoop s={scenario} out={outputs} live={live} />
      <BallastSection ballast={snap.engines?.ballast} />
      <ObservedEvidence engine={engine} />
      <OilStructure engine={engine} />
      <ScenarioLab
        s={scenario}
        source={source}
        editedFields={editedFields.current}
        onFieldChange={updateScenarioField}
        onReset={resetScenario}
      />
      <SourcesAndLimits engine={engine} />
    </div>
  );
}
