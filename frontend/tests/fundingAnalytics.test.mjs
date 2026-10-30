import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const uri = source => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText).toString('base64');
const read = name => readFile(new URL('../src/research/' + name, import.meta.url), 'utf8');
const core = uri(await read('core.ts')), charts = uri(await read('analyticsCharts.ts'));
const series = uri((await read('seriesModel.ts')).replace("from \"./core\"", `from '${core}'`));
const fundingSources = uri((await read('fundingSources.ts')).replace('from "./core"', `from '${core}'`).replace('from "./seriesModel"', `from '${series}'`));
const funding = uri((await read('fundingAnalytics.ts')).replace("from './core'", `from '${core}'`).replace("from './seriesModel'", `from '${series}'`).replace("from './analyticsCharts'", `from '${charts}'`).replace("import './analytics.css';", '').replace('from "./fundingSources"', `from '${fundingSources}'`));
const {difference, changes, dateValue, windowPoints, csv, number} = await import(charts);
const {fundingPoints, moneyFundBillions, treasuryCurve, fundingSpecs} = await import(funding);
const {publishedFundingModels, atlasFundingModels, mergeFundingModels} = await import(fundingSources);
const point = (date, y) => ({x: dateValue(date), y, label: date});
const model = (key, points, unit = '%') => ({key, label: key, unit, points: points.map(([date, value]) => ({date, value})), asOf: points.at(-1)[0], state: 'fresh', source: 'fred', remote: key, cadence: 'daily', lag: 'one business day'});

test('funding spreads join exact dates, preserve zero, and convert percentage points to bp', () => {
  const a = [point('2026-09-21', 0), point('2026-09-22', 4), point('2026-09-23', 4.1)];
  const b = [point('2026-09-21', 0), point('2026-09-23', 4)];
  const spread = difference(a, b, 100);
  assert.equal(spread.length, 2); assert.equal(spread[0].y, 0); assert.ok(Math.abs(spread[1].y - 10) < 1e-10);
  assert.equal(difference([point('2026-09-21', null)], b).length, 0);
});
test('treasury curve uses one common observation date and never substitutes a nearest tenor', () => {
  const rows = new Map(['DGS3M', 'DGS2', 'DGS10', 'DGS30'].map((key, i) => [key, model(key, [['2026-09-21', i + 1], ['2026-09-22', i + 2]])]));
  rows.set('DGS30', model('DGS30', [['2026-09-21', 4]]));
  const curve = treasuryCurve(rows); assert.equal(curve.date, '2026-09-21'); assert.deepEqual(curve.points.map(p => p.x), [.25, 2, 10, 30]); assert.deepEqual(curve.points.map(p => p.y), [1, 2, 3, 4]);
  rows.delete('DGS2'); assert.deepEqual(treasuryCurve(rows), {points: [], date: null});
});
test('wrong source units cannot enter reserve or rate calculations', () => {
  assert.deepEqual(fundingPoints(model('SOFR', [['2026-09-21', 4]], 'bp'), '%'), []);
  assert.equal(fundingPoints(model('WRESBAL', [['2026-09-21', 2000000]], '$M'), '$M', 1000)[0].y, 2000);
});
test('OFR repo balances match Seiche backend billions through raw dollars, normalized values and latest zero', () => {
  const raw = {...model('MMF_REPO_TOT', [['2026-07-31', 2925256922261.54], ['2026-08-31', 2832193657532.53]], '$B'), source: 'ofr', remote: 'MMF-MMF_RP_TOT-M'};
  const normalized = {...raw, points: raw.points.map(p => ({...p, value: p.value / 1e9}))};
  assert.deepEqual(moneyFundBillions(raw), moneyFundBillions(normalized));
  assert.ok(Math.abs(moneyFundBillions(raw).at(-1).y - 2832.19365753253) < 1e-9);
  const withZero = {...raw, points: [...raw.points, {date: '2026-09-01', value: 0}]};
  assert.equal(moneyFundBillions(withZero).at(-1).y, 0);
  assert.equal(moneyFundBillions(withZero)[0].y, moneyFundBillions(raw)[0].y);
  const card = fundingSpecs(new Map([[raw.key, raw]]), null).find(s => s.id === 'mmf-repo-allocation');
  assert.equal(card.unit, 'USD bn'); assert.equal(card.series[0].points.at(-1).y, moneyFundBillions(raw).at(-1).y);
});
test('OFR normalization cannot silently rescale another source or an unbound remote series', () => {
  const raw = {...model('MMF_REPO_FED', [['2026-06-30', 6829980601.94]], '$B'), source: 'ofr', remote: 'MMF-MMF_RP_wFR-M'};
  assert.ok(Math.abs(moneyFundBillions(raw)[0].y - 6.82998060194) < 1e-10);
  assert.deepEqual(moneyFundBillions({...raw, source: 'fred'}), []);
  assert.deepEqual(moneyFundBillions({...raw, remote: 'MMF-MMF_RP_TOT-M'}), []);
  assert.deepEqual(moneyFundBillions({...raw, unit: '$M'}), []);
  assert.deepEqual(moneyFundBillions({...raw, key: 'WALCL'}), []);
});
test('history windows are relative to source observations and changes preserve missingness', () => {
  const points = [point('2026-06-01', 4), point('2026-09-21', null), point('2026-09-22', 4.2)];
  assert.equal(windowPoints(points, 90).length, 2); assert.deepEqual(changes(points).map(p => p.y), [null, null]);
  assert.equal(dateValue('2026-02-30'), null);
});
test('ten distinct funding analyses remain explicitly empty when data is absent', () => {
  const specs = fundingSpecs(new Map(), 365); assert.equal(specs.length, 10); assert.equal(new Set(specs.map(s => s.id)).size, 10);
  assert.ok(specs.every(s => s.series.every(row => row.points.length === 0)));
});
test('CSV export does not turn externally supplied labels into spreadsheet formulas', () => {
  assert.equal(csv([['=SUM(A1)', -3, null]]), '"\'=SUM(A1)","-3",""\r\n');
});
test('tiny nonzero exit estimates never round to a displayed zero', () => {
  assert.equal(number(.003, 2), '0.003'); assert.equal(number(0, 2), '0');
});
test('future-dated scheduled rates are excluded from observed history', () => {
  const m = model('IORB', [['2026-09-25', 4], ['2026-09-28', 4]]);
  assert.deepEqual(fundingPoints(m, '%', 1, dateValue('2026-09-26')).map(p => p.label), ['2026-09-25']);
});

