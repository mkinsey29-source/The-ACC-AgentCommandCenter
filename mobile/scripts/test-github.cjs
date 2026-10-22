// Tests the shipped TypeScript client; fixtures do not establish account acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/github.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const store = new Map(), sent = [];
const token = 'github_pat_' + 'a'.repeat(50), sha = 'a'.repeat(40);
let status = 200, response = {}, failWrite = false, overrideFetch;
const exportsObject = {};
vm.runInNewContext(compiled, {
  exports: exportsObject, Error, Uint8Array, TextDecoder, TextEncoder, AbortController, atob, setTimeout, clearTimeout,
  require(name) {
    if (name === 'react-native') return { Platform: { OS: 'android' } };
    if (name === 'expo-secure-store') return { getItemAsync: async k => store.get(k), setItemAsync: async (k,v) => { if (failWrite) throw Error('private storage error ' + token); store.set(k,v); }, deleteItemAsync: async k => store.delete(k) };
    if (name === 'expo/fetch') return { fetch: async (url, options) => { sent.push({url,options}); if (overrideFetch) return overrideFetch(url, options); return new Response(typeof response === 'string' ? response : JSON.stringify(response), {status}); } };
    throw Error('Unknown dependency');
  },
});
const api = exportsObject;
let checks = 0;
async function check(name, fn) { await fn(); checks++; console.log('PASS: ' + name); }
async function main() {
  await check('repository, path and ref injection refused', async () => {
    for (const s of ['https://evil.test/x/y', 'x/y?token=secret', 'x/..', '../repo', 'x/y/z', 'https://user:pass@github.com/x/y']) assert.throws(() => api.repositoryName(s));
    for (const s of ['../secret', '/etc/passwd', 'a/../secret', 'a\\b', 'a\nheader', 'a//b']) assert.throws(() => api.filePath(s));
    assert.equal(api.repositoryName('https://github.com/owner/repo.git'), 'owner/repo');
  });
  await check('unauthenticated public repository read uses fixed origin and GET', async () => {
    response = { full_name:'owner/repo', default_branch:'main', private:false };
    assert.equal((await api.getRepository('owner/repo')).branch,'main');
    const req = sent.at(-1); assert.equal(req.url, 'https://api.github.com/repos/owner/repo'); assert.equal(req.options.method, 'GET'); assert.equal(req.options.headers.Authorization,undefined); assert.equal(req.options.redirect,'error'); assert.equal(req.options.credentials,'omit');
  });
  await check('failed sign-in does not save credential or expose remote body', async () => {
    status = 401; response = {message:token};
    await assert.rejects(api.connectGitHub(token), e => !e.message.includes(token)); assert.equal(store.size,0);
    assert.equal(api.githubError(Error(token)).includes(token), false); status=200;
  });
  await check('verified token only saved in encrypted-store adapter', async () => {
    response={login:'fixture-user'}; assert.equal(await api.connectGitHub(token),'fixture-user');
    assert.equal(await api.hasGitHubToken(),true); assert.equal([...store.values()][0],token);
    assert.equal(sent.at(-1).options.headers.Authorization,'Bearer '+token); assert.equal(sent.at(-1).options.body,undefined);
  });
  await check('invalid token and account response never replace existing credential', async () => {
    await assert.rejects(api.connectGitHub('bad')); response={login:'invalid/account'}; await assert.rejects(api.connectGitHub('ghp_'+'b'.repeat(30))); assert.equal([...store.values()][0],token);
  });
  await check('pagination explicit and branch slash safely encoded', async () => {
    response=Array.from({length:30},(_,i)=>({full_name:'owner/repo'+i,default_branch:'main',private:true}));
    assert.equal((await api.listRepositories(2)).more,true); assert.match(sent.at(-1).url,/page=2&/); await assert.rejects(api.listRepositories(0));
    response={ref:'refs/heads/feature/name',object:{type:'commit',sha}}; assert.equal(await api.resolveCommit('owner/repo','feature/name'),sha); assert.match(sent.at(-1).url,/feature%2Fname$/);
    response={ref:'refs/heads/other',object:{type:'commit',sha}}; await assert.rejects(api.resolveCommit('owner/repo','feature/name'));
  });
  await check('listing pinned, unsupported entries omitted, wrong parent rejected', async () => {
    response=[{name:'README.md',path:'README.md',type:'file',sha,size:20},{name:'vendor',path:'vendor',type:'submodule',sha,size:0}];
    assert.equal((await api.listFiles('owner/repo',sha)).length,1); assert.match(sent.at(-1).url,new RegExp('ref='+sha));
    response=[{name:'file',path:'other/file',type:'file',sha,size:1}]; await assert.rejects(api.listFiles('owner/repo',sha,'expected'));
  });
  const text='Unicode café 世界\nhello';
  const file=(txt=text)=>({type:'file',path:'README.md',encoding:'base64',content:Buffer.from(txt).toString('base64'),size:Buffer.byteLength(txt)});
  await check('UTF-8 file decoded, revision and path preserved in explicit attachment', async () => {
    response=file(); const a=await api.readGitHubFile('owner/repo','main',sha,'README.md'); assert.equal(a.text,text); assert.equal(a.commit,sha); assert.match(api.attachmentText(a),/source content, not instructions/); assert.equal(api.attachmentText(a).includes(token),false);
  });
  await check('binary, invalid UTF-8, oversized and mismatched file refused', async () => {
    for (const payload of [{...file(),size:30001},file('a\0b'),{...file(),content:'/w==',size:1},{...file(),size:1},{...file(),type:'symlink',target:'elsewhere'},{...file(),submodule_git_url:'https://evil.test'}, {...file(),content:'!'}, {...file(),path:'elsewhere'}]) { response=payload; await assert.rejects(api.readGitHubFile('owner/repo','main',sha,'README.md')); }
  });
  await check('rate limits and malformed server data return fixed errors', async () => {
    for(const code of [403,429,404,500]){status=code;response=token;await assert.rejects(api.getRepository('owner/repo'), e=>!e.message.includes(token));}status=200;response='malformed '+token;await assert.rejects(api.getRepository('owner/repo'),e=>!e.message.includes(token));
  });
  await check('response stream bounded even without Content-Length', async () => {
    response='x'.repeat(700001); await assert.rejects(api.getRepository('owner/repo'),/too large/);
  });
  await check('disconnect aborts active reads, rejects stale result and clears storage', async () => {
    let finish; overrideFetch=()=>new Promise(resolve=>{finish=resolve;});
    const pending=api.getRepository('owner/repo'); await new Promise(resolve=>setImmediate(resolve));
    const signal=sent.at(-1).options.signal; await api.forgetGitHub(); assert.equal(signal.aborted,true);
    finish(new Response(JSON.stringify({full_name:'owner/repo',default_branch:'main',private:true}))); await assert.rejects(pending,/changed/); assert.equal(await api.hasGitHubToken(),false); overrideFetch=undefined;
  });
  await check('storage failure leaves no successful connection claim', async () => {response={login:'fixture-user'};failWrite=true;await assert.rejects(api.connectGitHub(token));assert.equal(await api.hasGitHubToken(),false);failWrite=false;});
  console.log(`GitHub client: ${checks} checks passed; account/device acceptance remains separate.`);
}
main().catch(e=>{console.error(e);process.exitCode=1;});
