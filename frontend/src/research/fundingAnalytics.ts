import {publishedFundingModels, atlasFundingModels, mergeFundingModels, readFundingAtlas} from "./fundingSources";
import {atlasUsMarket, observatoryCards, type AtlasRead} from "./observatoryCards";
import {readJSON, record, visibleRefresh} from './core';
import {seriesModel, type EconomicSeries} from './seriesModel';
import {chartCard, dashboard, stats, selectControl, el, difference, changes, windowPoints, type Point, type Series, type ChartSpec} from './analyticsCharts';
import './analytics.css';

const API = 'https://api.seiche.info';
export const FUNDING_KEYS = ['SOFR', 'EFFR', 'IORB', 'TGCR', 'BGCR', 'WRESBAL', 'TGA_LONG', 'RRPONTSYD', 'WALCL', 'CP_FIN_3M', 'CP_NONFIN_3M', 'DGS3M', 'DGS2', 'DGS10', 'DGS30', 'MMF_REPO_FED', 'MMF_REPO_FICC', 'MMF_REPO_TOT'];
const MMF_REMOTE_IDS: Record<string, string> = {MMF_REPO_TOT: 'MMF-MMF_RP_TOT-M', MMF_REPO_FED: 'MMF-MMF_RP_wFR-M', MMF_REPO_FICC: 'MMF-MMF_RP_wFICC-M'};
export function fundingPoints(model: EconomicSeries | undefined, unit: string, divisor = 1, cutoff = Date.now()): Point[] {
  if (!model || model.unit !== unit) return [];
  return model.points.map(p => ({x: Date.parse(p.date + 'T00:00:00Z'), y: p.value / divisor, label: p.date, note: `${model.key} · source state: ${model.state}`})).filter(p => p.x <= cutoff);
}
export function moneyFundBillions(model: EconomicSeries | undefined, cutoff = Date.now()): Point[] {
  const publishedColumn: Record<string, string> = {MMF_REPO_TOT:"total_repo_b", MMF_REPO_FED:"fed_repo_b", MMF_REPO_FICC:"ficc_repo_b"};
  if (model?.source === "published_board" && publishedColumn[model.key]
      && model.remote === "engines.money_market.charts.mmf." + publishedColumn[model.key] && model.unit === "$B") return fundingPoints(model, "$B", 1, cutoff);
  if (!model || model.source !== 'ofr' || !MMF_REMOTE_IDS[model.key] || model.remote !== MMF_REMOTE_IDS[model.key] || model.unit !== '$B') return [];
  // Match assemble._vol_b: the raw OFR endpoint can retain whole dollars while
  // declaring the configured $B unit. Inspect the full history, not its latest
  // value or selected window, so zero balances cannot change the unit boundary.
  const divisor = model.points.some(p => Math.abs(p.value) > 1e6) ? 1e9 : 1;
  return fundingPoints(model, '$B', divisor, cutoff);
}
// Treasury's own par yields only: the FRED constant-maturity copies are never read for the curve.
export const CURVE_KEYS = ['UST_3M', 'UST_2Y', 'UST_10Y', 'UST_30Y'];
export function treasuryCurve(models: Map<string, EconomicSeries>, cutoff = Date.now()): {points: Point[]; date: string | null} {
  const tenors: [string, number][] = [['UST_3M', .25], ['UST_2Y', 2], ['UST_10Y', 10], ['UST_30Y', 30]];
  const series = tenors.map(([key]) => models.get(key));
  if (series.some(s => !s || s.unit !== '%')) return {points: [], date: null};
  const dates = series[0]!.points.map(p => p.date).filter(date => Date.parse(date + 'T00:00:00Z') <= cutoff && series.every(s => s!.points.some(p => p.date === date)));
  const date = dates.at(-1) || null;
  return {date, points: date ? tenors.map(([key, tenor], i) => ({x: tenor, y: series[i]!.points.find(p => p.date === date)!.value, label: key, note: `Observed ${date}`})) : []};
}
// Charts without a public history say why they are empty: the FRED copy is withheld, and the
// publisher's own series is absent from this atlas response or, for commercial paper, awaits a
// rights decision.
const NO_PUBLIC_HISTORY = 'Missing data is not zero.';
export const WITHHELD_CHARTS = {
  reserves: `Withheld: the FRED copy is not cleared for republication, and the current atlas response has no public value from the Federal Reserve's own H.4.1 series. ${NO_PUBLIC_HISTORY}`,
  reverseRepo: `Withheld: the FRED copy is not cleared for republication, and the current atlas response has no public value from the New York Fed's own reverse repo series. ${NO_PUBLIC_HISTORY}`,
  commercialPaper: `Withheld: the Federal Reserve Board's commercial paper rates are derived from data supplied by DTCC and await a rights decision, and the FRED copies are not cleared for republication. Treasury's own three-month yield is served, but a premium needs both legs on one date. ${NO_PUBLIC_HISTORY}`,
  treasuryCurve: `Withheld: the FRED copies of the Treasury constant-maturity yields are not cleared for republication, and the current atlas response has no public value from Treasury's own par yields for all four maturities on one date. ${NO_PUBLIC_HISTORY}`,
  balanceSheet: `Withheld: the FRED copy of H.4.1 total assets is not cleared for republication, and the current atlas response has no public value from the Federal Reserve's own H.4.1 total assets. ${NO_PUBLIC_HISTORY}`,
} as const;
export function fundingSpecs(models: Map<string, EconomicSeries>, days: number | null): ChartSpec[] {
  const cutoff = Date.now();
  const end = Math.max(0, ...[...models.values()].flatMap(m => m.points.map(p => Date.parse(p.date + 'T00:00:00Z')).filter(date => date <= cutoff)));
  const raw = (key: string, unit = '%', divisor = 1) => fundingPoints(models.get(key), unit, divisor, cutoff);
  const points = (key: string, unit = '%', divisor = 1) => windowPoints(raw(key, unit, divisor), days, end);
  const named = (key: string, name = key, unit = '%', divisor = 1): Series => ({name, points: points(key, unit, divisor)});
  const moneyFund = (key: string, name: string): Series => ({name, points: windowPoints(moneyFundBillions(models.get(key), cutoff), days, end)});
  const spread = (left: string, right: string): Series => ({name: `${left} − ${right}`, points: windowPoints(models.has(left + '_MINUS_' + right) ? raw(left + '_MINUS_' + right, 'bp') : difference(raw(left), raw(right), 100), days, end)});
  const sourceModel = (key: string) => models.get(key) || models.get(key + '_MINUS_DGS3M');
  const sources = (keys: string[]) => keys.flatMap(key => {
    const m = sourceModel(key);
    const entry = {label: `${key} · ${m?.label || 'Source unavailable'} · ${m?.asOf || 'No observation date'} · ${m?.state || 'unavailable'}`, url: m?.sourceUrl || `${API}/api/series/${key}`};
    // A cited source keeps its terms beside its values.
    return m?.terms ? [entry, {label: `${m.attribution || key} · terms of use`, url: m.terms}] : [entry];
  });
  const base = (id: string, index: number, title: string, description: string, unit: string, keys: string[], note: string): ChartSpec => {
    const dates = keys.flatMap(key => {const m = sourceModel(key), p = m?.points.filter(p => Date.parse(p.date + 'T00:00:00Z') <= cutoff).at(-1); return p ? [p.date] : [];}).sort();
    const clock = dates.length ? `Latest input dates: ${dates[0]}${dates.at(-1) !== dates[0] ? ' to ' + dates.at(-1) : ''}. ` : '';
    const future = keys.some(key => models.get(key)?.points.some(p => Date.parse(p.date + 'T00:00:00Z') > cutoff));
    const cited = [...new Set(keys.map(key => sourceModel(key)?.attribution).filter((text): text is string => Boolean(text)))];
    return {id, number: String(index).padStart(2, '0'), title, description, unit, xType: 'date', sources: sources(keys),
      note: clock + note + (cited.length ? ' ' + cited.join(' ') + '.' : '') + (future ? ' Future-dated entries in the source are excluded from observed history.' : '')};
  };
  const curve = treasuryCurve(models);
  const daily = windowPoints(changes(raw('SOFR'), 1, 100), days, end);
  // The publishers' own series lead; a published board history remains the fallback.
  const reserves = models.has('RESERVES_DIRECT') ? 'RESERVES_DIRECT' : 'WRESBAL';
  const reverseRepo = models.has('ON_RRP_DIRECT')
    ? {key: 'ON_RRP_DIRECT', series: named('ON_RRP_DIRECT', 'ON RRP', '$M', 1000)}
    : {key: 'RRPONTSYD', series: named('RRPONTSYD', 'ON RRP', '$B')};
  const assets = models.has('TOTAL_ASSETS_DIRECT') ? 'TOTAL_ASSETS_DIRECT' : 'WALCL';
  // Treasury's own three-month par yield is the bill leg. No commercial paper leg is public, so a
  // premium appears only where a published premium already exists; nothing is reconstructed.
  const premium = (cp: string): Series => models.has(cp + '_MINUS_DGS3M')
    ? {name: `${cp} − DGS3M`, points: windowPoints(raw(cp + '_MINUS_DGS3M', 'bp'), days, end)}
    : spread(cp, 'UST_3M');
  return [
    {...base('funding-corridor', 1, 'Inside the overnight funding corridor', 'Secured funding, general collateral and the policy anchor on the same rate axis.', '%', ['SOFR', 'TGCR', 'BGCR', 'EFFR', 'IORB'], 'Observed rates. IORB is administered; SOFR, TGCR, BGCR and EFFR measure different transaction populations. Dashed and solid lines distinguish series. Gaps longer than ten days are left open.'), series: ['SOFR', 'TGCR', 'BGCR', 'EFFR', 'IORB'].map(k => named(k))},
    {...base('funding-spreads', 2, 'Where funding departs from the anchors', 'Exact-date spreads isolate rate differences in basis points.', 'bp', ['SOFR', 'IORB', 'EFFR', 'TGCR'], 'Derived: (left rate − right rate) × 100. Only identical observation dates are joined. A positive spread means the left rate is higher; it is not a distress probability.'), series: [spread('SOFR', 'IORB'), spread('EFFR', 'IORB'), spread('SOFR', 'TGCR')], zero: true},
    {...base('reserve-balances', 3, 'The banking system’s reserve buffer', 'Reserve balances held at Federal Reserve Banks.', 'USD bn', [reserves], 'Weekly average reserve balances, expressed in billions. A system-wide stock, not a measure of how evenly reserves are distributed.'), series: [named(reserves, 'Reserve balances', '$M', 1000)], maxGapDays: 15, empty: WITHHELD_CHARTS.reserves},
    {...base('treasury-cash', 4, 'Treasury cash parked at the Fed', 'The Treasury General Account’s own reporting history.', 'USD bn', ['TGA_LONG'], 'Source dollars in millions divided by 1,000. This is a cash stock; a chart movement is not attributed to an individual auction, tax payment or spending event.'), series: [named('TGA_LONG', 'Treasury General Account', '$M', 1000)], maxGapDays: 15},
    {...base('reverse-repo', 5, 'Cash absorbed by overnight reverse repo', 'ON RRP take-up makes the size of this liquidity buffer visible.', 'USD bn', [reverseRepo.key], 'Federal Reserve overnight reverse-repurchase take-up. Zero is a valid observation; absent dates remain absent.'), series: [reverseRepo.series], zero: true, empty: WITHHELD_CHARTS.reverseRepo},
    {...base('commercial-paper-premium', 6, 'Short-term corporate funding premiums', 'Three-month AA commercial paper against the three-month Treasury yield.', 'bp', ['CP_FIN_3M', 'CP_NONFIN_3M', 'UST_3M'], 'Derived from exact-date rate differences × 100. Commercial paper and Treasury yields have different market conventions; this is a reference spread, not a quoted arbitrage return.'), series: [premium('CP_FIN_3M'), premium('CP_NONFIN_3M')], zero: true, empty: WITHHELD_CHARTS.commercialPaper},
    {...base('treasury-curve', 7, 'The Treasury term structure', `Four published maturities, all observed on ${curve.date || 'a common date that is currently unavailable'}.`, '%', CURVE_KEYS, 'Treasury par yields at fixed maturities. Latest common observation date across all four maturities. Lines only connect the observed tenors; intervening maturities are not estimated. Independent of the history-window control.'), xType: 'linear', xLabel: 'Maturity · years', series: [{name: curve.date || 'Common date unavailable', points: curve.points}], empty: WITHHELD_CHARTS.treasuryCurve},
    {...base('overnight-repricing', 8, 'SOFR repricing, fixing by fixing', 'Changes between consecutive available daily observations.', 'bp / observation', ['SOFR'], 'Derived: (current SOFR − previous available SOFR) × 100. Weekends and holidays are not fabricated. The series describes observed repricing, not expected volatility.'), series: [{name: 'SOFR change', points: daily}], zero: true},
    {...base('mmf-repo-allocation', 9, 'Who receives money-fund repo cash?', 'Total repo lending alongside the Fed and FICC components.', 'USD bn', ['MMF_REPO_TOT', 'MMF_REPO_FED', 'MMF_REPO_FICC'], 'OFR dollar balances are normalized to billions using Seiche’s existing series-wide unit rule. Fed and FICC are components of the total, so these lines must not be added together. Monthly observations retain their native cadence.'), series: [moneyFund('MMF_REPO_TOT', 'Total repo'), moneyFund('MMF_REPO_FED', 'With the Fed'), moneyFund('MMF_REPO_FICC', 'With FICC')], maxGapDays: 65, zero: true},
    {...base('fed-balance-sheet', 10, 'The Federal Reserve balance sheet', 'Total assets provide the balance-sheet context around reserve conditions.', 'USD bn', [assets], 'H.4.1 total assets; source dollars in millions divided by 1,000. This series is not equated with reserves or combined into a synthetic net-liquidity score.'), series: [named(assets, 'Fed total assets', '$M', 1000)], maxGapDays: 15, empty: WITHHELD_CHARTS.balanceSheet},
  ];
}

