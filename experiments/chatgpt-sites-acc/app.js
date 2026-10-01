'use strict';

const $ = (id) => document.getElementById(id);

const WORKSPACES = {
  command: {title: 'Command Center', left: 'Global overview', views: ['Projects', 'Active work', 'Waiting', 'Recent completions']},
  coding: {title: 'Coding', left: 'Code controls', views: ['Agent View', 'Task View', 'Branches', 'Build/Test']},
  business: {title: 'Business Operations', left: 'Business controls', views: ['Plans', 'Operations', 'KPIs', 'Decisions']},
  communications: {title: 'Communications', left: 'Accounts & inboxes', views: ['Inbox', 'Threads', 'Drafts', 'Follow-ups']},
  meetings: {title: 'Meetings', left: 'Calendar controls', views: ['Calendar', 'Prep', 'Agenda', 'Actions']},
  research: {title: 'Research', left: 'Research controls', views: ['Questions', 'Sources', 'Findings', 'Report']},
  documents: {title: 'Documents & Data', left: 'Artifact controls', views: ['Documents', 'Sheets', 'Presentations', 'Datasets']},
  creative: {title: 'Creative', left: 'Creative controls', views: ['Brief', 'Assets', 'Previews', 'Versions']},
  game: {title: 'Game / 3D', left: 'Engine controls', views: ['Assets', 'Blender', 'Unity / Unreal', 'Builds']}
};

const AGENTS = ['Codex', 'Claude', 'DeepSeek', 'Gemini', 'Local Reviewer', 'Research Worker'];
const STATUSES = ['working', 'queued', 'review', 'blocked', 'accepted'];
let activeWorkspace = 'command';
let liveMode = false;
let socket = null;
let demoTimer = null;
let renderSamples = [];
let sequence = 5000;

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
}

function seededTasks(count = 96) {
  const names = [
    'Platform API contract', 'Review evidence model', 'Drive knowledge adapter', 'Workspace shell',
    'Plugin auth path', 'Unity connector', 'Research brief', 'Business KPI pass'
  ];
  const areas = Object.keys(WORKSPACES);
  return Array.from({length: count}, (_, index) => ({
    id: 'task-' + (index + 1),
    number: index + 1,
    title: names[index % names.length] + ' ' + (Math.floor(index / names.length) + 1),
    status: STATUSES[index % STATUSES.length],
    agent: AGENTS[index % AGENTS.length],
    area: areas[index % areas.length],
    priority: 100 - (index % 70)
  }));
}

const state = {
  project: 'ACC Platform',
  tasks: seededTasks(96),
  workers: AGENTS.map((name, index) => ({
    name,
    enabled: index !== 5,
    configured: true,
    healthy: index !== 4,
    busy: index < 3,
    cost: [0.18, 0.33, 0.09, 0.12, 0, 0.07][index]
  })),
  attention: [
    {level: 'warn', text: 'M09 hosted API is not built; live transport remains a staging seam.'},
    {level: 'bad', text: 'Local node offline — Unity and Blender lanes unavailable.'},
    {level: 'warn', text: '2 reviews waiting for an independent top-level reviewer.'}
  ],
  events: [],
  chat: [
    {role: 'assistant', text: 'ACC shell feasibility mode is running with generated state.'},
    {role: 'user', text: 'Show me what needs attention before the next release.'}
  ]
};

function addEvent(kind, message) {
  state.events.push({seq: ++sequence, at: Date.now(), kind, message});
  if (state.events.length > 500) state.events.splice(0, state.events.length - 500);
}

for (let index = 0; index < 36; index += 1) {
  const task = state.tasks[index % state.tasks.length];
  addEvent(['worker', 'task', 'review', 'connector'][index % 4],
    'Simulated event ' + (index + 1) + ': ' + task.title);
}

function statusClass(status) {
  if (status === 'accepted') return 'ok';
  if (status === 'blocked') return 'bad';
  if (status === 'review') return 'warn';
  return '';
}

function renderWorkspaceButtons() {
  $('workspace-tabs').innerHTML = Object.entries(WORKSPACES).map(([id, workspace]) =>
    '<button data-workspace="' + id + '" class="' + (id === activeWorkspace ? 'selected' : '') + '">' +
    escapeHTML(workspace.title) + '</button>'
  ).join('');
}

