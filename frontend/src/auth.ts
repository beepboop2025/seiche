// Supporter session: a bearer token from POST /api/auth/login, kept in
// localStorage. No self-signup — accounts are provisioned by the desk
// (`seiche user add`), payments come later. Fail loud: helpers return
// explicit nulls, never fake a session.
import { API_BASE } from "./apiBase";

const KEY = "seiche_token";

export const getToken = (): string | null => localStorage.getItem(KEY);
export const clearToken = () => localStorage.removeItem(KEY);

export const authHeaders = (): Record<string, string> => {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
};

export async function login(username: string, password: string): Promise<{ ok: true; tier: string } | { ok: false; error: string }> {
  const r = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }).catch(() => null);
  if (!r) return { ok: false, error: "could not reach the API" };
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({})))?.detail ?? `HTTP ${r.status}`;
    return { ok: false, error: String(detail) };
  }
  const body = await r.json();
  localStorage.setItem(KEY, body.token);
  return { ok: true, tier: body.tier };
}
