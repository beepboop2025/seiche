// Where to find a live Seiche API from this page's origin.
//
// Dev (vite proxy) and a self-hosted box serve /api on the same origin, so
// the base is empty. The PUBLIC static site (seiche.info / github.io) has no
// backend process; there the Hetzner box exposes a read-only window at
// api.seiche.info (overview + asof only — Caddy 404s everything else).
// Callers keep their existing fallbacks: if the box is unreachable the site
// degrades to the CI-baked snapshot exactly as before.
const PUBLIC_HOSTS = ["seiche.info", "www.seiche.info", "beepboop2025.github.io"];

export const API_BASE = PUBLIC_HOSTS.includes(window.location.hostname)
  ? "https://api.seiche.info"
  : "";
