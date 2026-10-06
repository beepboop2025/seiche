import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const here=path.dirname(fileURLToPath(import.meta.url));
const publicRoot=existsSync(path.join(here,'../public/questions')) ? path.join(here,'../public') : path.join(here,'..');
const scope={console, Date, setTimeout, clearTimeout};scope.window=scope;
const ctx=vm.createContext(scope);
if (existsSync(path.join(publicRoot,'questions/exit-research.js'))) vm.runInContext(readFileSync(path.join(publicRoot,'questions/exit-research.js'),'utf8'),ctx);
vm.runInContext(readFileSync(path.join(publicRoot,'questions/questions.js'),'utf8'),ctx);
const q=scope.QuestionEvidence;
const funding=()=>({schema:'seiche.money-market-desk.v1',ok:true,sections:[{metrics:[
  {id:'policy.sofr',value:3.88,unit:'%',asof:'2026-10-02',status:'available',freshness:'aging'},
  {id:'policy.iorb',value:3.90,unit:'%',asof:'2026-10-02',status:'available',freshness:'aging'},
  {id:'policy.sofr_minus_iorb',value:-2,unit:'bp',asof:'2026-10-02',status:'available',freshness:'aging'},
  {id:'liquidity.reserves',value:2948.09,unit:'$B',asof:'2026-09-30',status:'available',freshness:'fresh'},
  {id:'liquidity.srf',value:0,unit:'$B',asof:'2026-10-02',status:'available',freshness:'aging'}]}]});

test('funding reader preserves older observation dates and units',()=>{const text=q.money(funding());assert.match(text,/2026-09-30/);assert.match(text,/-2 bp/);assert.match(text,/aging/);assert.match(text,/not a bank-failure signal/);});
test('mismatched rate dates cannot create a synthetic current spread',()=>{const d=funding();d.sections[0].metrics[1].asof='2026-10-01';assert.throws(()=>q.money(d),/same-date/);});
test('unavailable, duplicate, wrong-unit and inconsistent funding evidence fail closed',()=>{
  for(const alter of [d=>d.sections[0].metrics[0].status='restricted',d=>d.sections[0].metrics[0].value=null,d=>d.sections[0].metrics[0].unit='bp',d=>d.sections[0].metrics[2].value=2,d=>d.sections[0].metrics.push({...d.sections[0].metrics[0]})]){const d=funding();alter(d);assert.throws(()=>q.money(d));}
});
test('future, malformed and impossible source dates fail closed',()=>{for(const v of ['2099-01-01','2026-02-30','yesterday',''])assert.throws(()=>q.day(v));});
test('filing reader does not turn an unavailable ratio into zero',()=>{const text=q.banks({current_disclosures:{rows:[{name:'Example SFB',sector:'sfb',status:'unavailable',metrics:{gnpa_pct:{value:0}}}]}});assert.match(text,/unavailable/);assert.doesNotMatch(text,/GNPA 0/);});
test('filing reader preserves actual period and scope',()=>{const text=q.banks({current_disclosures:{rows:[{name:'Example SFB',sector:'sfb',status:'observed',period_end:'2026-06-30',metrics:{gnpa_pct:{value:2.1,status:'observed',unit:'percent'}}}]}});assert.match(text,/2.10% · period 2026-06-30/);assert.match(text,/selected set/);});

if(scope.UTExitResearch){
  const retained=JSON.parse(readFileSync(path.join(publicRoot,'questions/evidence-2026-10-06.json'),'utf8')).undertow;
  const pack=()=>({schema:'undertow.crypto_desk.v2',public_subset:true,asof:'2026-10-06',generated_at:retained.generated_at,assets:{BTC:{exit_table:structuredClone(retained.rungs),venue_observation_at_utc_by_venue:retained.venue_observation_at_utc_by_venue,unreachable_venues:{}}}});
  test('BTC reader uses the exact rung and converts bp to dollars',()=>{const text=q.exit(pack(),1000000);assert.match(text,/2.081 bp · \$208.10/);assert.match(text,/not executable/);assert.throws(()=>q.exit(pack(),999999));});
  test('missing conversion or depth never produces a quoted cost',()=>{
    for(const field of ['quote_conversion_by_venue','depth_coverage_by_venue']){const p=pack();p.assets.BTC.exit_table.find(r=>r.q_usd===100000)[field].binance={};const text=q.exit(p,100000);assert.match(text,/binance: (quote_conversion_unavailable|required_depth_coverage_unavailable)/);assert.doesNotMatch(text,/binance: 0.209/);}
  });
  test('restricted public pack and missing exact rung fail closed',()=>{const p=pack();p.rights_status='restricted';assert.throws(()=>q.exit(p,100000));const other=pack();other.assets.BTC.exit_table=other.assets.BTC.exit_table.filter(r=>r.q_usd!==100000);assert.throws(()=>q.exit(other,100000));});
  test('missing venue clock stays unavailable even when an estimate exists',()=>{const p=pack();p.assets.BTC.venue_observation_at_utc_by_venue={};assert.match(q.exit(p,100000),/source clock unavailable/);assert.doesNotMatch(q.exit(p,100000),/binance: 0.209/);});
}
