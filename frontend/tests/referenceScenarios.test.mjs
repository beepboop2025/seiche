import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source = await readFile(new URL('../src/referenceScenarios.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022}});
const {referenceConversion, fundingComparison} = await import(`data:text/javascript;base64,${Buffer.from(code.outputText).toString('base64')}`);
const ref = {base:'USD', quote:'INR', rate:90, as_of:'2026-10-02', status:'fresh', provider:'ecb'};
const fx = {amount:'1000',fee_bps:'100',fixed_fee_quote:'10'};
const funding = {currency:'USD',principal:'1000000',rate_a_pct:'6',rate_b_pct:'5',fees_a:'0',fees_b:'1000',days:'30',day_count:'360'};
test('conversion retains source identity and applies both fees in receiving currency', () => {
  const result=referenceConversion(fx,ref);
  assert.deepEqual(result.outputs,{gross_quote:'90000',variable_fee_quote:'900',fixed_fee_quote:'10',net_quote:'89090'});
  assert.equal(result.reference.as_of,ref.as_of); assert.equal(result.reference.provider,'ecb');
});
test('stale references remain dated historical scenarios; missing and restricted evidence is rejected', () => {
  assert.equal(referenceConversion(fx,{...ref,status:'stale'}).reference.status,'stale');
  for(const change of [{rate:null},{rate:0},{rate:Infinity},{status:'restricted'},{status:'unavailable'},{as_of:'2026-02-30'},{as_of:null},{provider:''}]) assert.throws(()=>referenceConversion(fx,{...ref,...change}));
});
test('large amounts retain decimal cents, and malformed inputs do not coerce to zero', () => {
  assert.equal(referenceConversion({...fx,amount:'999999999999.99',fee_bps:'0',fixed_fee_quote:'0'},{...ref,rate:1}).outputs.net_quote,'999999999999.99');
  for(const amount of ['', '1e3','NaN','1,000','-1','0']) assert.throws(()=>referenceConversion({...fx,amount},ref));
  assert.throws(()=>referenceConversion({...fx,fee_bps:'10000.01'},ref));
});
test('fees can make net proceeds negative without fabricating a positive balance', () => {
  assert.equal(referenceConversion({...fx,amount:'1',fixed_fee_quote:'100'},ref).outputs.net_quote,'-10.9');
});
test('funding comparison handles fees, day count and direction independently', () => {
  const r=fundingComparison(funding);
  assert.equal(r.outputs.interest_a,'5000');assert.equal(r.outputs.total_cost_b,'5166.666667');assert.equal(r.outputs.cost_b_minus_a,'166.666667');
  assert.equal(fundingComparison({...funding,day_count:'365'}).outputs.interest_a,'4931.506849');
  assert.equal(fundingComparison({...funding,rate_a_pct:'-1.2'}).outputs.interest_a,'-1000');
});
test('term, amount and unit errors are refused before a result can be exported', () => {
  for(const change of [{days:'0'},{days:'3661'},{days:'1.5'},{day_count:'366'},{principal:'0'},{currency:'usd'},{rate_a_pct:'-101'},{fees_a:'-1'}]) assert.throws(()=>fundingComparison({...funding,...change}));
});
