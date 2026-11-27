import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker from './worker.mjs';

test('request paths cannot replace configured upstream or leak credentials',async()=>{
  const original=globalThis.fetch, calls=[];
  globalThis.fetch=async(url,options)=>{calls.push([url,options]);return new Response('{}',{headers:{'content-type':'application/json'}});};
  try{
    for(const path of ['//attacker.example/secret','/%2f%2fattacker.example/','/products/seiche/mcp']){
      const request=new Request('https://public.example'+path,{headers:{Authorization:'Bearer test-only-token','x-platform-country':'US'}});
      Object.defineProperty(request,'cf',{value:{country:'IN'}});
      const response=await worker.fetch(request,{PLATFORM_ORIGIN:'https://origin.example/',GEO_HMAC_KEY:'secret-test-only-'.repeat(3)});
      assert.equal(response.status,200);
      const [url,opts]=calls.at(-1);
      assert.equal(url.origin,'https://origin.example');
      assert.equal(opts.headers.get('x-platform-country'),'IN');
      assert.equal(opts.redirect,'manual');
    }
  }finally{globalThis.fetch=original;}
});

test('unconfigured keys and redirecting origin fail closed',async()=>{
  const request=new Request('https://public.example/products/seiche/mcp');
  assert.equal((await worker.fetch(request,{})).status,503);
  const original=globalThis.fetch;
  globalThis.fetch=async()=>new Response(null,{status:302,headers:{location:'https://attacker.example/'}});
  try{assert.equal((await worker.fetch(request,{PLATFORM_ORIGIN:'https://origin.example/',GEO_HMAC_KEY:'secret-test-only-'.repeat(3)})).status,502);}
  finally{globalThis.fetch=original;}
});

test('fixed origin prefix preserves public-path country signature',async()=>{
  const original=globalThis.fetch;
  let captured;
  globalThis.fetch=async(url,options)=>{captured={url,headers:options.headers};return new Response('{}');};
  try{
    const request=new Request('https://agents.example/products/seiche/mcp');
    Object.defineProperty(request,'cf',{value:{country:'IN'}});
    const secret='test-geo-secret-'.repeat(3);
    const response=await worker.fetch(request,{PLATFORM_ORIGIN:'https://origin.example',ORIGIN_PATH_PREFIX:'/agents',GEO_HMAC_KEY:secret});
    assert.equal(response.status,200);assert.equal(captured.url.pathname,'/agents/products/seiche/mcp');
    const h=captured.headers;
    const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
    const message=['IN',h.get('x-platform-geo-time'),h.get('x-platform-geo-id'),'GET','/products/seiche/mcp'].join('\n');
    const signature=await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(message));
    assert.equal(h.get('x-platform-geo-signature'),[...new Uint8Array(signature)].map(b=>b.toString(16).padStart(2,'0')).join(''));
  }finally{globalThis.fetch=original;}
});

// Last-good fallback. The in-memory stand-in for the Workers Cache API keeps bytes and
// headers exactly as put; like the runtime, match() leaves Cache-Tag out.
const ENV={PLATFORM_ORIGIN:'https://origin.example/',ORIGIN_PATH_PREFIX:'/agents',GEO_HMAC_KEY:'secret-test-only-'.repeat(3),LAST_GOOD_MAX_AGE_SECONDS:'259200'};
// Bytes a re-serialiser would change: spacing, key order, a trailing zero and non-ASCII text.
const BODY=new TextEncoder().encode('{"schema": "liquilens.research-data.v1", "generated_at": "2026-11-24T18:12:11+00:00", "z": 1.50, "a": "₹ crore, “quoted”"}\n');
const MAINTENANCE='{"status":"maintenance","provider":"hetzner","retry_after_seconds":120}';
const PATH='/v1/source-data/catalog', COPY='https://agents.example/__last-good/v1/-';
const answer=(status=200,body=BODY,extra={})=>new Response(body,{status,headers:{'content-type':'application/json','cache-control':'no-store','x-dataset-id':'research-data.discovery',vary:'Origin, Accept-Encoding',...extra}});
const bytes=async response=>new Uint8Array(await response.arrayBuffer());
const stamp=entries=>new Headers([...entries.values()][0].headers).get('x-last-good-at');

