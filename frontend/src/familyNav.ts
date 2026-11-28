/**
 * The family menu on the desk: six tabs, each group's sub-tabs, and the group every desk view
 * sits under.
 *
 * frontend/public/research-ui/header.html is the menu of the static pages and of the pages
 * rendered at publish time; this module is the same menu for the desk, and
 * tests/familyNav.test.mjs holds the two equal. A link that opens a desk view names that view in
 * `tab`; every other link is a page. Seiche is free, so the mast carries no Pricing link.
 */
import type { TerminalTab } from "./productRoutes";

export type FamilyLink = { readonly label: string; readonly href: string; readonly tab?: TerminalTab };
export type FamilyRow = { readonly label: string; readonly group: string; readonly links: readonly FamilyLink[] };

export const FAMILY_TABS: readonly FamilyLink[] = [
  { label: "Overview", href: "/" },
  { label: "Money markets", href: "/money-markets/" },
  { label: "Markets", href: "/markets/" },
  { label: "India", href: "/gift-city/" },
  { label: "Research", href: "/articles/" },
  { label: "Company", href: "https://liquilens.in/about/" },
];

export const FAMILY_ROWS: readonly FamilyRow[] = [
  {
    label: "Money markets", group: "/money-markets/", links: [
      { label: "Evidence map", href: "/money-markets/" },
      { label: "Funding desk", href: "/#today", tab: "TODAY" },
      { label: "Money-market desk", href: "/#money%20markets", tab: "MONEY MARKETS" },
      { label: "Funding board", href: "/#board", tab: "BOARD" },
    ],
  },
  {
    label: "Markets", group: "/markets/", links: [
      { label: "World markets", href: "/markets/" },
      { label: "Forex", href: "/markets/forex/" },
      { label: "Capital markets", href: "/markets/capital-markets/" },
      { label: "China macro", href: "/markets/china-macro/" },
      { label: "Market atlas", href: "/#corpus", tab: "CORPUS" },
      { label: "Workbench", href: "/#workbench", tab: "WORKBENCH" },
    ],
  },
  {
    label: "India", group: "/gift-city/", links: [
      { label: "GIFT City guide", href: "/gift-city/" },
      { label: "GIFT City desk", href: "/#gift%20city", tab: "GIFT CITY" },
      { label: "India geography", href: "/#geography", tab: "GEOGRAPHY" },
    ],
  },
  {
    label: "Research", group: "/articles/", links: [
      { label: "Published research", href: "/articles/" },
      { label: "Investigations", href: "/investigations/" },
      { label: "Dispatches", href: "/dispatches/" },
      { label: "Methodology", href: "/methodology" },
      { label: "Skeptic pack", href: "/skeptic" },
      { label: "Use cases", href: "/use-cases" },
      { label: "Questions", href: "/questions/" },
      { label: "Guide", href: "/guide" },
      { label: "Research catalog", href: "/#research", tab: "RESEARCH" },
    ],
  },
  {
    label: "API and MCP", group: "/developers", links: [
      { label: "Developer guide", href: "/developers" },
      { label: "Quant workflows", href: "/agents/quant/" },
      { label: "OFR dataset", href: "/datasets/direct-ofr/" },
    ],
  },
];

/** The family tab (by its href) each desk view sits under. The bare front page is the Overview. */
export const DESK_GROUP: Readonly<Record<TerminalTab, string>> = {
  TODAY: "/money-markets/", "MONEY MARKETS": "/money-markets/", BOARD: "/money-markets/", GLOBAL: "/money-markets/",
  CALENDAR: "/money-markets/", SCARCITY: "/money-markets/", SUPPLY: "/money-markets/", POSITIONING: "/money-markets/",
  SYSTEM: "/money-markets/",
  CORPUS: "/markets/", WORKBENCH: "/markets/", "FX×MATERIALS": "/markets/", "OIL×FUNDING": "/markets/", MARKET: "/markets/",
  "GIFT CITY": "/gift-city/", GEOGRAPHY: "/gift-city/",
  RESEARCH: "/articles/", DISPATCHES: "/articles/", FORECAST: "/articles/", PHYSICS: "/articles/", HELM: "/articles/",
  RESONANCE: "/articles/", "TIME MACHINE": "/articles/", PROOF: "/articles/", REFEREE: "/articles/",
  ACCOUNT: "/",
};

/** The group a desk view sits under; no view (the bare front page) is the Overview. */
export const deskGroup = (tab: TerminalTab | null): string => (tab ? DESK_GROUP[tab] : "/");

/** The sub-tab row a desk view shows, when its group has one. */
export const deskRow = (tab: TerminalTab | null): FamilyRow | undefined =>
  FAMILY_ROWS.find((row) => row.group === deskGroup(tab));
