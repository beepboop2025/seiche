import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
const source=await readFile(new URL('../src/boardRequest.ts',import.meta.url),'utf8');
const context={exports:{},fetch,AbortController,setTimeout,clearTimeout,TextDecoder,Uint8Array};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
const {requestBoard,validateBoard}=context.exports;
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
