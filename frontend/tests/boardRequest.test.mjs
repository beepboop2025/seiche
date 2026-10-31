import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
const source=await readFile(new URL('../src/boardRequest.ts',import.meta.url),'utf8');
const context={exports:{},fetch,AbortController,DOMException,setTimeout,clearTimeout,TextDecoder,Uint8Array};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
const {requestBoard,validateBoard,refreshBoardSources,BoardWithheldError,sourceRightsRestriction}=context.exports;
// The exact payload the publisher writes for /data/overview.json (public_rights.legacy_unavailable).
const restriction={schema:'seiche.source-rights-restriction.v1',ok:false,status:'FAILED',category:'source_rights_restricted',
  availability:'unavailable',publication_allowed:false,surface:'/data/overview.json',policy:'seiche.public-source-rights.2026-10-28'};
const board={generated_at:'2026-09-24T20:00:00Z',engines:{composite:{value:44}}};
const options=fetcher=>({signal:new AbortController().signal,fetcher});
test('returns a source-dated snapshot, including explicit unavailable engines',async()=>{
  const result=await requestBoard('/board',options(async()=>Response.json(board)));
  assert.equal(result.generated_at,board.generated_at);
  assert.equal(result.engines.composite.value,44);
  assert.doesNotThrow(()=>validateBoard({...board,engines:{composite:{ok:false}}}));
});
test('rejects HTML fallback, missing clocks, malformed engines and excessive payloads',async()=>{
  for(const value of [null,[],{}, {...board,generated_at:'unknown'}, {...board,engines:[]}])assert.throws(()=>validateBoard(value));
  await assert.rejects(requestBoard('/board',options(async()=>new Response('<html>fallback</html>',{headers:{'content-type':'text/html'}}))),/unexpected/);
  await assert.rejects(requestBoard('/board',options(async()=>new Response('x'.repeat(4*1024*1024+1),{headers:{'content-type':'application/json'}}))),/display limit/);
});
const waiting=(_url,{signal})=>new Promise((_resolve,reject)=>{if(signal.aborted)reject(new DOMException('aborted','AbortError'));else signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});});
test('a stalled request times out with a useful message',async()=>{
  await assert.rejects(requestBoard('/board',{...options(waiting),timeoutMs:5}),/took too long/);
});
test('unmount cancellation aborts an in-flight request',async()=>{
  const controller=new AbortController();
  const promise=requestBoard('/board',{signal:controller.signal,fetcher:waiting});controller.abort();
  await assert.rejects(promise,{name:'AbortError'});
});

test('restricted live responses preserve their status for published refreshes',async()=>{
  await assert.rejects(requestBoard('/board',options(async()=>Response.json({category:'source_rights_restricted'},{status:403}))),error => error.status === 403 && /Live board refresh is restricted/.test(error.message));
});

