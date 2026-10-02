import test from 'node:test'; import assert from 'node:assert/strict';
import { assemble, preview, SOURCES, fetchSource, canonical, sha256 } from '../src/evidence.js';
import { fixtures, sourceFetcher, NOW } from './fixtures.js';
test('dossier preserves limits, clocks and validation blockers; suppresses restricted/stale numerics and all histories', async()=>{
 const d=await assemble(sourceFetcher(),NOW); const json=JSON.stringify(d);
 assert.equal(d.chargeable,true);assert.equal(d.status,'AVAILABLE');assert.equal(d.can_authorize_order,false);
 assert.match(json,/FAILED_NEGATIVE_CONTROL/);assert.match(json,/retired_sources/);assert.match(json,/2022-01-21/);
 assert.doesNotMatch(json,/"value":777|"value":888|"history":|secret_unapproved_numeric/);
 assert.match(json,/"value":4.5/);assert.match(json,/published_at/);assert.match(json,/knowledge_time/);
 const {integrity,...body}=d;assert.equal(integrity.payload_sha256,await sha256(canonical(body)));assert.equal(integrity.authenticated,false);
});
test('403 remains unavailable; optional source outage can never become calm',async()=>{
 const d=await assemble(sourceFetcher(fixtures(),{institutions:403}),NOW);assert.equal(d.status,'PARTIAL');assert.equal(d.chargeable,true);
 assert.equal(d.sections[4].status,'UNAVAILABLE');assert.equal(d.sections[4].content,null);assert.equal(d.sections[4].reason,'upstream_http_403');
 assert.equal('content' in preview(d).sources[0],false);
});
test('required source failure prevents charge',async()=>{
 for(const id of ['funding','liquidity','money_markets'])assert.equal((await assemble(sourceFetcher(fixtures(),{[id]:503}),NOW)).chargeable,false);
});
test('stale/missing/future snapshot clocks fail closed',async()=>{
 for(const date of ['2026-09-01','2027-01-01','garbage']){const f=fixtures();f.funding.generated_at=date;const s=await fetchSource(SOURCES[0],sourceFetcher(f),NOW);assert.notEqual(s.status,'AVAILABLE');assert.equal(s.data,null)}
});
test('schema drift never becomes neutral evidence',async()=>{
 const f=fixtures();f.funding.schema='unexpected';assert.equal((await fetchSource(SOURCES[0],sourceFetcher(f),NOW)).reason,'source_schema_invalid');
});
test('streamed response bound rejects oversized source before JSON parsing',async()=>{
 const fetcher=(async()=>new Response(' '.repeat(1048577))) as typeof fetch;assert.equal((await fetchSource(SOURCES[0],fetcher,NOW)).reason,'source_too_large');
});
test('fixed source URLs and no redirects are enforced',async()=>{
 let seen=0;const f=sourceFetcher();const wrapped=(async(u: Parameters<typeof fetch>[0], init?:RequestInit)=>{assert.equal(init?.redirect,'manual');assert.ok(SOURCES.some(s=>s.url===String(u)));seen++;return f(u,init)}) as typeof fetch;
 await assemble(wrapped,NOW);assert.equal(seen,5);
});
test('malformed scalar, unknown publication clock and future knowledge clock withhold numeric observations',async()=>{
 for(const change of [{value:{invalid:true}},{canonical_value:'Infinity'},{published_at:null},{knowledge_time:'2099-01-01T00:00:00Z'}]){
  const f=fixtures();Object.assign(f.money_markets.markets[0].benchmark,change);const d=await assemble(sourceFetcher(f),NOW);
  const section=d.sections.find(s=>s.id==='money_markets')!;const benchmark=(section.content!.markets as any[])[0].benchmark;
  assert.equal(benchmark.numeric_observation_included,false);assert.equal('value' in benchmark,false);
 }
});
test('manual redirect mode preserves fixed-origin no-follow policy on Workers',async()=>{
 let requests=0;const fetcher=(async(_u:Parameters<typeof fetch>[0],init?:RequestInit)=>{requests++;assert.equal(init?.redirect,'manual');return new Response(null,{status:302,headers:{Location:'https://outside.example/secret'}})}) as typeof fetch;
 const s=await fetchSource(SOURCES[0],fetcher,NOW);assert.equal(s.status,'UNAVAILABLE');assert.equal(s.reason,'upstream_http_302');assert.equal(requests,1);assert.equal(s.data,null);
});
