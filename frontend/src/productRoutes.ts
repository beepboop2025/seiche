/** Named terminal routes stay stable as the public entry becomes a product page. */
export const TERMINAL_TABS = [
  "TODAY", "DISPATCHES", "BOARD", "MONEY MARKETS", "WORKBENCH", "CORPUS", "RESEARCH", "GLOBAL", "FX×MATERIALS", "OIL×FUNDING", "SCARCITY", "SUPPLY", "FORECAST", "PHYSICS", "HELM", "MARKET",
  "CALENDAR", "POSITIONING", "RESONANCE", "TIME MACHINE", "PROOF", "REFEREE", "SYSTEM", "ACCOUNT",
] as const;

export type TerminalTab = (typeof TERMINAL_TABS)[number];

export function terminalTabFromHash(hash: string): TerminalTab | null {
  try {
    const tab = decodeURIComponent(hash.replace(/^#/, "")).split("/")[0].toUpperCase();
    return (TERMINAL_TABS as readonly string[]).includes(tab) ? tab as TerminalTab : null;
  } catch {
    return null;
  }
}

const LABELS: Partial<Record<TerminalTab, string>> = {
  TODAY: "Today", BOARD: "Funding board", CORPUS: "Market atlas", "FX×MATERIALS": "FX and materials", "OIL×FUNDING": "Oil and funding",
};
export const tabLabel = (tab: TerminalTab): string => LABELS[tab] ?? tab.toLowerCase().replace(/(^| )\S/g, (letter) => letter.toUpperCase());