const refreshOptions = (fetcher, updates, extra = {}) => ({
  signal: new AbortController().signal, fetcher,
  onSnapshot: update => updates.push(update), ...extra,
});
test('a restricted live API cannot hide any published engine', async () => {
  const published = {...board, engines: {composite:{value:44}, tails:{ok:true}, kink:{ok:false,reason:'source missing'}}};
  const updates = [], calls = [];
  const result = await refreshBoardSources('/published','/live',refreshOptions(async url => {
    calls.push(url);
    return url === '/published' ? Response.json(published) : Response.json({category:'source_rights_restricted'},{status:403});
  }, updates));
  assert.deepEqual(calls, ['/published','/live']);
  assert.equal(updates.length,1);
  assert.equal(updates[0].live,false);
  assert.deepEqual(JSON.parse(JSON.stringify(updates[0].data)),published);
  assert.equal(result.apiRestricted,true);
});
test('a published board renders before a slow live upgrade and keeps its clock', async () => {
  let completeLive;
  const api = new Promise(resolve => {completeLive = resolve;});
  const updates = [];
  const task = refreshBoardSources('/published','/live',refreshOptions(async url =>
    url === '/published' ? Response.json(board) : api, updates));
  await new Promise(resolve => setTimeout(resolve,0));
  assert.equal(updates.length,1);
  assert.equal(updates[0].data.generated_at,board.generated_at);
  completeLive(Response.json({...board,generated_at:'2026-09-24T21:00:00Z'}));
  const result = await task;
  assert.equal(updates.length,2);
  assert.equal(updates[1].live,true);
  assert.equal(result.apiChecked,true);
});
test('an older API generation does not replace a newer published generation', async () => {
  const updates=[];
  await refreshBoardSources('/published','/live',refreshOptions(async url =>
    Response.json({...board,generated_at:url === '/published' ? '2026-09-24T21:00:00Z':'2026-09-24T20:00:00Z'}),updates));
  assert.equal(updates.length,1);
  assert.equal(updates[0].live,false);
});
test('later refreshes read publication updates without retrying a known restricted API', async () => {
  const calls=[],updates=[];
  const result=await refreshBoardSources('/published','/live',refreshOptions(async url=>{
    calls.push(url);return Response.json(board);
  },updates,{apiEnabled:false}));
  assert.deepEqual(calls,['/published']);
  assert.equal(updates.length,1);
  assert.equal(result.apiRestricted,true);
  assert.equal(result.refreshError,null);
});
test('live evidence still loads when the published file is absent',async()=>{
  const updates=[];
  const result=await refreshBoardSources('/published','/live',refreshOptions(async url=>
    url==='/published'?new Response('not found',{status:404}):Response.json(board),updates));
  assert.equal(updates.length,1);
  assert.equal(updates[0].live,true);
  assert.equal(result.apiChecked,true);
});
test('temporary API failure preserves published data and remains retryable',async()=>{
  const updates=[];
  const result=await refreshBoardSources('/published','/live',refreshOptions(async url=>
    url==='/published'?Response.json(board):new Response('unavailable',{status:503}),updates));
  assert.equal(updates.length,1);
  assert.equal(result.apiRestricted,false);
  assert.match(result.refreshError,/temporarily unavailable/);
});
test('both failed sources produce an explicit error rather than an empty board',async()=>{
  const updates=[];
  await assert.rejects(refreshBoardSources('/published','/live',refreshOptions(async ()=>
    new Response('unavailable',{status:503}),updates)),/temporarily unavailable/);
  assert.equal(updates.length,0);
});
test('cancellation prevents the second source and any late visible updates',async()=>{
  const controller=new AbortController(),updates=[],calls=[];
  await assert.rejects(refreshBoardSources('/published','/live',refreshOptions(async url=>{
    calls.push(url);controller.abort();return Response.json(board);
  },updates,{signal:controller.signal})),{name:'AbortError'});
  assert.deepEqual(calls,['/published']);
  assert.equal(updates.length,0);
});

test('a published source-rights restriction is a withheld board, never a parse failure',async()=>{
  assert.deepEqual(JSON.parse(JSON.stringify(sourceRightsRestriction(restriction))),{policy:'seiche.public-source-rights.2026-10-28'});
  for(const value of [null,[],board,{...restriction,category:'other'},{...restriction,schema:'other'}])assert.equal(sourceRightsRestriction(value),null);
  await assert.rejects(requestBoard('/published',options(async()=>Response.json(restriction))),
    error=>error instanceof BoardWithheldError && error.status===403 && error.policy===restriction.policy
      && /withheld pending source-rights qualification/.test(error.message) && /not a calm, zero or last-known/.test(error.message));
});
test('a withheld publication outranks a denied or failed live refresh and shows no board',async()=>{
  for(const live of [Response.json({category:'source_rights_restricted'},{status:403}),new Response('unavailable',{status:503})]){
    const updates=[];
    await assert.rejects(refreshBoardSources('/published','/live',refreshOptions(async url=>
      url==='/published'?Response.json(restriction):live.clone(),updates)),
      error=>error instanceof BoardWithheldError && !/Live board refresh is restricted/.test(error.message));
    assert.equal(updates.length,0);
  }
});
test('periodic checks of a withheld publication do not call the live API',async()=>{
  const calls=[],updates=[];
  await assert.rejects(refreshBoardSources('/published','/live',refreshOptions(async url=>{
    calls.push(url);return Response.json(restriction);
  },updates,{apiEnabled:false})),error=>error instanceof BoardWithheldError);
  assert.deepEqual(calls,['/published']);
  assert.equal(updates.length,0);
});
test('a qualified live board still renders when the publication is withheld',async()=>{
  const updates=[];
  const result=await refreshBoardSources('/published','/live',refreshOptions(async url=>
    url==='/published'?Response.json(restriction):Response.json(board),updates));
  assert.equal(updates.length,1);
  assert.equal(updates[0].live,true);
  assert.equal(result.apiChecked,true);
});