function useCache(t){
  const entries=new Map();
  globalThis.caches={default:{
    async put(request,response){assert.equal(request.method,'GET');entries.set(request.url,{headers:[...response.headers],body:await bytes(response)});},
    async match(request){const hit=entries.get(request.url);if(!hit)return undefined;const headers=new Headers(hit.headers);headers.delete('cache-tag');return new Response(hit.body.slice(),{headers});},
  }};
  t.after(()=>{delete globalThis.caches;});
  return entries;
}

// One edge request against a scripted origin, with the work handed to waitUntil settled.
async function edge(path,{origin=()=>answer(),init={},env=ENV}={}){
  const original=globalThis.fetch, waits=[];
  globalThis.fetch=async(url,options)=>origin(url,options);
  try{
    const request=new Request('https://agents.example'+path,init);
    Object.defineProperty(request,'cf',{value:{country:'IN'}});
    const response=await worker.fetch(request,env,{waitUntil:promise=>waits.push(promise)});
    await Promise.all(waits);
    return response;
  }finally{globalThis.fetch=original;}
}

test('last-good stays off until its maximum age is configured',async t=>{
  const entries=useCache(t);
  for(const value of [undefined,'','0','72h','-1']){
    assert.equal((await edge(PATH,{env:{...ENV,LAST_GOOD_MAX_AGE_SECONDS:value}})).status,200);
    assert.equal(entries.size,0);
  }
  entries.set(COPY+PATH,{headers:[['x-last-good-at',new Date().toISOString()]],body:BODY});
  const response=await edge(PATH,{env:{...ENV,LAST_GOOD_MAX_AGE_SECONDS:'0'},origin:()=>answer(503,MAINTENANCE)});
  assert.equal(response.status,503);assert.equal(await response.text(),MAINTENANCE);
});

test('only the reviewed public reads are stored',async t=>{
  const entries=useCache(t);
  const reviewed=['/v1/source-data/catalog','/v1/banking/coverage','/v1/banking/monitoring/watchlist','/v1/banking/institutions/cosmos-ucb'];
  const other=['/v1/source-data/catalog?product=seiche','/v1/banking/coverage?as_of=2026-11-01','/v1/banking/institutions/cosmos-ucb?include_history=true',
    '/v1/banking/institutions/Cosmos_UCB','/v1/banking/monitoring/institutions/cosmos-ucb','/v1/source-data/catalog/','/v1/source-data/healthz',
    '/v1/data-status','/v1/data/undertow.board','/v1/data/riptide.allocation','/v1/data/liquilens.failure_radar','/v1/economy/series/SOFR',
    '/v1/corpus/v1/catalog','/products/seiche/mcp','/agents/v1/banking/coverage'];
  for(const path of other)assert.equal((await edge(path)).status,200);
  assert.equal(entries.size,0);
  for(const path of reviewed)assert.equal((await edge(path)).status,200);
  assert.deepEqual([...entries.keys()].sort(),reviewed.map(path=>COPY+path).sort());
});

