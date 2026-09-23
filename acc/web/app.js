'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('acc-token') || '';
history.replaceState(null, '', location.pathname);
let state = null, selected = null, workView = 'tasks', cursor = 0, refreshTimer = null, streamController = null, eventHistory = new Map();
const labels = {launching:'Launching', queued:'Queued', running:'Working', stopping:'Stopping', interrupted:'Needs inspection', paused:'Paused', failed:'Failed', completed:'Completed', awaiting_review:'Needs review', accepted:'Accepted', publishing:'Publishing'};
function error(message) { for (const id of ['error','task-error']) { $(id).textContent = message || ''; $(id).hidden = !message; } }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function name(id) { return state?.agents.find(a => a.id === id)?.name || id || 'None'; }
function taskLabel(t) { return `Task ${t.task_number}`; }
function options(value) { return state.agents.map(a => `<option value="${escapeHTML(a.id)}" ${a.id===value?'selected':''} ${!a.available?'disabled':''}>${escapeHTML(a.name)}${a.available?'':' · not configured'}</option>`).join(''); }
async function refresh() {
  state = await api('state');
  for (const e of state.events) eventHistory.set(e.seq, e);
  state.events = [...eventHistory.values()].sort((a,b)=>a.seq-b.seq);
  if (!selected && state.tasks.length) selected = state.tasks[0].id;
  render();
}
function render() {
  renderConversation();
  renderGitHub();
  renderIntegrations();
  $('project').textContent = state.project;
  $('project-mode').value = state.project_mode || 'online';
  $('branch').textContent = 'Branch: ' + (state.git.branch || 'unavailable');
  const active = state.tasks.filter(t=>t.status==='running').length;
  const accepted = state.tasks.filter(t=>t.status==='accepted').length;
  $('counts').textContent = `${state.tasks.length} tasks · ${active} working · ${accepted} accepted`;
  renderWorkList();
  $('changes').innerHTML = !state.git.available ? escapeHTML(state.git.message) : state.git.files.length ? state.git.files.map(f=>`<div class="file"><code>${escapeHTML(f.status)}</code> ${escapeHTML(f.path)} <span class="muted">· origin not inferred</span></div>`).join('') : '<p class="muted">Working tree is clean.</p>';
  $('commits').innerHTML = (state.git.commits || []).map(c=>`<div class="commit"><code>${escapeHTML(c.sha)}</code> ${escapeHTML(c.subject)}</div>`).join('') || '<p class="muted">No commits.</p>';
  $('events').innerHTML = state.events.length ? [...state.events].reverse().map(e=>`<div class="event"><time>${new Date(e.at*1000).toLocaleTimeString()}</time><span class="kind">${escapeHTML(e.kind.replaceAll('_',' '))}</span><pre>${escapeHTML(e.data.message || '')}</pre></div>`).join('') : '<p class="muted">No events yet.</p>';
  $('new-agent').innerHTML = options($('new-agent').value || 'local-command');
  // Preserve open input fields while events continue arriving.
  if (!$('detail').contains(document.activeElement) || document.activeElement.tagName === 'BUTTON') renderDetail();
}
function renderIntegrations() {
  const hub=state.integrations||{providers:[],jobs:[],memory:[],job_counts:{}};
  $('provider-summary').innerHTML=hub.providers.map(p=>`<div class="provider-card"><strong>${escapeHTML(p.name)}</strong><small>${escapeHTML(p.status.replaceAll('_',' '))} · ${p.local?'local':'cloud'}</small><small>${escapeHTML(p.capabilities.join(', '))}</small></div>`).join('')||'<p class="muted">No providers registered.</p>';
  if(!$('integration-job-form').contains(document.activeElement)){
    const selectedProvider=$('integration-provider').value;
    $('integration-provider').innerHTML='<option value="">Automatic</option>'+hub.providers.map(p=>`<option value="${escapeHTML(p.id)}" ${p.id===selectedProvider?'selected':''}>${escapeHTML(p.name)} · ${escapeHTML(p.status.replaceAll('_',' '))}</option>`).join('');
  }
  $('integration-jobs').innerHTML=hub.jobs.slice(0,20).map(j=>`<div class="integration-record"><strong>${escapeHTML(j.capability)}</strong><small>${escapeHTML(j.provider)} · ${escapeHTML(j.status.replaceAll('_',' '))} · priority ${escapeHTML(j.priority)}</small><small>${escapeHTML(j.data_classification||'internal')} · ${escapeHTML((j.workspace_scope||'project').replaceAll('_',' '))}</small>${j.last_error?`<small>${escapeHTML(j.last_error)}</small>`:''}<div class="actions">${['queued','blocked_offline','waiting_provider'].includes(j.status)?`<button data-job="${escapeHTML(j.id)}" data-job-action="cancel">Cancel</button>`:''}${j.status==='failed'?`<button data-job="${escapeHTML(j.id)}" data-job-action="retry">Retry</button>`:''}</div></div>`).join('')||'<p class="muted">No integration jobs.</p>';
  $('memory-list').innerHTML=hub.memory.slice(0,20).map(m=>`<div class="memory-record"><strong>${escapeHTML(m.title)}</strong><small>${escapeHTML(m.kind)} · v${escapeHTML(m.version)} · ${escapeHTML(m.status)}</small><p>${escapeHTML(m.body)}</p>${m.status==='proposed'?`<div class="actions"><button data-memory="${escapeHTML(m.id)}" data-memory-status="active">Accept</button><button data-memory="${escapeHTML(m.id)}" data-memory-status="rejected">Reject</button></div>`:''}</div>`).join('')||'<p class="muted">No shared memory yet.</p>';
}
function renderWorkList() {
  $('view-tasks').classList.toggle('selected',workView==='tasks');
  $('view-agents').classList.toggle('selected',workView==='agents');
  if(workView==='tasks') {
    $('tasks').innerHTML = state.tasks.length ? [...state.tasks].sort((a,b)=>b.task_number-a.task_number).map(t => `<button class="task ${selected===t.id?'selected':''}" data-task="${t.id}"><div class="top"><strong><span class="task-number">${escapeHTML(taskLabel(t))}</span> ${escapeHTML(t.title)}</strong><span class="badge">${labels[t.status] || escapeHTML(t.status)}</span></div><small>Assigned: ${escapeHTML(name(t.agent))} · Active: ${escapeHTML(name(t.active_agent))}</small><small>${escapeHTML(t.activity)}</small></button>`).join('') : '<div class="empty">No tasks yet. Add your first instruction, or connect the orchestrator bridge.</div>';
    return;
  }
  $('tasks').innerHTML = state.agents.map(agent=>{
    const assigned=state.tasks.filter(t=>t.agent===agent.id||t.active_agent===agent.id||Object.values(t.workflow||{}).includes(agent.id)).sort((a,b)=>b.task_number-a.task_number);
    const active=assigned.find(t=>t.active_agent===agent.id&&['launching','running','stopping','processing_result','publishing'].includes(t.status));
    const profiles=(state.routing?.profiles||[]).filter(p=>p.agent===agent.id);
    const performance=profiles.map(p=>`${p.role}: ${Math.round(p.reliability*100)}% reliable / ${p.samples} runs`).join(' · ');
    return `<article class="agent-card"><div class="top"><strong>${escapeHTML(agent.name)}</strong><span class="badge">${agent.available?'Available':'Not configured'}</span></div><small>${active?`Working on ${escapeHTML(taskLabel(active))}: ${escapeHTML(active.title)}`:'No active task'} · ${assigned.length} linked</small>${performance?`<small>${escapeHTML(performance)}</small>`:''}<div class="agent-tasks">${assigned.slice(0,6).map(t=>`<button data-task="${t.id}" class="${selected===t.id?'selected':''}"><span>${escapeHTML(taskLabel(t))}</span> ${escapeHTML(t.title)} <small>${escapeHTML(labels[t.status]||t.status)}</small></button>`).join('')||'<span class="muted">No task history for this agent.</span>'}</div></article>`;
  }).join('');
}
function renderDetail() {
  const t = [...state.tasks,...(state.conversation?.background_runs||[])].find(t=>t.id===selected);
  if (!t) { $('detail').innerHTML='<div class="empty">Select a task or an active background run to inspect it.</div>'; return; }
  if(t.internal){
    $('detail').innerHTML=`<h2>${escapeHTML(t.title)}</h2><p>${escapeHTML(t.activity)}</p><p>Agent: ${escapeHTML(name(t.active_agent||t.agent))} · PID: ${escapeHTML(t.pid)}</p><p>Run: ${escapeHTML(t.run_id)}</p><button id="stop-internal" ${['running','stopping'].includes(t.status)?'':'disabled'}>Stop this run</button>${t.status==='interrupted'?'<p>Inspect the old process and descendants on this computer before recovery.</p><label><span><input type="checkbox" id="inspected"> I inspected the previous process tree and retained files.</span></label><button id="recover">Release interrupted run</button>':''}`;
    $('stop-internal').onclick=()=>action('stop',{});
    if($('recover'))$('recover').onclick=()=>action('recover',{process_tree_inspected:$('inspected').checked});
    return;
  }
  const busy = ['launching','running','stopping','processing_result','publishing','interrupted'].includes(t.status);
  const workflowRecord = t.task_kind === 'workflow_step';
  const w = t.workflow;
  const available = state.agents.find(a=>a.id===t.agent)?.available;
  $('detail').innerHTML = `<small>${escapeHTML(taskLabel(t).toUpperCase())} · REQUIREMENTS REVISION ${t.revision} · ${escapeHTML(labels[t.status])}</small><h2>${escapeHTML(t.title)}</h2><div class="instruction">${escapeHTML(t.instruction)}</div>
  <label>Assigned worker<select id="assigned" ${workflowRecord||busy||w?'disabled':''}>${options(t.agent)}</select></label>
  <div class="current"><strong>Active: ${escapeHTML(name(t.active_agent))}</strong><small>${escapeHTML(t.activity)}</small><small>Next: ${escapeHTML(t.next_step)}</small></div>
  ${t.routing?`<div class="notice"><strong>Automatically routed</strong><br>${escapeHTML(t.routing.task_area)} · ${escapeHTML(String(t.routing.source||'routing').replaceAll('_',' '))}${t.routing.low_confidence?' · deterministic policy resolved low confidence':''}<br>Implementation: ${escapeHTML(name(t.routing.selected?.implementer))} · Review: ${escapeHTML(name(t.routing.selected?.reviewer))} · Coordination: ${escapeHTML(name(t.routing.selected?.coordinator))}</div>`:''}
  ${t.pending_switch?`<div class="notice">Waiting for the current step to finish, then switching ${escapeHTML(t.pending_switch.role)} to ${escapeHTML(name(t.pending_switch.agent))}.</div>`:''}
  ${state.recovery_required?'<div class="notice">An interrupted runner needs process-tree inspection before this workspace can run new work.</div>':''}
  ${workflowRecord?`<div class="notice">This ${escapeHTML(t.workflow_stage)} record belongs to Task ${t.parent_task_number}. Run and control it through the parent task.</div>`:''}<div class="actions"><button id="start" ${workflowRecord||busy||w||!available||t.status==='accepted'||state.recovery_required?'disabled':''}>${t.runs.length?'Run again':'Start task'}</button><button id="stop" ${workflowRecord||!['running','stopping'].includes(t.status)?'disabled':''}>Stop now</button></div>
  ${w && !['accepted','publishing','interrupted'].includes(t.status)?`<form id="switch-form"><label>Change role<select name="role"><option value="implementer">Implementation</option><option value="reviewer">Review</option><option value="coordinator">Coordination</option></select></label><label>Use agent<select name="agent">${options(w.implementer)}</select></label><button ${t.pending_switch?'disabled':''}>Switch after current step</button><p class="muted">The current step finishes first. Its files and reports stay available to the next agent.</p></form>`:''}
  ${workflowRecord?'':`<details><summary>Priority and prerequisites</summary><form id="schedule-form"><label>Priority (higher runs first)<input name="priority" type="number" min="0" max="100" value="${t.priority??50}" ${busy?'disabled':''}></label><label>Wait for accepted tasks<select name="depends_on" multiple ${busy?'disabled':''}>${state.tasks.filter(x=>x.id!==t.id&&x.task_kind!=='workflow_step').map(x=>`<option value="${x.id}" ${(t.depends_on||[]).includes(x.id)?'selected':''}>${escapeHTML(taskLabel(x))}: ${escapeHTML(x.title)}</option>`).join('')}</select></label><button ${busy?'disabled':''}>Save schedule</button></form></details>`}
  <details ${w?'open':''}><summary>Background coordination${w?' · '+escapeHTML(w.phase):''}</summary>
  ${w?`<p><strong>${escapeHTML(w.stage)} · round ${w.round}/${w.max_rounds}</strong><br>${w.enabled?'Enabled':'Paused or complete'} · ${escapeHTML(w.mode)}</p><small>Snapshot: ${escapeHTML(w.snapshot?.id || 'Not captured yet')}</small>`:''}
  <form id="workflow-form">
  <label>Implementation<select name="implementer" ${busy?'disabled':''}>${options(w?.implementer || t.agent)}</select></label>
  <label>Independent review<select name="reviewer" ${busy?'disabled':''}>${options(w?.reviewer || 'claude')}</select></label>
  <label>Coordinator<select name="coordinator" ${busy?'disabled':''}>${options(w?.coordinator || 'hermes-coordinator')}</select></label>
  <label>Connection mode<select name="mode" ${busy?'disabled':''}><option value="online" ${w?.mode!=='offline'?'selected':''}>Online · preferred agents</option><option value="offline" ${w?.mode==='offline'?'selected':''}>Offline · permitted local agents</option></select></label>
  ${['implementer','reviewer','coordinator'].map(role=>`<label>Permitted local fallback: ${role}<select name="fallback_${role}" ${busy?'disabled':''}><option value="">Wait if unavailable</option>${state.agents.filter(a=>a.local).map(a=>`<option value="${escapeHTML(a.id)}" ${w?.fallbacks?.[role]===a.id?'selected':''}>${escapeHTML(a.name)}</option>`).join('')}</select></label>`).join('')}
  <label>Maximum implementation rounds<input type="number" name="max_rounds" min="1" max="10" value="${w?.max_rounds || 3}" ${busy?'disabled':''}></label>
  <label><span><input type="checkbox" name="restart" ${busy?'disabled':''}> Restart implementation and discard previous approval</span></label><div class="actions"><button ${busy?'disabled':''}>${w?'Save and resume workflow':'Enable workflow'}</button><button type="button" id="pause-workflow" ${!w?.enabled?'disabled':''}>Pause workflow</button></div>
  </form>
  ${w?.history?.length?`<details><summary>Handoff reports (${w.history.length})</summary>${w.history.map(h=>`<div class="evidence"><small>${escapeHTML(h.stage)} · ${escapeHTML(name(h.agent))}</small><strong>${escapeHTML(h.result.summary)}</strong><small>${escapeHTML(h.result.verdict || h.result.action || '')}</small>${(h.result.findings || []).map(f=>`<p>${escapeHTML(typeof f==='string'?f:JSON.stringify(f))}</p>`).join('')}<small>Checks: ${escapeHTML(JSON.stringify(h.result.checks || []))}</small></div>`).join('')}</details>`:''}
  <p class="muted">Assignments change between runs. Use Stop now to interrupt a runner; inspect retained work before resuming. Local adapters require setup.</p></details>
  <p class="muted">${t.exit_code===null?'No completed exit result.':'Last exit code: '+t.exit_code+' · Completion alone is not approval.'}</p>
  <details><summary>Instructions and history</summary><form id="instructions-form"><label>Current instruction<textarea name="instruction" rows="5" ${busy?'disabled':''}>${escapeHTML(t.instruction)}</textarea></label><button ${busy?'disabled':''}>Save revision</button></form>${t.requirements_history.map(r=>`<div class="evidence"><small>Revision ${r.revision}</small>${escapeHTML(r.instruction)}</div>`).join('')}</details>
  <details><summary>Evidence and review (${t.evidence.length})</summary>${t.evidence.map(e=>`<div class="evidence"><small>${escapeHTML(e.source)} · revision ${e.revision}</small>${escapeHTML(e.message)}<small>${escapeHTML(e.reference)}</small></div>`).join('')}
  <form id="report-form"><label>Result or review findings<textarea name="message" required rows="3"></textarea></label><label>Evidence / snapshot reference<input name="reference" placeholder="Commit, snapshot ID, or evidence path"></label><div class="actions"><button>Record report</button><button type="button" id="accept" ${w||t.status!=='awaiting_review'?'disabled':''}>Record review acceptance</button></div></form></details>
  <details><summary>Run history (${t.runs.length})</summary>${t.runs.map(r=>`<div class="evidence"><small>${escapeHTML(name(r.agent))} · PID ${r.pid} · revision ${r.revision}</small><code>${escapeHTML(r.id)}</code><small>${escapeHTML(r.folder)}</small><small>${r.ended?'Exited '+r.exit_code:'No terminal result recorded'}</small></div>`).join('')}</details>
  ${t.status==='interrupted'?'<details><summary>Recover interrupted task</summary><p class="notice">Inspect the recorded PID and every descendant outside ACC. Confirm none can still write to this workspace.</p><label><span><input type="checkbox" id="inspected"> I inspected the old process tree and retained files.</span></label><button id="recover">Release interrupted task</button></details>':''}
  <details><summary>Contingency controls</summary><p class="muted">Use replacement only after the prior worker has stopped. ACC creates a numbered recovery task with the known worktree and run evidence.</p><form id="recovery-handoff-form"><label>Replacement agent<select name="agent" ${busy?'disabled':''}>${options(t.agent)}</select></label><label>Why the previous worker is unavailable<textarea name="reason" required maxlength="2000" ${busy?'disabled':''}></textarea></label><button ${busy?'disabled':''}>Create recovery handoff</button></form></details>
  <div class="notice">${t.publication?`Publication: ${escapeHTML(t.publication.status)} ${t.publication.url?githubLink(t.publication.url,'Open PR'):''}${t.publication.error?`<p>${escapeHTML(t.publication.error)}</p>`:''}`:'Accepted managed work can be published after inspecting its preview.'}</div><button id="publish-preview" ${t.status==='accepted'&&w?'':'disabled'}>Preview commit and PR</button>`;
  if($('switch-form')) $('switch-form').onsubmit=e=>{e.preventDefault();action('switch',{...Object.fromEntries(new FormData(e.target)),request_id:crypto.randomUUID()});};
  if($('schedule-form')) $('schedule-form').onsubmit=e=>{e.preventDefault();const data=new FormData(e.target);action('schedule',{priority:Number(data.get('priority')),depends_on:data.getAll('depends_on')});};
  $('publish-preview').onclick=()=>publicationPreview(t.id);
  $('workflow-form').onsubmit = e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));const fallbacks={};for(const role of ['implementer','reviewer','coordinator']){if(data['fallback_'+role]) fallbacks[role]=data['fallback_'+role];delete data['fallback_'+role];}action('workflow',{...data,enabled:true,restart:data.restart==='on',max_rounds:Number(data.max_rounds),fallbacks});};
  $('pause-workflow').onclick = ()=>action('workflow',{enabled:false});
  $('assigned').onchange = e => action('assign',{agent:e.target.value});
  $('start').onclick = ()=>action('start',{});
  $('stop').onclick = ()=>action('stop',{});
  $('instructions-form').onsubmit = e=>{e.preventDefault();action('instructions',{instruction:new FormData(e.target).get('instruction')});};
  $('report-form').onsubmit = e=>{e.preventDefault();action('report',Object.fromEntries(new FormData(e.target)));};
  $('accept').onclick = ()=>action('review',{...Object.fromEntries(new FormData($('report-form'))),revision:t.revision,run_id:t.run_id});
  if($('recover')) $('recover').onclick = ()=>action('recover',{process_tree_inspected:$('inspected').checked});
  $('recovery-handoff-form').onsubmit=e=>{e.preventDefault();action('recovery-handoff',Object.fromEntries(new FormData(e.target)));};
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
      await refresh();
      flushOutbox();
      const reader=response.body.getReader(), decoder=new TextDecoder(); let pending='';
      while (true) {
        const {value,done}=await reader.read(); if(done) throw new Error('Disconnected');
        pending+=decoder.decode(value,{stream:true});
        let end;
        while((end=pending.indexOf('\n\n'))!==-1) {
          const frame=pending.slice(0,end);pending=pending.slice(end+2);
          const line=frame.split('\n').find(l=>l.startsWith('data: '));
          if(line) {const event=JSON.parse(line.slice(6));eventHistory.set(event.seq,event);cursor=Math.max(cursor,event.seq);if(!refreshTimer) refreshTimer=setTimeout(()=>{refreshTimer=null;refresh().catch(e=>error(e.message));},100);}
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
  try { error('');await refresh();cursor=state.cursor;sessionStorage.setItem('acc-token',token);$('connect-panel').hidden=true;restoreDraft();stream(); }
  catch(e){$('connect-panel').hidden=false;error(e.message);}
}
$('tasks').onclick=e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;render();renderDetail();}};
$('view-tasks').onclick=()=>{workView='tasks';render();};
$('view-agents').onclick=()=>{workView='agents';render();};
$('project-mode').onchange=async e=>{try{await api('project/mode',{mode:e.target.value});await refresh();}catch(err){error(err.message);await refresh();}};
$('archive-search').onsubmit=async e=>{e.preventDefault();try{const data=Object.fromEntries(new FormData(e.target));const query=new URLSearchParams(Object.fromEntries(Object.entries(data).filter(([,v])=>v))).toString();const result=await api('archive?'+query);$('archive-results').innerHTML=result.tasks.map(t=>`<button class="task" data-task="${t.id}"><div class="top"><strong>${escapeHTML(taskLabel(t))} ${escapeHTML(t.title)}</strong><span class="badge">${escapeHTML(labels[t.status]||t.status)}</span></div><small>${escapeHTML(t.activity)}</small></button>`).join('')||'<p class="muted">No matching tasks.</p>';}catch(err){error(err.message);}};
$('archive-results').onclick=e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;render();renderDetail();}};
$('archive-export').onsubmit=async e=>{e.preventDefault();try{const result=await api('archive/export',Object.fromEntries(new FormData(e.target)));$('archive-status').textContent=`Exported ${result.tasks} tasks to ${result.markdown} and ${result.database}`;}catch(err){error(err.message);}};
$('integration-job-form').onsubmit=async e=>{e.preventDefault();try{const data=Object.fromEntries(new FormData(e.target));data.input=JSON.parse(data.input);if(!data.provider)delete data.provider;if(!data.idempotency_key)delete data.idempotency_key;await api('integrations/jobs',data);error('');await refresh();}catch(err){error(err.message);}};
$('integration-jobs').onclick=async e=>{const b=e.target.closest('[data-job-action]');if(!b)return;try{await api(`integrations/jobs/${b.dataset.job}/${b.dataset.jobAction}`,{});await refresh();}catch(err){error(err.message);}};
$('memory-form').onsubmit=async e=>{e.preventDefault();try{const data=Object.fromEntries(new FormData(e.target));data.tags=data.tags.split(',').map(x=>x.trim()).filter(Boolean);data.source='acc-dashboard';data.request_id=crypto.randomUUID();await api('memory/propose',data);e.target.reset();error('');await refresh();}catch(err){error(err.message);}};
$('memory-list').onclick=async e=>{const b=e.target.closest('[data-memory-status]');if(!b)return;try{await api(`memory/${b.dataset.memory}/review`,{status:b.dataset.memoryStatus,reviewer:'acc-dashboard'});await refresh();}catch(err){error(err.message);}};
$('connect-form').onsubmit=e=>{e.preventDefault();token=$('token').value.trim();connect();};
$('new-task').onclick=()=>{if(!state){error('Connect to the local coordinator first.');return;} $('task-dialog').showModal();};
$('close-dialog').onclick=()=>$('task-dialog').close();
$('task-form').onsubmit=async e=>{e.preventDefault();try{const data=Object.fromEntries(new FormData(e.target));data.argv=data.argv.trim()?JSON.parse(data.argv):[];const t=await api('tasks',data);selected=t.id;$('task-dialog').close();e.target.reset();error('');await refresh();}catch(err){error(err.message);}};



