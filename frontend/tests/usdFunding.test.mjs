import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/usdFunding.ts", import.meta.url), "utf8");
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
const { usdFundingCards, usdMarket, fundingValue } = context.exports;
const saved = (id, value, unit = "%", asof = "2026-10-28") => ({id, value, unit, asof, status:"available", source:"Published source"});
const snap = (...metrics) => ({engines:{money_market:{sections:[{metrics}]}}});
const live = (id, value, unit = "%", asof = "2026-10-29") => ({id, value, unit, asof, availability:"AVAILABLE", redistribution_status:"allowed", source:"nyfed_rates"});
const card = (snapshot, market, id) => usdFundingCards(snapshot, market).find(row => row.id === id);
test("all eight funding fields remain visible, including explicit gaps", () => {
  const result = usdFundingCards(null, null);
  assert.equal(result.length,8);
  assert.ok(result.every(row => row.value === null && row.asof === null));
  assert.equal(fundingValue(result[0]),"Unavailable");
});
test("a newer qualified direct observation replaces only its own dated card", () => {
  const result = usdFundingCards(snap(saved("policy.sofr",3.94),saved("liquidity.reserves",2893.77,"$B")), {
    metrics:[live("US.NYFED.SOFR_MEDIAN",3.95)]
  });
  assert.equal(result[0].value,3.95);
  assert.equal(result[0].asof,"2026-10-29");
  assert.equal(result[3].value,2893.77);
  assert.equal(result[3].asof,"2026-10-28");
  assert.equal(result[3].origin,"published");
});
test("a metadata-only atlas row cannot erase a published reserve observation", () => {
  const result = card(snap(saved("liquidity.reserves",2893.77,"$B")), {
    metrics:[{...live("US.FED.RESERVE_BALANCES",999999,"local_currency_millions"),redistribution_status:"metadata_only",availability:"RESTRICTED"}]
  }, "liquidity.reserves");
  assert.equal(result.value,2893.77);
  assert.equal(result.origin,"published");
});
test("older API data and wrong units cannot replace a newer published value", () => {
  for (const row of [live("US.NYFED.SOFR_MEDIAN",9,"%", "2026-10-27"),live("US.NYFED.SOFR_MEDIAN",394,"bp")]) {
    assert.equal(card(snap(saved("policy.sofr",3.94)),{metrics:[row]},"policy.sofr").value,3.94);
  }
});
test("a spread comes from the server's same-date derivation, never latest mismatched legs", () => {
  const market={metrics:[live("US.NYFED.SOFR_MEDIAN",4),live("US.FED.IORB_DIRECT",3.5,"%","2026-10-30")]};
  assert.equal(card({},market,"policy.sofr_minus_iorb").value,null);
  market.policy_relative_spread={
    ...live("US.NYFED.SOFR_MEDIAN_MINUS_US.FED.IORB_DIRECT",4,"bp","2026-10-28"),
    redistribution_status:undefined, semantic_role:"POLICY_RELATIVE_SPREAD", formula_version:"mm.atlas.policy-spread.v1",
    input_lineage:["US.NYFED.SOFR_MEDIAN","US.FED.IORB_DIRECT"].map(instrument_id=>({instrument_id,event_time:"2026-10-28T00:00:00+00:00"})),
  };
  assert.equal(card({},market,"policy.sofr_minus_iorb").value,4);
  assert.equal(card({},market,"policy.sofr_minus_iorb").asof,"2026-10-28");
  market.policy_relative_spread.input_lineage[1].event_time="2026-10-30T00:00:00+00:00";
  assert.equal(card({},market,"policy.sofr_minus_iorb").value,null);
});
const H41 = {source:"fed_h41", attribution:"Source: Board of Governors of the Federal Reserve System, H.4.1", terms_url:"https://www.federalreserve.gov/disclaimer.htm"};
test("zero is an observation and reserve millions convert to billions exactly once", () => {
  const result=card({}, {metrics:[{...live("US.FED.RESERVE_BALANCES_DIRECT",2893770,"local_currency_millions"),...H41}]}, "liquidity.reserves");
  assert.equal(result.value,2893.77);
  assert.equal(fundingValue(result),"$2,893.77B");
  assert.equal(result.source,"Source: Board of Governors of the Federal Reserve System, H.4.1");
  assert.equal(result.terms,"https://www.federalreserve.gov/disclaimer.htm");
  assert.equal(result.gap,null);
  assert.equal(card(snap(saved("liquidity.on_rrp",0,"$B")),null,"liquidity.on_rrp").value,0);
});
test("the New York Fed's own ON RRP take-up fills its card in billions", () => {
  const result=card(null, {metrics:[{...live("US.NYFED.ON_RRP_TAKEUP",4375,"local_currency_millions","2026-10-30"),source:"nyfed_facilities"}]}, "liquidity.on_rrp");
  assert.equal(result.value,4.375);
  assert.equal(result.asof,"2026-10-30");
  assert.equal(result.origin,"live");
  assert.equal(result.source,"nyfed facilities");
  assert.equal(result.terms,null);
});
test("until the direct series arrive, reserves and ON RRP say why they are withheld and never show the FRED copy", () => {
  const fred={metrics:[live("US.FED.RESERVE_BALANCES",2893770,"local_currency_millions"),live("US.NYFED.RRPONTSYD",5000,"local_currency_millions")]};
  const reserves=card(null,fred,"liquidity.reserves"), rrp=card(null,fred,"liquidity.on_rrp");
  assert.equal(reserves.value,null);
  assert.equal(fundingValue(reserves),"Withheld");
  assert.equal(reserves.gap,"The FRED copy is not cleared for republication; the Federal Reserve's own H.4.1 series arrives in the next release.");
  assert.equal(fundingValue(rrp),"Withheld");
  assert.equal(rrp.gap,"The FRED copy is not cleared for republication; the New York Fed's own reverse repo series arrives in the next release.");
  assert.equal(card(null,null,"policy.sofr").gap,null, "other empty cards stay plainly unavailable");
  const unsafe=card(null,{metrics:[{...live("US.FED.RESERVE_BALANCES_DIRECT",2893770,"local_currency_millions"),...H41,terms_url:"http://example.invalid/terms"}]},"liquidity.reserves");
  assert.equal(unsafe.terms,null, "only https terms links are rendered");
});
test("malformed dates, nonfinite values and unavailable rows stay gaps", () => {
  for (const row of [
    saved("policy.sofr",NaN), saved("policy.sofr",Infinity), saved("policy.sofr",3.9,"%", "2026-02-30"),
    {...saved("policy.sofr",3.9),status:"unavailable"}, {...saved("policy.sofr",3.9),value:"3.9"},
  ]) assert.equal(card(snap(row),null,"policy.sofr").value,null);
});
test("only the USD market in the expected atlas contract is accepted", () => {
  assert.equal(usdMarket({markets:[{market_id:"US-USD"}]}),null);
  assert.equal(usdMarket({schema:"seiche.global-money-markets.v1",markets:[{market_id:"EA-EUR"},{market_id:"US-USD"}]}).market_id,"US-USD");
});