function renderMasterControls() {
  const controls = [
    ['Internet / cloud', true], ['New delegation', true], ['Codex worker', true],
    ['External providers', true], ['Local execution', false], ['GitHub publication', true],
    ['Email send actions', false], ['Automatic failover', true]
  ];
  $('master-controls').innerHTML = controls.map(([name, on]) =>
    '<div class="control"><span>' + escapeHTML(name) + '</span><span class="switch ' +
    (on ? 'on' : 'off') + '" aria-label="' + (on ? 'on' : 'off') + '"></span></div>'
  ).join('');
  $('system-count').textContent = controls.filter((entry) => entry[1]).length + ' ON';
}

function renderLeftRail() {
  const workspace = WORKSPACES[activeWorkspace];
  $('left-title').textContent = workspace.left;
  const choices = {
    command: ['Projects · 5 active', 'Workers · 3 busy', 'Reviews · 2 waiting', 'Cloud mode · online'],
    coding: ['Branch policy · isolated', 'Build lane · available', 'GitHub · connected', 'Reviewer · required'],
    business: ['KPI refresh · hourly', 'Approvals · 1 waiting', 'QuickBooks · optional', 'Drive · connected'],
    communications: ['Gmail · optional', 'Outlook · disabled', 'Outbound · approval', 'Follow-ups · 4'],
    meetings: ['Calendar · connected', 'Conflicts · 1', 'Prep agent · ready', 'Notes · auto-draft'],
    research: ['Depth · standard', 'Web · enabled', 'Evidence · required', 'Sources · 18'],
    documents: ['Drive · connected', 'Artifact store · ready', 'Exports · 6', 'Data checks · enabled'],
    creative: ['Asset store · ready', 'Preview lane · active', 'Versions · 12', 'Publish · approval'],
    game: ['Desktop node · offline', 'Blender · unavailable', 'Unity · unavailable', 'Lease · none']
  }[activeWorkspace];
  $('left-context').innerHTML = choices.map((value, index) =>
    '<div class="context-chip">' + escapeHTML(value) + '<small>' +
    (index === 0 ? 'workspace scoped' : 'ACC managed') + '</small></div>'
  ).join('');
}

function relevantTasks() {
  return activeWorkspace === 'command' ? state.tasks : state.tasks.filter((task) => task.area === activeWorkspace);
}

function renderCenter() {
  const started = performance.now();
  const workspace = WORKSPACES[activeWorkspace];
  const tasks = relevantTasks();
  $('workspace-title').textContent = workspace.title;
  $('workspace-kpi').textContent = tasks.length + ' relevant tasks';

  const cards = workspace.views.map((view, index) => {
    if (index === 1) {
      const rows = tasks.slice(0, 24).map((task) =>
        '<div class="task-row"><code>#' + task.number + '</code><span>' + escapeHTML(task.title) +
        '</span><span class="status ' + statusClass(task.status) + '">' + escapeHTML(task.status) +
        '</span><span>' + escapeHTML(task.agent) + '</span></div>'
      ).join('');
      return '<article class="card span-8"><h2>' + escapeHTML(view.toUpperCase()) +
        '</h2><div class="body task-table">' + rows + '</div></article>';
    }

    const metrics = [tasks.length, state.workers.filter((worker) => worker.busy).length,
      state.attention.length, state.events.length];
    const items = tasks.slice(index * 3, index * 3 + 3).map((task) =>
      '<div class="item"><div class="row"><span>#' + task.number + ' ' + escapeHTML(task.title) +
      '</span><span class="' + statusClass(task.status) + '">' + escapeHTML(task.status) +
      '</span></div><small>' + escapeHTML(task.agent) + ' · p' + task.priority + '</small></div>'
    ).join('');
    return '<article class="card span-4"><h2>' + escapeHTML(view.toUpperCase()) +
      '</h2><div class="body"><div class="metric">' + metrics[index % metrics.length] +
      '<small> current</small></div><div class="list">' + items + '</div></div></article>';
  });

  cards.push('<article class="card span-12"><h2>WORKSPACE CONTRACT</h2><div class="body"><div class="row">' +
    '<span>Center changes by work type; Master Control, Attention, Orchestrator Chat, and Play-by-Play remain global.</span>' +
    '<span class="ok">Sites shell candidate</span></div></div></article>');

  $('workspace-center').innerHTML = cards.join('');
  renderSamples.push(performance.now() - started);
  if (renderSamples.length > 120) renderSamples.shift();
}