test('a successful anonymous read reaches the caller unchanged and is stored with its capture time',async t=>{
  const entries=useCache(t), before=Date.now();
  let sent;
  const response=await edge(PATH,{origin:(url,options)=>{sent={url,options};return answer(200,BODY,{'content-length':String(BODY.byteLength),etag:'"v1"'});}});
  assert.equal(sent.url.href,'https://origin.example/agents/v1/source-data/catalog');assert.equal(sent.options.redirect,'manual');
  assert.equal(sent.options.headers.get('x-platform-country'),'IN');assert.ok(sent.options.headers.get('x-platform-geo-signature'));
  assert.equal(response.status,200);assert.equal(response.headers.get('x-liquilens-served'),null);assert.equal(response.headers.get('cache-control'),'no-store');
  assert.deepEqual(await bytes(response),BODY);
  const stored=entries.get(COPY+PATH), headers=new Headers(stored.headers), captured=headers.get('x-last-good-at');
  assert.deepEqual(stored.body,BODY);
  assert.equal(headers.get('cache-control'),'public, max-age=259200');assert.equal(headers.get('cache-tag'),'last-good');
  assert.equal(headers.get('content-length'),null);assert.equal(headers.get('etag'),'"v1"');assert.equal(headers.get('x-dataset-id'),'research-data.discovery');
  assert.equal(new Date(Date.parse(captured)).toISOString(),captured);assert.ok(Date.parse(captured)>=before && Date.parse(captured)<=Date.now());
});

test('origin failures are answered from the last good copy, byte-identical and marked stale',async t=>{
  const entries=useCache(t), init={headers:{Origin:'https://liquilens.in'}};
  await edge(PATH,{init,origin:()=>answer(200,BODY,{'access-control-allow-origin':'https://liquilens.in','access-control-expose-headers':'X-Dataset-ID, etag'})});
  const captured=stamp(entries);
  const failures=[()=>answer(503,MAINTENANCE,{'retry-after':'120'}),()=>answer(502,'{"error":{"code":"upstream_unavailable"}}'),()=>answer(500,'{}'),
    ()=>new Response('error code: 522',{status:522}),()=>{throw new TypeError('Network connection lost');}];
  for(const failure of failures){
    const response=await edge(PATH,{init,origin:failure}), headers=response.headers;
    assert.equal(response.status,200);assert.deepEqual(await bytes(response),BODY);
    assert.equal(headers.get('x-liquilens-served'),'last-good');assert.equal(headers.get('x-last-good-at'),captured);
    assert.equal(headers.get('warning'),'110 - "Response is Stale"');assert.equal(headers.get('cache-control'),'no-store');
    assert.equal(headers.get('access-control-allow-origin'),'https://liquilens.in');
    assert.equal(headers.get('access-control-expose-headers'),'X-Dataset-ID, etag, X-LiquiLens-Served, X-Last-Good-At, Warning');
    assert.equal(headers.get('x-dataset-id'),'research-data.discovery');assert.equal(headers.get('retry-after'),null);
  }
  assert.equal(stamp(entries),captured,'a failure never replaces the stored copy');
});

test('credentialed requests neither store nor receive the last good copy',async t=>{
  const entries=useCache(t);
  const credentials=[{Authorization:'Bearer test-only'},{Cookie:'session=test-only'},{'X-API-Key':'test-only'},{'X-LiquiLens-Key':'test-only'},{'X-Undertow-Token':'test-only'}];
  for(const headers of credentials)await edge('/v1/banking/coverage',{init:{headers}});
  assert.equal(entries.size,0);
  await edge('/v1/banking/coverage');
  assert.equal(entries.size,1);
  for(const headers of credentials){
    const response=await edge('/v1/banking/coverage',{init:{headers},origin:()=>answer(503,MAINTENANCE)});
    assert.equal(response.status,503);assert.equal(response.headers.get('x-liquilens-served'),null);assert.equal(await response.text(),MAINTENANCE);
  }
});

test('answers below 500 pass through, and each Origin keeps its own copy',async t=>{
  useCache(t);
  const init={headers:{Origin:'https://liquilens.in'}};
  await edge(PATH,{init,origin:()=>answer(200,BODY,{'access-control-allow-origin':'https://liquilens.in'})});
  for(const status of [400,404,429]){
    const response=await edge(PATH,{init,origin:()=>answer(status,'{"error":"refused"}')});
    assert.equal(response.status,status);assert.equal(response.headers.get('x-liquilens-served'),null);
  }
  assert.equal((await edge(PATH,{init,origin:()=>new Response(null,{status:302,headers:{location:'https://attacker.example/'}})})).status,502);
  for(const headers of [{Origin:'https://pro.openbb.co'},{}])assert.equal((await edge(PATH,{init:{headers},origin:()=>answer(503,MAINTENANCE)})).status,503);
});

