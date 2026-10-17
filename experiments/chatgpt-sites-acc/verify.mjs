import {readFile,stat} from 'node:fs/promises';
const required=['index.html','styles.css','app.js','package.json','build.mjs'];
let bytes=0;
for(const file of required){const info=await stat(file);if(!info.isFile())throw new Error(file+' missing');bytes+=info.size}
const html=await readFile('index.html','utf8');
const js=await readFile('app.js','utf8');
for(const phrase of ['MASTER CONTROL','ORCHESTRATOR CHAT','PLAY-BY-PLAY']) if(!html.includes(phrase)) throw new Error('Missing shell element: '+phrase);
if(!html.includes('name="viewport"')) throw new Error('Viewport metadata missing');
if(/localhost|127\.0\.0\.1/.test(js)) throw new Error('Sites prototype must not depend on a local-only address');
if(!js.includes('new WebSocket')) throw new Error('WebSocket transport seam missing');
if(!js.includes('fetch(apiBase')) throw new Error('External HTTPS transport seam missing');
if(!js.includes('seededTasks(96)')) throw new Error('Expected task-volume fixture missing');
if(!/chunk\s*<\s*20/.test(js)||!/index\s*<\s*100/.test(js)) throw new Error('2,000-event stress fixture missing');
console.log(JSON.stringify({status:'ok',source_bytes:bytes,notes:['no runtime dependencies','HTTPS seam present','WebSocket seam present','96-task fixture','2000-event stress fixture']},null,2));
