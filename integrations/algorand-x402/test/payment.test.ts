import test from 'node:test';import assert from 'node:assert/strict';
import { encodeAddress } from '@algorandfoundation/algokit-utils/common';
import { SettleError } from '@x402/core/types';
import type { FacilitatorClient } from '@x402/core/server';
import { validateDiscoveryExtension } from '@x402-avm/extensions/bazaar';
import { createApp } from '../src/app.js';
import { configuration, NETWORKS, selectNetwork } from '../src/payment.js';
import { sourceFetcher, fixtures, NOW, BASE } from './fixtures.js';
// Deterministic PUBLIC KEY only: no private key, account creation, signing or chain access.
const receiver = encodeAddress(Uint8Array.from({length:32},(_,i)=>i+1));
const env={SEICHE_ALGORAND_PAY_TO:receiver,SEICHE_ALGORAND_NETWORK:'testnet',SEICHE_PUBLIC_ORIGIN:BASE};
function mock(mode='success', network:string=NETWORKS.testnet.network) {
 const calls={verify:0,settle:0,supported:0};
 const facilitator:FacilitatorClient={async getSupported(){calls.supported++;return {kinds:[{x402Version:2,scheme:'exact',network:network as `algorand:${string}`,extra:{feePayer:receiver}}],extensions:['bazaar'],signers:{}}},async verify(){calls.verify++;return mode==='reject'?{isValid:false,invalidReason:'fixture_rejection'}:{isValid:true,payer:receiver}},async settle(){calls.settle++;return {success:mode!=='fail',network:network as `algorand:${string}`,transaction:mode==='malformed'?'BAD':'A'.repeat(52),payer:receiver}}};
 return {facilitator,calls};
}
async function request(mode='success',fetcher=sourceFetcher()){
 const m=mock(mode),app=createApp({env,facilitator:m.facilitator,fetcher,now:()=>NOW});
 const unpaid=await app.request(BASE+'/v1/dossier');assert.equal(unpaid.status,402);
 const challenge=JSON.parse(Buffer.from(unpaid.headers.get('payment-required')!,'base64').toString());
 const payment={x402Version:2,accepted:challenge.accepts[0],resource:challenge.resource,payload:{paymentGroup:['MOCK_NOT_A_TRANSACTION'],paymentIndex:0},extensions:challenge.extensions};
 return {app,challenge,payment,...m};
}
function header(payment:unknown){return {'PAYMENT-SIGNATURE':Buffer.from(JSON.stringify(payment)).toString('base64')}}
test('dormant configuration never releases protected evidence or contacts facilitator',async()=>{
 const m=mock(), app=createApp({env:{},facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});
 assert.equal((await app.request(BASE+'/v1/dossier')).status,503);assert.equal(m.calls.supported,0);
 assert.equal((await app.request(BASE+'/v1/preview')).status,200);
});
test('real SDK emits tagged 402 with valid Bazaar and no protected content',async()=>{
 const {challenge,calls}=await request();assert.equal(challenge.accepts[0].amount,'10000');assert.equal(challenge.accepts[0].asset,'10458941');assert.equal(challenge.accepts[0].extra.tag,'x402-global-challenge');assert.equal(challenge.accepts[0].extra.feePayer,receiver);assert.equal(validateDiscoveryExtension(challenge.extensions.bazaar).valid,true);assert.equal(calls.verify,0);assert.equal(calls.settle,0);
});
test('successful MOCK settlement releases same compiled snapshot with receipt',async()=>{
 const {app,payment,calls}=await request();const r=await app.request(BASE+'/v1/dossier',{headers:header(payment)});assert.equal(r.status,200);assert.ok(r.headers.get('payment-response'));assert.match(r.headers.get('cache-control')!,/private|no-store/);assert.equal((await r.json()).schema,'seiche.evidence-dossier.v1');assert.equal(calls.settle,1);
});
test('invalid verification, failed settle and malformed receipt never release dossier',async()=>{
 for(const mode of ['reject','fail','malformed']){const {app,payment}=await request(mode);const r=await app.request(BASE+'/v1/dossier',{headers:header(payment)});assert.equal(r.status,mode==='reject'?402:503);assert.doesNotMatch(await r.text(),/"sections":\[\{"id"/)}
});
test('wrong amount, destination or resource are rejected before verification',async()=>{
 for(const mutation of ['amount','payTo','resource']){const {app,payment,calls}=await request();if(mutation==='resource')payment.resource.url='https://evil.example/v1/dossier';else payment.accepted[mutation]='wrong';const r=await app.request(BASE+'/v1/dossier',{headers:header(payment)});assert.equal(r.status,402);assert.equal(calls.verify,0);assert.equal(calls.settle,0)}
});
test('canonical path rejects query, foreign host, non-GET and oversize signatures',async()=>{
 const {app}=await request();for(const u of [BASE+'/v1/dossier?anything=x','https://evil.example/v1/dossier'])assert.equal((await app.request(u)).status,400);
 assert.equal((await app.request(BASE+'/v1/dossier',{method:'POST'})).status,405);
 assert.equal((await app.request(BASE+'/v1/dossier',{headers:{'PAYMENT-SIGNATURE':'a'.repeat(16385)}})).status,400);
});
test('required evidence outage prevents any facilitator call',async()=>{
 const m=mock(),app=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(fixtures(),{liquidity:503}),now:()=>NOW});assert.equal((await app.request(BASE+'/v1/dossier')).status,503);assert.equal(m.calls.supported,0);assert.equal(m.calls.verify,0);
});
test('canonical and legacy aliases are selected only from supported correct network',async()=>{
 for(const name of ['testnet','mainnet'] as const){for(const network of [NETWORKS[name].network,NETWORKS[name].network.slice(0,41)]){const supported=await mock('success',network).facilitator.getSupported();assert.equal(selectNetwork(supported,name),network)}}
 assert.throws(()=>configuration({...env,SEICHE_ALGORAND_PAY_TO:'invalid'}));assert.throws(()=>configuration({...env,SEICHE_PUBLIC_ORIGIN:'http://example.com'}));
});
test('facilitator outage sanitized and never yields unpaid success',async()=>{
 const m=mock();m.facilitator.getSupported=async()=>{throw new Error('secret diagnostic')};const app=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});const r=await app.request(BASE+'/v1/dossier');assert.equal(r.status,503);assert.doesNotMatch(await r.text(),/secret diagnostic/);
});
test('legacy header cap and facilitator rejection diagnostics remain bounded and sanitized',async()=>{
 const {app}=await request();assert.equal((await app.request(BASE+'/v1/dossier',{headers:{'X-PAYMENT':'a'.repeat(16385)}})).status,400);
 const m=mock();m.facilitator.verify=async()=>{throw new Error('private credential marker')};
 const a=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});const unpaid=await a.request(BASE+'/v1/dossier');const c=JSON.parse(Buffer.from(unpaid.headers.get('payment-required')!,'base64').toString());
 const r=await a.request(BASE+'/v1/dossier',{headers:header({x402Version:2,accepted:c.accepts[0],resource:c.resource,payload:{paymentGroup:['FIXTURE'],paymentIndex:0}})});assert.equal(r.status,402);assert.doesNotMatch(await r.text(),/private credential marker/);
});
test('indeterminate settlement returns 503 guidance and exposes receipt for reconciliation',async()=>{
 const m=mock();m.facilitator.settle=async()=>{throw new Error('timeout with private diagnostic')};
 const app=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});const unpaid=await app.request(BASE+'/v1/dossier');const c=JSON.parse(Buffer.from(unpaid.headers.get('payment-required')!,'base64').toString());const r=await app.request(BASE+'/v1/dossier',{headers:header({x402Version:2,accepted:c.accepts[0],resource:c.resource,payload:{paymentGroup:['MOCK'],paymentIndex:0}})});
 assert.equal(r.status,503);assert.match(r.headers.get('access-control-expose-headers')!,/PAYMENT-RESPONSE/);assert.ok(r.headers.get('payment-response'));assert.equal(r.headers.get('x-content-type-options'),'nosniff');const body=await r.text();assert.match(body,/Do not repeat this payment/);assert.doesNotMatch(body,/private diagnostic|"sections"/);
});
test('confirmation failure preserves validated transaction ID and never claims unpaid',async()=>{
 const m=mock();m.facilitator.settle=async()=>({success:false,errorReason:'ErrConfirmationFailed',errorMessage:'untrusted upstream diagnostics',transaction:'B'.repeat(52),network:NETWORKS.testnet.network,payer:receiver});
 const app=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});const unpaid=await app.request(BASE+'/v1/dossier');const c=JSON.parse(Buffer.from(unpaid.headers.get('payment-required')!,'base64').toString());const r=await app.request(BASE+'/v1/dossier',{headers:header({x402Version:2,accepted:c.accepts[0],resource:c.resource,payload:{paymentGroup:['MOCK'],paymentIndex:0}})});
 assert.equal(r.status,503);const receipt=JSON.parse(Buffer.from(r.headers.get('payment-response')!,'base64').toString());assert.equal(receipt.transaction,'B'.repeat(52));assert.equal(receipt.network,NETWORKS.testnet.network);assert.equal(receipt.payer,receiver);assert.equal(receipt.errorReason,'settlement_outcome_unknown_do_not_retry');assert.doesNotMatch(JSON.stringify(receipt),/untrusted upstream/);assert.match(r.headers.get('access-control-expose-headers')!,/PAYMENT-RESPONSE/);
});
test('malformed or scalar payment headers are rejected before SDK logging',async()=>{
 const {app,calls}=await request();const originalWarn=console.warn, originalError=console.error;const logs:unknown[]=[];console.warn=(...x)=>{logs.push(x)};console.error=(...x)=>{logs.push(x)};
 try{for(const value of ['PRIVATE_PAYMENT_MARKER','null','3','{"x402Version":2}']){const r=await app.request(BASE+'/v1/dossier',{headers:{'PAYMENT-SIGNATURE':Buffer.from(value).toString('base64')}});assert.equal(r.status,400);assert.doesNotMatch(await r.text(),/PRIVATE_PAYMENT/)}assert.equal(logs.length,0);assert.equal(calls.verify,0)}finally{console.warn=originalWarn;console.error=originalError}
});
test('facilitator without Bazaar fails503 before SDK initialization',async()=>{
 const m=mock();const get=m.facilitator.getSupported;m.facilitator.getSupported=async()=>({...await get(),extensions:[]});const app=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});assert.equal((await app.request(BASE+'/v1/dossier')).status,503);assert.equal(m.calls.verify,0);
});

test('non-2xx SettleError preserves only validated reconciliation evidence',async()=>{
 const m=mock();m.facilitator.settle=async()=>{throw new SettleError(502,{success:false,errorReason:'ErrConfirmationFailed',errorMessage:'sensitive upstream marker',transaction:'C'.repeat(52),network:NETWORKS.testnet.network,payer:receiver})};
 const app=createApp({env,facilitator:m.facilitator,fetcher:sourceFetcher(),now:()=>NOW});const unpaid=await app.request(BASE+'/v1/dossier');const c=JSON.parse(Buffer.from(unpaid.headers.get('payment-required')!,'base64').toString());const r=await app.request(BASE+'/v1/dossier',{headers:header({x402Version:2,accepted:c.accepts[0],resource:c.resource,payload:{paymentGroup:['MOCK'],paymentIndex:0}})});
 assert.equal(r.status,503);const receipt=JSON.parse(Buffer.from(r.headers.get('payment-response')!,'base64').toString());assert.equal(receipt.transaction,'C'.repeat(52));assert.equal(receipt.errorReason,'settlement_outcome_unknown_do_not_retry');assert.doesNotMatch(JSON.stringify(receipt),/sensitive upstream/);
});
