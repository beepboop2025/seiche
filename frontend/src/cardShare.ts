/**
 * Every .card on every tab grows a quiet share chip, with no per-tab wiring:
 * a MutationObserver decorates cards as tabs render, and the card composes
 * itself from its own DOM at click time (h2 title, .sub line, .kv readings).
 * Charts inside cards keep their own share bar; this chip shares the numbers.
 */
import { composeStatCard, copyCard, deepLink, fileName, nativeShare, savePng } from "./share";

const clean = (el: Element | null): string =>
  el ? (el as HTMLElement).innerText.replace(/\s+/g, " ").trim() : "";

function harvest(card: HTMLElement) {
  const title = clean(card.querySelector("h2"));
  const body = clean(card.querySelector(".sub"));
  const stats = [...card.querySelectorAll(".kv .item")]
    .map((it) => ({ k: clean(it.querySelector(".k")), v: clean(it.querySelector(".v")) }))
    .filter((s) => s.k && s.v);
  return { title, body, stats, link: deepLink() };
}

function onShare(card: HTMLElement, chip: HTMLButtonElement) {
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

  if (shareable) {
    compose()
      .then((cv) => nativeShare(cv, meta.title, meta.link))
      .then((ok) => { if (!ok) say("share failed"); });
    return;
  }
  copyCard(compose).then((ok) => {
    if (ok) { say("copied ✓"); return; }
    compose()
      .then((cv) => savePng(cv, fileName(meta.title)))
      .then(() => say("saved ✓"), () => say("failed"));
  });
}

function decorate() {
  document.querySelectorAll<HTMLElement>(".tabview .card").forEach((card) => {
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

export function mountCardShare(): () => void {
  const mo = new MutationObserver(decorate);
  mo.observe(document.body, { childList: true, subtree: true });
  decorate();
  return () => mo.disconnect();
}
