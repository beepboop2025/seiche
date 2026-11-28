import { useEffect, useRef } from "react";
import { tabLabel, type TerminalTab } from "./productRoutes";
import { FAMILY_TABS, deskGroup, deskRow, type FamilyLink } from "./familyNav";

// The desk views that no family sub-tab opens, in the desk's own menu. Every other view is one
// click away in its group's sub-tabs, and the dated letters are the Dispatches archive there.
const groups: { title: string; tabs: TerminalTab[] }[] = [
  { title: "Funding and markets", tabs: ["GLOBAL", "FX×MATERIALS", "OIL×FUNDING", "SCARCITY", "SUPPLY", "MARKET", "CALENDAR", "POSITIONING"] },
  { title: "Research and history", tabs: ["FORECAST", "PHYSICS", "HELM", "RESONANCE", "TIME MACHINE"] },
  { title: "Evidence and settings", tabs: ["PROOF", "REFEREE", "SYSTEM", "ACCOUNT"] },
];

export default function WorkspaceNavigation({ tab, goTab, goOverview, openCommands, openHelp, contextLink = href => href }: {
  tab: TerminalTab | null; goTab: (tab: TerminalTab) => void; goOverview: () => void;
  openCommands: () => void; openHelp: () => void; contextLink?: (href: string) => string;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const row = useRef<HTMLElement>(null);
  const subRow = useRef<HTMLElement>(null);
  useEffect(() => { if (menu.current) menu.current.open = false; }, [tab]);
  // On a narrow screen each row scrolls sideways: keep its current link in view without moving the page.
  useEffect(() => {
    for (const nav of [row.current, subRow.current]) {
      const current = nav?.querySelector<HTMLElement>(':scope > [aria-current="page"]') ?? (nav === subRow.current ? menu.current : null);
      if (!nav || !current || nav.scrollWidth <= nav.clientWidth) continue;
      nav.scrollLeft = Math.max(0, current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2);
    }
  }, [tab]);
  const close = () => { if (menu.current) menu.current.open = false; };
  const group = deskGroup(tab);
  const sub = deskRow(tab);
  // A link into a desk view opens it in place and Overview returns to the front page; every other
  // family link is a page, carrying the institution in review.
  const family = (item: FamilyLink, current: boolean) => {
    const view = item.tab;
    if (view) return <a key={item.href} href={item.href} aria-current={current ? "page" : undefined} onClick={(event) => {
      event.preventDefault();
      close();
      goTab(view);
    }}>{item.label}</a>;
    if (item.href === "/") return <a key={item.href} href={contextLink("/")} aria-current={current ? "page" : undefined} onClick={(event) => {
      event.preventDefault();
      close();
      goOverview();
    }}>{item.label}</a>;
    return <a key={item.href} href={contextLink(item.href)} aria-current={current ? "page" : undefined}>{item.label}</a>;
  };
  const link = (target: TerminalTab) => <a key={target} href={`#${target.toLowerCase()}`} aria-current={target === tab ? "page" : undefined} onClick={(event) => {
    event.preventDefault();
    close();
    goTab(target);
  }}>{tabLabel(target)}</a>;
  // The family tab row (.research-tabs) and the group's sub-tab row (.research-subtabs), as on
  // every family page; the desk's own tools close the sub-tab row.
  return <>
    <nav ref={row} className="research-tabs workspace-tabs" aria-label="Seiche sections">
      {FAMILY_TABS.map((item) => family(item, item.href === group))}
    </nav>
    <nav ref={subRow} className="research-subtabs workspace-subtabs" aria-label={sub?.label ?? "Desk tools"}>
      {sub?.links.map((item) => family(item, item.tab !== undefined && item.tab === tab))}
      <details className="workspace-tools" ref={menu} onKeyDown={(event) => { if (event.key === "Escape" && menu.current) { menu.current.open = false; menu.current.querySelector("summary")?.focus(); } }}>
        <summary>{tab && groups.some((entry) => entry.tabs.includes(tab)) ? tabLabel(tab) : "All tools"}</summary>
        <div className="workspace-tools__menu">{groups.map((entry) => <section key={entry.title}><h2>{entry.title}</h2>{entry.tabs.map(link)}</section>)}</div>
      </details>
      <button className="cmdk" onClick={openCommands} aria-label="Search desk tools" title="Search tools">⌘K</button>
      <button className="cmdk" onClick={openHelp} aria-label="Keyboard shortcuts" title="Keyboard shortcuts">?</button>
    </nav>
  </>;
}