test("the editorial distinguishes a four-week model mean from the current daily spread", () => {
  const snapshot={...snap(saved("policy.sofr_minus_iorb",4,"bp"))};
  snapshot.engines.kink={ok:true,asof:"2026-10-28",observed_spread_now_bp:-.8,kink_reserves_b:3759.9};
  const editorial={thesis:"Dated thesis",evidence:[{engine:"kink",claim:"Old claim"}],countercase:[{
    claim:"SOFR is 0.8bp below IORB, a current abundance signal.",source:"NY Fed secured rates and Federal Reserve IORB",asof:"2026-10-28"
  }]};
  const result=context.exports.clarifyFundingEditorial(snapshot,editorial);
  assert.match(result.evidence[0].claim,/four-week mean.*-0.8 bp/);
  assert.match(result.countercase[0].claim,/latest daily spread is \+4 bp on 2026-10-28/);
  assert.doesNotMatch(result.countercase[0].claim,/current abundance signal/);
  assert.equal(editorial.evidence[0].claim,"Old claim");
});

test("newer published headline observations retain their native dates without recomputing the spread", () => {
  const snapshot=snap(saved("policy.iorb",3.9),saved("policy.sofr_minus_iorb",4,"bp"),saved("liquidity.on_rrp",.18,"$B"));
  snapshot.headline={iorb_pct:{value:3.9,asof:"2026-10-30"},rrp_b:{value:.353,asof:"2026-10-29"}};
  assert.equal(card(snapshot,null,"policy.iorb").asof,"2026-10-30");
  assert.equal(card(snapshot,null,"liquidity.on_rrp").value,.353);
  assert.equal(card(snapshot,null,"policy.sofr_minus_iorb").asof,"2026-10-28");
  assert.equal(card(snapshot,null,"policy.sofr_minus_iorb").value,4);
});

test("direct Treasury and NY Fed facility rows fill the TGA and SRF cards while the board is withheld", () => {
  const market={metrics:[
    {...live("US.TREASURY.GOVERNMENT_CASH",1015632,"local_currency_millions","2026-10-29"),source:"fiscaldata"},
    {...live("US.NYFED.SRF_TAKEUP",0,"local_currency_millions","2026-10-30"),source:"nyfed_facilities"},
  ]};
  const tga=card(null,market,"liquidity.tga");
  assert.equal(tga.value,1015.632);
  assert.equal(tga.asof,"2026-10-29");
  assert.equal(tga.origin,"live");
  assert.equal(tga.source,"fiscaldata");
  assert.equal(fundingValue(tga),"$1,015.63B");
  const srf=card(null,market,"liquidity.srf");
  assert.equal(srf.value,0);
  assert.equal(srf.asof,"2026-10-30");
  assert.equal(srf.source,"nyfed facilities");
  assert.equal(card(null,market,"liquidity.on_rrp").value,null);
});

test("restricted or older direct TGA and SRF rows never replace or invent a value", () => {
  const restricted={metrics:[{...live("US.TREASURY.GOVERNMENT_CASH",1,"local_currency_millions"),availability:"RESTRICTED",redistribution_status:"metadata_only"}]};
  assert.equal(card(null,restricted,"liquidity.tga").value,null);
  const older={metrics:[live("US.NYFED.SRF_TAKEUP",5000,"local_currency_millions","2026-10-27")]};
  assert.equal(card(snap(saved("liquidity.srf",0,"$B","2026-10-28")),older,"liquidity.srf").value,0);
  assert.equal(card(snap(saved("liquidity.srf",0,"$B","2026-10-28")),older,"liquidity.srf").origin,"published");
});
