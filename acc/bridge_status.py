"""Durable, privacy-preserving evidence for the external MCP bridge."""
import json
import re
from .core import now


class BridgeStatus:
    """Record connection milestones without retaining tool arguments or prompts."""

    FLOW = (
        'acc_orchestrator_select',
        'acc_conversation_claim',
        'acc_conversation_send',
        'acc_conversation_renew',
        'acc_conversation_complete',
        'acc_conversation_read',
    )

    def __init__(self, coordinator):
        self.c = coordinator

    def _saved(self):
        with self.c.store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='bridge_status'").fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self):
        saved = self._saved()
        if not saved:
            return {'connected': False, 'tools_discovered': False,
                    'conversation_steps': [], 'conversation_verified_at': None}
        return {key: saved.get(key) for key in (
            'connected', 'bridge_id', 'client', 'connected_at', 'last_seen',
            'tools_discovered', 'tools_discovered_at', 'last_tool', 'last_tool_at',
            'conversation_steps', 'conversation_verified_at')}

    def observe(self, payload):
        bridge_id = payload.get('bridge_id')
        phase = payload.get('phase')
        if (not isinstance(bridge_id, str) or not 1 <= len(bridge_id) <= 100
                or not re.fullmatch(r'[A-Za-z0-9._-]+', bridge_id)):
            raise ValueError('Invalid bridge identifier.')
        if phase not in ('initialize', 'tools_list', 'tool_call'):
            raise ValueError('Invalid bridge observation.')
        client = payload.get('client') or {}
        if (not isinstance(client, dict) or set(client) - {'name', 'version'}
                or any(not isinstance(value, str) or len(value) > 100
                       for value in client.values())):
            raise ValueError('Invalid MCP client metadata.')
        tool = payload.get('tool')
        if phase == 'tool_call' and (not isinstance(tool, str)
                                     or not re.fullmatch(r'acc_[a-z0-9_]+', tool)):
            raise ValueError('Invalid bridge tool observation.')
        stamp = now()
        with self.c.lock, self.c.store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='bridge_status'").fetchone()
            saved = json.loads(row[0]) if row else None
            new_bridge = not saved or saved.get('bridge_id') != bridge_id
            if new_bridge:
                last_verified = saved.get('conversation_verified_at') if saved else None
                saved = {'connected': True, 'bridge_id': bridge_id, 'client': {},
                         'connected_at': stamp, 'last_seen': stamp,
                         'tools_discovered': False, 'tools_discovered_at': None,
                         'last_tool': None, 'last_tool_at': None,
                         'conversation_steps': [], 'conversation_verified_at': last_verified}
            saved['connected'] = True
            saved['last_seen'] = stamp
            if client:
                saved['client'] = client
            discovered_now = phase == 'tools_list' and not saved['tools_discovered']
            previous_steps = tuple(saved['conversation_steps'])
            if phase == 'tools_list':
                saved['tools_discovered'] = True
                saved['tools_discovered_at'] = stamp
            if phase == 'tool_call':
                saved['last_tool'] = tool
                saved['last_tool_at'] = stamp
                steps = saved['conversation_steps']
                if tool == self.FLOW[0]:
                    steps = [tool] if payload.get('remote_session') is True else []
                elif len(steps) < len(self.FLOW) and tool == self.FLOW[len(steps)]:
                    steps = steps + [tool]
                saved['conversation_steps'] = steps
                if tuple(steps) == self.FLOW and previous_steps != self.FLOW:
                    saved['conversation_verified_at'] = stamp
            db.execute("INSERT OR REPLACE INTO meta VALUES ('bridge_status',?)",
                       (json.dumps(saved),))
            progress_changed = phase == 'tool_call' and tuple(saved['conversation_steps']) != previous_steps
            if new_bridge or discovered_now or progress_changed:
                db.execute('INSERT INTO events(at,kind,data) VALUES (?,?,?)',
                           (stamp, 'bridge_observed', json.dumps({
                               'message': ('External orchestrator bridge completed a verified conversation turn.'
                                           if saved['conversation_verified_at'] == stamp else
                                           'External orchestrator bridge validation advanced.'),
                               'phase': phase, 'tool': tool})))
        return self.snapshot()