test('responses that are not a complete public 200 JSON body are never stored',async t=>{
  const entries=useCache(t);
  for(const unsafe of [()=>answer(203),()=>answer(206),()=>answer(200,BODY,{'set-cookie':'a=b'}),()=>answer(200,BODY,{'cache-control':'private, no-store'}),
    ()=>answer(200,'<html>maintenance</html>',{'content-type':'text/html'}),()=>answer(200,''),()=>new Response(BODY,{headers:{'content-type':'application/json'}}),
    ()=>answer(503,MAINTENANCE)])await edge(PATH,{origin:unsafe});
  assert.equal(entries.size,0);
});

test('no replay past the maximum staleness',async t=>{
  const entries=useCache(t);
  await edge(PATH);
  const age=seconds=>{const entry=[...entries.values()][0], headers=new Headers(entry.headers);
    headers.set('x-last-good-at',seconds===null?'not-a-date':new Date(Date.now()-seconds*1000).toISOString());entry.headers=[...headers];};
  const during=env=>edge(PATH,{env,origin:()=>answer(503,MAINTENANCE)});
  age(72*3600-60);assert.equal((await during(ENV)).status,200);
  age(72*3600+60);
  const late=await during(ENV);assert.equal(late.status,503);assert.equal(await late.text(),MAINTENANCE);
  // A shorter configured age applies; a longer one is held to the 72-hour cap.
  age(2*3600);assert.equal((await during({...ENV,LAST_GOOD_MAX_AGE_SECONDS:'3600'})).status,503);
  age(73*3600);assert.equal((await during({...ENV,LAST_GOOD_MAX_AGE_SECONDS:'999999'})).status,503);
  age(null);assert.equal((await during(ENV)).status,503);
});

test('only GET reads are stored or answered from the copy',async t=>{
  const entries=useCache(t);
  await edge(PATH,{init:{method:'HEAD'}});await edge(PATH,{init:{method:'POST',body:'{}'}});
  assert.equal(entries.size,0);
  await edge(PATH);
  for(const init of [{method:'HEAD'},{method:'POST',body:'{}'},{method:'DELETE'}]){
    const response=await edge(PATH,{init,origin:()=>answer(503,MAINTENANCE)});
    assert.equal(response.status,503);assert.equal(response.headers.get('x-liquilens-served'),null);
  }
});

test('a slow origin is answered from the copy after the soft deadline, and still awaited without one',{timeout:5000},async t=>{
  const entries=useCache(t);
  await edge(PATH);
  t.mock.timers.enable({apis:['setTimeout']});
  let reached, aborted=false;
  let calling=new Promise(resolve=>{reached=resolve;});
  const replay=edge(PATH,{origin:(url,options)=>{reached();return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{aborted=true;reject(options.signal.reason);}));}});
  await calling;t.mock.timers.tick(10000);
  const response=await replay;
  assert.equal(response.headers.get('x-liquilens-served'),'last-good');assert.deepEqual(await bytes(response),BODY);assert.ok(aborted);
  entries.clear();
  let settle;
  calling=new Promise(resolve=>{reached=resolve;});
  const slow=edge(PATH,{origin:()=>{reached();return new Promise(resolve=>{settle=resolve;});}});
  await calling;t.mock.timers.tick(10000);settle(answer());
  const live=await slow;
  assert.equal(live.status,200);assert.equal(live.headers.get('x-liquilens-served'),null);assert.equal(entries.size,1);
});
