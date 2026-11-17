// URLs derivadas do ambiente: nada de localhost fixo.
const base = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export const apiUrl = (path: string) => `${base}${path}`;

export function wsUrl(path: string, token?: string): string {
  let origin: string;
  if (base) origin = base.replace(/^http/, 'ws');
  else origin = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
  const q = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${origin}${path}${q}`;
}

const TOKEN_KEY = 'goltrader_token';
export const getToken = () => sessionStorage.getItem(TOKEN_KEY) || '';
export const setToken = (t: string) => (t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY));

export async function apiPost(path: string, body?: unknown): Promise<{ ok: boolean; status: number; data: any }> {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  return { ok: res.ok, status: res.status, data };
}
