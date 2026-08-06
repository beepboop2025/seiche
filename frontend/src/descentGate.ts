// The cinematic descent is a desktop introduction. On a narrow or coarse-
// pointer device it delays the answer and replaces native scrolling at the
// exact moment a first-time visitor is deciding whether the terminal is useful.
const SEEN_KEY = "seiche_descended";
const SKIP_MEDIA = "(max-width: 800px), (pointer: coarse), (prefers-reduced-motion: reduce)";

export const shouldDescend = (): boolean => {
  try {
    if (localStorage.getItem(SEEN_KEY)) return false;
  } catch {
    return false;
  }
  if (window.matchMedia(SKIP_MEDIA).matches) return false;
  const hash = window.location.hash.replace("#", "");
  return hash === "" || hash === "global"; // never intercept a deep link
};

export const markDescended = () => {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* private mode: the descent just plays again next time */
  }
};
