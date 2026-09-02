import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const src = (name) => new URL(`../src/${name}`, import.meta.url);
const routesUrl = src("shareRoutes.ts");
const routesSource = await readFile(routesUrl, "utf8");
const transpiled = ts.transpileModule(routesSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: routesUrl.pathname,
});
const routes = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled.outputText).toString("base64")}`
);

test("covered share routes are real paths, never SPA fragments", () => {
  const paths = [
    routes.boardSharePath(),
    routes.seriesSharePath("sofr_iorb_bp"),
    routes.moneyMarketSharePath("US-USD"),
    routes.worldMarketSharePath("forex"),
    routes.dispatchSharePath("2026-08-30-daily"),
    routes.articleSharePath("cash-clock-analysis"),
  ];

  assert.deepEqual(paths, [
    "/views/board/composite/",
    "/views/series/sofr-iorb-bp/",
    "/views/money-markets/US-USD/",
    "/views/world-markets/forex/",
    "/dispatches/2026-08-30-daily",
    "/articles/cash-clock-analysis/",
  ]);
  for (const path of paths) {
    assert.ok(path.startsWith("/"));
    assert.doesNotMatch(path, /\/#|[?#]/);
    assert.equal(
      routes.stableShareUrl(path, "https://seiche.info/#board"),
      `https://seiche.info${path}`,
    );
  }
});

test("every finite public tab has a stable fallback and excluded tabs have no share action", () => {
  const finite = {
    TODAY: "/views/tabs/today/",
    DISPATCHES: "/views/tabs/dispatches/",
    BOARD: "/views/board/composite/",
    "MONEY MARKETS": "/views/money-markets/overview/",
    GLOBAL: "/views/world-markets/summary/",
    "FX×MATERIALS": "/views/tabs/fx-materials/",
    "OIL×FUNDING": "/views/tabs/oil-funding/",
    SCARCITY: "/views/tabs/scarcity/",
    SUPPLY: "/views/tabs/supply/",
    FORECAST: "/views/tabs/forecast/",
    PHYSICS: "/views/tabs/physics/",
    HELM: "/views/tabs/helm/",
    MARKET: "/views/tabs/market/",
    CALENDAR: "/views/tabs/calendar/",
    POSITIONING: "/views/tabs/positioning/",
    RESONANCE: "/views/tabs/resonance/",
    PROOF: "/views/tabs/proof/",
    REFEREE: "/views/tabs/referee/",
    SYSTEM: "/views/tabs/system/",
  };
  for (const [tab, expected] of Object.entries(finite)) {
    const path = routes.tabSharePath(tab);
    assert.equal(path, expected);
    assert.doesNotMatch(path, /[?#]/);
    assert.doesNotMatch(routes.stableShareUrl(path, "https://seiche.info/#old"), /\/#/);
  }

  for (const tab of ["CORPUS", "TIME MACHINE", "ACCOUNT"]) {
    assert.equal(routes.tabSharePath(tab), null);
    assert.ok(routes.UNSHAREABLE_UI_TABS[tab]);
  }
});

test("stable share URLs reject fragment, query, cross-origin and unsafe ids", () => {
  for (const path of ["/#board", "/views/board/?live=1", "//evil.example/card"])
    assert.throws(() => routes.stableShareUrl(path, "https://seiche.info"));
  assert.throws(() => routes.dispatchSharePath("../../admin"));
  assert.throws(() => routes.moneyMarketSharePath(""));
});

test("public share affordances declare the closest owned card route", async () => {
  const [app, cardShare, dispatches, chart, board, money, global, estuary] = await Promise.all([
    readFile(src("App.tsx"), "utf8"),
    readFile(src("cardShare.ts"), "utf8"),
    readFile(src("tabs/Dispatches.tsx"), "utf8"),
    readFile(src("Chart.tsx"), "utf8"),
    readFile(src("tabs/Board.tsx"), "utf8"),
    readFile(src("tabs/MoneyMarkets.tsx"), "utf8"),
    readFile(src("tabs/Global.tsx"), "utf8"),
    readFile(src("tabs/Estuary.tsx"), "utf8"),
  ]);

  assert.match(app, /data-share-path=\{tabCardPath \?\? undefined\}/);
  assert.match(app, /data-share-disabled=\{tabCardPath \? undefined : "true"\}/);
  assert.match(cardShare, /card\.closest\("\[data-share-disabled\]"\)/);
  assert.match(dispatches, /link=\{\(\) => stableShareUrl\(dispatchSharePath\(slug\)\)\}/);
  assert.match(chart, /data-share-path=\{sharePath\}/);
  for (const id of ["sofr_pct", "effr_pct", "iorb_pct", "sofr_iorb_bp", "vix", "hy_oas_pct"])
    assert.match(board, new RegExp(`seriesSharePath\\(\\"${id}\\"\\)`));
  assert.match(board, /link=\{\(\) => stableShareUrl\(seriesSharePath\("sofr_iorb_bp"\)\)\}/);
  assert.match(money, /moneyMarketSharePath\(selected\?\.market_id \|\| "overview"\)/);
  for (const key of ["summary", "money-markets", "forex"])
    assert.match(`${global}\n${estuary}`, new RegExp(`worldMarketSharePath\\(\\"${key}\\"\\)`));
});
