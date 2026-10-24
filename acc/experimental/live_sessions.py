"""Opt-in WebRTC session broker with a server-owned sideband and idle watchdog.

The sideband, not browser-supplied events, owns delegated lookups and session close.
No application task is created or resumed. All session state is transient.
"""
from __future__ import annotations
import asyncio
from copy import deepcopy
import json
import os
import re
import secrets

from ..platform.api import ApiError
from .sentry import LIVE_CONFIG, LiveConversation


class LiveSessions:
    def __init__(self, reads, http=None, *, enabled=False, idle_seconds=45, max_seconds=180, connector=None):
        if type(enabled) is not bool or not 0 < idle_seconds <= 60 or not 0 < max_seconds <= 180:
            raise ValueError('bounded, explicit voice configuration required')
        self.reads, self.http, self.enabled = reads, http, enabled
        self.idle_seconds, self.max_seconds = idle_seconds, max_seconds
        self.connector = connector
        self.sessions = {}
        self.lock = asyncio.Lock()

    def _owner(self, authorization, account, project):
        _, _, ctx = self.reads.api._authorize(authorization, account, permissions=('project.read', 'task.read'))
        self.reads.api.project_state(authorization, account, project)
        return (ctx.session.user_id, account, project)

    async def open(self, authorization, account, project, sdp):
        owner = self._owner(authorization, account, project)
        if not self.enabled or self.http is None:
            raise ApiError(503, 'voice_off', 'Voice is off. Text status remains available.')
        if not isinstance(sdp, str) or not 1 <= len(sdp) <= 60_000:
            raise ApiError(400, 'invalid_sdp', 'Invalid SDP offer.')
        # Verify optional websocket dependency BEFORE incurring initialization charges.
        connector = self.connector
        if connector is None:
            from websockets.asyncio.client import connect
            connector = connect
        async with self.lock:
            if self.sessions:  # small prototype: one session globally, including connecting/closing
                raise ApiError(409, 'session_busy', 'Close the current prototype session first.')
            key = secrets.token_urlsafe(24)
            entry = {'owner': owner, 'state': 'connecting', 'companion': None, 'close_confirmed': False,
                     'final_usage': None, 'measured_cost_usd': None}
            self.sessions[key] = entry
            try:
                result = await asyncio.wait_for(self.http.post('/v1/live/sessions', {
                    'session': deepcopy(LIVE_CONFIG), 'transport': {'type': 'webrtc', 'sdp': sdp}}, 10), 11)
                sid, answer = result['session']['id'], result['transport']['sdp']
                if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', sid) or not isinstance(answer, str) or len(answer) > 100_000:
                    raise ValueError('invalid Live response')
                entry['controller'] = LiveConversation(self.reads, authorization, account, project,
                    idle_seconds=self.idle_seconds, max_seconds=self.max_seconds)
                entry['id'] = sid
                entry['attached'] = asyncio.Event()
                entry['task'] = asyncio.create_task(self._run(entry, connector))
                await asyncio.wait_for(entry['attached'].wait(), 10)
                if entry['state'] != 'running':
                    raise ValueError('sideband unavailable')
                return {'session_key': key, 'session': {'id': sid},
                        'transport': {'type': 'webrtc', 'sdp': answer}, 'max_seconds': self.max_seconds,
                        'idle_seconds': self.idle_seconds}
            except BaseException:
                entry['state'] = 'unconfirmed_close'
                if entry.get('controller'):
                    entry['controller'].closed = True
                # Unknown create/attach outcomes stay held. Never blindly retry a billable create.
                raise

    async def _run(self, entry, connector):
        pending = set()
        closed = asyncio.Event()
        try:
            headers = {'Authorization': 'Bearer ' + os.environ.get('OPENAI_API_KEY', ''),
                       'OpenAI-Organization': self.http.permit.organization_id,
                       'OpenAI-Project': self.http.permit.project_id}
            async with connector('wss://api.openai.com/v1/live/sessions/' + entry['id'] + '/attach',
                                 additional_headers=headers, open_timeout=10, max_size=1_000_000) as ws:
                entry['controller'].started_event = True  # attached sideband is already running
                entry['state'] = 'running'
                entry['attached'].set()
                async def delegate(event):
                    result = await entry['controller'].event(event)
                    if result and not entry['controller'].expired():
                        # Revalidate live auth and current revision before returning any result.
                        self._owner(entry['controller'].authorization, *entry['owner'][1:])
                        entry['companion'] = {**result['companion'], 'input_end_ms': result['input_end_ms']}
                        wire = {k: v for k, v in result.items() if k not in ('companion', 'request_revision', 'input_end_ms')}
                        wire['event_id'] = 'sentry_' + secrets.token_hex(8)
                        await ws.send(json.dumps(wire))
                async def read():
                    async for message in ws:
                        event = json.loads(message)
                        if event.get('type') == 'session.closed':
                            entry['close_confirmed'] = True
                            # Only bounded numeric accounting fields, never transcript/raw event content.
                            usage = event.get('usage', {})
                            entry['final_usage'] = {k: v for k, v in usage.items()
                                if k in ('input_tokens', 'output_tokens', 'total_tokens', 'duration_seconds', 'duration_ms')
                                and type(v) in (int, float) and 0 <= v < 10**12}
                            entry['state'] = 'closed'
                            closed.set()
                            return
                        if event.get('type') == 'session.delegation.created':
                            task = asyncio.create_task(delegate(event))
                            pending.add(task)
                            def finished(t):
                                pending.discard(t)
                                if not t.cancelled() and t.exception() is not None:
                                    entry['controller'].closed = True
                            task.add_done_callback(finished)
                        elif event.get('type') == 'session.input_transcript.delta':
                            await entry['controller'].event(event)
                            entry['companion'] = None
                reader = asyncio.create_task(read())
                try:
                    while not reader.done() and not entry['controller'].expired() and self.enabled:
                        # Auth revocation cannot leave an idle paid session running.
                        self._owner(entry['controller'].authorization, *entry['owner'][1:])
                        await asyncio.sleep(.25)
                finally:
                    entry['controller'].disconnect()
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await ws.send(json.dumps({'type': 'session.close'}))
                    try:
                        await asyncio.wait_for(closed.wait(), 5)
                    except asyncio.TimeoutError:
                        entry['state'] = 'unconfirmed_close'
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
        except Exception:
            if not entry['close_confirmed']:
                entry['state'] = 'unconfirmed_close'
        finally:
            if entry.get('controller'):
                entry['controller'].disconnect()
            entry['attached'].set()

    def status(self, authorization, account, project, key):
        owner = self._owner(authorization, account, project)
        entry = self.sessions.get(key)
        if not entry or entry['owner'] != owner:
            raise ApiError(404, 'not_found', 'Session not found.')
        return {k: entry[k] for k in ('state', 'companion', 'close_confirmed', 'final_usage', 'measured_cost_usd')}

    async def close(self, authorization, account, project, key):
        self.status(authorization, account, project, key)
        entry = self.sessions[key]
        if entry.get('controller'):
            entry['controller'].closed = True
        if entry.get('task'):
            await asyncio.wait_for(asyncio.shield(entry['task']), 7)
        if entry['close_confirmed']:
            del self.sessions[key]
        return {'state': entry['state'], 'close_confirmed': entry['close_confirmed']}

    async def shutdown(self):
        self.enabled = False
        for entry in self.sessions.values():
            if entry.get('controller'):
                entry['controller'].closed = True
        tasks = [e['task'] for e in self.sessions.values() if e.get('task')]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 7)
