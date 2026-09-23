"""Persistent orchestrator-session selection and fenced conversation handoff."""
from .core import Conflict, now


class OrchestratorSessions:
    """Own the seam between ChatGPT Remote and ACC-hosted model sessions."""

    EXTERNAL = 'chatgpt-remote'
    AUTO = 'auto'

    def __init__(self, coordinator, defaults=None):
        self.c = coordinator
        saved = self._meta('orchestrator_sessions')
        initial = (defaults or {}).get('selected', self.AUTO)
        self.data = saved or {'selected': initial, 'pending': None, 'handoff': None,
                              'generation': 0, 'automatic_decision': None}
        self.data.setdefault('pending', None)
        self.data.setdefault('automatic_decision', None)
        self._validate(self.data['selected'])
        if not saved:
            self._save()

    def _meta(self, key, default=None):
        with self.c.store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if not row:
            return default
        import json
        return json.loads(row[0])

    def _save(self, db=None):
        import json
        if db is not None:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                       ('orchestrator_sessions', json.dumps(self.data)))
            return
        with self.c.store.connect() as connection:
            self._save(connection)

    def _validate(self, session_id):
        if session_id in (self.AUTO, self.EXTERNAL):
            return
        if not isinstance(session_id, str) or not session_id.startswith('agent:'):
            raise ValueError('Choose auto, chatgpt-remote, or agent:<configured-id>.')
        agent_id = session_id.removeprefix('agent:')
        agent = self.c.agents.get(agent_id)
        roles = (agent or {}).get('routing', {}).get('roles', ('implementer', 'reviewer', 'coordinator'))
        if not agent or agent.get('kind') != 'model' or 'coordinator' not in roles:
            raise ValueError('Direct orchestrator sessions require a model adapter with the coordinator role.')

    @staticmethod
    def _lease_session(lease):
        if not lease:
            return None
        if lease['kind'] == 'external':
            return lease.get('session_id', OrchestratorSessions.EXTERNAL)
        return 'agent:' + lease['owner']

    def select(self, payload):
        session_id = payload.get('session_id')
        self._validate(session_id)
        with self.c.lock:
            lease = self.c.conversation._expire()
            previous = self._lease_session(lease) or self.data['selected']
            if session_id == self.data['selected'] and not self.data.get('pending'):
                return self.snapshot()
            if lease and session_id == previous and not self.data.get('pending'):
                with self.c.store.connect() as db:
                    self.data.update(selected=session_id,
                                     generation=self.data.get('generation', 0) + 1)
                    self._save(db)
                    self.c.conversation.event(db, 'orchestrator_session_pinned', {
                        'message': f'{session_id} remains active and is now the selected session.',
                        'to': session_id})
                return self.snapshot()
            if lease and lease['kind'] == 'local':
                with self.c.store.connect() as db:
                    pending = [row[0] for row in db.execute(
                        "SELECT id FROM messages WHERE status='pending' ORDER BY seq")]
                    self.data.update(
                        pending=session_id,
                        handoff={'from': previous, 'to': session_id, 'at': now(),
                                 'pending_message_ids': pending})
                    self._save(db)
                    self.c.conversation.event(db, 'orchestrator_switch_queued', {
                        'message': f'Orchestrator switch to {session_id} will apply after the current decision.',
                        'from': previous, 'to': session_id})
                return self.snapshot()
            with self.c.store.connect() as db:
                pending = [row[0] for row in db.execute(
                    "SELECT id FROM messages WHERE status='pending' ORDER BY seq")]
                if lease and lease['kind'] == 'external':
                    self.c.conversation.put(db, 'conversation_lease', None)
                self.data = {
                    'selected': session_id,
                    'pending': None,
                    'generation': self.data.get('generation', 0) + 1,
                    'automatic_decision': self.data.get('automatic_decision'),
                    'handoff': {'from': previous, 'to': session_id, 'at': now(),
                                'pending_message_ids': pending},
                }
                self._save(db)
                self.c.conversation.event(db, 'orchestrator_session_switched', {
                    'message': f'Orchestrator session switched from {previous} to {session_id}.',
                    'from': previous, 'to': session_id})
            return self.snapshot()

    def complete_pending(self, db):
        target = self.data.get('pending')
        if not target:
            return False
        previous = self.data['selected']
        self.data.update(selected=target, pending=None,
                         generation=self.data.get('generation', 0) + 1)
        self._save(db)
        self.c.conversation.event(db, 'orchestrator_session_switched', {
            'message': f'Orchestrator session switched from {previous} to {target}.',
            'from': previous, 'to': target})
        return True

    def authorize_external(self, session_id):
        session_id = session_id or self.EXTERNAL
        if session_id != self.EXTERNAL:
            raise ValueError('Only chatgpt-remote is currently an external orchestrator session.')
        selected = self.data['selected']
        if selected not in (self.AUTO, self.EXTERNAL):
            raise Conflict('The selected orchestrator is ' + selected + '. Switch sessions before claiming.')
        return session_id

    def chosen_agent(self, settings, offline=False, pending_messages=()):
        selected = self.data['selected']
        if selected.startswith('agent:'):
            return selected.removeprefix('agent:')
        if selected == self.EXTERNAL:
            return settings.get('local_agent') if offline else settings.get('preferred_agent') or settings.get('local_agent')
        if self.c.router.enabled and pending_messages:
            message_ids = [item['id'] for item in pending_messages]
            cached = self.data.get('automatic_decision') or {}
            candidate = cached.get('selected')
            if (cached.get('message_ids') == message_ids and cached.get('offline') == offline and
                    candidate in self.c.agents and self.c.agents[candidate].get('available')):
                return candidate
            try:
                candidate, decision = self.c.router.select_orchestrator(
                    '\n\n'.join(item['text'] for item in pending_messages),
                    preferred=settings.get('preferred_agent'), force_offline=offline)
                self.data['automatic_decision'] = {
                    **decision, 'message_ids': message_ids, 'offline': offline}
                self._save()
                return candidate
            except ValueError:
                pass
        return (settings.get('local_agent') if offline else
                settings.get('preferred_agent') or settings.get('local_agent'))

    def snapshot(self):
        sessions = [
            {'id': self.AUTO, 'name': 'Automatic', 'kind': 'automatic', 'available': True},
            {'id': self.EXTERNAL, 'name': 'ChatGPT Remote', 'kind': 'external', 'available': True},
        ]
        sessions.extend({'id': 'agent:' + agent['id'], 'name': agent['name'] + ' Direct',
                         'kind': 'direct', 'agent': agent['id'],
                         'available': bool(agent.get('available'))}
                        for agent in self.c.agents.values()
                        if agent.get('kind') == 'model' and
                        'coordinator' in agent.get('routing', {}).get(
                            'roles', ('implementer', 'reviewer', 'coordinator')))
        lease = self.c.conversation._expire() if hasattr(self.c, 'conversation') else None
        return {**self.data, 'active': self._lease_session(lease), 'sessions': sessions}
