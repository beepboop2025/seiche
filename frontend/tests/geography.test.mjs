import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source = await readFile(new URL('../src/geographyModel.ts', import.meta.url), 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { evidenceModel, recentMonths, createEvidenceLoader, imageURL, imageryState, moveBox, PRESETS, validDate, validBox, safeSource, readBytes } = await import(`data:text/javascript;base64,${Buffer.from(output.outputText).toString('base64')}`);
const id = 'in-dist-rajasthan-jaipur';
function fixture() { return { schema: 'liquilens.lending-geography.v1', id, kind: 'district', scope: 'research', score_authority: false, scoring_eligible: false, training_corpus_changed: false, as_of: '2026-11-04', status: 'observed', district: { id, name: 'Jaipur', state: 'Rajasthan', jjas: {year:2026, mm:482, normal_mm:602, departure_pct:-20, category:'Deficient'}, recent_months:{from:'2025-12',mm:[0,null],normal_mm:[1,2],departure_pct:[-100,null]}, vegetation_health:{as_of:'2026-W43',vhi:50.4},soil_moisture:{as_of:'2024-10-15',sm_m3_m3:0.201},declared_drought:{years:['2023-24'],source:'CMP 2026 Appendix 4'} }, sources:{rainfall:{product:'CHIRPS',through:'2026-09'}, vegetation_health:{as_of:'2026-W43',product:'NOAA STAR VHP',credit:'NOAA'},soil_moisture:{as_of:'2024-10-15',product:'ESA CCI',credit:'ESA'}}, institutions:[],limits:[] }; }
test('observation dates remain separate from response dates; missing rainfall never becomes zero',()=>{
  const data=evidenceModel(fixture(),id),district=data.districts[0];
  assert.equal(data.generated,'2026-11-04'); assert.match(district.metrics[5].date,/2024-10-15.*historical/); assert.equal(district.metrics[5].value,'0.201 m³/m³');
  assert.equal(district.months[0].mm,0); assert.equal(district.months[1].mm,null); assert.equal(district.months[1].month,'2026-01');
  assert.equal(district.metrics[1].value,'Not available');
});
test('untrusted evidence fails closed on authority, identity, invalid dates and physical values',()=>{
  for(const change of [d=>d.score_authority=true,d=>d.id='in-dist-other-place',d=>d.as_of='2026-02-30',d=>d.district.soil_moisture.sm_m3_m3=2,d=>d.district.vegetation_health.vhi=NaN,d=>d.sources.soil_moisture.as_of='2025-10-15',d=>d.district.vegetation_health.as_of='2025-W53']) { const d=fixture(); change(d);assert.throws(()=>evidenceModel(d,id)); }
  assert.throws(()=>recentMonths({from:'2026-01',mm:[-1],normal_mm:[2],departure_pct:[-150]}));
  assert.throws(()=>recentMonths({from:'2026-01',mm:[1,2],normal_mm:[2],departure_pct:[-50]}));
});
test('district pages reject loops, mismatched offsets and silent truncation',()=>{
 const d=fixture();d.id='in-state-rajasthan';d.kind='state';d.state='Rajasthan';d.districts=[d.district];d.offset=0;d.districts_total=2;d.next_offset=1;
 assert.equal(evidenceModel(d,d.id).nextOffset,1);
 for(const bad of [{next_offset:0},{next_offset:null},{offset:1},{districts_total:0}]) assert.throws(()=>evidenceModel({...d,...bad},d.id));
});
test('source links accept HTTPS only without embedded credentials',()=>{
 assert.equal(safeSource('javascript:alert(1)'),null);assert.equal(safeSource('https://user:pass@example.com'),null);assert.equal(safeSource('https://example.com/source'),'https://example.com/source');
});
test('old evidence requests cannot overwrite later selection even if fetch ignores abort',async()=>{
 const pending=[];const loader=createEvidenceLoader(()=>new Promise(resolve=>pending.push(resolve)));
 const old=loader.load(id),latest=loader.load(id);pending[1](new Response(JSON.stringify(fixture())));assert.ok(await latest);pending[0](new Response(JSON.stringify(fixture())));assert.equal(await old,null);
});
test('transport errors, byte limits and timeouts provide explicit failure states',async()=>{
 await assert.rejects(readBytes(new Response('x'.repeat(30)),10),/display limit/);await assert.rejects(readBytes(new Response('{}',{status:429}),100),/rate limit/);
 const loader=createEvidenceLoader((_u,init)=>new Promise((_r,reject)=>init.signal.addEventListener('abort',()=>reject(new Error('abort')))),5);
 await assert.rejects(loader.load(id),/timed out/);
});
test('NASA pair retains identical WMS viewport, axis order and explicit dates',()=>{
 const before=new URL(imageURL('2025-11-02',PRESETS.india.bbox)),after=new URL(imageURL('2026-11-02',PRESETS.india.bbox));
 assert.equal(before.origin,'https://gibs.earthdata.nasa.gov');assert.equal(before.searchParams.get('VERSION'),'1.1.1');assert.equal(before.searchParams.get('BBOX'),'66,5,99,38');assert.equal(before.searchParams.get('LAYERS'),'MODIS_Terra_CorrectedReflectance_TrueColor');
 after.searchParams.set('TIME','2025-11-02');assert.equal(before.href,after.href);
});
test('share inputs and repeated pan/zoom remain within the declared geographic extent',()=>{
 assert.deepEqual(imageryState('view=NaN,0,99,38').box,PRESETS.india.bbox);assert.equal(validDate('2025-02-30'),false);assert.equal(validDate('2099-11-02'),false);assert.equal(validDate('2015-11-23'),false);
 assert.deepEqual(imageryState('before=2025-11-02&after=2026-11-02&view=74.6,25.9,77.1,28.4'),{before:'2025-11-02',after:'2026-11-02',box:PRESETS.jaipur.bbox});
 let box=PRESETS.india.bbox;for(let i=0;i<40;i++)box=moveBox(box,.6,30,30);assert.equal(validBox(box),true);for(let i=0;i<40;i++)box=moveBox(box,2,-30,-30);assert.equal(validBox(box),true);
});
test('native geography is independent of funding errors and grants only its required source origins',async()=>{
 const app=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8'),headers=await readFile(new URL('../public/_headers',import.meta.url),'utf8');
 assert.ok(app.indexOf('if (tab === "GEOGRAPHY")')<app.indexOf('if (err && !snap)'));
 assert.match(headers,/img-src 'self' data: blob: https:\/\/gibs\.earthdata\.nasa\.gov;/);
 assert.match(headers,/connect-src 'self' https:\/\/gibs\.earthdata\.nasa\.gov https:\/\/api\.liquilens\.in /);
 assert.doesNotMatch(headers,/frame-src|script-src[^;]*unsafe-inline/);
 const ui=await readFile(new URL('../src/tabs/Geography.tsx',import.meta.url),'utf8');assert.doesNotMatch(ui,/<iframe/);assert.match(ui,/data-share|Copy view link/);
});
