import {readJSON, visibleRefresh} from './core';
import {type EconomicSeries} from './seriesModel';
import {FUNDING_KEYS, publishedFunding, refreshFunding, retainFunding} from './fundingHistory';
import {chartCard, dashboard, stats, selectControl, el, number, difference, changes, windowPoints, type Point, type Series, type ChartSpec} from './analyticsCharts';
import './analytics.css';

const API = 'https://api.seiche.info';
export {FUNDING_KEYS} from './fundingHistory';
const MMF_REMOTE_IDS: Record<string, string> = {MMF_REPO_TOT: 'MMF-MMF_RP_TOT-M', MMF_REPO_FED: 'MMF-MMF_RP_wFR-M', MMF_REPO_FICC: 'MMF-MMF_RP_wFICC-M'};
export function fundingPoints(model: EconomicSeries | undefined, unit: string, divisor = 1, cutoff = Date.now()): Point[] {
  if (!model || model.unit !== unit) return [];
  return model.points.map(p => ({x: Date.parse(p.date + 'T00:00:00Z'), y: p.value / divisor, label: p.date, note: `${model.key} · source state: ${model.state}`})).filter(p => p.x <= cutoff);
}
export function moneyFundBillions(model: EconomicSeries | undefined, cutoff = Date.now()): Point[] {
  if (!model || model.source !== 'ofr' || !MMF_REMOTE_IDS[model.key] || model.remote !== MMF_REMOTE_IDS[model.key] || model.unit !== '$B') return [];
  // Match assemble._vol_b: the raw OFR endpoint can retain whole dollars while
  // declaring the configured $B unit. Inspect the full history, not its latest
  // value or selected window, so zero balances cannot change the unit boundary.
  const divisor = model.points.some(p => Math.abs(p.value) > 1e6) ? 1e9 : 1;
  return fundingPoints(model, '$B', divisor, cutoff);
}
export function treasuryCurve(models: Map<string, EconomicSeries>, cutoff = Date.now()): {points: Point[]; date: string | null} {
  const tenors: [string, number][] = [['DGS3M', .25], ['DGS2', 2], ['DGS10', 10], ['DGS30', 30]];
  const series = tenors.map(([key]) => models.get(key));
  if (series.some(s => !s || s.unit !== '%')) return {points: [], date: null};
  const dates = series[0]!.points.map(p => p.date).filter(date => Date.parse(date + 'T00:00:00Z') <= cutoff && series.every(s => s!.points.some(p => p.date === date)));
  const date = dates.at(-1) || null;
  return {date, points: date ? tenors.map(([key, tenor], i) => ({x: tenor, y: series[i]!.points.find(p => p.date === date)!.value, label: key, note: `Observed ${date}`})) : []};
}
export function fundingSpecs(models: Map<string, EconomicSeries>, days: number | null): ChartSpec[] {
  const cutoff = Date.now();
  const end = Math.max(0, ...[...models.values()].flatMap(m => m.points.map(p => Date.parse(p.date + 'T00:00:00Z')).filter(date => date <= cutoff)));
  const raw = (key: string, unit = '%', divisor = 1) => fundingPoints(models.get(key), unit, divisor, cutoff);
  const points = (key: string, unit = '%', divisor = 1) => windowPoints(raw(key, unit, divisor), days, end);
  const named = (key: string, name = key, unit = '%', divisor = 1): Series => ({name, points: points(key, unit, divisor)});
  const moneyFund = (key: string, name: string): Series => ({name, points: windowPoints(moneyFundBillions(models.get(key), cutoff), days, end)});
  const spread = (left: string, right: string): Series => ({name: `${left} − ${right}`, points: windowPoints(difference(raw(left), raw(right), 100), days, end)});
  const sources = (keys: string[]) => keys.map(key => ({label: `${key} · ${models.get(key)?.label || 'Source unavailable'} · ${models.get(key)?.asOf || 'No observation date'} · ${models.get(key)?.state || 'unavailable'}`, url: `${API}/api/series/${key}`}));
  const base = (id: string, index: number, title: string, description: string, unit: string, keys: string[], note: string): ChartSpec => {
    const dates = keys.flatMap(key => {const m = models.get(key), p = m?.points.filter(p => Date.parse(p.date + 'T00:00:00Z') <= cutoff).at(-1); return p ? [p.date] : [];}).sort();
    const clock = dates.length ? `Latest input dates: ${dates[0]}${dates.at(-1) !== dates[0] ? ' to ' + dates.at(-1) : ''}. ` : '';
    const future = keys.some(key => models.get(key)?.points.some(p => Date.parse(p.date + 'T00:00:00Z') > cutoff));
    return {id, number: String(index).padStart(2, '0'), title, description, unit, xType: 'date', sources: sources(keys), note: clock + note + (future ? ' Future-dated entries in the source are excluded from observed history.' : '')};
  };
  const curve = treasuryCurve(models);
  const daily = windowPoints(changes(raw('SOFR'), 1, 100), days, end);
  return [
    {...base('funding-corridor', 1, 'Inside the overnight funding corridor', 'Secured funding, general collateral and the policy anchor on the same rate axis.', '%', ['SOFR', 'TGCR', 'BGCR', 'EFFR', 'IORB'], 'Observed rates. IORB is administered; SOFR, TGCR, BGCR and EFFR measure different transaction populations. Dashed and solid lines distinguish series. Gaps longer than ten days are left open.'), series: ['SOFR', 'TGCR', 'BGCR', 'EFFR', 'IORB'].map(k => named(k))},
    {...base('funding-spreads', 2, 'Where funding departs from the anchors', 'Exact-date spreads isolate rate differences in basis points.', 'bp', ['SOFR', 'IORB', 'EFFR', 'TGCR'], 'Derived: (left rate − right rate) × 100. Only identical observation dates are joined. A positive spread means the left rate is higher; it is not a distress probability.'), series: [spread('SOFR', 'IORB'), spread('EFFR', 'IORB'), spread('SOFR', 'TGCR')], zero: true},
    {...base('reserve-balances', 3, 'The banking system’s reserve buffer', 'Reserve balances held at Federal Reserve Banks.', 'USD bn', ['WRESBAL'], 'Source dollars in millions divided by 1,000. A system-wide stock, not a measure of how evenly reserves are distributed.'), series: [named('WRESBAL', 'Reserve balances', '$M', 1000)], maxGapDays: 15},
    {...base('treasury-cash', 4, 'Treasury cash parked at the Fed', 'The Treasury General Account’s own reporting history.', 'USD bn', ['TGA_LONG'], 'Source dollars in millions divided by 1,000. This is a cash stock; a chart movement is not attributed to an individual auction, tax payment or spending event.'), series: [named('TGA_LONG', 'Treasury General Account', '$M', 1000)], maxGapDays: 15},
    {...base('reverse-repo', 5, 'Cash absorbed by overnight reverse repo', 'ON RRP take-up makes the size of this liquidity buffer visible.', 'USD bn', ['RRPONTSYD'], 'Federal Reserve overnight reverse-repurchase take-up. Zero is a valid observation; absent dates remain absent.'), series: [named('RRPONTSYD', 'ON RRP', '$B')], zero: true},
    {...base('commercial-paper-premium', 6, 'Short-term corporate funding premiums', 'Three-month AA commercial paper against the three-month Treasury yield.', 'bp', ['CP_FIN_3M', 'CP_NONFIN_3M', 'DGS3M'], 'Derived from exact-date rate differences × 100. Commercial paper and Treasury yields have different market conventions; this is a reference spread, not a quoted arbitrage return.'), series: [spread('CP_FIN_3M', 'DGS3M'), spread('CP_NONFIN_3M', 'DGS3M')], zero: true},
    {...base('treasury-curve', 7, 'The Treasury term structure', `Four published maturities, all observed on ${curve.date || 'a common date that is currently unavailable'}.`, '%', ['DGS3M', 'DGS2', 'DGS10', 'DGS30'], 'Latest common observation date across all four maturities. Lines only connect the observed tenors; intervening maturities are not estimated. Independent of the history-window control.'), xType: 'linear', xLabel: 'Maturity · years', series: [{name: curve.date || 'Common date unavailable', points: curve.points}]},
    {...base('overnight-repricing', 8, 'SOFR repricing, fixing by fixing', 'Changes between consecutive available daily observations.', 'bp / observation', ['SOFR'], 'Derived: (current SOFR − previous available SOFR) × 100. Weekends and holidays are not fabricated. The series describes observed repricing, not expected volatility.'), series: [{name: 'SOFR change', points: daily}], zero: true},
    {...base('mmf-repo-allocation', 9, 'Who receives money-fund repo cash?', 'Total repo lending alongside the Fed and FICC components.', 'USD bn', ['MMF_REPO_TOT', 'MMF_REPO_FED', 'MMF_REPO_FICC'], 'OFR dollar balances are normalized to billions using Seiche’s existing series-wide unit rule. Fed and FICC are components of the total, so these lines must not be added together. Monthly observations retain their native cadence.'), series: [moneyFund('MMF_REPO_TOT', 'Total repo'), moneyFund('MMF_REPO_FED', 'With the Fed'), moneyFund('MMF_REPO_FICC', 'With FICC')], maxGapDays: 65, zero: true},
    {...base('fed-balance-sheet', 10, 'The Federal Reserve balance sheet', 'Total assets provide the balance-sheet context around reserve conditions.', 'USD bn', ['WALCL'], 'H.4.1 total assets; source dollars in millions divided by 1,000. This series is not equated with reserves or combined into a synthetic net-liquidity score.'), series: [named('WALCL', 'Fed total assets', '$M', 1000)], maxGapDays: 15},
  ];
}

