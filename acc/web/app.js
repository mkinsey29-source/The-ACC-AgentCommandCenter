'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('acc-token') || '';
history.replaceState(null, '', location.pathname);
let state = null, selected = null, cursor = 0, refreshTimer = null, streamController = null;
const labels = {queued:'Queued', running:'Working', stopping:'Stopping', interrupted:'Needs inspection', paused:'Paused', failed:'Failed', awaiting_review:'Needs review', accepted:'Accepted'};
function error(message) { for (const id of ['error','task-error']) { $(id).textContent = message || ''; $(id).hidden = !message; } }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function name(id) { return state?.agents.find(a => a.id === id)?.name || id || 'None'; }
function options(value) { return state.agents.map(a => `<option value="${escapeHTML(a.id)}" ${a.id===value?'selected':''} ${!a.available?'disabled':''}>${escapeHTML(a.name)}${a.available?'':' · not configured'}</option>`).join(''); }
async function refresh() {
  state = await api('state');
  if (!selected && state.tasks.length) selected = state.tasks[0].id;
  render();
}
function render() {
  $('project').textContent = state.project;
  $('branch').textContent = 'Branch: ' + (state.git.branch || 'unavailable');
  const active = state.tasks.filter(t=>t.status==='running').length;
  const accepted = state.tasks.filter(t=>t.status==='accepted').length;
  $('counts').textContent = `${state.tasks.length} tasks · ${active} working · ${accepted} accepted`;
  $('tasks').innerHTML = state.tasks.length ? state.tasks.map(t => `<button class="task ${selected===t.id?'selected':''}" data-task="${t.id}"><div class="top"><strong>${escapeHTML(t.title)}</strong><span class="badge">${labels[t.status] || escapeHTML(t.status)}</span></div><small>Assigned: ${escapeHTML(name(t.agent))} · Active: ${escapeHTML(name(t.active_agent))}</small><small>${escapeHTML(t.activity)}</small></button>`).join('') : '<div class="empty">No tasks yet. Add your first instruction, or connect the orchestrator bridge.</div>';
  $('changes').innerHTML = !state.git.available ? escapeHTML(state.git.message) : state.git.files.length ? state.git.files.map(f=>`<div class="file"><code>${escapeHTML(f.status)}</code> ${escapeHTML(f.path)} <span class="muted">· origin not inferred</span></div>`).join('') : '<p class="muted">Working tree is clean.</p>';
  $('commits').innerHTML = (state.git.commits || []).map(c=>`<div class="commit"><code>${escapeHTML(c.sha)}</code> ${escapeHTML(c.subject)}</div>`).join('') || '<p class="muted">No commits.</p>';
  $('events').innerHTML = state.events.length ? [...state.events].reverse().map(e=>`<div class="event"><time>${new Date(e.at*1000).toLocaleTimeString()}</time><span class="kind">${escapeHTML(e.kind.replaceAll('_',' '))}</span><pre>${escapeHTML(e.data.message || '')}</pre></div>`).join('') : '<p class="muted">No events yet.</p>';
  $('new-agent').innerHTML = options($('new-agent').value || 'local-command');
  // Preserve open input fields while events continue arriving.
  if (!$('detail').contains(document.activeElement) || document.activeElement.tagName === 'BUTTON') renderDetail();
}
function renderDetail() {
  const t = state.tasks.find(t=>t.id===selected);
  if (!t) return;
  const busy = ['running','stopping','interrupted'].includes(t.status);
  const available = state.agents.find(a=>a.id===t.agent)?.available;
  $('detail').innerHTML = `<small>REQUIREMENTS REVISION ${t.revision} · ${escapeHTML(labels[t.status])}</small><h2>${escapeHTML(t.title)}</h2><div class="instruction">${escapeHTML(t.instruction)}</div>
  <label>Assigned worker<select id="assigned" ${busy?'disabled':''}>${options(t.agent)}</select></label>
  <div class="current"><strong>Active: ${escapeHTML(name(t.active_agent))}</strong><small>${escapeHTML(t.activity)}</small><small>Next: ${escapeHTML(t.next_step)}</small></div>
  ${busy?'<div class="notice">Active takeover is not enabled in v0.1. Stop this runner, inspect its retained work, then reassign.</div>':''}
  ${state.recovery_required?'<div class="notice">An interrupted runner needs process-tree inspection before this workspace can run new work.</div>':''}
  <div class="actions"><button id="start" ${busy||!available||t.status==='accepted'||state.recovery_required?'disabled':''}>${t.runs.length?'Run again':'Start task'}</button><button id="stop" ${t.status!=='running'?'disabled':''}>Stop now</button></div>
  <p class="muted">${t.exit_code===null?'No completed exit result.':'Last exit code: '+t.exit_code+' · Completion alone is not approval.'}</p>
  <details><summary>Instructions and history</summary><form id="instructions-form"><label>Current instruction<textarea name="instruction" rows="5" ${busy?'disabled':''}>${escapeHTML(t.instruction)}</textarea></label><button ${busy?'disabled':''}>Save revision</button></form>${t.requirements_history.map(r=>`<div class="evidence"><small>Revision ${r.revision}</small>${escapeHTML(r.instruction)}</div>`).join('')}</details>
  <details><summary>Evidence and review (${t.evidence.length})</summary>${t.evidence.map(e=>`<div class="evidence"><small>${escapeHTML(e.source)} · revision ${e.revision}</small>${escapeHTML(e.message)}<small>${escapeHTML(e.reference)}</small></div>`).join('')}
  <form id="report-form"><label>Result or review findings<textarea name="message" required rows="3"></textarea></label><label>Evidence / snapshot reference<input name="reference" placeholder="Commit, snapshot ID, or evidence path"></label><div class="actions"><button>Record report</button><button type="button" id="accept" ${t.status!=='awaiting_review'?'disabled':''}>Record review acceptance</button></div></form></details>
  <details><summary>Run history (${t.runs.length})</summary>${t.runs.map(r=>`<div class="evidence"><small>${escapeHTML(name(r.agent))} · PID ${r.pid} · revision ${r.revision}</small><code>${escapeHTML(r.id)}</code><small>${escapeHTML(r.folder)}</small><small>${r.ended?'Exited '+r.exit_code:'No terminal result recorded'}</small></div>`).join('')}</details>
  ${t.status==='interrupted'?'<details><summary>Recover interrupted task</summary><p class="notice">Inspect the recorded PID and every descendant outside ACC. Confirm none can still write to this workspace.</p><label><span><input type="checkbox" id="inspected"> I inspected the old process tree and retained files.</span></label><button id="recover">Release interrupted task</button></details>':''}
  <div class="notice">Publishing is not connected yet. Local commits and remote PRs are separate states.</div>`;
  $('assigned').onchange = e => action('assign',{agent:e.target.value});
  $('start').onclick = ()=>action('start',{});
  $('stop').onclick = ()=>action('stop',{});
  $('instructions-form').onsubmit = e=>{e.preventDefault();action('instructions',{instruction:new FormData(e.target).get('instruction')});};
  $('report-form').onsubmit = e=>{e.preventDefault();action('report',Object.fromEntries(new FormData(e.target)));};
  $('accept').onclick = ()=>action('review',{...Object.fromEntries(new FormData($('report-form'))),revision:t.revision,run_id:t.run_id});
  if($('recover')) $('recover').onclick = ()=>action('recover',{process_tree_inspected:$('inspected').checked});
}
async function action(kind, payload) {
  try { error(''); await api(`tasks/${selected}/${kind}`,payload); await refresh(); renderDetail(); }
  catch(e) { error(e.message); }
}
async function stream() {
  if (streamController) streamController.abort();
  const controller = new AbortController(); streamController = controller;
  while (!controller.signal.aborted) {
    try {
      const response = await fetch('/api/events?after='+cursor, {headers:{Authorization:'Bearer '+token},signal:controller.signal});
      if (!response.ok) throw new Error('Activity stream unavailable');
      $('connection').textContent='Live local events';
      const reader=response.body.getReader(), decoder=new TextDecoder(); let pending='';
      while (true) {
        const {value,done}=await reader.read(); if(done) throw new Error('Disconnected');
        pending+=decoder.decode(value,{stream:true});
        let end;
        while((end=pending.indexOf('\n\n'))!==-1) {
          const frame=pending.slice(0,end);pending=pending.slice(end+2);
          const line=frame.split('\n').find(l=>l.startsWith('data: '));
          if(line) {const event=JSON.parse(line.slice(6));cursor=Math.max(cursor,event.seq);if(!refreshTimer) refreshTimer=setTimeout(()=>{refreshTimer=null;refresh().catch(e=>error(e.message));},100);}
        }
      }
    } catch(e) {
      if(controller.signal.aborted) return;
      $('connection').textContent='Reconnecting · state may be stale';
      await new Promise(r=>setTimeout(r,2000));
    }
  }
}
async function connect() {
  try { error('');await refresh();cursor=state.cursor;sessionStorage.setItem('acc-token',token);$('connect-panel').hidden=true;stream(); }
  catch(e){$('connect-panel').hidden=false;error(e.message);}
}
$('tasks').onclick=e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;render();renderDetail();}};
$('connect-form').onsubmit=e=>{e.preventDefault();token=$('token').value.trim();connect();};
$('new-task').onclick=()=>{if(!state){error('Connect to the local coordinator first.');return;} $('task-dialog').showModal();};
$('close-dialog').onclick=()=>$('task-dialog').close();
$('task-form').onsubmit=async e=>{e.preventDefault();try{const data=Object.fromEntries(new FormData(e.target));data.argv=data.argv.trim()?JSON.parse(data.argv):[];const t=await api('tasks',data);selected=t.id;$('task-dialog').close();e.target.reset();error('');await refresh();}catch(err){error(err.message);}};
if(token) connect();