function renderRightRail() {
  $('attention-count').textContent = String(state.attention.length);
  $('attention-list').innerHTML = state.attention.map((item) =>
    '<div class="item"><div class="row"><span>' + escapeHTML(item.text) +
    '</span><span class="' + item.level + '">●</span></div></div>'
  ).join('');

  $('worker-list').innerHTML = state.workers.map((worker) =>
    '<div class="item"><div class="row"><strong>' + escapeHTML(worker.name) +
    '</strong><span class="' + (worker.healthy ? 'ok' : 'warn') + '">' +
    (worker.healthy ? 'healthy' : 'warning') + '</span></div><small>Configured: ' +
    (worker.configured ? 'yes' : 'no') + ' · Enabled: ' + (worker.enabled ? 'yes' : 'no') +
    ' · Busy: ' + (worker.busy ? 'yes' : 'no') + ' · $' + worker.cost.toFixed(2) + '</small></div>'
  ).join('');
}

function renderBottom() {
  $('event-count').textContent = state.events.length + ' events';
  $('activity-log').innerHTML = [...state.events].reverse().slice(0, 90).map((item) =>
    '<div class="activity"><time>' + new Date(item.at).toLocaleTimeString() + '</time><span>' +
    escapeHTML(item.kind) + '</span><span>' + escapeHTML(item.message) + '</span></div>'
  ).join('');
  $('chat-log').innerHTML = state.chat.map((message) =>
    '<div class="chat ' + message.role + '"><small>' +
    (message.role === 'user' ? 'You' : 'ACC Orchestrator') + '</small><div>' +
    escapeHTML(message.text) + '</div></div>'
  ).join('');
  $('chat-log').scrollTop = $('chat-log').scrollHeight;
}

function render() {
  renderWorkspaceButtons();
  renderMasterControls();
  renderLeftRail();
  renderCenter();
  renderRightRail();
  renderBottom();
  $('project-label').textContent = 'PROJECT · ' + state.project.toUpperCase();
}

function demoTick() {
  const task = state.tasks[Math.floor(Math.random() * state.tasks.length)];
  addEvent('worker', task.agent + ' · ' + task.title + ' · ' + task.status);
  renderBottom();
}

function startDemoTimer() {
  clearInterval(demoTimer);
  demoTimer = setInterval(demoTick, 1100);
}

async function connectLive(apiBase, wsUrl, token) {
  if (socket) {
    liveMode = false;
    socket.close();
    socket = null;
  }
  const result = $('connection-result');
  const base = apiBase.replace(/\/$/, '');
  result.textContent = 'Checking HTTPS state endpoint…';
  const headers = token ? {Authorization: 'Bearer ' + token} : {};

  const response = await fetch(base + '/api/state', {headers});
  if (!response.ok) throw new Error('State endpoint returned HTTP ' + response.status);
  const incoming = await response.json();
  if (!incoming || !Array.isArray(incoming.tasks)) throw new Error('State response is missing tasks[]');

  result.textContent = 'HTTPS state: OK\nRequesting one-time WebSocket ticket…';
  const ticketResponse = await fetch(base + '/api/ws-ticket', {
    method: 'POST',
    headers: {...headers, 'Content-Type': 'application/json'}
  });
  if (!ticketResponse.ok) throw new Error('WebSocket ticket endpoint returned HTTP ' + ticketResponse.status);
  const ticket = await ticketResponse.json();
  if (!ticket.ticket) throw new Error('WebSocket ticket response is missing ticket');

  state.project = incoming.project || 'ACC';
  state.tasks = incoming.tasks.map((task, index) => ({
    id: task.id || 'live-' + index,
    number: task.task_number || index + 1,
    title: task.title || 'Untitled',
    status: task.status || 'queued',
    agent: task.active_agent || task.agent || 'Unknown',
    area: String(task.task_area || 'general').split('.')[0],
    priority: task.priority ?? 50
  }));

  let socketUrl = wsUrl.trim();
  if (!socketUrl) {
    const derived = new URL(base);
    derived.protocol = derived.protocol === 'https:' ? 'wss:' : 'ws:';
    derived.pathname = ticket.path || '/events';
    derived.search = '';
    derived.hash = '';
    socketUrl = derived.toString();
  }
  const authenticatedSocketUrl = new URL(socketUrl);
  authenticatedSocketUrl.searchParams.set('ticket', ticket.ticket);

  clearInterval(demoTimer);
  liveMode = true;
  $('connection-badge').className = 'badge live';
  $('connection-badge').textContent = 'LIVE';
  socket = new WebSocket(authenticatedSocketUrl.toString());
  socket.onopen = () => {
    result.textContent = 'HTTPS state: OK\nWebSocket ticket: OK\nWebSocket: OPEN';
    addEvent('transport', 'Live WebSocket connected with one-time ticket.');
    render();
  };
  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      addEvent(message.kind || 'event', message.message || message.data?.message || 'Live event received.');
    } catch (_) {
      addEvent('event', String(event.data).slice(0, 180));
    }
    renderBottom();
  };
  socket.onerror = () => {
    result.textContent += '\nWebSocket: ERROR';
    addEvent('transport', 'WebSocket error — HTTPS state remains loaded.');
    renderBottom();
  };
  socket.onclose = () => {
    if (liveMode) {
      result.textContent += '\nWebSocket: CLOSED';
      addEvent('transport', 'WebSocket closed.');
      renderBottom();
    }
  };
  render();
}

