import { useMemo } from "react";
import { P } from "../palette";
import Chart from "../Chart";
import { Any, fmt, Method, Roll } from "../lib";

/* ---------------------------------------------------------------------------
   SCARCITY: the reserve demand curve, the Fed comparison, the runway and the
   ledger the whole thing is built on. Four cards, in the order a reader needs
   them: where the hinge is, whether our hinge tracks the official statistic,
   when the path reaches it, and which liability the dollars actually sit in.
   Every card degrades to its engine's own reason rather than to a blank.
   ------------------------------------------------------------------------- */

const B = (v: number | null | undefined, d = 0) => (v == null ? "not published" : `$${fmt(v, d)}B`);

/* ---------------------------------------------------------------------------
   1. Reserve Demand Curve. The hinge fit: spread = a + slope * max(0, kink -
   reserves/GDP). Fit quality leads rather than hides, because an R2 near 0.6
   makes the kink a region and not a line. The engine's scatter is weekly
   points in reserve-ratio space, and the board's Chart is a time axis, so the
   points are bucketed into a table instead of being drawn on the wrong axis.
   ------------------------------------------------------------------------- */
function KinkCard({ k }: { k: Any }) {
  const gdp =
    k?.kink_reserves_b != null && k?.kink_ratio ? Number(k.kink_reserves_b) / Number(k.kink_ratio) : null;

  const buckets = useMemo(() => {
    const raw: Any[] = Array.isArray(k?.scatter) ? k.scatter : [];
    const pts = raw
      .filter((p) => Array.isArray(p) && p.length >= 2)
      .map((p) => [Number(p[0]), Number(p[1])])
      .filter(([x, y]) => isFinite(x) && isFinite(y));
    if (pts.length < 20) return null;
    const xs = pts.map((p) => p[0]);
    const lo = Math.min(...xs), hi = Math.max(...xs);
    if (!(hi > lo)) return null;
    const N = 5, w = (hi - lo) / N;
    const out: Any[] = [];
    for (let i = 0; i < N; i++) {
      const a = lo + i * w, b = i === N - 1 ? hi : lo + (i + 1) * w;
      const inBin = pts.filter(([x]) => x >= a && (i === N - 1 ? x <= b : x < b));
      if (!inBin.length) continue;
      const ys = inBin.map((p) => p[1]).sort((m, n) => m - n);
      const mid =
        ys.length % 2 ? ys[(ys.length - 1) / 2] : (ys[ys.length / 2 - 1] + ys[ys.length / 2]) / 2;
      out.push({ lo: a, hi: b, mid: (a + b) / 2, n: inBin.length, median: mid });
    }
    return out.length >= 2 ? out : null;
  }, [k]);

  if (!k?.ok) {
    return (
      <div className="card span6">
        <h2>Reserve Demand Curve</h2>
        <div className="sub">the hinge: where the funding spread stops ignoring reserves and starts pricing scarcity</div>
        <div className="faults">unavailable: {k?.reason ?? "no kink fit"}</div>
      </div>
    );
  }

  const dist = Number(k.distance_b ?? 0);
  const inside = dist <= 0;
  const drift = Number(k.drain_per_bday_b ?? 0);
  const r2 = k.r2 as number | null | undefined;
  const gap =
    k.predicted_spread_now_bp != null && k.observed_spread_now_bp != null
      ? Number(k.predicted_spread_now_bp) - Number(k.observed_spread_now_bp)
      : null;
  const fitWord =
    r2 == null ? "no fit statistic published"
      : r2 >= 0.75 ? "a firm fit"
      : r2 >= 0.55 ? "a modest fit and nothing more"
      : r2 >= 0.35 ? "a weak fit"
      : "too weak to lean on";
  const daysToKink =
    k.days_to_kink != null ? `${fmt(k.days_to_kink, 0)}bd`
      : inside ? "already inside"
      : "not on this drift";

  return (
    <div className="card span6">
      <h2>Reserve Demand Curve</h2>
      <div className="sub">
        the hinge fit: reserves are abundant while the SOFR-IORB spread ignores them, and scarce once the
        curve turns down. The NY Fed estimates this curve as periodic research; this fits it continuously,
        grid-searching the breakpoint on weekly public data.
      </div>
      <div className="kv">
        <div className="item"><div className="k">fitted kink</div>
          <div className="v">$<Roll v={k.kink_reserves_b} d={0} />B</div></div>
        <div className="item"><div className="k">reserves now</div>
          <div className="v">$<Roll v={k.current_reserves_b} d={0} />B</div></div>
        <div className="item"><div className="k">distance</div>
          <div className={`v ${inside ? "bad" : Math.abs(dist) < 200 ? "warn" : ""}`}>
            {B(Math.abs(dist))} {inside ? "below" : "above"}
          </div></div>
        <div className="item"><div className="k">days to kink</div>
          <div className={`v ${inside ? "bad" : ""}`}>{daysToKink}</div></div>
        <div className="item"><div className="k">reserve drift / bday</div>
          <div className="v">{`${drift >= 0 ? "+" : "-"}$${fmt(Math.abs(drift), 2)}B`}
            <span className="dimsmall"> {drift >= 0 ? "building" : "draining"}</span></div></div>
        <div className="item"><div className="k">fit R2</div>
          <div className={`v ${r2 != null && r2 < 0.35 ? "bad" : r2 != null && r2 < 0.55 ? "warn" : ""}`}>
            {fmt(r2, 3)}</div></div>
        <div className="item"><div className="k">consistency</div>
          <div className={`v ${(k.consistency ?? 1) < 0.6 ? "warn" : ""}`}>{fmt(k.consistency, 2)}</div></div>
        <div className="item"><div className="k">kink ratio</div>
          <div className="v">{fmt((k.kink_ratio ?? 0) * 100, 2)}%
            <span className="dimsmall"> of GDP</span></div></div>
      </div>

      <div className="crunch">
        <b>fit quality</b> R2 {fmt(r2, 3)}
        {r2 != null && `, so the hinge accounts for about ${fmt(r2 * 100, 0)}% of the weekly variance in the spread: ${fitWord}`}.
        {r2 != null && r2 >= 0.35
          ? " That clears the 0.35 line where the engine stops discounting the sub-score, which is a floor and not a badge."
          : " That sits under the 0.35 line, so the engine discounts the sub-score for it."}
        {" "}Read the kink as a region several hundred billion wide, not as a line.
      </div>

      <div className="sub">
        model against market: the fit predicts {fmt(k.predicted_spread_now_bp, 1)}bp at today's reserve level
        and the market is printing {fmt(k.observed_spread_now_bp, 1)}bp
        {gap != null && `, a gap of ${fmt(Math.abs(gap), 1)}bp`}. The engine carries that disagreement as the
        consistency discount above rather than assuming the model is right.
      </div>

      {buckets && (
        <>
          <table className="mini">
            <thead>
              <tr>
                <th colSpan={5}>the hinge in raw points, no model: weekly observations bucketed by reserve ratio</th>
              </tr>
              <tr>
                <th>reserves / GDP</th><th>at today's GDP</th><th>weeks</th>
                <th>median SOFR-IORB</th><th>side of the kink</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((b: Any) => {
                const kr = k.kink_ratio == null ? null : Number(k.kink_ratio);
                const straddles = kr != null && kr >= b.lo && kr <= b.hi;
                const scarce = kr != null && b.mid < kr;
                const side = kr == null ? "no kink" : straddles ? "straddles the kink" : scarce ? "scarce" : "abundant";
                return (
                  <tr key={b.lo}>
                    <td className="num">{fmt(b.lo * 100, 2)} to {fmt(b.hi * 100, 2)}%</td>
                    <td className="num">{gdp ? B(b.mid * gdp) : "not derivable"}</td>
                    <td className="num">{b.n}</td>
                    <td className="num" style={{ color: b.median > 0 ? P.stress : b.median > -5 ? P.strain : undefined }}>
                      {b.median > 0 ? "+" : ""}{fmt(b.median, 1)}bp
                    </td>
                    <td style={{ color: straddles ? P.erosion : scarce ? P.strain : P.calm }}>{side}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="dimsmall">
            observations only, before any fitting: each row is the median SOFR-IORB print across the weeks whose
            reserve ratio landed in that band, so the shape the hinge is estimating is visible without the hinge.
            {gdp && ` ratios are restated in today's dollars at the implied nominal GDP of ${B(gdp)}.`}
          </div>
        </>
      )}

      <Method>
        {k.method} · slope {fmt(k.slope_bp_per_ratio, 1)}bp per unit of reserves/GDP · as of {k.asof} ·
        the scatter is fit in reserve-ratio space, so it is bucketed here rather than drawn on the board's
        time axis
      </Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   2. The Fed Comparison. Their statistic, our public-data approximation of it,
   graded against their own published print and bands. The scorecard is
   walk-forward: each row refits on inputs truncated to that release period.
   ------------------------------------------------------------------------- */
function FedComparisonCard({ n }: { n: Any }) {
  if (!n?.ok) {
    return (
      <div className="card span6">
        <h2>The Fed Comparison: Reserve Demand Elasticity</h2>
        <div className="sub">our continuous fit against the NY Fed's own monthly RDE print</div>
        <div className="faults">unavailable: {n?.reason ?? "no comparison"}</div>
      </div>
    );
  }

  const rows: Any[] = Array.isArray(n.scorecard) ? n.scorecard : [];
  const graded = rows.filter((r) => r.gradable);
  const banded = graded.filter((r) => r.within_band != null);
  const s = n.scorecard_summary ?? {};
  const bandRate = banded.length ? (s.within_band ?? 0) / banded.length : null;
  const b68: number[] = Array.isArray(n.nyfed_band_68) ? n.nyfed_band_68 : [];
  const b95: number[] = Array.isArray(n.nyfed_band_95) ? n.nyfed_band_95 : [];

  return (
    <div className="card span6">
      <h2>The Fed Comparison: Reserve Demand Elasticity</h2>
      <div className="sub">
        the NY Fed publishes Reserve Demand Elasticity monthly (Afonso, Giannone, La Spada and Williams):
        basis points of funds-IORB spread per 1 percent change in reserves. This is not their model and not
        their data. It is an approximation of their statistic built from free public series, mapped into
        their units and graded against their published median and bands. Their print is the referee. Ours
        is only the reading between releases.
      </div>
      <div className="kv">
        <div className="item"><div className="k">ours (bp per 1% reserves)</div>
          <div className="v"><Roll v={n.ours_bp_per_1pct} d={3} /></div></div>
        <div className="item"><div className="k">NY Fed print</div>
          <div className="v"><Roll v={n.nyfed_bp_per_1pct} d={3} /></div></div>
        <div className="item"><div className="k">their 68 band</div>
          <div className="v" style={{ fontSize: 14 }}>
            {b68.length === 2 ? `${fmt(b68[0], 3)} to ${fmt(b68[1], 3)}` : "not published"}</div></div>
        <div className="item"><div className="k">their 95 band</div>
          <div className="v" style={{ fontSize: 14 }}>
            {b95.length === 2 ? `${fmt(b95[0], 3)} to ${fmt(b95[1], 3)}` : "not published"}</div></div>
        <div className="item"><div className="k">inside their 68 band</div>
          <div className={`v ${n.within_68_band === false ? "bad" : ""}`}>
            {n.within_68_band == null ? "no band" : n.within_68_band ? "yes" : "no"}</div></div>
        <div className="item"><div className="k">direction agrees</div>
          <div className={`v ${n.direction_agree === false ? "bad" : ""}`}>
            {n.direction_agree ? "yes" : "no"}</div></div>
        <div className="item"><div className="k">nowcast lead</div>
          <div className="v">{fmt(n.nowcast_lead_days, 0)}d
            <span className="dimsmall"> ahead of their print</span></div></div>
        <div className="item"><div className="k">their print as of</div>
          <div className="v" style={{ fontSize: 13 }}>{n.nyfed_asof}</div></div>
      </div>

      {s.n != null && (
        <div className="crunch">
          <b>the grade</b> {s.within_band} of {banded.length} releases landed inside their 68 percent band
          {bandRate != null && ` (${fmt(bandRate * 100, 0)}%)`}, {s.direction_agree} of {s.n} agreed on
          direction, mean absolute gap {fmt(s.mean_abs_diff_bp, 3)}bp.
          {bandRate != null && bandRate >= 0.68
            ? " A calibrated nowcast lands inside a 68 percent band about two times in three, and this one does."
            : " A calibrated nowcast lands inside a 68 percent band about two times in three, and this one lands under that, so the sample is wider than their stated uncertainty allows."}
          {" "}We are on the {n.our_side_of_kink} side of our own kink, where the two methods are least
          comparable.
        </div>
      )}

      {rows.length > 0 && (
        <table className="mini">
          <thead>
            <tr><th colSpan={5}>walk-forward: each row refits on inputs truncated to that release period</th></tr>
            <tr><th>their release cutoff</th><th>ours</th><th>NY Fed (68 band)</th><th>in band</th><th>direction</th></tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((r: Any) => {
              const band: number[] = Array.isArray(r.nyfed_band_68) ? r.nyfed_band_68 : [];
              if (!r.gradable) {
                return (
                  <tr key={r.cutoff}>
                    <td className="num">{r.cutoff}</td>
                    <td className="dimsmall" colSpan={4}>not gradable: {r.reason ?? "no fit"}</td>
                  </tr>
                );
              }
              return (
                <tr key={r.cutoff}>
                  <td className="num">{r.cutoff}</td>
                  <td className="num">{fmt(r.ours_bp_per_1pct, 3)}</td>
                  <td className="num">{fmt(r.nyfed_bp_per_1pct, 3)}
                    {band.length === 2 && (
                      <span className="dimsmall"> [{fmt(band[0], 2)}, {fmt(band[1], 2)}]</span>
                    )}
                  </td>
                  <td style={{ color: r.within_band == null ? undefined : r.within_band ? P.calm : P.stress }}>
                    {r.within_band == null ? "no band" : r.within_band ? "yes" : "no"}
                  </td>
                  <td style={{ color: r.direction_agree ? P.calm : P.stress }}>
                    {r.direction_agree ? "yes" : "no"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {(n.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{n.method}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   3. Reserve Runway. Three stated scenarios, thirteen weeks, one fitted kink
   line. Assumptions render as data because a projection with hidden
   assumptions is worth nothing to this audience.
   ------------------------------------------------------------------------- */
const SCENARIOS = [
  { key: "base", label: "base", basis: "trailing 13w drift plus the stated QT pace, TGA reverting to its trailing median" },
  { key: "fast_drain", label: "fast drain", basis: "same, but the TGA rebuilds to its trailing p75 and no RRP buffer" },
  { key: "slow", label: "slow", basis: "base, but ON RRP absorbs half of each week's drain until the balance is spent" },
];

const ASSUMPTION_LABELS: Record<string, string> = {
  horizon_weeks: "horizon (weeks)",
  trailing_drift_b_per_week: "trailing drift ($B per week)",
  drift_window_weeks: "drift window (weeks)",
  qt_pace_b_per_month: "stated QT pace ($B per month)",
  qt_b_per_week: "QT ($B per week)",
  tga_now_b: "TGA now ($B)",
  tga_median_b: "TGA trailing median ($B)",
  tga_p75_b: "TGA trailing p75 ($B)",
  tga_window_obs: "TGA window (daily prints)",
  rrp_now_b: "ON RRP now ($B)",
  slow_rrp_buffer_frac: "slow leg: share of the drain RRP absorbs",
  settlements_gross_b: "settlements in the window, gross ($B)",
  settlement_passthrough: "settlement passthrough (net new cash)",
  kink_reserves_b: "fitted kink ($B)",
  start_reserves_b: "start reserves ($B)",
  start_date: "start date",
};

const assumptionValue = (v: unknown): string =>
  typeof v === "number" ? fmt(v, Number.isInteger(v) ? 0 : 2)
    : v == null ? "not stated"
      : typeof v === "boolean" ? (v ? "yes" : "no")
        : String(v);

function RunwayCard({ r }: { r: Any }) {
  const chartRows = useMemo(() => {
    const sc = r?.scenarios ?? {};
    const byDate = new Map<string, (number | null)[]>();
    SCENARIOS.forEach((s, i) => {
      const path: Any[] = Array.isArray(sc[s.key]?.path) ? sc[s.key].path : [];
      for (const p of path) {
        if (!Array.isArray(p) || p.length < 2) continue;
        const d = String(p[0]);
        const row = byDate.get(d) ?? [null, null, null];
        row[i] = Number(p[1]);
        byDate.set(d, row);
      }
    });
    return [...byDate.entries()]
      .sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .map(([d, v]) => [d, ...v] as (string | number | null)[]);
  }, [r]);

  if (!r?.ok) {
    return (
      <div className="card span12">
        <h2>Reserve Runway</h2>
        <div className="sub">thirteen weeks of reserve arithmetic under three stated scenarios</div>
        <div className="faults">unavailable: {r?.reason ?? "no projection"}</div>
      </div>
    );
  }

  const a = r.assumptions ?? {};
  const kinkB = a.kink_reserves_b == null ? null : Number(a.kink_reserves_b);
  const entries = Object.entries(a);
  const half = Math.ceil(entries.length / 2);
  const halves = [entries.slice(0, half), entries.slice(half)];

  return (
    <div className="card span12">
      <h2>Reserve Runway</h2>
      <div className="sub">
        the kink says where scarcity starts, this says when the path gets there: the weekly reserve identity
        walked forward thirteen weeks under three scenarios that differ only in the TGA path and whether the
        ON RRP still buffers anything. Arithmetic on stated assumptions, not a forecast of policy.
      </div>

      <table className="mini">
        <thead>
          <tr><th>leg</th><th>what it assumes</th><th>13 week end level</th><th>crosses the kink</th><th>verdict</th></tr>
        </thead>
        <tbody>
          {SCENARIOS.map((s) => {
            const sc = r.scenarios?.[s.key];
            if (!sc) {
              return (
                <tr key={s.key}>
                  <td>{s.label}</td>
                  <td className="dimsmall">{s.basis}</td>
                  <td className="dimsmall" colSpan={3}>not published in this snapshot</td>
                </tr>
              );
            }
            const wk = sc.crossing_week;
            const tone = wk === 0 ? P.stress : wk != null ? P.strain : undefined;
            return (
              <tr key={s.key}>
                <td>{s.label}</td>
                <td className="dimsmall">{s.basis}</td>
                <td className="num">{B(sc.end_reserves_b)}</td>
                <td className="num" style={{ color: tone }}>
                  {sc.crossing_date ? `${sc.crossing_date}${wk != null ? ` (week ${wk})` : ""}` : "no crossing in the window"}
                </td>
                <td className="dimsmall">{sc.verdict}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {chartRows.length > 1 && (
        <Chart
          rows={chartRows}
          series={[
            { label: "base", color: P.accent },
            { label: "fast drain", color: P.stress },
            { label: "slow", color: P.calm },
          ]}
          height={200}
          yLabel="reserves $B"
          refLine={kinkB == null ? null : { value: kinkB, color: P.erosion, label: "fitted kink" }}
        />
      )}

      <div className="sub" style={{ marginTop: 10 }}>
        the assumptions, in full: every input the paths lean on, published rather than buried, because a
        projection whose assumptions are hidden is worth nothing. They ship as data with the paths, so both
        can be graded together later.
      </div>
      <div className="warehouse-row">
        {halves.map((chunk, i) => (
          <table className="mini" style={{ maxWidth: 430 }} key={i}>
            <thead><tr><th>assumption</th><th>value</th></tr></thead>
            <tbody>
              {chunk.map(([key, val]) => (
                <tr key={key}>
                  <td>{ASSUMPTION_LABELS[key] ?? key.replace(/_/g, " ")}</td>
                  <td className="num">{assumptionValue(val)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>

      {(r.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{r.method} · start {a.start_date ?? r.asof}</Method>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   4. Where the Dollars Sit. The H.4.1 identity closed to the dollar, then
   differenced so the week's reserve move gets attributed to a named liability.
   The residual is printed at full size: it is real balance sheet the ledger
   does not name, not an error term, and a residual that jumps is the alarm
   that one of the named legs is being read wrong.
   ------------------------------------------------------------------------- */
const LEVEL_ROWS = [
  { key: "currency_b", label: "currency in circulation" },
  { key: "reserves_b", label: "reserves (bank balances at the Fed)" },
  { key: "tga_b", label: "Treasury General Account" },
  { key: "onrrp_b", label: "ON RRP" },
  { key: "foreign_rrp_b", label: "foreign RRP" },
  { key: "residual_b", label: "unnamed residual" },
];

const LEG_SHORT: Record<string, string> = {
  assets: "assets",
  currency: "currency",
  tga: "TGA",
  onrrp: "ON RRP",
  foreign_rrp: "foreign RRP",
  residual: "residual",
};

function LedgerCard({ l }: { l: Any }) {
  if (!l?.ok) {
    return (
      <div className="card span12">
        <h2>Where the Dollars Sit</h2>
        <div className="sub">
          the H.4.1 identity: Fed assets = currency + reserves + TGA + ON RRP + foreign RRP + residual
        </div>
        <div className="faults">unavailable: {l?.reason ?? "the ledger engine is not in this snapshot"}</div>
      </div>
    );
  }

  const lv = l.levels ?? {};
  const at = l.attribution ?? {};
  const rc = l.residual_check ?? {};
  const assets = Number(lv.assets_b ?? 0);
  const sigma = typeof rc.sigma_b === "number" ? Math.abs(rc.sigma_b) : null;
  const legs: Any[] = (Array.isArray(l.legs) && l.legs.length ? l.legs : Object.keys(LEG_SHORT).map((k) => ({ key: k })))
    .map((g: Any) => ({ key: g.key, label: LEG_SHORT[g.key] ?? String(g.key).replace(/_/g, " ") }));
  const flows: Any[] = Array.isArray(l.flows_13w) ? l.flows_13w : [];

  return (
    <div className="card span12">
      <h2>Where the Dollars Sit</h2>
      <div className="sub">
        reserves moved, so which liability took the other side. The Fed's own balance sheet answers it as an
        accounting identity: assets = currency + reserves + TGA + ON RRP + foreign RRP + residual, closed to
        the dollar every Wednesday. Differencing the same identity attributes the week's reserve change to a
        named leg, with every flow signed as its contribution to reserves.
      </div>

      {l.letter_line && <div className="crunch"><b>the week</b> {l.letter_line}</div>}

      <div className="warehouse-row">
        <table className="mini" style={{ maxWidth: 460 }}>
          <thead>
            <tr><th colSpan={3}>levels, {lv.date ?? l.asof}</th></tr>
            <tr><th>leg</th><th>level</th><th>share of assets</th></tr>
          </thead>
          <tbody>
            <tr style={{ fontWeight: 600 }}>
              <td>Fed assets (WALCL)</td>
              <td className="num">{B(lv.assets_b)}</td>
              <td className="num">100%</td>
            </tr>
            {LEVEL_ROWS.map((row) => {
              const v = lv[row.key];
              const isResidual = row.key === "residual_b";
              return (
                <tr key={row.key} style={isResidual ? { fontWeight: 600 } : undefined}>
                  <td style={isResidual ? { color: P.erosion } : undefined}>{row.label}</td>
                  <td className="num">{B(v)}</td>
                  <td className="num">
                    {v == null || !assets ? "not derivable" : `${fmt((Number(v) / assets) * 100, 2)}%`}
                  </td>
                </tr>
              );
            })}
            <tr>
              <td className="dimsmall">closing check (assets minus every leg)</td>
              <td className="num">{fmt(lv.check_b, 2)}</td>
              <td className="dimsmall">zero by construction</td>
            </tr>
          </tbody>
        </table>

        <div style={{ flex: "1 1 380px", minWidth: 300 }}>
          <div className="kv">
            <div className="item"><div className="k">reserves change on the week</div>
              <div className={`v ${(at.reserves_chg_b ?? 0) < 0 ? "warn" : ""}`}>
                {(at.reserves_chg_b ?? 0) > 0 ? "+" : ""}<Roll v={at.reserves_chg_b} d={1} />B</div></div>
            <div className="item"><div className="k">it came from</div>
              <div className="v" style={{ fontSize: 14 }}>{at.driver_label ?? at.driver ?? "no driver named"}</div></div>
            <div className="item"><div className="k">that leg contributed</div>
              <div className="v">{(at.driver_contribution_b ?? 0) > 0 ? "+" : ""}{fmt(at.driver_contribution_b, 1)}B</div></div>
            <div className="item"><div className="k">share of gross flow</div>
              <div className="v">{at.driver_share_of_gross == null ? "not derivable" : `${fmt(at.driver_share_of_gross * 100, 0)}%`}
                <span className="dimsmall"> {at.sole_driver ? "sole driver" : "one of several"}</span></div></div>
          </div>
          <div className="kv">
            <div className="item"><div className="k">unnamed residual</div>
              <div className="v">$<Roll v={rc.level_b} d={1} />B</div></div>
            <div className="item"><div className="k">share of assets</div>
              <div className="v">{rc.share_pct == null ? "not derivable" : `${fmt(rc.share_pct, 2)}%`}</div></div>
            <div className="item"><div className="k">its move this week</div>
              <div className={`v ${rc.jump ? "bad" : ""}`}>
                {(rc.chg_b ?? 0) > 0 ? "+" : ""}{fmt(rc.chg_b, 1)}B
                <span className="dimsmall"> vs weekly sigma {fmt(rc.sigma_b, 1)}B</span></div></div>
            <div className="item"><div className="k">robust z</div>
              <div className={`v ${rc.jump ? "bad" : ""}`}>{fmt(rc.robust_z, 2)}
                <span className="dimsmall"> {rc.jump ? "JUMP" : "holding"}</span></div></div>
          </div>
          {rc.verdict && <div className="crunch"><b>residual</b> {rc.verdict}</div>}
          {rc.note && <div className="sub">{rc.note}</div>}
        </div>
      </div>

      {flows.length > 0 && (
        <table className="mini">
          <thead>
            <tr>
              <th colSpan={legs.length + 3}>
                the flow attribution, {flows.length} weeks: every leg signed as its contribution to reserves,
                the {legs.length} summing to the reserve change to the dollar
              </th>
            </tr>
            <tr>
              <th>week</th>
              <th>Δ reserves</th>
              {legs.map((g) => <th key={g.key}>{g.label}</th>)}
              <th>check</th>
            </tr>
          </thead>
          <tbody>
            {[...flows].reverse().map((f: Any) => (
              <tr key={f.date}>
                <td className="num">{f.date}</td>
                <td className="num" style={{ color: (f.reserves_chg_b ?? 0) < 0 ? P.stress : P.calm }}>
                  {(f.reserves_chg_b ?? 0) > 0 ? "+" : ""}{fmt(f.reserves_chg_b, 1)}
                </td>
                {legs.map((g) => {
                  const v = f[`${g.key}_b`];
                  const loud = g.key === "residual" && sigma != null && v != null && Math.abs(Number(v)) > sigma;
                  return (
                    <td className="num" key={g.key} style={{ color: loud ? P.erosion : undefined }}>
                      {v == null ? "not published" : `${Number(v) > 0 ? "+" : ""}${fmt(Number(v), 1)}`}
                    </td>
                  );
                })}
                <td className="num dimsmall">{fmt(f.check_b, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="dimsmall">
        the identity closes by construction because the residual is defined as the plug, so a balanced ledger
        proves nothing. The test that matters is whether the residual holds still.
        {l.currency_named === false && " Currency in circulation was not supplied, so the note liability is sitting inside the residual."}
        {l.onrrp_named === false && " No ON RRP series was supplied, so the facility balance is sitting inside the residual."}
      </div>
      {(l.caveats ?? []).map((c: string, i: number) => <div className="caveat" key={i}>▸ {c}</div>)}
      <Method>{l.method} · as of {l.asof}</Method>
    </div>
  );
}

export default function Scarcity({ snap }: { snap: Any }) {
  const e = snap.engines ?? {};
  return (
    <div className="grid">
      <KinkCard k={e.kink} />
      <FedComparisonCard n={e.rdenowcast} />
      <RunwayCard r={e.runway} />
      <LedgerCard l={e.ledger} />
    </div>
  );
}