const published = (charts, ledger) => ({engines:{money_market:{schema:"seiche.money-market-desk.v1",charts},ledger}});
test("the Observatory first paint has published SOFR, same-date spreads and weekly reserves", () => {
  const models=publishedFundingModels(published({
    policy:{columns:["date","sofr_pct","iorb_pct"],rows:[["2026-10-27",3.95,3.9],["2026-10-28",3.94,3.9]]},
    liquidity:{columns:["date","reserves_b","on_rrp_b"],rows:[["2026-10-27",null,0],["2026-10-28",2893.77,.18]]},
  }));
  const specs=fundingSpecs(models,null);
  assert.equal(specs.find(s=>s.id==="funding-corridor").series[0].points.at(-1).y,3.94);
  assert.ok(Math.abs(specs.find(s=>s.id==="funding-spreads").series[0].points.at(-1).y-4)<1e-10);
  assert.equal(specs.find(s=>s.id==="reserve-balances").series[0].points.at(-1).y,2893.77);
  assert.equal(specs.find(s=>s.id==="reverse-repo").series[0].points[0].y,0);
  assert.equal(models.get("WRESBAL").cadence,"weekly average");
  assert.equal(models.get("SOFR").sourceUrl,"/data/overview.json");
});
test("published OFR chart balances are already billions and cannot be divided twice", () => {
  const models=publishedFundingModels(published({mmf:{columns:["date","total_repo_b"],rows:[["2026-09-30",2832.19]]}}));
  assert.equal(moneyFundBillions(models.get("MMF_REPO_TOT"))[0].y,2832.19);
});
test("published commercial-paper premiums use supplied basis points without reconstructing raw rates", () => {
  const models=publishedFundingModels(published({unsecured:{columns:["date","financial_cp_minus_treasury_bp"],rows:[["2026-10-28",12.5]]}}));
  assert.equal(models.has("CP_FIN_3M"),false);
  const spec=fundingSpecs(models,null).find(s=>s.id==="commercial-paper-premium");
  assert.equal(spec.series[0].points[0].y,12.5);
  assert.equal(spec.sources[0].url,"/data/overview.json");
});
test("malformed published histories and ledger layouts cannot populate the Observatory", () => {
  for(const rows of [[["2026-02-30",3.9]],[["2026-10-28","3.9"]],[["2026-10-28",3.9],["2026-10-28",4]],[["2026-10-28",3.9,4]]]) {
    assert.equal(publishedFundingModels(published({policy:{columns:["date","sofr_pct"],rows}})).size,0);
  }
  assert.equal(publishedFundingModels(published({}, {ok:true,series:[["2026-10-28",6745.6]]})).size,0);
});
test("failed and older refreshes retain histories and their observation dates", () => {
  const old=new Map([["SOFR",model("SOFR",[["2026-10-28",3.94]])]]);
  assert.equal(mergeFundingModels(old,new Map()).get("SOFR").asOf,"2026-10-28");
  assert.equal(mergeFundingModels(old,new Map([["SOFR",model("SOFR",[["2026-10-27",3.95]])]])).get("SOFR").asOf,"2026-10-28");
  assert.equal(mergeFundingModels(old,new Map([["SOFR",model("SOFR",[["2026-10-29",4]])]])).get("SOFR").points[0].value,4);
});
test("the live atlas uses qualified original-publisher histories, never metadata-only duplicates", () => {
  const metric={id:"US.NYFED.SOFR_MEDIAN",unit:"%",availability:"AVAILABLE",redistribution_status:"allowed",source:"nyfed_rates",status:"FRESH",cadence:"P1D",asof:"2026-10-28",history:[["2026-10-28",3.94]]};
  const atlas=metrics=>({schema:"seiche.global-money-markets.v1",markets:[{market_id:"US-USD",metrics}]});
  assert.equal(atlasFundingModels(atlas([metric])).get("SOFR").points[0].value,3.94);
  assert.equal(atlasFundingModels(atlas([{...metric,redistribution_status:"metadata_only"}])).size,0);
  assert.equal(atlasFundingModels(atlas([{...metric,asof:"2026-10-29"}])).size,0);
});
