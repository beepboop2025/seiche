// The Funding Observatory's headline cards: live direct values, honest withheld and
// coming-source states, and never a FRED value. Runs against the saved live atlas.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

const uri = source => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText).toString('base64');
const src = path => readFile(new URL('../src/' + path, import.meta.url), 'utf8');
const core = uri(await src('research/core.ts'));
const usdFunding = uri(await src('usdFunding.ts'));
const {observatoryCards, atlasUsMarket, RESERVES_DIRECT_ID, ON_RRP_DIRECT_ID} = await import(uri((await src('research/observatoryCards.ts'))
  .replace('from "./core"', `from '${core}'`).replace('from "../usdFunding"', `from '${usdFunding}'`)));

const atlas = JSON.parse(await readFile(new URL('./moneyMarketsAtlas.live.json', import.meta.url), 'utf8'));
const copy = value => JSON.parse(JSON.stringify(value));
const us = () => copy(atlasUsMarket(atlas));
const ready = market => ({status: 'ready', market});
const cards = market => Object.fromEntries(observatoryCards(ready(market)).map(card => [card.label, card]));
// Shaped like the next release's direct rows: USD millions, AVAILABLE, redistribution allowed.
const direct = (id, value, asof, extra = {}) => ({id, label: id, availability: 'AVAILABLE', redistribution_status: 'allowed',
  unit: 'local_currency_millions', value, asof, cadence: id === RESERVES_DIRECT_ID ? 'P1W' : 'P1D', source: 'direct', ...extra});
const COMING_RESERVES = "The FRED copy is not cleared for republication, and the current atlas response has no public value from the Federal Reserve's own H.4.1 series.";
const COMING_RRP = "The FRED copy is not cleared for republication, and the current atlas response has no public value from the New York Fed's own reverse repo series.";

test('before the first atlas read every card says it is loading, never unavailable', () => {
  const loading = observatoryCards({status: 'loading'});
  assert.deepEqual(loading.map(card => [card.label, card.value, card.state]), [
    ['SOFR', 'Loading', 'loading'], ['SOFR − IORB', 'Loading', 'loading'],
    ['Reserve balances', 'Loading', 'loading'], ['ON RRP', 'Loading', 'loading']]);
  assert.ok(loading.every(card => !/Unavailable|No dated observation|No matching dates/.test(card.value + card.note)));
});

test('the live atlas fills SOFR and the server same-date spread with their dates and sources', () => {
  const {SOFR: sofr, 'SOFR − IORB': spread} = cards(us());
  assert.deepEqual([sofr.value, sofr.note, sofr.state], ['3.94%', '2026-10-29 · New York Fed', 'live']);
  assert.deepEqual([spread.value, spread.note, spread.state], ['+4 bp', '2026-10-29 · same date, New York Fed and Federal Reserve Board', 'live']);
});

test('reserve balances and ON RRP say the direct source is absent from the atlas, never a FRED value', () => {
  const market = us();
  // The atlas still lists the FRED reserves copy; even marked public it must not reach the card.
  Object.assign(market.metrics.find(row => row.id === 'US.FED.RESERVE_BALANCES'),
    {availability: 'AVAILABLE', redistribution_status: 'allowed', value: 999999, asof: '2026-10-28'});
  market.metrics.push({...direct('US.NYFED.RRPONTSYD', 5000, '2026-10-30'), source: 'fred_daily'});
  const {'Reserve balances': reserves, 'ON RRP': rrp} = cards(market);
  assert.deepEqual([reserves.value, reserves.note, reserves.state], ['Withheld', COMING_RESERVES, 'withheld']);
  assert.deepEqual([rrp.value, rrp.note, rrp.state], ['Withheld', COMING_RRP, 'withheld']);
  assert.doesNotMatch(reserves.value + rrp.value, /\$/);
});

