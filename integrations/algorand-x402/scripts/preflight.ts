/** READ ONLY. No private keys, wallet requests, transaction signing or settlement. */
import { HTTPFacilitatorClient } from '@x402/core/server';
import { configuration, FACILITATOR, selectNetwork } from '../src/payment.js';
import { assemble, preview } from '../src/evidence.js';
const results: Record<string, unknown> = { checked_at: new Date().toISOString(), read_only: true, payment_executed: false };
try {
 const config=configuration({ SEICHE_ALGORAND_NETWORK: process.env.SEICHE_ALGORAND_NETWORK, SEICHE_ALGORAND_PAY_TO: process.env.SEICHE_ALGORAND_PAY_TO, SEICHE_PUBLIC_ORIGIN: process.env.SEICHE_PUBLIC_ORIGIN });results.configuration={network:config.networkName,asset:config.asset,payTo:config.payTo,origin:config.origin};
 try { const supported=await new HTTPFacilitatorClient({url:FACILITATOR,timeoutMs:15000}).getSupported();results.facilitator={reachable:true,network:selectNetwork(supported,config.networkName),bazaar:supported.extensions.includes('bazaar')}; } catch {results.facilitator={reachable:false,reason:'facilitator_unavailable_or_unsupported'};process.exitCode=1}
 const host=config.networkName==='mainnet'?'https://mainnet-api.algonode.cloud':'https://testnet-api.algonode.cloud';
 try { const r=await fetch(host+'/v2/accounts/'+config.payTo,{redirect:'error',signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error();const account=await r.json() as {assets?:{'asset-id':number;'is-frozen'?:boolean;amount?:number}[]}; const holding=account.assets?.find(a=>String(a['asset-id'])===config.asset);results.receiver={usdc_opted_in:!!holding,frozen:holding?.['is-frozen']??null};if(!holding||holding['is-frozen'])process.exitCode=1; } catch {results.receiver={verified:false,reason:'public_account_lookup_unavailable'};process.exitCode=1}
} catch {results.configuration={valid:false,reason:'network_public_receiver_and_https_origin_required'};process.exitCode=1}
const dossier=await assemble();results.evidence=preview(dossier);if(!dossier.chargeable)process.exitCode=1;
results.remaining='Owner-run Testnet end-to-end flow, then a real Mainnet payment, receiver balance/transaction evidence, Bazaar + leaderboard listing and challenge submission. Preflight is not eligibility proof.';
console.log(JSON.stringify(results,null,2));
