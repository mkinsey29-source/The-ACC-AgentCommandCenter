// Exercise the actual TypeScript transport with native storage/network stubs.
// These are contract tests, not Android networking or live inference evidence.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const path = require('node:path');
const crypto = require('node:crypto');
const proof = text => crypto.createHmac('sha256', 'x'.repeat(43)).update(text).digest('hex');
const source = fs.readFileSync(path.join(__dirname, '../src/providers.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
let sent, badHello = false, badReply = false, sends = 0;
let response = { protocol: 2, runtime: 'acc-mobile', providers: [{ id: 'claude', state: 'signed_in', billing: 'subscription' }, { id: 'codex', state: 'sign_in_needed', billing: 'unknown' }] };
const saved = new Map();
const moduleExports = {};
const sandbox = {
  exports: moduleExports, Error, Uint8Array, TextEncoder, URL, AbortController, TextDecoder, setTimeout, clearTimeout,
  require(name) {
    if (name === 'react-native') return { Platform: { OS: 'android' } };
    if (name === 'expo-secure-store') return {
      getItemAsync: async k => saved.get(k), setItemAsync: async (k, v) => saved.set(k, v), deleteItemAsync: async k => saved.delete(k),
    };
    if (name === 'expo') return { uuid: { v4: () => crypto.randomUUID() } };
    if (name.startsWith('@noble/hashes/')) return require(name);
    if (name === 'expo/fetch') return { fetch: async (url, options) => {
      if (url.endsWith('/v1/handshake')) {
        const nonce = JSON.parse(options.body).nonce, boot = 'a'.repeat(32);
        return new Response(JSON.stringify({ protocol: 2, nonce, boot, proof: badHello ? '0'.repeat(64) : proof('acc-server-v2\n' + nonce + '\n' + boot) }));
      }
      sent = { url, options }; sends++;
      assert.equal(options.headers['X-ACC-Signature'], proof(['acc-request-v2', options.headers['X-ACC-Boot'], options.headers['X-ACC-Nonce'], options.headers['X-ACC-Time'], options.method, new URL(url).pathname, crypto.createHash('sha256').update(options.body || '').digest('hex')].join('\n')));
      const body = JSON.stringify(response);
      const signature = proof(['acc-response-v2', options.headers['X-ACC-Nonce'], '200', crypto.createHash('sha256').update(body).digest('hex')].join('\n'));
      return new Response(body, { status: 200, headers: { 'X-ACC-Proof': badReply ? '0'.repeat(64) : signature } });
    } };
    throw Error('Unexpected dependency: ' + name);
  },
};
vm.runInNewContext(compiled, sandbox);
const api = moduleExports;
const connection = { id: 'fixture_connection_123', url: 'http://127.0.0.1:8765', token: 'x'.repeat(43), allowApi: false, models: { claude: '', codex: '' } };

async function main() {
  for (const value of ['http://evil.example', 'http://127.0.0.1.evil.example', 'https://user:pass@example.com', 'https://example.com/?token=x', 'https://example.com/path', 'file:///etc/passwd', 'https://example.com/#hidden']) {
    assert.throws(() => api.normalizeEndpoint(value), undefined, value);
  }
  assert.equal(api.normalizeEndpoint('https://runtime.example/'), 'https://runtime.example');
  assert.equal(api.normalizeEndpoint(connection.url), connection.url);
  await api.saveConnection(connection);
  assert.equal((await api.loadConnection()).token, connection.token);
  assert.equal((await api.checkRuntime(connection)).length, 2);
  assert.equal(sent.options.redirect, 'error', 'Credential-bearing requests must refuse redirects');
  assert.equal(sent.options.credentials, 'omit');
  assert.equal(sent.options.headers.Authorization, undefined);
  assert.equal(JSON.stringify(sent.options).includes(connection.token), false);
  assert.equal(sent.options.headers['X-ACC-Signature'].length, 64);
  assert.equal(sent.url, connection.url + '/v1/status');
  const before = sends;
  badHello = true;
  await assert.rejects(() => api.runtimeRequest(connection, '/v1/chat', { private: 'must not be sent' }));
  assert.equal(sends, before, 'Conversation must not be sent to an impostor listener');
  badHello = false; badReply = true;
  await assert.rejects(() => api.checkRuntime(connection));
  badReply = false;
  await api.runtimeRequest(connection, '/v1/chat', { text: 'Unicode שלום and newline\n' });
  response.providers[1].id = 'claude';
  await assert.rejects(() => api.checkRuntime(connection));
  const job = { id: 'fixture_request_123', provider: 'claude', state: 'completed', reply: 'Hello', error: '' };
  assert.equal(api.validateJob(job, job.id, 'claude').reply, 'Hello');
  assert.throws(() => api.validateJob(job, job.id, 'codex'));
  assert.throws(() => api.validateJob({ ...job, reply: '' }, job.id, 'claude'));
  assert.throws(() => api.validateJob({ ...job, id: 'different' }, job.id, 'claude'));
  assert.equal(api.safeConnectionError(new Error('secret ' + connection.token), connection), 'secret [redacted]');
  assert.equal(api.safeConnectionError(new Error('Invalid saved connection. Reconnect through Connections.'), { ...connection, token: '' }), 'Invalid saved connection. Reconnect through Connections.', 'An empty secret must preserve a readable validation error');
  await api.saveConnection(null);
  assert.equal(await api.loadConnection(), null);
  console.log('Provider transport: endpoint, encrypted-store boundary, signed transport with no transmitted pairing secret, redirect refusal, billing/status shape, result identity and redaction checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