test('the direct H.4.1 and NY Fed series fill the cards in billions with their dates and attribution', () => {
  const market = us();
  market.metrics.push(direct(RESERVES_DIRECT_ID, 2893770, '2026-10-28', {terms_url: 'https://www.federalreserve.gov/disclaimer.htm'}),
    direct(ON_RRP_DIRECT_ID, 1234, '2026-10-30'));
  const {'Reserve balances': reserves, 'ON RRP': rrp} = cards(market);
  assert.equal(reserves.terms, 'https://www.federalreserve.gov/disclaimer.htm', 'the cited source keeps its terms beside the value');
  assert.equal(rrp.terms, undefined);
  assert.deepEqual([reserves.value, reserves.note, reserves.state],
    ['$2,893.8bn', '2026-10-28 · Source: Board of Governors of the Federal Reserve System, H.4.1', 'live']);
  assert.deepEqual([rrp.value, rrp.note, rrp.state],
    ['$1.2bn', '2026-10-30 · Source: Federal Reserve Bank of New York, reverse repo operation results', 'live']);
  for (const [millions, shown] of [[0, '$0bn'], [50, '$0.05bn'], [999, '$0.999bn']]) {
    const small = us(); small.metrics.push(direct(ON_RRP_DIRECT_ID, millions, '2026-10-30'));
    assert.equal(cards(small)['ON RRP'].value, shown, 'zero and small take-up are observations');
  }
  const attributed = us();
  attributed.metrics.push(direct(RESERVES_DIRECT_ID, 2893770, '2026-10-28', {attribution: 'Source: H.4.1 as served', terms_url: 'http://example.invalid/terms'}));
  assert.equal(cards(attributed)['Reserve balances'].note, '2026-10-28 · Source: H.4.1 as served');
  assert.equal(cards(attributed)['Reserve balances'].terms, undefined, 'only https terms links are rendered');
});

test('a direct series without permission, a date or the expected unit shows no value', () => {
  const cases = [
    [{availability: 'RESTRICTED', redistribution_status: 'metadata_only', value: null}, 'Withheld', 'is served without a public value (metadata only)'],
    [{asof: '2026-02-30'}, 'Withheld', 'is served without a public value (no dated value)'],
    [{redistribution_status: undefined}, 'Withheld', 'is served without a public value (redistribution not stated)'],
    [{unit: '%'}, 'Unavailable', 'arrived in an unexpected unit (%)'],
  ];
  for (const [change, value, note] of cases) {
    const market = us(); market.metrics.push({...direct(RESERVES_DIRECT_ID, 2893770, '2026-10-28'), ...change});
    const card = cards(market)['Reserve balances'];
    assert.equal(card.value, value, JSON.stringify(change));
    assert.ok(card.note.includes(note), card.note);
    assert.doesNotMatch(card.value, /\$/);
  }
});

test('the spread card never pairs mismatched dates or a withheld leg', () => {
  const shifted = us();
  shifted.policy_relative_spread.input_lineage[1].event_time = '2026-10-30T00:00:00+00:00';
  assert.deepEqual([cards(shifted)['SOFR − IORB'].value, cards(shifted)['SOFR − IORB'].state], ['Unavailable', 'unavailable']);
  const restricted = us();
  Object.assign(restricted.metrics.find(row => row.id === 'US.NYFED.SOFR_MEDIAN'), {redistribution_status: 'metadata_only'});
  const view = cards(restricted);
  assert.deepEqual([view.SOFR.value, view.SOFR.state], ['Withheld', 'withheld']);
  assert.match(view.SOFR.note, /served without a public value \(metadata only\)/);
  assert.equal(view['SOFR − IORB'].value, 'Unavailable');
});

test('a failed first read says the atlas could not be read, and only the global atlas contract is accepted', () => {
  const failed = observatoryCards({status: 'failed'});
  assert.ok(failed.every(card => card.value === 'Unavailable' && /could not be read/.test(card.note) && card.state === 'unavailable'));
  assert.equal(atlasUsMarket({...atlas, schema: 'seiche.money-market.usd-fallback.v1'}), null);
  assert.equal(atlasUsMarket({schema: 'seiche.global-money-markets.v1', markets: [{market_id: 'EA-EUR', metrics: []}]}), null);
  const empty = cards(null);
  assert.equal(empty.SOFR.value, 'Unavailable');
  assert.equal(empty['Reserve balances'].note, COMING_RESERVES);
});

test('the Observatory renders these cards and no longer reads FRED keys for them', async () => {
  const source = await src('research/fundingAnalytics.ts');
  assert.match(source, /const cards = observatoryCards\(cardSource\);/);
  assert.match(source, /if \(market\) cardSource = \{status: 'ready', market\};/);
  assert.doesNotMatch(source, /latest\('WRESBAL'\)|latest\('RRPONTSYD'\)|No dated observation|No matching dates/);
  assert.match(source, /item\.querySelector\('small'\)\?\.append\(' · Terms: ', terms\);/);
});
