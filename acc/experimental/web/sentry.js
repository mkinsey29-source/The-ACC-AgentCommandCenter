'use strict';
const byId = id => document.getElementById(id);
let pc, dc, stream, sessionKey, activePath, timer, closeTimer, maximumTimer;
let ready = false, stopping = false, lastUser = 0, inputEnd = 0;
const state = text => { byId('state').textContent = text; };
function path() {
  const ids = ['account', 'project'].map(id => byId(id).value);
  if (ids.some(id => !/^[A-Za-z0-9_-]{1,100}$/.test(id))) throw new Error('Enter valid account and project IDs.');
  return '/experimental/sentry/' + ids.join('/');
}
async function request(action, data, target = path()) {
  const response = await fetch(target + '/' + action, { method: 'POST',
    headers: { Authorization: 'Bearer ' + byId('token').value, 'Content-Type': 'application/json' },
    body: JSON.stringify(data), signal: AbortSignal.timeout(12000), cache: 'no-store' });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || 'Backend unavailable; use text status.');
  return result;
}
function companion(result) {
  byId('companion').textContent = JSON.stringify(result, null, 2);
  byId('links').replaceChildren();
  for (const evidence of result.evidence || []) {
    const url = new URL(evidence.ref, location.origin);
    if (url.origin !== location.origin) continue;
    const a = document.createElement('a'); a.href = url.href; a.textContent = 'Evidence · version ' + evidence.version;
    a.rel = 'noopener'; byId('links').append(a);
  }
}
function cleanup() {
  clearInterval(timer); clearTimeout(closeTimer); clearTimeout(maximumTimer);
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (dc) dc.close(); if (pc) pc.close();
  pc = dc = stream = undefined; ready = false;
  for (const id of ['account', 'project', 'token', 'start']) byId(id).disabled = false;
}
async function stop() {
  if (stopping) return; stopping = true;
  if (ready && dc?.readyState === 'open') dc.send(JSON.stringify({ type: 'session.close' }));
  state('Closing voice; final usage remains unconfirmed until session.closed.');
  closeTimer = setTimeout(cleanup, 6000);
  if (sessionKey) {
    try {
      const result = await request('close', { session_key: sessionKey }, activePath);
      state(result.close_confirmed ? 'Voice closed. Text status is available.' : 'Close unconfirmed; do not restart voice. Check the backend.');
      if (result.close_confirmed) sessionKey = undefined;
    } catch { state('Voice disconnected; close/usage unconfirmed. Text status is available.'); }
  }
  cleanup(); stopping = false;
}
byId('lookup').onclick = async () => {
  try { companion(await request('lookup', { intent: byId('intent').value })); state('Read-only text status updated.'); }
  catch (error) { state(error.message); }
};
byId('start').onclick = async () => {
  if (pc || sessionKey) return;
  try {
    activePath = path(); byId('start').disabled = true;
    for (const id of ['account', 'project', 'token']) byId(id).disabled = true;
    state('Connecting voice…'); stopping = false; inputEnd = 0; lastUser = Date.now();
    pc = new RTCPeerConnection();
    pc.ontrack = event => { byId('audio').srcObject = event.streams[0]; };
    pc.onconnectionstatechange = () => {
      if (['failed', 'disconnected', 'closed'].includes(pc?.connectionState) && !stopping) void stop();
    };
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach(track => pc.addTrack(track, stream));
    dc = pc.createDataChannel('oai-events');
    dc.onmessage = event => {
      let message; try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === 'session.started') { ready = true; lastUser = Date.now(); state('Voice connected. Try “project status” or “explain blockers”.'); }
      if (message.type === 'session.input_transcript.delta') {
        lastUser = Date.now(); inputEnd = Math.max(inputEnd, message.end_ms || 0); byId('audio').muted = true;
        byId('transcript').textContent = (byId('transcript').textContent + message.delta).slice(-4000);
      }
      if (message.type === 'session.closed') { state('Voice closed; final usage received.'); void stop(); }
      if (message.type === 'error') { state('Voice error; use text status.'); void stop(); }
    };
    await pc.setLocalDescription(await pc.createOffer());
    if (pc.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('ICE timeout')), 10000);
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc?.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
      });
    });
    const created = await request('session', { sdp: pc.localDescription.sdp }, activePath);
    sessionKey = created.session_key;
    await pc.setRemoteDescription({ type: 'answer', sdp: created.transport.sdp });
    maximumTimer = setTimeout(() => void stop(), created.max_seconds * 1000);
    let companionVersion = '';
    timer = setInterval(async () => {
      if (Date.now() - lastUser > created.idle_seconds * 1000) { void stop(); return; }
      try {
        const result = await request('status', { session_key: sessionKey }, activePath);
        if (result.companion && result.companion.input_end_ms >= inputEnd && companionVersion !== result.companion.as_of) {
          companionVersion = result.companion.as_of; companion(result.companion); byId('audio').muted = false;
        }
        if (result.state !== 'running') void stop();
      } catch { void stop(); }
    }, 1000);
  } catch (error) { state(error.message); if (sessionKey) await stop(); else cleanup(); }
};
byId('stop').onclick = () => void stop();
document.addEventListener('visibilitychange', () => { if (document.hidden && pc) void stop(); });
window.addEventListener('pagehide', cleanup); // backend watchdog closes even after a lost browser