// Durable browser outbox: network retries reuse ids, including recorded audio.
let outboxDB, flushing = false, recorder = null, recordingTimer = null, routingDirty = false, sendingMessage = false;
let displayedConversationSession = null;
const messageHistory = new Map();
function openOutbox() {
  if (!outboxDB) outboxDB = new Promise((resolve,reject)=>{
    const r=indexedDB.open('acc-conversation',1);
    r.onupgradeneeded=()=>r.result.createObjectStore('outbox',{keyPath:'id'});
    r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);
  });
  return outboxDB;
}
async function outboxOperation(mode, operation) {
  const db=await openOutbox();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction('outbox',mode), request=operation(tx.objectStore('outbox'));
    let result; request.onsuccess=()=>{result=request.result;};
    tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
  });
}
async function outboxCount() {
  const entries=await outboxOperation('readonly',s=>s.getAll());
  const count=entries.filter(e=>e.project===state?.project).length;
  $('outbox-status').textContent=count ? `${count} message or recording saved on this browser, waiting to send.` : '';
}
async function flushOutbox() {
  if(flushing || !state) return;
  flushing=true;
  try {
    const entries=await outboxOperation('readonly',s=>s.getAll());
    entries.sort((a,b)=>a.at-b.at);
    for(const entry of entries) {
      if(entry.project!==state.project) continue;
      if(entry.kind==='audio') {
        const audio=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=()=>reject(r.error);r.readAsDataURL(entry.blob);});
        await api('voice/save',{id:entry.id,mime:entry.blob.type,audio,session_id:entry.session_id});
      } else await api('conversation/send',{id:entry.id,text:entry.text,source:'acc',session_id:entry.session_id});
      await outboxOperation('readwrite',s=>s.delete(entry.id));
    }
    await outboxCount();
  } catch(e) {await outboxCount();error('Saved on this browser. '+e.message);}
  finally {flushing=false;}
}
function draftKey(){return 'acc-draft:'+state.project+':'+(state.orchestrators?.selected||'auto');}
function restoreDraft(force=false){if(force||!$('message').value) $('message').value=localStorage.getItem(draftKey())||'';}
$('message').oninput=()=>{if(state) localStorage.setItem(draftKey(),$('message').value);};
$('message').onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){$('conversation-form').requestSubmit();e.preventDefault();}};
$('conversation-form').onsubmit=async e=>{
  e.preventDefault();if(!state){error('Connect to ACC first. Your text is still here.');return;}
  const text=$('message').value;if(!text.trim()||sendingMessage)return;
  sendingMessage=true;$('send-message').disabled=true;
  try {
    await outboxOperation('readwrite',s=>s.put({id:crypto.randomUUID(),kind:'text',text,project:state.project,session_id:state.orchestrators?.selected||'auto',at:Date.now()}));
    if($('message').value===text){$('message').value='';localStorage.removeItem(draftKey());}
    error('');await flushOutbox();await refresh();
  } catch(e){error(e.message);}
  finally{sendingMessage=false;$('send-message').disabled=false;}
};
$('load-history').onclick=async()=>{
  if(!state)return;
  $('load-history').disabled=true;
  try{let after=0;const session=state.orchestrators?.selected||'auto';while(true){const page=await api('conversation?after='+after+'&session_id='+encodeURIComponent(session));for(const m of page.messages)messageHistory.set(m.id,m);if(page.messages.length<100)break;after=page.messages.at(-1).seq;}renderConversation();}catch(e){error(e.message);}finally{$('load-history').disabled=false;}
};
$('retry-conversation').onclick=async()=>{try{await api('conversation/retry',{});await refresh();}catch(e){error(e.message);}};
function modelOptions(selected, localOnly=false, emptyLabel='None'){
  return `<option value="">${emptyLabel}</option>`+state.agents.filter(a=>a.kind==='model'&&(!localOnly||a.local)).map(a=>`<option value="${escapeHTML(a.id)}" ${a.id===selected?'selected':''}>${escapeHTML(a.name)}${a.available?'':' · not installed'}</option>`).join('');
}
function renderConversation(){
  const c=state.conversation;if(!c)return;
  const sessions=state.orchestrators||{selected:'auto',pending:null,sessions:[]};
  if(displayedConversationSession!==sessions.selected){messageHistory.clear();displayedConversationSession=sessions.selected;restoreDraft(true);}
  const sessionSelect=$('orchestrator-session');
  if(document.activeElement!==sessionSelect){
    sessionSelect.innerHTML=sessions.sessions.map(s=>`<option value="${escapeHTML(s.id)}" ${s.id===sessions.selected?'selected':''} ${!s.available?'disabled':''}>${escapeHTML(s.name)}${s.available?'':' · not configured'}</option>`).join('');
  }
  const owner=c.owner?.owner;
  const switchNote=sessions.pending?` Switch to ${sessions.sessions.find(s=>s.id===sessions.pending)?.name||sessions.pending} queued after this decision.`:'';
  $('orchestrator-status').textContent=(c.held ? 'Needs attention: '+c.held : owner ? `${name(owner)} is handling your messages.` : c.pending ? `${c.pending} saved message(s) waiting for an orchestrator.` : 'Ready for your next message. Ideas remain discussion; requested work appears in the work plan.')+switchNote;
  const box=$('messages'), nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<60;
  for(const m of c.messages)messageHistory.set(m.id,m);
  const html=[...messageHistory.values()].sort((a,b)=>a.seq-b.seq).map(m=>`<article class="message ${m.role}"><small>${m.role==='user'?'You':escapeHTML(name(m.source))} · ${new Date(m.at*1000).toLocaleTimeString()}${m.status==='pending'?' · saved, waiting':''}</small><div>${escapeHTML(m.text)}</div>${m.data?.task_ids?.length?`<small>Linked tasks: ${m.data.task_ids.map(id=>{const task=state.tasks.find(t=>t.id===id);return `<button class="task-link" data-task="${escapeHTML(id)}">${task?escapeHTML(taskLabel(task)+': '+task.title):escapeHTML(id)}</button>`;}).join(' ')}</small>`:''}</article>`).join('');
  if(box.innerHTML!==html){box.innerHTML=html||'<p class="muted">Your conversation starts here. Messages from the desktop orchestrator appear here when it saves them through MCP.</p>';if(nearBottom)box.scrollTop=box.scrollHeight;}
  $('background-runs').innerHTML=(c.background_runs||[]).map(t=>`<button data-task="${escapeHTML(t.id)}">${escapeHTML(t.title)} · ${escapeHTML(t.status)} · inspect / stop</button>`).join('');
  $('retry-conversation').hidden=!c.held;
  $('record-voice').disabled=!c.voice_available||!navigator.mediaDevices||typeof MediaRecorder==='undefined';
  if(!recorder)$('voice-status').textContent=c.voice_available?'Transcribed on this computer.':'Voice needs a local transcription command in host settings.';
  $('recordings').innerHTML=(c.recordings||[]).slice(-5).map(r=>`<div class="muted">Recording: ${escapeHTML(r.activity)}${r.status==='paused'?` <button data-retry-voice="${escapeHTML(r.id)}">Retry transcription</button>`:''}</div>`).join('');
  if(!routingDirty&&!$('routing-form').contains(document.activeElement)){
    const s=c.settings||{},w=s.workflow||{};
    const router=state.routing||{};
    $('routing-fields').innerHTML=`<p class="muted">Autonomous routing: ${router.enabled?'on':'off'} · TypeSafe Jev: ${router.typesafe_configured?'ready':'deterministic fallback'} · no operator assignment approval</p><label>Preferred online orchestrator<select name="preferred_agent">${modelOptions(s.preferred_agent,false,'External session or local agent')}</select></label><label>Always-available local agent<select name="local_agent">${modelOptions(s.local_agent,true,'Save for later until configured')}</select></label><label>Connection preference<select name="mode"><option value="online" ${s.mode!=='offline'?'selected':''}>Try online, fall back locally</option><option value="offline" ${s.mode==='offline'?'selected':''}>Local only</option></select></label><label><span><input type="checkbox" name="enabled" ${s.enabled!==false?'checked':''}> Handle saved messages automatically</span></label><p class="muted">${router.enabled?'Continuity defaults; the router may select stronger matches':'Default assignments for work requested in conversation'}</p>`+['implementer','reviewer','coordinator'].map(role=>`<label>${role}<select name="${role}">${modelOptions(w[role])}</select></label><label>Local ${role}<select name="fallback_${role}">${modelOptions(w.fallbacks?.[role],true)}</select></label>`).join('');
  }
}
$('orchestrator-session').onchange=async e=>{try{await api('orchestrators/select',{session_id:e.target.value});error('');await refresh();}catch(err){error(err.message);await refresh();}};
$('messages').onclick=e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;render();$('detail').scrollIntoView({behavior:'smooth'});}};
$('background-runs').onclick=e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;renderDetail();$('detail').scrollIntoView({behavior:'smooth'});}};
$('routing-form').oninput=()=>{routingDirty=true;};
$('routing-form').onsubmit=async e=>{
  e.preventDefault();const data=Object.fromEntries(new FormData(e.target));
  const payload={preferred_agent:data.preferred_agent||null,local_agent:data.local_agent||null,mode:data.mode,enabled:data.enabled==='on'};
  if(['implementer','reviewer','coordinator'].some(r=>data[r])){
    const w={fallbacks:{}};for(const role of ['implementer','reviewer','coordinator']){w[role]=data[role];if(data['fallback_'+role])w.fallbacks[role]=data['fallback_'+role];}payload.workflow=w;
  }else payload.workflow=null;
  try{await api('conversation/configure',payload);routingDirty=false;error('');await refresh();}catch(e){error(e.message);}
};
$('recordings').onclick=async e=>{const b=e.target.closest('[data-retry-voice]');if(b)try{await api('voice/retry',{task_id:b.dataset.retryVoice});await refresh();}catch(e){error(e.message);}};
$('record-voice').onclick=async()=>{
  if(recorder){recorder.stop();return;}
  let media;
  try {
    media=await navigator.mediaDevices.getUserMedia({audio:true});
    const mime=['audio/webm','audio/ogg','audio/mp4'].find(m=>MediaRecorder.isTypeSupported(m));
    if(!mime)throw new Error('This browser has no supported recording format.');
    const current=new MediaRecorder(media,{mimeType:mime}),parts=[];let size=0;
    recorder=current;$('record-voice').textContent='Stop recording';$('voice-status').textContent='Recording… up to 2 minutes.';
    current.ondataavailable=e=>{parts.push(e.data);size+=e.data.size;if(size>9*1024*1024&&current.state==='recording')current.stop();};
    current.onstop=async()=>{
      clearTimeout(recordingTimer);media.getTracks().forEach(t=>t.stop());recorder=null;$('record-voice').textContent='Record voice';
      try{await outboxOperation('readwrite',s=>s.put({id:crypto.randomUUID(),kind:'audio',blob:new Blob(parts,{type:mime}),project:state.project,session_id:state.orchestrators?.selected||'auto',at:Date.now()}));await flushOutbox();await refresh();}catch(e){error(e.message);}
    };
    current.start(1000);recordingTimer=setTimeout(()=>{if(current.state==='recording')current.stop();},120000);
  }catch(e){media?.getTracks().forEach(t=>t.stop());error(e.message);}
};
window.addEventListener('online',()=>flushOutbox());
setInterval(()=>{if(state)flushOutbox();},5000);
if(token) connect();