export function mountFundingAnalytics(target: HTMLElement, snapshot: () => unknown = () => undefined): () => void {
  const view = dashboard(target, {eyebrow: 'SEICHE / FUNDING OBSERVATORY', title: 'Follow the pressure through the system.', description: 'From the overnight corridor to reserves, Treasury cash and money-fund repo. Ten analytical views preserve the clocks and definitions behind the funding picture.'});
  let models = publishedFundingModels(snapshot()), days: number | null = 365, request: AbortController | null = null, disposed = false;
  // The headline cards read direct atlas rows only; until the first read settles they say so.
  let cardSource: AtlasRead = {status: 'loading'};
  const render = () => {
    view.grid.replaceChildren(...fundingSpecs(models, days).map(chartCard));
    const cards = observatoryCards(cardSource);
    stats(view.stats, cards.map((card): [string, string, string] => [card.label, card.value, card.note]));
    [...view.stats.children].forEach((item, index) => {
      const card = cards[index];
      if (!(item instanceof HTMLElement) || !card) return;
      item.dataset.state = card.state;
      // A source that asks to be cited keeps its terms beside its value.
      if (card.terms) {
        const terms = el('a', card.terms.replace(/^https:\/\//, ''));
        terms.href = card.terms; terms.target = '_blank'; terms.rel = 'noopener noreferrer';
        item.querySelector('small')?.append(' · Terms: ', terms);
      }
    });
  };
  view.controls.append(selectControl('History window', [['90', '3 months'], ['365', '1 year'], ['all', 'All loaded observations']], '365', value => {days = value === 'all' ? null : Number(value); render();}));
  const refreshButton = el('button', 'Refresh sources'); refreshButton.type = 'button'; view.controls.append(refreshButton);
  const link = el('a', 'Explore the funding workbench →'); link.href = '#workbench'; view.controls.append(link);
  async function refresh() {
    if (request) return; const controller = new AbortController(); request = controller; refreshButton.disabled = true;
    // Disposal cancels everything. The large atlas and the small series reads keep separate time
    // budgets, and the series reads no longer wait for the atlas, so a slow atlas cannot abort them.
    const seriesBudget = new AbortController(), atlasBudget = new AbortController();
    const cancel = () => {seriesBudget.abort(); atlasBudget.abort();};
    controller.signal.addEventListener('abort', cancel, {once: true});
    const seriesTimer = setTimeout(() => seriesBudget.abort(), 45000), atlasTimer = setTimeout(() => atlasBudget.abort(), 90000);
    let next = publishedFundingModels(snapshot());
    view.status.textContent = 'Checking source dates and histories; dated observations stay visible…';
    try {
      const settle = (read: Promise<unknown>) => read.then(value => ({ok: true as const, value}), () => ({ok: false as const, value: undefined}));
      const atlasRead = settle(readFundingAtlas(atlasBudget.signal));
      const catalog = await settle(readJSON(`${API}/api/series/index.json`, seriesBudget.signal));
      const loaded = new Map<string, ReturnType<typeof seriesModel>>();
      if (catalog.ok && record(catalog.value) && catalog.value.schema === 'seiche.series-index.v1' && Array.isArray(catalog.value.series)) {
        const entries: unknown[] = catalog.value.series;
        for (let i = 0; i < FUNDING_KEYS.length; i += 3) {
          await Promise.all(FUNDING_KEYS.slice(i, i + 3).map(async key => {
            const entry = entries.find(row => record(row) && row.mnemonic === key);
            if (!record(entry) || entry.available !== true || entry.csv_restricted || entry.json !== `/api/series/${key}`) return;
            try {
              loaded.set(key, seriesModel(await readJSON(`${API}${entry.json}?n=520`, seriesBudget.signal), entry));
            } catch { /* Retain the dated published or previously collected history. */ }
          }));
        }
      }
      const atlas = await atlasRead;
      // Same precedence as before: published, then the atlas, then each series history in key order.
      if (atlas.ok) next = mergeFundingModels(next, atlasFundingModels(atlas.value));
      // A failed refresh keeps the last dated card values; only a first failure reports it.
      const market = atlas.ok ? atlasUsMarket(atlas.value) : null;
      if (market) cardSource = {status: 'ready', market};
      else if (cardSource.status !== 'ready') cardSource = {status: 'failed'};
      for (const key of FUNDING_KEYS) { const item = loaded.get(key); if (item) next = mergeFundingModels(next, new Map([[key, item]])); }
      if (disposed) return;
      models = mergeFundingModels(models, next); render();
      view.status.textContent = `${models.size} dated histories loaded. Each chart keeps its own source dates; unavailable histories remain explicit.`;
      if (!atlas.ok && !catalog.ok) view.status.textContent += ' Live refresh failed; dated histories retained.';
    } catch {
      if (cardSource.status === 'loading') cardSource = {status: 'failed'};
      if (!disposed) {render(); view.status.textContent = 'Live refresh failed; dated histories remain visible. Retry to check for newer observations.';}
    } finally {clearTimeout(seriesTimer); clearTimeout(atlasTimer); controller.signal.removeEventListener('abort', cancel); request = null; if (!disposed) refreshButton.disabled = false;}
  }
  const updateSnapshot = () => {models = mergeFundingModels(models, publishedFundingModels(snapshot())); render();};
  target.addEventListener("funding-snapshot", updateSnapshot);
  refreshButton.addEventListener('click', () => void refresh()); render();
  const stop = visibleRefresh(refresh, 60000); return () => {disposed = true; target.removeEventListener("funding-snapshot", updateSnapshot); stop(); request?.abort();};
}
