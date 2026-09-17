"""Durable conversation inbox and fenced, transactional orchestration turns."""
import json
from pathlib import Path
from .core import Conflict, identifier, now
from .snapshots import inventory


class Conversation:
    def __init__(self, coordinator, defaults=None, transcription=None):
        self.c = coordinator
        self.transcription = transcription
        with self.c.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_receipts (
                token TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
            ''')
        self.settings = self.meta('conversation_settings', {})
        if not self.settings and defaults:
            self.configure(defaults)

    def meta(self, key, default=None):
        with self.c.store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, key, value):
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    @staticmethod
    def event(db, kind, details):
        db.execute('INSERT INTO events(at,kind,data) VALUES (?,?,?)', (now(), kind, json.dumps(details)))

    def configure(self, payload):
        with self.c.lock:
            settings = {**self.settings, **payload}
            if set(settings) - {'enabled', 'preferred_agent', 'local_agent', 'mode', 'workflow'}:
                raise ValueError('Unknown conversation setting.')
            if type(settings.get('enabled', True)) is not bool:
                raise ValueError('enabled must be boolean.')
            for key in ('preferred_agent', 'local_agent'):
                agent = settings.get(key)
                if agent and (agent not in self.c.agents or agent == 'local-command'):
                    raise ValueError('Choose a configured model adapter for ' + key)
            if settings.get('local_agent') and self.c.agents[settings['local_agent']].get('local') is not True:
                raise ValueError('The fallback must explicitly declare local: true.')
            if settings.get('mode', 'online') not in ('online', 'offline'):
                raise ValueError('Mode must be online or offline.')
            if settings.get('workflow'):
                settings['workflow'] = self.c.workflows.specification(settings['workflow'])
            with self.c.store.connect() as db:
                self.put(db, 'conversation_settings', settings)
                self.event(db, 'conversation_settings', {'message': 'Conversation routing updated; active turns keep their owner.'})
            self.settings = settings
            return self.state()

    def messages(self, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Use a nonnegative cursor and limit 1–200.')
        with self.c.store.connect() as db:
            rows = db.execute('SELECT * FROM messages WHERE seq>? ORDER BY seq LIMIT ?', (after, limit)).fetchall()
        return [dict(r, data=json.loads(r['data'])) for r in rows]

    def state(self):
        with self.c.store.connect() as db:
            rows = db.execute('SELECT * FROM messages ORDER BY seq DESC LIMIT 60').fetchall()
            pending = db.execute("SELECT COUNT(*) FROM messages WHERE status='pending'").fetchone()[0]
        lease = self.meta('conversation_lease')
        return {'messages': [dict(r, data=json.loads(r['data'])) for r in reversed(rows)],
                'pending': pending, 'owner': {k: v for k, v in (lease or {}).items() if k != 'token'},
                'settings': self.settings, 'held': self.meta('conversation_held', ''),
                'background_runs': [{k: t.get(k) for k in ('id', 'title', 'status', 'internal', 'activity', 'agent', 'active_agent', 'pid', 'run_id')} for t in self.c.store.tasks() if t.get('internal') and t['status'] in ('running', 'launching', 'stopping', 'interrupted')],
                'voice_available': bool(self.transcription),
                'recordings': [{k: t.get(k) for k in ('id', 'status', 'activity', 'audio_file')}
                               for t in self.c.store.tasks() if t.get('internal') == 'transcription']}

    def append(self, payload):
        text, mid = payload.get('text'), payload.get('id')
        source = payload.get('source', 'acc')
        if not isinstance(text, str) or not text.strip() or len(text) > 50000:
            raise ValueError('Message must contain 1–50,000 characters.')
        if not isinstance(mid, str) or not 1 <= len(mid) <= 100 or not isinstance(source, str) or len(source) > 100:
            raise ValueError('Supply a stable message id and short source name.')
        with self.c.lock, self.c.store.connect() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            if row:
                if row['text'] != text or row['source'] != source or row['role'] != 'user':
                    raise Conflict('This message id already belongs to different content.')
                return dict(row, data=json.loads(row['data']))
            db.execute('INSERT INTO messages(id,role,text,source,status,at,data) VALUES (?,?,?,?,?,?,?)',
                       (mid, 'user', text, source, 'pending', now(), '{}'))
            self.event(db, 'message_saved', {'message': 'Your message is saved.', 'message_id': mid})
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            return dict(row, data={})

    def _expire(self):
        lease = self.meta('conversation_lease')
        if lease and lease['kind'] == 'external' and lease['expires'] <= now():
            with self.c.store.connect() as db:
                self.put(db, 'conversation_lease', None)
                self.event(db, 'orchestrator_expired', {'message': 'External orchestrator lease expired; pending messages can use local routing.'})
            return None
        return lease

    def claim(self, payload):
        owner = payload.get('owner', 'Desktop orchestrator')
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 100:
            raise ValueError('Provide an orchestrator name.')
        with self.c.lock:
            lease = self._expire()
            if lease:
                raise Conflict('A conversation turn already belongs to ' + lease['owner'] + '. Renew it or wait for its handoff.')
            if self.c.running_task or self.c.recovery_required or self.c.github.busy:
                raise Conflict('Wait for the current runner to finish or recover before claiming orchestration.')
            lease = {'token': identifier(), 'kind': 'external', 'owner': owner, 'expires': now() + 120,
                     'message_ids': [], 'offline': False}
            lease['message_ids'] = [m['id'] for m in self.context(lease)['pending']]
            with self.c.store.connect() as db:
                self.put(db, 'conversation_lease', lease)
                self.event(db, 'orchestrator_claimed', {'message': owner + ' is handling the conversation.'})
            # Capture the user's remote words AFTER claiming; then renew to refresh the batch.
            return {'token': lease['token'], 'expires': lease['expires'], 'context': self.context(lease)}

    def _owned(self, token):
        lease = self._expire()
        if not lease or lease['token'] != token:
            raise Conflict('Stale orchestration turn. Read current context and claim again.')
        return lease

    def renew(self, payload):
        with self.c.lock:
            lease = self._owned(payload.get('token'))
            if lease['kind'] != 'external':
                raise Conflict('Only an external session can renew its lease.')
            lease['expires'] = now() + 120
            context = self.context(lease)
            lease['message_ids'] = [m['id'] for m in context['pending']]
            with self.c.store.connect() as db:
                self.put(db, 'conversation_lease', lease)
            return {'token': lease['token'], 'expires': lease['expires'], 'context': context}

    def release(self, payload):
        with self.c.lock:
            lease = self._owned(payload.get('token'))
            if lease['kind'] != 'external':
                raise Conflict('Stop and inspect the supervised local turn before releasing it.')
            with self.c.store.connect() as db:
                self.put(db, 'conversation_lease', None)
                self.event(db, 'orchestrator_released', {'message': lease['owner'] + ' released the conversation.'})
            return {'released': True}

    def context(self, lease):
        with self.c.store.connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE status='pending' ORDER BY seq LIMIT 10").fetchall()
        pending = [dict(r, data=json.loads(r['data'])) for r in rows]
        history = self.state()['messages']
        return {'pending': pending, 'recent_history': history,
                'tasks': [{k: t.get(k) for k in ('id', 'task_number', 'task_kind', 'parent_task_id',
                          'parent_task_number', 'title', 'instruction', 'revision', 'status', 'activity',
                          'next_step', 'review', 'source_ids')}
                          for t in self.c.store.tasks() if not t.get('internal')],
                'workflow_defaults': self.settings.get('workflow'),
                'offline': lease['offline'],
                'result_contract': {
                    'token': lease['token'], 'reply': 'Plain-language response to the user, including next step.',
                    'intent': 'discussion, clarification, or request. Only request may contain actions.',
                    'actions': [{'type': 'create or revise', 'title': 'For create only',
                                 'instruction': 'Concrete requirements; do not broaden user authorization.',
                                 'source_ids': 'Nonempty array of pending message ids that request this work.',
                                 'task_id': 'For revise only', 'revision': 'Current integer revision for revise only'}],
                    'rules': ['Return token, reply, intent, actions as JSON. Do not run code or mutate files yourself.',
                              'Use discussion for ideas; ask in reply if intent is unclear. Actions execute with configured workflow roles.',
                              'Read existing tasks and outcomes before creating work; revise existing work instead of duplicating it.',
                              'To inspect older history use acc_conversation_read with an after cursor.']}}

    def complete(self, payload, internal_task=None):
        serialized = json.dumps(payload, sort_keys=True)
        if len(serialized.encode()) > 100000:
            raise ValueError('Turn result exceeds 100 KB.')
        with self.c.lock:
            token = payload.get('token')
            with self.c.store.connect() as db:
                receipt = db.execute('SELECT * FROM conversation_receipts WHERE token=?', (token,)).fetchone()
            if receipt:
                if receipt['request'] != serialized:
                    raise Conflict('A different result already completed this turn.')
                return json.loads(receipt['response'])
            lease = self._owned(token)
            if lease['kind'] == 'local' and (not internal_task or internal_task['id'] != lease.get('task_id')):
                raise Conflict('This turn belongs to the supervised adapter.')
            reply, intent, actions = payload.get('reply'), payload.get('intent'), payload.get('actions')
            if not isinstance(reply, str) or not reply.strip() or len(reply) > 50000:
                raise ValueError('Return a nonempty reply of at most 50,000 characters.')
            if intent not in ('discussion', 'clarification', 'request') or not isinstance(actions, list) or len(actions) > 20:
                raise ValueError('Return a valid intent and up to 20 actions.')
            if actions and intent != 'request':
                raise ValueError('Discussion and clarification cannot start work.')
            ids = lease['message_ids']
            if not ids:
                raise Conflict('No messages were captured. Renew the turn after saving the user message.')
            tasks, changed = [], set()
            spec = None
            if actions:
                if not self.settings.get('workflow'):
                    raise ValueError('Set default implementation, review, and coordinator roles before starting conversational work.')
                spec = self.c.workflows.specification(self.settings['workflow'])
                spec['mode'] = 'offline' if lease['offline'] else 'online'
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError('Each action must be an object.')
                sources = action.get('source_ids')
                if not isinstance(sources, list) or not sources or any(x not in ids for x in sources):
                    raise ValueError('Every action must cite pending user message ids in this turn.')
                if action.get('type') == 'create':
                    task = self.c.build_task({'title': action.get('title'), 'instruction': action.get('instruction'), 'agent': spec['implementer']})
                elif action.get('type') == 'revise':
                    task = self.c.store.get(action.get('task_id'))
                    if task.get('internal') or type(action.get('revision')) is not int or task['revision'] != action['revision']:
                        raise Conflict('Revision does not match the current task.')
                    if task['status'] in ('running', 'launching', 'stopping', 'processing_result', 'publishing', 'interrupted'):
                        raise Conflict('An active task cannot be revised until its runner finishes.')
                    instruction = action.get('instruction')
                    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 50000:
                        raise ValueError('Revised instruction must contain 1–50,000 characters.')
                    task['requirements_history'].append({'revision': task['revision'], 'instruction': task['instruction']})
                    task.update(revision=task['revision'] + 1, instruction=instruction, review=None)
                else:
                    raise ValueError('Unknown conversation action.')
                if task['id'] in changed:
                    raise ValueError('Revise a task at most once per turn.')
                changed.add(task['id'])
                task.update(workflow=self.c.workflows.initial(spec), agent=spec['implementer'], status='queued',
                            source_ids=list(dict.fromkeys(task.get('source_ids', []) + sources)),
                            activity='Requested through conversation.', next_step='Run implementation, then independent review')
                tasks.append(task)
            response = {'reply': reply, 'task_ids': [t['id'] for t in tasks], 'message_ids': ids}
            with self.c.store.connect() as db:
                for task in tasks:
                    self.c.store.ensure_task_number(db, task)
                    db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))
                    self.event(db, 'conversation_task', {'message': task['title'], 'task_id': task['id'],
                               'task_number': task['task_number'], 'revision': task['revision']})
                for mid in ids:
                    db.execute("UPDATE messages SET status='handled', data=? WHERE id=? AND status='pending'",
                               (json.dumps({'turn': token, 'task_ids': response['task_ids']}), mid))
                db.execute('INSERT INTO messages(id,role,text,source,status,at,data) VALUES (?,?,?,?,?,?,?)',
                           (identifier(), 'assistant', reply, lease['owner'], 'handled', now(), json.dumps(response)))
                if internal_task:
                    internal_task.update(status='accepted', activity=reply[:240], next_step='Conversation handled')
                    db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (internal_task['id'], json.dumps(internal_task)))
                self.put(db, 'conversation_lease', None)
                self.put(db, 'conversation_held', '')
                db.execute('INSERT INTO conversation_receipts VALUES (?,?,?)', (token, serialized, json.dumps(response)))
                self.event(db, 'conversation_replied', {'message': reply, 'task_ids': response['task_ids']})
            self.c.workflows.wake.set()
            return response

    def packet(self, task, run_id):
        lease = self._owned(task['conversation_token'])
        context = self.context(lease)
        # Only the messages captured at launch may be consumed; later arrivals wait.
        context['pending'] = [m for m in context['pending'] if m['id'] in lease['message_ids']]
        context.update(run_id=run_id)
        return context

    def tick(self):
        lease = self._expire()
        if lease:
            if lease['kind'] == 'local':
                task = self.c.store.get(lease['task_id'])
                if task.pop('internal_retry', False) and task['status'] == 'queued':
                    self.c.store.save(task, 'conversation_retry_started', {'message': 'Starting local fallback.'})
                    try:
                        self.c.start(task['id'], _internal=True)
                    except Exception as exc:
                        self.fail(self.c.store.get(task['id']), str(exc), retry=False)
            return True  # External reasoning reserves the next scheduling decision.
        for recording in self.c.store.tasks():
            if recording.get('internal') == 'transcription' and recording['status'] == 'queued':
                try:
                    self.c.start(recording['id'], _internal=True)
                except Exception as exc:
                    recording.update(status='paused', activity=str(exc))
                    self.c.store.save(recording, 'transcription_held', {'message': str(exc)})
                return True
        if self.meta('conversation_held') or not self.settings.get('enabled', True):
            return False
        if not self.state()['pending']:
            return False
        project_offline = self.c.controls.mode() == 'offline'
        preferred = self.settings.get('preferred_agent') if self.settings.get('mode', 'online') == 'online' and not project_offline else None
        local = self.settings.get('local_agent')
        agent = preferred if preferred and self.c.agents[preferred]['available'] else local
        if not agent or not self.c.agents[agent]['available']:
            return False  # Messages stay saved until a host adapter or external session is available.
        task = self.c.build_task({'title': 'Respond to conversation', 'instruction': 'Read conversation packet and propose the requested next steps.', 'agent': agent, 'timeout_seconds': 180})
        lease = {'token': identifier(), 'kind': 'local', 'owner': agent, 'expires': None,
                 'offline': project_offline or self.settings.get('mode') == 'offline' or (bool(preferred) and agent != preferred),
                 'task_id': task['id'], 'message_ids': []}
        lease['message_ids'] = [m['id'] for m in self.context(lease)['pending']]
        task.update(internal='conversation', conversation_token=lease['token'],
                    conversation_inventory=inventory(self.c.project))
        with self.c.store.connect() as db:
            self.put(db, 'conversation_lease', lease)
            db.execute('INSERT INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))
            self.event(db, 'conversation_routing', {'message': agent + ' is reading your messages.'})
        try:
            self.c.start(task['id'], _internal=True)
        except Exception as exc:
            self.fail(self.c.store.get(task['id']), str(exc), retry=True)
        return True

    def finish(self, task, folder, code, stopped):
        if inventory(self.c.project) != task['conversation_inventory']:
            return self.fail(task, 'Planner changed project files. Inspect retained changes before retrying.', retry=False)
        if code or stopped or task['timed_out']:
            return self.fail(task, 'Conversation runner stopped, failed, or timed out.', retry=not stopped or task['timed_out'])
        path = folder / 'result.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 100000:
            raise ValueError('Conversation runner must return result.json of at most 100 KB.')
        self.complete(json.loads(path.read_text(encoding='utf-8')), task)

    def fail(self, task, reason, retry=False):
        lease = self.meta('conversation_lease')
        local = self.settings.get('local_agent')
        if retry and lease and local and lease['owner'] != local and self.c.agents[local]['available']:
            # The failed planner has no authority to execute its proposed tasks.
            lease.update(owner=local, offline=True)
            task.update(agent=local, status='queued', activity='Online planner unavailable; trying configured local planner.', internal_retry=True)
            with self.c.store.connect() as db:
                self.put(db, 'conversation_lease', lease)
                db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))
                self.event(db, 'conversation_fallback', {'message': task['activity']})
            return
        task.update(status='paused', activity=reason, next_step='Read retained messages and retry conversation')
        with self.c.store.connect() as db:
            self.put(db, 'conversation_lease', None)
            self.put(db, 'conversation_held', reason)
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))
            self.event(db, 'conversation_held', {'message': reason})

    def scheduler_error(self, reason):
        with self.c.store.connect() as db:
            self.put(db, 'conversation_held', reason)
            self.event(db, 'conversation_held', {'message': reason})

    def retry(self):
        with self.c.lock:
            if self.c.running_task or self.c.recovery_required or self.c.github.busy:
                raise Conflict('Wait for or recover the active runner first.')
            lease = self._expire()
            if lease and lease['kind'] == 'external':
                raise Conflict('Release the external conversation turn first.')
            with self.c.store.connect() as db:
                self.put(db, 'conversation_lease', None)
                self.put(db, 'conversation_held', '')
                self.event(db, 'conversation_retry', {'message': 'Saved messages are ready to be handled.'})
            return self.state()
