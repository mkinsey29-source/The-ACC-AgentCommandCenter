import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { fetch as expoFetch } from 'expo/fetch';

export type Provider = 'claude' | 'codex';
export const providerNames: Record<Provider, string> = { claude: 'Claude Code', codex: 'ChatGPT / Codex' };
export type Connection = { id: string; url: string; token: string; allowApi: boolean; models: Record<Provider, string> };
export type ProviderStatus = { id: Provider; state: 'signed_in' | 'sign_in_needed' | 'not_installed' | 'check_failed'; billing: 'subscription' | 'api' | 'unknown' };
export type Message = { role: 'user' | 'assistant'; text: string };
export type Job = { id: string; provider: Provider; state: 'running' | 'completed' | 'failed' | 'cancelled'; reply: string; error: string };
const storageKey = 'acc.runtime.connection.v1';
let webConnection: Connection | null = null;

export function normalizeEndpoint(value: string): string {
  let url: URL;
  try { url = new URL(value.trim()); } catch { throw new Error('Enter a complete runtime URL.'); }
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || !url.hostname || /[^\x21-\x7e]/.test(value)) {
    throw new Error('Use only a runtime origin, without credentials, paths or query strings.');
  }
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname))) {
    throw new Error('Use HTTPS for another device. HTTP is allowed only for this device’s loopback runtime.');
  }
  return url.origin;
}

export function validateConnection(value: unknown): Connection {
  const c = value as Connection;
  if (!c || typeof c !== 'object' || typeof c.id !== 'string' || !/^[a-zA-Z0-9_-]{16,80}$/.test(c.id) ||
      typeof c.url !== 'string' || typeof c.token !== 'string' || !/^[A-Za-z0-9_-]{40,128}$/.test(c.token) ||
      typeof c.allowApi !== 'boolean' || !c.models || !['claude', 'codex'].every(p => {
        const model = c.models[p as Provider];
        return typeof model === 'string' && model.length <= 120 && !model.startsWith('-') && !/[^\x21-\x7e]/.test(model);
      })) throw new Error('Invalid saved connection. Reconnect through Connections.');
  return { ...c, url: normalizeEndpoint(c.url) };
}

export async function loadConnection(): Promise<Connection | null> {
  if (Platform.OS === 'web') return webConnection;
  const raw = await SecureStore.getItemAsync(storageKey);
  return raw ? validateConnection(JSON.parse(raw)) : null;
}

export async function saveConnection(connection: Connection | null) {
  if (Platform.OS === 'web') { webConnection = connection; return; }
  if (connection) await SecureStore.setItemAsync(storageKey, JSON.stringify(validateConnection(connection)));
  else await SecureStore.deleteItemAsync(storageKey);
}

export async function runtimeRequest(connection: Connection, path: string, body?: unknown): Promise<unknown> {
  const c = validateConnection(connection);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await expoFetch(c.url + path, {
      method: body === undefined ? 'GET' : 'POST', redirect: 'error', credentials: 'omit',
      headers: { Authorization: `Bearer ${c.token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
    });
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Runtime returned an empty response.');
    const decoder = new TextDecoder();
    let raw = '', bytes = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.length;
      if (bytes > 700_000) { await reader.cancel(); throw new Error('Runtime response is too large.'); }
      raw += decoder.decode(part.value, { stream: true });
    }
    raw += decoder.decode();
    if (response.status === 401) throw new Error('Runtime pairing was refused. Check Connections.');
    if (response.status === 404) throw new Error('Request not found. The runtime may have restarted. It has not been resent.');
    if (!response.ok) throw new Error(`Runtime refused the request (${response.status}). Check the runtime and retry deliberately.`);
    try { return JSON.parse(raw); } catch { throw new Error('Runtime returned invalid data.'); }
  } finally { clearTimeout(timer); }
}

export async function checkRuntime(connection: Connection): Promise<ProviderStatus[]> {
  const data = await runtimeRequest(connection, '/v1/status') as { protocol?: number; runtime?: string; providers?: ProviderStatus[] };
  if (data?.protocol !== 1 || data.runtime !== 'acc-mobile' || !Array.isArray(data.providers) || data.providers.length !== 2 ||
      !(['claude', 'codex'] as Provider[]).every(id => data.providers?.filter(p => p.id === id).length === 1) ||
      data.providers.some(p => !['signed_in', 'sign_in_needed', 'not_installed', 'check_failed'].includes(p.state) || !['subscription', 'api', 'unknown'].includes(p.billing))) {
    throw new Error('This runtime is not compatible with ACC Build 2.');
  }
  return data.providers;
}

export function validateJob(data: unknown, id: string, provider: Provider): Job {
  const j = data as Job;
  if (!j || j.id !== id || j.provider !== provider || !['running', 'completed', 'failed', 'cancelled'].includes(j.state) ||
      typeof j.reply !== 'string' || typeof j.error !== 'string' || j.reply.length > 100_000 || j.error.length > 1000 || (j.state === 'completed' && !j.reply.trim())) {
    throw new Error('Runtime returned a mismatched or incomplete response.');
  }
  return j;
}

export function safeConnectionError(error: unknown, connection?: Connection | null): string {
  const message = error instanceof Error ? error.message : 'Connection failed.';
  // Network implementations can include URLs; never copy a pairing secret into UI/logs.
  return (connection ? message.split(connection.token).join('[redacted]') : message).slice(0, 400);
}
