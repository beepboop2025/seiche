/**
 * Every .card on every tab grows a quiet share chip, with no per-tab wiring:
 * a MutationObserver decorates cards as tabs render, and the card composes
 * itself from its own DOM at click time (h2 title, .sub line, .kv readings).
 * Charts inside cards keep their own share bar; this chip shares the numbers.
 */
import { composeStatCard, contextualLink, copyCard, fileName, nativeShare, savePng } from "./share";

const clean = (el: Element | null): string =>
  el ? (el as HTMLElement).innerText.replace(/\s+/g, " ").trim() : "";

function harvest(card: HTMLElement) {
  const title = clean(card.querySelector("h2"));
  const body = clean(card.querySelector(".sub"));
  const stats = [...card.querySelectorAll(".kv .item")]
    .map((it) => ({ k: clean(it.querySelector(".k")), v: clean(it.querySelector(".v")) }))
    .filter((s) => s.k && s.v);
  return { title, body, stats, link: contextualLink(card) };
}

// A second navigator.share() while the first is still open throws
// InvalidStateError on Safari and Chrome, so a double click must not start
// two. Tracked per chip, since two different cards may legitimately overlap.
const inFlight = new WeakSet<HTMLButtonElement>();

function onShare(card: HTMLElement, chip: HTMLButtonElement) {
  if (inFlight.has(chip)) return;
  const say = (msg: string) => {
    chip.textContent = msg;
    window.setTimeout(() => { chip.textContent = "share"; }, 2000);
  };
  const meta = harvest(card);
  if (!meta.title) { say("nothing here"); return; }
  const compose = () => Promise.resolve(composeStatCard(meta));

  let shareable = false;
  try {
    shareable = !!navigator.canShare?.({
      files: [new File([new Uint8Array(1)], "x.png", { type: "image/png" })],
    });
  } catch { shareable = false; }

  inFlight.add(chip);
  const done = () => { inFlight.delete(chip); };

  if (shareable) {
    compose()
      .then((cv) => nativeShare(cv, meta.title, meta.link))
      .then((ok) => { if (!ok) say("share failed"); })
      .catch(() => say("share failed"))
      .then(done, done);
    return;
  }
  copyCard(compose).then((ok) => {
    if (ok) { say("copied ✓"); return; }
    return compose()
      .then((cv) => savePng(cv, fileName(meta.title)))
      .then(() => say("saved ✓"), () => say("failed"));
  }).catch(() => say("failed")).then(done, done);
}

function decorate(root: ParentNode) {
  root.querySelectorAll<HTMLElement>(".tabview .card").forEach((card) => {
    // Do not manufacture a public link for unbounded replay/corpus surfaces or
    // private account state. Those tabs deliberately expose no share chip.
    if (card.closest("[data-share-disabled]")) return;
    if (card.querySelector(":scope > .cardshare")) return;
    if (!card.querySelector("h2")) return;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "cardshare";
    chip.textContent = "share";
    chip.title = "share this card as an image";
    chip.addEventListener("click", (e) => { e.stopPropagation(); onShare(card, chip); });
    card.appendChild(chip);
  });
}

// Cheap gate in front of the query. The board polls, so document mutates
// constantly, and re-running querySelectorAll plus a per-card lookup on every
// text-node change was most of this module's cost.
const addsACard = (records: MutationRecord[]): boolean =>
  records.some((r) =>
    Array.from(r.addedNodes).some(
      (n) => n instanceof Element && (n.matches(".card") || !!n.querySelector(".card")),
    ),
  );

export function mountCardShare(): () => void {
  // The .tabview element is keyed by tab, so React replaces it on every tab
  // switch and it cannot be the observer root. Its parent survives, and
  // watching that instead of document.body also skips the offscreen host the
  // chart export mounts on the body.
  const shell = (): Element => document.querySelector(".tabview")?.parentElement
    ?? document.body;
  let host = shell();
  const mo = new MutationObserver((records) => {
    const want = shell();
    if (want !== host) {
      host = want;
      mo.disconnect();
      mo.observe(host, { childList: true, subtree: true });
    }
    if (addsACard(records)) decorate(host);
  });
  mo.observe(host, { childList: true, subtree: true });
  decorate(host);
  return () => mo.disconnect();
}
