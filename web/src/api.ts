import { nativeDesktop, nativeInvoke } from './desktop';
export type Data = Record<string, any>;
let token = sessionStorage.getItem('marsd-session') || '';
export function authenticated() {
  return nativeDesktop || Boolean(token);
}
export function forgetSession() {
  token = '';
  sessionStorage.removeItem('marsd-session');
}
export async function api<T = Data>(
  path: string,
  body?: unknown,
  method = body === undefined ? 'GET' : 'POST',
): Promise<T> {
  if (nativeDesktop) {
    const reply = await nativeInvoke<{ status: number; data: Data }>('backend_request', {
      path,
      method,
      body: body ?? null,
    });
    if (reply.status < 200 || reply.status >= 300)
      throw new Error(
        `${reply.data.error?.code || reply.status}: ${reply.data.error?.message || 'Request failed.'} ${reply.data.error?.suggested_action || ''}`,
      );
    return reply.data as T;
  }
  const response = await fetch(`/api/${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      `${data.error?.code || response.status}: ${data.error?.message || 'Request failed.'} ${data.error?.suggested_action || ''}`,
    );
  return data as T;
}
export async function login(code: string) {
  const result = await api('session', { code });
  token = result.access_token;
  sessionStorage.setItem('marsd-session', token);
}
export function display(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return '—';
  if (typeof value === 'object') {
    const v = value as Data;
    if ('$int' in v) return v.$int;
    if ('$float' in v) return v.$float;
    if ('$date' in v) return v.$date;
    return JSON.stringify(value);
  }
  return String(value);
}