function useDemoMode() {
  liveMode = false;
  if (socket) {
    socket.close();
    socket = null;
  }
  $('connection-badge').className = 'badge demo';
  $('connection-badge').textContent = 'DEMO';
  $('connection-result').textContent = 'Demo mode uses generated ACC-shaped state.';
  startDemoTimer();
  render();
}

$('workspace-tabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-workspace]');
  if (!button) return;
  activeWorkspace = button.dataset.workspace;
  render();
});

$('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const input = $('chat-input');
  if (!input.value.trim()) return;
  state.chat.push({role: 'user', text: input.value.trim()});
  state.chat.push({role: 'assistant', text: 'Feasibility shell only: command captured, no production orchestration executed.'});
  addEvent('chat', 'Orchestrator message captured in feasibility mode.');
  input.value = '';
  renderBottom();
});

$('connection-button').onclick = () => { $('connection-panel').hidden = false; };
$('connection-close').onclick = () => { $('connection-panel').hidden = true; };
$('demo-mode').onclick = () => { useDemoMode(); $('connection-panel').hidden = true; };

$('connection-form').onsubmit = async (event) => {
  event.preventDefault();
  const apiBase = $('api-base').value.trim();
  const wsUrl = $('ws-url').value.trim();
  const token = $('access-token').value;
  sessionStorage.setItem('acc-sites-token', token);
  localStorage.setItem('acc-sites-api', apiBase);
  localStorage.setItem('acc-sites-ws', wsUrl);
  try {
    await connectLive(apiBase, wsUrl, token);
    $('connection-panel').hidden = true;
  } catch (error) {
    $('connection-result').textContent = 'Live connection failed:\n' + error.message + '\n\nDemo mode remains available.';
  }
};

$('api-base').value = localStorage.getItem('acc-sites-api') || '';
$('ws-url').value = localStorage.getItem('acc-sites-ws') || '';
$('access-token').value = sessionStorage.getItem('acc-sites-token') || '';

$('stress-button').onclick = async () => {
  const button = $('stress-button');
  button.disabled = true;
  const before = performance.now();
  renderSamples = [];
  for (let chunk = 0; chunk < 20; chunk += 1) {
    for (let index = 0; index < 100; index += 1) {
      addEvent('stress', 'Stress event ' + (chunk * 100 + index + 1));
    }
    renderCenter();
    renderBottom();
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  const total = performance.now() - before;
  const average = renderSamples.reduce((sum, value) => sum + value, 0) / Math.max(1, renderSamples.length);
  const pass = average < 50 && total < 3000;
  $('perf-result').textContent = (pass ? 'PASS' : 'CHECK') + ' · 2,000 events · avg center render ' +
    average.toFixed(1) + ' ms · total ' + total.toFixed(0) + ' ms';
  button.disabled = false;
};

render();
startDemoTimer();