function githubLink(url, label) {
  try { const parsed=new URL(url); if(parsed.protocol==='https:' && parsed.hostname==='github.com') return `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)}</a>`; } catch(_) {}
  return escapeHTML(label);
}
function renderGitHub() {
  const g=state.github || {};
  $('git-status').textContent='GitHub: '+(g.connected?'connected':g.stale?'cached / unavailable':'checking');
  $('github-activity').innerHTML=`<p>${escapeHTML(g.message)}</p><small>${g.last_success?'Last successful refresh: '+new Date(g.last_success*1000).toLocaleString():'No successful remote refresh yet.'} ${g.stale?'· May be out of date':''}</small>${(g.pull_requests||[]).map(p=>`<div class="commit">${githubLink(p.url,'#'+p.number+' '+p.title)}<small>${escapeHTML(p.state)} · ${escapeHTML(p.headRefName)} → ${escapeHTML(p.baseRefName)} · ${escapeHTML(p.reviewDecision||'No review verdict')}</small></div>`).join('')}<details><summary>Recent remote commits</summary>${(g.commits||[]).map(c=>`<div class="commit">${githubLink(c.url,c.sha.slice(0,8)+' '+c.subject)}</div>`).join('')}</details>`;
  if (!$('github-settings').contains(document.activeElement) && g.settings) for(const [key,value] of Object.entries(g.settings)){const input=$('github-settings').elements.namedItem(key);if(input){if(input.type==='checkbox')input.checked=value;else input.value=value;}}
}
$('github-refresh').onclick=async()=>{try{await api('github/refresh',{});await refresh();}catch(e){error(e.message);}};
$('github-settings').onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));try{await api('github/configure',{...d,enabled:d.enabled==='on',interval:Number(d.interval)});await refresh();}catch(e){error(e.message);}};
let publication=null;
async function publicationPreview(taskId) {
  try{
    const p=await api(`tasks/${taskId}/publish-preview`,{});
    publication={taskId,preview_id:p.id,request_id:crypto.randomUUID()};
    $('publish-content').innerHTML=`<h2>Publish reviewed work</h2><p>${escapeHTML(p.repository)} · ${escapeHTML(p.branch)} → ${escapeHTML(p.base)}</p><p>${escapeHTML(p.title)}</p><p>Files to commit:</p><pre>${escapeHTML(p.paths.join('\n')||'Already committed; no new local commit needed.')}</pre><p>Existing outgoing commits:</p><pre>${escapeHTML(JSON.stringify(p.outgoing_commits||[],null,2))}</pre><p>Full PR commit history:</p><pre>${escapeHTML(JSON.stringify(p.pr_commits||[],null,2))}</pre><details><summary>PR description</summary><pre>${escapeHTML(p.body)}</pre></details><p>The source branch will be pushed and a PR created or reused. Merging remains a separate action.</p>`;
    $('publish-confirm').disabled=false;$('publish-dialog').showModal();
  }catch(e){error(e.message);}
}
$('publish-close').onclick=()=>$('publish-dialog').close();
$('publish-confirm').onclick=async()=>{if(!publication)return;$('publish-confirm').disabled=true;try{await api(`tasks/${publication.taskId}/publish`,{preview_id:publication.preview_id,request_id:publication.request_id});$('publish-dialog').close();await refresh();}catch(e){$('publish-confirm').disabled=false;error(e.message);}};
