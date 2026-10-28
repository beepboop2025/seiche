import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const uri = source => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText).toString('base64');
const read = name => readFile(new URL('../src/research/' + name, import.meta.url), 'utf8');
const core = uri(await read('core.ts'));
const series = uri((await read('seriesModel.ts')).replace('from "./core"', `from '${core}'`));
const module = uri((await read('fundingHistory.ts')).replace("from './core'", `from '${core}'`).replace("from './seriesModel'", `from '${series}'`));
const {publishedFunding, refreshFunding, retainFunding} = await import(module);
const entry = key => ({mnemonic: key, available: true, json: `/api/series/${key}`, csv_restricted: null, unit: '%', source: 'fred', remote_id: key});
const payload = (key, value = 3.88) => ({provenance: {mnemonic: key, unit: '%', asof: '2026-09-29', source: 'fred', remote_id: key, staleness: 'fresh'}, points: [['2026-09-29', value]]});
const bundle = () => ({schema: 'seiche.funding-history.v1', generated_at: '2026-09-30T12:00:00Z', series: [{entry: entry('SOFR'), payload: payload('SOFR')}]});
const signal = () => new AbortController().signal;

test('a published history renders during an outage without changing observation clocks', () => {
  const published = publishedFunding(bundle());
  const retained = retainFunding(published.models).get('SOFR');
  assert.equal(retained.asOf, '2026-09-29');
  assert.deepEqual(retained.points, [{date: '2026-09-29', value: 3.88}]);
  assert.match(retained.state, /refresh unavailable/);
  assert.match(published.models.get('SOFR').state, /published 2026-09-30/);
});
test('restricted, malformed, duplicate and future published histories are rejected', () => {
  for (const mutate of [
    b => {b.series[0].entry.csv_restricted = true;},
    b => {b.series[0].payload.points[0][1] = null;},
    b => {b.series.push(b.series[0]);},
    b => {b.generated_at = '2999-01-01T00:00:00Z';},
    b => {b.series[0].entry.json = 'https://untrusted.example/SOFR';},
    b => {b.series[0].payload.provenance.source = 'unverified';},
  ]) {const b = bundle(); mutate(b); assert.throws(() => publishedFunding(b));}
});
test('a transient per-source failure retains history while successful sources refresh', async () => {
  const previous = publishedFunding(bundle()).models;
  const read = async url => {
    if (url.endsWith('index.json')) return {schema: 'seiche.series-index.v1', series: [entry('SOFR'), entry('EFFR')]};
    if (url.includes('/SOFR?')) throw new Error('HTTP 502');
    return payload('EFFR', 0);
  };
  const result = await refreshFunding(previous, signal(), read);
  assert.equal(result.refreshed, 1);
  assert.equal(result.models.get('EFFR').points[0].value, 0);
  assert.equal(result.models.get('SOFR').points[0].value, 3.88);
  assert.match(result.models.get('SOFR').state, /refresh unavailable/);
});
test('a successful catalog revokes withdrawn or restricted histories, even when cached', async () => {
  const previous = publishedFunding(bundle()).models;
  for (const rows of [[], [{...entry('SOFR'), available: false}], [{...entry('SOFR'), csv_restricted: true}], [entry('SOFR'), entry('SOFR')]]) {
    const result = await refreshFunding(previous, signal(), async () => ({schema: 'seiche.series-index.v1', series: rows}));
    assert.equal(result.models.size, 0);
  }
});
test('a failed catalog leaves the previously verified map available to the caller', async () => {
  const previous = publishedFunding(bundle()).models;
  await assert.rejects(refreshFunding(previous, signal(), async () => {throw new Error('HTTP 502');}));
  assert.equal(previous.get('SOFR').points[0].value, 3.88);
});
test('a changed source identity cannot reuse the old source on a failed request', async () => {
  const previous = publishedFunding(bundle()).models;
  const result = await refreshFunding(previous, signal(), async url => {
    if (url.endsWith('index.json')) return {schema: 'seiche.series-index.v1', series: [{...entry('SOFR'), remote_id: 'DIFFERENT'}]};
    throw new Error('HTTP 502');
  });
  assert.equal(result.models.size, 0);
});
