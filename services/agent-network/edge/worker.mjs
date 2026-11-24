// Trusted country attribution for the central platform. No request-body logging.
// Deploy with PLATFORM_ORIGIN (HTTPS origin URL) and GEO_HMAC_KEY (secret >=32 chars).
// LAST_GOOD_MAX_AGE_SECONDS (1 to 259200) turns on the last-good fallback; unset or 0 keeps it off.

// Anonymous GET reads that this data center may answer from its last good copy when the
// origin fails. Each body states its own as-of clock and none takes a query, so
// point-in-time (as_of) depth is never stored. Health, source status, gauges, boards and
// MCP stay out: a stale state reading must never stand in for a live one.
const LAST_GOOD_PATH=/^\/v1\/(?:source-data\/catalog|banking\/coverage|banking\/monitoring\/watchlist|banking\/institutions\/[a-z0-9]+(?:-[a-z0-9]+)*)$/;
const LAST_GOOD_CAP_SECONDS=259200, ORIGIN_SOFT_DEADLINE_MS=10000;
// A header that can carry a credential keeps the request away from the copy both ways.
const CREDENTIAL_HEADER=/auth|cookie|key|token|secret|session|credential|jwt|password/;

function lastGoodMaxAge(env){
  const value=String(env.LAST_GOOD_MAX_AGE_SECONDS ?? '');
  return /^[0-9]{1,9}$/.test(value) ? Math.min(Number(value),LAST_GOOD_CAP_SECONDS) : 0;
}

function lastGoodKey(request,incoming,maxAge){
  if(!maxAge || typeof caches==='undefined' || request.method!=='GET' || incoming.search || !LAST_GOOD_PATH.test(incoming.pathname))return null;
  for(const name of request.headers.keys())if(CREDENTIAL_HEADER.test(name))return null;
  // The origin reflects an allowed Origin into its CORS headers, so each Origin keeps its own copy.
  return new Request(`${incoming.origin}/__last-good/v1/${encodeURIComponent(request.headers.get('origin') || '-')}${incoming.pathname}`);
}

// Store only a complete 200 JSON body from the reviewed data route: never an error body,
// a cookie-setting answer or one the origin marks private.
async function storeLastGood(copyKey,response,maxAge){
  const media=(response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  if(!response.headers.has('x-dataset-id') || response.headers.has('set-cookie') || /private/i.test(response.headers.get('cache-control') || '') || !(media==='application/json' || media.endsWith('+json')))return;
  const body=await response.arrayBuffer();
  if(!body.byteLength)return;
  const headers=new Headers(response.headers);
  for(const name of ['content-length','content-encoding','expires'])headers.delete(name);
  headers.set('cache-control',`public, max-age=${maxAge}`);headers.set('cache-tag','last-good');headers.set('x-last-good-at',new Date().toISOString());
  await caches.default.put(copyKey,new Response(body,{status:200,headers}));
}

// The stored bytes, unchanged and marked stale. A copy past the maximum age, or one
// without a readable capture time, is not served.
async function lastGood(copyKey,maxAge){
  const copy=await caches.default.match(copyKey);
  if(!copy || !(Date.now()-Date.parse(copy.headers.get('x-last-good-at'))<=maxAge*1000))return null;
  const headers=new Headers(copy.headers);
  for(const name of ['content-length','content-encoding'])headers.delete(name);
  headers.set('cache-control','no-store');headers.set('x-liquilens-served','last-good');headers.set('warning','110 - "Response is Stale"');
  if(headers.has('access-control-allow-origin'))headers.set('access-control-expose-headers',[headers.get('access-control-expose-headers'),'X-LiquiLens-Served, X-Last-Good-At, Warning'].filter(Boolean).join(', '));
  return new Response(await copy.arrayBuffer(),{status:200,headers});
}

export default {
  async fetch(request, env, ctx) {
    if (!env.GEO_HMAC_KEY || env.GEO_HMAC_KEY.length < 32) return new Response('Edge not configured', {status:503});
    let origin;
    try { origin=new URL(env.PLATFORM_ORIGIN); } catch { return new Response('Invalid origin', {status:503}); }
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/') return new Response('Invalid origin', {status:503});
    const incoming=new URL(request.url), destination=new URL(origin);
    const prefix=env.ORIGIN_PATH_PREFIX || '';
    if(prefix && !/^\/[a-z0-9-]+$/.test(prefix))return new Response('Invalid origin prefix',{status:503});
    destination.pathname=prefix+incoming.pathname;destination.search=incoming.search;
    if(destination.origin!==origin.origin)return new Response('Origin mismatch',{status:503});
    if (destination.origin === incoming.origin) return new Response('Origin loop refused', {status:503});
    const headers=new Headers(request.headers);
    for (const name of [...headers.keys()]) if (name.startsWith('x-platform-geo') || name === 'x-platform-country' || ['host','forwarded','x-forwarded-for','x-real-ip','cf-connecting-ip','cookie'].includes(name)) headers.delete(name);
    const country=/^[A-Z]{2}$/.test(request.cf?.country || '') ? request.cf.country : 'XX';
    const stamp=String(Math.floor(Date.now()/1000)), id=crypto.randomUUID();
    const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(env.GEO_HMAC_KEY),{name:'HMAC',hash:'SHA-256'},false,['sign']);
    // Sign the public application path. The fixed reverse-proxy prefix is
    // stripped before the request reaches the application.
    const message=[country,stamp,id,request.method,incoming.pathname].join('\n');
    const signature=await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(message));
    headers.set('x-platform-country',country);headers.set('x-platform-geo-time',stamp);headers.set('x-platform-geo-id',id);
    headers.set('x-platform-geo-signature',[...new Uint8Array(signature)].map(b=>b.toString(16).padStart(2,'0')).join(''));
    const maxAge=lastGoodMaxAge(env), copyKey=lastGoodKey(request,incoming,maxAge), controller=new AbortController();
    // The origin never sees a reusable client-supplied geo assertion. Reject redirects
    // to prevent forwarding platform credentials to an unrelated destination.
    const pending=fetch(destination,{method:request.method,headers,body:['GET','HEAD'].includes(request.method)?undefined:request.body,redirect:'manual',signal:controller.signal});
    let response;
    if(!copyKey)response=await pending;
    else{
      // A network error, any 5xx or no answer within the soft deadline is answered from a
      // fresh enough last good copy. Without one, the origin's own outcome passes through.
      let timer;
      const first=await Promise.race([pending.then(value=>({value}),error=>({error})),new Promise(resolve=>{timer=setTimeout(()=>resolve({late:true}),ORIGIN_SOFT_DEADLINE_MS);})]);
      clearTimeout(timer);
      if(first.late || first.error || first.value.status>=500){
        const copy=await lastGood(copyKey,maxAge).catch(()=>null);
        if(copy){controller.abort();return copy;}
      }
      if(first.error)throw first.error;
      response=first.late?await pending:first.value;
    }
    if(response.status>=300 && response.status<400)return new Response('Origin redirect refused',{status:502});
    if(copyKey && response.status===200){
      const stored=storeLastGood(copyKey,response.clone(),maxAge).catch(()=>{});
      if(ctx)ctx.waitUntil(stored);else await stored;
    }
    return response;
  }
};
