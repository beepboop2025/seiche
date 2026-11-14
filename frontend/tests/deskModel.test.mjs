import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source=await readFile(new URL('../src/desk/deskModel.ts',import.meta.url),'utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const {historyPoints,segments,sourceClocks,shown}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
test('plot retains zeros and missing nodes, and never joins across absent days',()=>{
 const points=historyPoints([['2026-11-06',0],['2026-11-09',2],['2026-11-10',null],['2026-11-11',3],['2026-11-12',4]]);
 assert.equal(points[0].value,0);
 assert.deepEqual(segments(points,'value',86400000).map(s=>s.map(p=>p.value)),[[0],[2],[3,4]]);
 assert.deepEqual(segments([{x:1,value:2},{x:2,value:null},{x:3,value:4}],'value').map(s=>s.length),[1,1]);
 assert.equal(shown(null),'Unavailable');
});
test('clock audit includes board, atlas and new catalog instruments with explicit unknown capture',()=>{
 const actual=sourceClocks({provenance:[{mnemonic:'A',asof:'2020-01-01',fetched_at:'2026-11-13T10:00:00Z',staleness:'stale'}]}, {markets:[{market_id:'EA-EUR',metrics:[{id:'B',asof:'2026-11-12',knowledge_time:'2026-11-13T10:00:00Z',status:'FRESH'}]}]}, {markets:[{market_id:'EA-EUR',instruments:[{instrument_id:'B'},{instrument_id:'C',last_observation:'2026-11-12'}]}]});
 assert.equal(actual.length,3);
 assert.equal(actual[0].date,'2020-01-01');
 assert.equal(actual[0].state,'STALE');
 assert.equal(actual[2].state,'UNKNOWN');
 assert.equal(actual[2].captured,undefined);
});