export function mountFundingAnalytics(target: HTMLElement): () => void {
  const view = dashboard(target, {eyebrow: 'SEICHE / FUNDING OBSERVATORY', title: 'Follow the pressure through the system.', description: 'From the overnight corridor to reserves, Treasury cash and money-fund repo. Ten analytical views preserve the clocks and definitions behind the funding picture.'});
  let models = new Map<string, EconomicSeries>(), days: number | null = 365, request: AbortController | null = null, disposed = false, liveAttempted = false;
  const render = () => {
    view.grid.replaceChildren(...fundingSpecs(models, days).map(chartCard));
    const latest = (key: string) => models.get(key)?.points.filter(p => Date.parse(p.date + 'T00:00:00Z') <= Date.now()).at(-1);
    const sofr = latest('SOFR'), reserve = latest('WRESBAL'), rrp = latest('RRPONTSYD');
    const sp = difference(fundingPoints(models.get('SOFR'), '%'), fundingPoints(models.get('IORB'), '%'), 100).at(-1);
    stats(view.stats, [['SOFR', sofr ? `${number(sofr.value)}%` : 'Unavailable', sofr?.date || 'No dated observation'],
      ['SOFR − IORB', sp ? `${number(sp.y!)} bp` : 'Unavailable', sp?.label || 'No matching dates'],
      ['Reserve balances', reserve && models.get('WRESBAL')?.unit === '$M' ? `$${number(reserve.value / 1000, 1)}bn` : 'Unavailable', reserve?.date || 'No dated observation'],
      ['ON RRP', rrp && models.get('RRPONTSYD')?.unit === '$B' ? `$${number(rrp.value, 1)}bn` : 'Unavailable', rrp?.date || 'No dated observation']]);
  };
  view.controls.append(selectControl('History window', [['90', '3 months'], ['365', '1 year'], ['all', 'All loaded observations']], '365', value => {days = value === 'all' ? null : Number(value); render();}));
  const refreshButton = el('button', 'Refresh sources'); refreshButton.type = 'button'; view.controls.append(refreshButton);
  const link = el('a', 'Explore the funding workbench →'); link.href = '#workbench'; view.controls.append(link);
  async function refresh() {
    if (request) return; const controller = new AbortController(); request = controller; refreshButton.disabled = true;
    liveAttempted = true;
    const timer = setTimeout(() => controller.abort(), 45000);
    view.status.textContent = 'Checking source dates and histories…';
    try {
      const next = await refreshFunding(models, controller.signal);
      if (disposed) return; models = next.models; render();
      const kept = models.size - next.refreshed;
      view.status.textContent = `${next.refreshed} / ${FUNDING_KEYS.length} source histories refreshed.${kept ? ` ${kept} previously verified histories retained while their refresh is unavailable.` : ''} Each chart carries its own observation dates.`;
    } catch {
      if (!disposed) {
        models = retainFunding(models); render();
        view.status.textContent = models.size
          ? 'Live refresh is unavailable. Previously verified histories remain visible with their original observation dates.'
          : 'Sources could not be verified. Retry to load dated funding observations.';
      }
    } finally {clearTimeout(timer); request = null; if (!disposed) refreshButton.disabled = false;}
  }
  refreshButton.addEventListener('click', () => void refresh()); render();
  const published = new AbortController();
  const publishedTimer = setTimeout(() => published.abort(), 4000);
  let stop = () => {};
  void readJSON('/data/funding-series.json', published.signal).then(value => {
    if (disposed || liveAttempted) return;
    const snapshot = publishedFunding(value); models = snapshot.models; render();
    view.status.textContent = `Published source histories assembled ${snapshot.generatedAt}. Observation dates remain unchanged.`;
  }).catch(() => { /* Live loading still works before the first history publication. */ }).finally(() => {
    clearTimeout(publishedTimer);
    if (!disposed) stop = visibleRefresh(refresh);
  });
  return () => {disposed = true; stop(); clearTimeout(publishedTimer); published.abort(); request?.abort();};
}
