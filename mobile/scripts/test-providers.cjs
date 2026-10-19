// Exercise the actual TypeScript transport with native storage/network stubs.
// These are contract tests, not Android networking or live inference evidence.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/providers.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
let sent;
let response = { protocol: 1, runtime: 'acc-mobile', providers: [{ id: 'claude', state: 'signed_in', billing: 'subscription' }, { id: 'codex', state: 'sign_in_needed', billing: 'unknown' }] };
const saved = new Map();
const moduleExports = {};
const sandbox = {
  exports: moduleExports, Error, URL, AbortController, TextDecoder, setTimeout, clearTimeout,
  require(name) {
    if (name === 'react-native') return { Platform: { OS: 'android' } };
    if (name === 'expo-secure-store') return {
      getItemAsync: async k => saved.get(k), setItemAsync: async (k, v) => saved.set(k, v), deleteItemAsync: async k => saved.delete(k),
    };
    if (name === 'expo/fetch') return { fetch: async (url, options) => {
      sent = { url, options };
      return new Response(JSON.stringify(response), { status: 200 });
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
  assert.equal(sent.options.headers.Authorization, 'Bearer ' + connection.token);
  assert.equal(sent.url, connection.url + '/v1/status');
  response.providers[1].id = 'claude';
  await assert.rejects(() => api.checkRuntime(connection));
  const job = { id: 'fixture_request_123', provider: 'claude', state: 'completed', reply: 'Hello', error: '' };
  assert.equal(api.validateJob(job, job.id, 'claude').reply, 'Hello');
  assert.throws(() => api.validateJob(job, job.id, 'codex'));
  assert.throws(() => api.validateJob({ ...job, reply: '' }, job.id, 'claude'));
  assert.throws(() => api.validateJob({ ...job, id: 'different' }, job.id, 'claude'));
  assert.equal(api.safeConnectionError(new Error('secret ' + connection.token), connection), 'secret [redacted]');
  await api.saveConnection(null);
  assert.equal(await api.loadConnection(), null);
  console.log('Provider transport: endpoint, encrypted-store boundary, redirect refusal, billing/status shape, result identity and redaction checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
