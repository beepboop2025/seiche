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
