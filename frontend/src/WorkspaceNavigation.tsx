import { useEffect, useRef } from "react";
import { tabLabel, type TerminalTab } from "./productRoutes";

const groups: { title: string; tabs: TerminalTab[] }[] = [
  { title: "Funding and markets", tabs: ["BOARD", "GLOBAL", "FX×MATERIALS", "OIL×FUNDING", "SCARCITY", "SUPPLY", "MARKET", "CALENDAR", "POSITIONING"] },
  { title: "Research and history", tabs: ["DISPATCHES", "FORECAST", "PHYSICS", "HELM", "RESONANCE", "TIME MACHINE"] },
  { title: "Evidence and settings", tabs: ["PROOF", "REFEREE", "SYSTEM", "ACCOUNT"] },
];

export default function WorkspaceNavigation({ tab, goTab, openCommands, openHelp, contextLink = href => href }: {
  tab: TerminalTab; goTab: (tab: TerminalTab) => void; openCommands: () => void; openHelp: () => void;
  contextLink?: (href: string) => string;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const row = useRef<HTMLElement>(null);
  useEffect(() => { if (menu.current) menu.current.open = false; }, [tab]);
  // On a narrow screen the row scrolls sideways: keep the current tab in view without moving the page.
  useEffect(() => {
    const nav = row.current;
    const current = nav?.querySelector<HTMLElement>(':scope > [aria-current="page"]') ?? menu.current;
    if (!nav || !current || nav.scrollWidth <= nav.clientWidth) return;
    nav.scrollLeft = Math.max(0, current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2);
  }, [tab]);
  const link = (target: TerminalTab) => <a key={target} href={`#${target.toLowerCase()}`} aria-current={target === tab ? "page" : undefined} onClick={(event) => {
    event.preventDefault();
    if (menu.current) menu.current.open = false;
    goTab(target);
  }}>{tabLabel(target)}</a>;
  // The family tab row (.research-tabs): text links, the current tab underlined in paper white.
  return <nav ref={row} className="research-tabs workspace-tabs" aria-label="Funding desk tools">
    {(["TODAY", "MONEY MARKETS", "GIFT CITY", "GEOGRAPHY", "WORKBENCH", "CORPUS", "RESEARCH"] as TerminalTab[]).map(link)}
    <a href="https://liquilens.in/investors/">Company &amp; investors</a>
    <details className="workspace-tools" ref={menu} onKeyDown={(event) => { if (event.key === "Escape" && menu.current) { menu.current.open = false; menu.current.querySelector("summary")?.focus(); } }}>
      <summary>{groups.some((group) => group.tabs.includes(tab)) ? tabLabel(tab) : "All tools"}</summary>
      <div className="workspace-tools__menu">{groups.map((group) => <section key={group.title}><h2>{group.title}</h2>{group.tabs.map(link)}{group.title === "Evidence and settings" && <><a href={contextLink("/markets/")}>World markets</a><a href={contextLink("/articles/")}>Published research</a><a href={contextLink("/use-cases")}>Use cases</a><a href={contextLink("/guide")}>How to read the evidence</a></>}</section>)}</div>
    </details>
    <button className="cmdk" onClick={openCommands} aria-label="Search desk tools" title="Search tools">⌘K</button>
    <button className="cmdk" onClick={openHelp} aria-label="Keyboard shortcuts" title="Keyboard shortcuts">?</button>
  </nav>;
}
