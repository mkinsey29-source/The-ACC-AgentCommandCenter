import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { fetch as expoFetch } from 'expo/fetch';
import { uuid } from 'expo';
import { hmac } from '@noble/hashes/hmac.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex, utf8ToBytes } from '@noble/hashes/utils.js';

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
  const mac = (text: string) => bytesToHex(hmac(sha256, utf8ToBytes(c.token), utf8ToBytes(text)));
  const digest = (text: string) => bytesToHex(sha256(utf8ToBytes(text)));
  async function exchange(route: string, method: string, rawBody?: string, headers?: Record<string, string>) {
    return expoFetch(c.url + route, {
      method, redirect: 'error', credentials: 'omit',
      headers: { ...(rawBody === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      body: rawBody, signal: controller.signal,
    });
  }
  async function readResponse(response: Awaited<ReturnType<typeof exchange>>, limit: number) {
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Runtime returned an empty response.');
    const decoder = new TextDecoder();
    let raw = '', bytes = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.length;
      if (bytes > limit) { await reader.cancel(); throw new Error('Runtime response is too large.'); }
      raw += decoder.decode(part.value, { stream: true });
    }
    return raw + decoder.decode();
  }
  try {
    // Prove possession of the pairing secret before sending any conversation.
    // A replacement loopback listener never receives the secret itself.
    const challenge = uuid.v4();
    const helloResponse = await exchange('/v1/handshake', 'POST', JSON.stringify({ nonce: challenge }));
    const hello = JSON.parse(await readResponse(helloResponse, 4096));
    if (!helloResponse.ok || hello.protocol !== 2 || hello.nonce !== challenge || typeof hello.boot !== 'string' ||
        !/^[a-f0-9]{32}$/.test(hello.boot) || hello.proof !== mac(`acc-server-v2\n${challenge}\n${hello.boot}`)) {
      throw new Error('Runtime identity could not be verified. Check the pairing secret and runtime.');
    }
    const nonce = uuid.v4(), stamp = Math.floor(Date.now() / 1000).toString();
    const method = body === undefined ? 'GET' : 'POST';
    const rawBody = body === undefined ? undefined : JSON.stringify(body);
    const signature = mac(['acc-request-v2', hello.boot, nonce, stamp, method, path, digest(rawBody || '')].join('\n'));
    const response = await exchange(path, method, rawBody, { 'X-ACC-Nonce': nonce, 'X-ACC-Time': stamp, 'X-ACC-Boot': hello.boot, 'X-ACC-Signature': signature });
    const raw = await readResponse(response, 700_000);
    if (response.headers.get('X-ACC-Proof') !== mac(['acc-response-v2', nonce, String(response.status), digest(raw)].join('\n'))) {
      throw new Error('Runtime response could not be authenticated.');
    }
    if (response.status === 401) throw new Error('Runtime pairing was refused. Check Connections.');
    if (response.status === 404) throw new Error('Request not found. The runtime may have restarted. It has not been resent.');
    if (!response.ok) throw new Error(`Runtime refused the request (${response.status}). Check the runtime and retry deliberately.`);
    try { return JSON.parse(raw); } catch { throw new Error('Runtime returned invalid data.'); }
  } finally { clearTimeout(timer); }
}

export async function checkRuntime(connection: Connection): Promise<ProviderStatus[]> {
  const data = await runtimeRequest(connection, '/v1/status') as { protocol?: number; runtime?: string; providers?: ProviderStatus[] };
  if (data?.protocol !== 2 || data.runtime !== 'acc-mobile' || !Array.isArray(data.providers) || data.providers.length !== 2 ||
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
  return (connection?.token ? message.split(connection.token).join('[redacted]') : message).slice(0, 400);
}
