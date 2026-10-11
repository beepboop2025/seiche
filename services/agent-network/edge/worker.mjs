// Trusted country attribution for the central platform. No request-body logging.
// Deploy with PLATFORM_ORIGIN (HTTPS origin URL) and GEO_HMAC_KEY (secret >=32 chars).
export default {
  async fetch(request, env) {
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
    // The origin never sees a reusable client-supplied geo assertion. Reject redirects
    // to prevent forwarding platform credentials to an unrelated destination.
    const response=await fetch(destination,{method:request.method,headers,body:['GET','HEAD'].includes(request.method)?undefined:request.body,redirect:'manual'});
    if(response.status>=300 && response.status<400)return new Response('Origin redirect refused',{status:502});
    return response;
  }
};
