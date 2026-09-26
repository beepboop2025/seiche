import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const uri = source => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText).toString('base64');
const read = name => readFile(new URL('../src/research/' + name, import.meta.url), 'utf8');
const core = uri(await read('core.ts')), charts = uri(await read('analyticsCharts.ts'));
const series = uri((await read('seriesModel.ts')).replace("from \"./core\"", `from '${core}'`));
const funding = uri((await read('fundingAnalytics.ts')).replace("from './core'", `from '${core}'`).replace("from './seriesModel'", `from '${series}'`).replace("from './analyticsCharts'", `from '${charts}'`).replace("import './analytics.css';", ''));
const {difference, changes, dateValue, windowPoints, csv, number} = await import(charts);
const {fundingPoints, treasuryCurve, fundingSpecs} = await import(funding);
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
