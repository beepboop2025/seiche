// Where to find a live Seiche API from this page's origin.
//
// Dev (vite proxy) and a self-hosted box serve /api on the same origin, so
// the base is empty. The PUBLIC static site (seiche.info / github.io) has no
// backend process; there the Hetzner box exposes a read-only window at
// api.seiche.info (overview + asof only — Caddy 404s everything else).
// Callers keep their existing fallbacks: if the box is unreachable the site
// degrades to the CI-baked snapshot exactly as before.
const PUBLIC_HOSTS = ["seiche.info", "www.seiche.info", "beepboop2025.github.io"];
const CANONICAL_API_ORIGIN = "https://api.seiche.info";

export const API_BASE = PUBLIC_HOSTS.includes(window.location.hostname)
  ? CANONICAL_API_ORIGIN
  : "";

// The market corpus is a separately operated, read-only service mounted on the
// shared API origin. Local UI development talks to that canonical service
// directly; Seiche request handlers never fetch it or fold catalog rows into
// analytics.
export const CORPUS_API_BASE = `${API_BASE || CANONICAL_API_ORIGIN}/api/v2/corpus`;
