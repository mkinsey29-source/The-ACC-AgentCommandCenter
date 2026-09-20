"""Durable integration jobs, provider capabilities, artifacts, and shared memory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time

from .core import Conflict, identifier, now


PROVIDER_CATALOG = (
    {'id': 'deepseek-harness', 'name': 'DeepSeek Harness', 'local': True,
     'capabilities': ('agent.execute', 'agent.failover', 'model.local')},
    {'id': 'gemini', 'name': 'Gemini', 'local': False,
     'capabilities': ('reason', 'vision.inspect', 'image.generate', 'image.edit')},
    {'id': 'meshy', 'name': 'Meshy', 'local': False,
     'capabilities': ('mesh.generate', 'texture.generate', 'rig.generate')},
    {'id': 'tripo', 'name': 'Tripo', 'local': False,
     'capabilities': ('mesh.generate', 'texture.generate')},
    {'id': 'aura', 'name': 'Aura', 'local': False,
     'capabilities': ('image.generate', 'image.edit', 'video.generate')},
    {'id': 'typesafe-jev', 'name': 'TypeSafe Jev', 'local': False,
     'capabilities': ('decision.choice', 'decision.score', 'decision.noul')},
    {'id': 'tesana', 'name': 'Tesana', 'local': False, 'transport': 'interactive',
     'capabilities': ('game.plan', 'game.build', 'game.iterate', 'asset.import', 'project.export')},
    {'id': 'runpod', 'name': 'RunPod', 'local': False,
     'capabilities': ('gpu.execute', 'render.blender', 'render.unity')},
    {'id': 'hearth-pipeline', 'name': 'Hearth and Havoc Pipeline', 'local': True,
     'capabilities': ('blender.process', 'unity.import', 'asset.validate')},
)

FINAL_JOB_STATES = frozenset(('succeeded', 'failed', 'cancelled'))
WAITING_JOB_STATES = frozenset(('queued', 'blocked_offline', 'waiting_provider'))
MEMORY_KINDS = frozenset(('architecture', 'constraint', 'decision', 'fact', 'finding', 'procedure'))
CAPABILITY = re.compile(r'[a-z][a-z0-9-]{0,39}(?:\.[a-z][a-z0-9-]{0,39}){0,3}\Z')
PROVIDER_ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')


def _json_object(value, label, limit=100_000):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(label + ' must be a JSON object.')
    encoded = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    if len(encoded.encode('utf-8')) > limit:
        raise ValueError(label + ' is too large.')
    return json.loads(encoded)


def _event(db, kind, details, task_id=None):
    db.execute('INSERT INTO events(at,task_id,kind,data) VALUES (?,?,?,?)',
               (now(), task_id, kind, json.dumps(details)))


class IntegrationHub:
    """The seam between ACC orchestration and replaceable external adapters."""

    def __init__(self, coordinator, settings=None):
        self.c = coordinator
        self.providers = self._providers(settings or {})
        with self.c.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS integration_jobs (
                id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
                status TEXT NOT NULL, provider TEXT NOT NULL, capability TEXT NOT NULL,
                idempotency_key TEXT UNIQUE, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS integration_jobs_queue
                ON integration_jobs(status, created);
            CREATE TABLE IF NOT EXISTS integration_artifacts (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created REAL NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES integration_jobs(id));
            CREATE INDEX IF NOT EXISTS integration_artifacts_job
                ON integration_artifacts(job_id, created);
            CREATE TABLE IF NOT EXISTS project_memory (
                id TEXT PRIMARY KEY, memory_key TEXT NOT NULL, version INTEGER NOT NULL,
                status TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, source TEXT NOT NULL, task_id TEXT, branch TEXT NOT NULL,
                request_id TEXT UNIQUE, created REAL NOT NULL, updated REAL NOT NULL,
                data TEXT NOT NULL, UNIQUE(memory_key, version));
            CREATE INDEX IF NOT EXISTS project_memory_lookup
                ON project_memory(status, kind, memory_key, version);
            ''')

    @staticmethod
    def _providers(settings):
        configured = settings.get('providers', [])
        if not isinstance(configured, list):
            raise ValueError('Integration providers must be a JSON array.')
        overrides = {}
        for item in configured:
            if not isinstance(item, dict) or not PROVIDER_ID.fullmatch(str(item.get('id', ''))):
                raise ValueError('Integration provider IDs must use lowercase letters, numbers, and hyphens.')
            overrides[item['id']] = item
        providers = {}
        for base in PROVIDER_CATALOG:
            override = overrides.pop(base['id'], {})
            capabilities = override.get('capabilities', base['capabilities'])
            if not isinstance(capabilities, (list, tuple)) or not capabilities or not all(
                    isinstance(x, str) and CAPABILITY.fullmatch(x) for x in capabilities):
                raise ValueError('Provider capabilities must be dotted lowercase names.')
            enabled = override.get('enabled') is True
            credential_env = override.get('credential_env')
            if credential_env is not None and (not isinstance(credential_env, str) or
                                               not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', credential_env)):
                raise ValueError('credential_env must be an uppercase environment variable name.')
            credentials_present = not credential_env or bool(os.environ.get(credential_env))
            configured_ok = enabled and credentials_present
            providers[base['id']] = {
                **base, 'capabilities': sorted(set(capabilities)),
                'local': bool(override.get('local', base['local'])),
                'configured': configured_ok, 'enabled': enabled,
                'verified': bool(override.get('verified')) and configured_ok,
                'status': ('verified' if override.get('verified') and configured_ok else
                           'configured_unverified' if configured_ok else
                           'credentials_missing' if enabled and not credentials_present else 'not_configured'),
            }
        if overrides:
            raise ValueError('Unknown integration provider: ' + sorted(overrides)[0])
        return providers

    def _load_job(self, db, job_id):
        row = db.execute('SELECT data FROM integration_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row[0])

    @staticmethod
    def _save_job(db, job):
        db.execute('''INSERT OR REPLACE INTO integration_jobs
            (id,created,updated,status,provider,capability,idempotency_key,data)
            VALUES (?,?,?,?,?,?,?,?)''',
            (job['id'], job['created'], job['updated'], job['status'], job['provider'],
             job['capability'], job.get('idempotency_key'), json.dumps(job)))

    def _desired_state(self, provider):
        item = self.providers[provider]
        if self.c.controls.mode() == 'offline' and not item['local']:
            return 'blocked_offline'
        return 'queued' if item['configured'] else 'waiting_provider'

    def _select_provider(self, capability, requested=None):
        if requested is not None:
            if requested not in self.providers:
                raise ValueError('Unknown integration provider.')
            if capability not in self.providers[requested]['capabilities']:
                raise ValueError('Provider does not advertise the requested capability.')
            return requested
        candidates = [p for p in self.providers.values() if capability in p['capabilities']]
        if not candidates:
            raise ValueError('No provider advertises that capability.')
        mode = self.c.controls.mode()
        candidates.sort(key=lambda p: (
            not (p['configured'] and (mode == 'online' or p['local'])),
            not p['verified'], not p['local'], p['id']))
        return candidates[0]['id']

    def _expire_leases(self, db):
        rows = db.execute("SELECT data FROM integration_jobs WHERE status='running'").fetchall()
        stamp = now()
        for row in rows:
            job = json.loads(row[0])
            if job.get('lease_until', 0) > stamp:
                continue
            job.update(status=self._desired_state(job['provider']), updated=stamp,
                       lease_owner=None, lease_until=None, lease_token_hash=None,
                       last_error='Worker lease expired; job returned to the durable queue.')
            job['attempts'] = job.get('attempts', 0) + 1
            self._save_job(db, job)
            _event(db, 'integration_lease_expired', {'message': job['last_error'], 'job_id': job['id']},
                   job.get('task_id'))

    def snapshot(self, job_limit=100, memory_limit=50):
        with self.c.lock, self.c.store.connect() as db:
            self._expire_leases(db)
            rows = db.execute('SELECT data FROM integration_jobs ORDER BY created DESC LIMIT ?',
                              (job_limit,)).fetchall()
            jobs = [{key: value for key, value in json.loads(row[0]).items()
                     if key != 'lease_token_hash'} for row in rows]
            memory_rows = db.execute('''SELECT id,memory_key,version,status,kind,title,body,source,
                                      task_id,branch,created,updated,data
                                      FROM project_memory ORDER BY updated DESC LIMIT ?''',
                                     (memory_limit,)).fetchall()
            memories = [self._memory_dict(row) for row in memory_rows]
        counts = {}
        for job in jobs:
            counts[job['status']] = counts.get(job['status'], 0) + 1
        return {'providers': list(self.providers.values()), 'jobs': jobs, 'memory': memories,
                'job_counts': counts}

    def submit(self, payload):
        capability = str(payload.get('capability', '')).strip()
        if not CAPABILITY.fullmatch(capability):
            raise ValueError('Capability must be a dotted lowercase name.')
        provider = self._select_provider(capability, payload.get('provider'))
        task_id = payload.get('task_id')
        if task_id is not None:
            if not isinstance(task_id, str):
                raise ValueError('task_id must be a string.')
            self.c.store.get(task_id)
        idempotency_key = payload.get('idempotency_key')
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or
                                            not 1 <= len(idempotency_key) <= 200):
            raise ValueError('idempotency_key must contain 1–200 characters.')
        priority = payload.get('priority', 50)
        if type(priority) is not int or not 0 <= priority <= 100:
            raise ValueError('Priority must be an integer from 0 to 100.')
        request = _json_object(payload.get('input'), 'Job input')
        budget = _json_object(payload.get('budget'), 'Job budget', 10_000)
        with self.c.lock, self.c.store.connect() as db:
            if idempotency_key:
                row = db.execute('SELECT data FROM integration_jobs WHERE idempotency_key=?',
                                 (idempotency_key,)).fetchone()
                if row:
                    existing = json.loads(row[0])
                    comparable = (existing['capability'], existing['provider'], existing['input'],
                                  existing.get('budget', {}), existing.get('task_id'))
                    incoming = (capability, provider, request, budget, task_id)
                    if comparable != incoming:
                        raise Conflict('Idempotency key was already used for a different job.')
                    return existing
            stamp = now()
            job = {'id': identifier(), 'capability': capability, 'provider': provider,
                   'status': self._desired_state(provider), 'input': request, 'budget': budget,
                   'priority': priority, 'task_id': task_id, 'idempotency_key': idempotency_key,
                   'created': stamp, 'updated': stamp, 'attempts': 0, 'fence': 0,
                   'lease_owner': None, 'lease_until': None, 'lease_token_hash': None,
                   'result': None, 'cost': {}, 'last_error': None}
            self._save_job(db, job)
            _event(db, 'integration_job_created',
                   {'message': f"{capability} queued for {provider} ({job['status']}).",
                    'job_id': job['id'], 'provider': provider, 'status': job['status']}, task_id)
            return job

    def claim(self, payload):
        owner = payload.get('owner')
        if not isinstance(owner, str) or not 1 <= len(owner.strip()) <= 100:
            raise ValueError('Provide a worker owner name (1–100 characters).')
        provider = payload.get('provider')
        if provider is not None and provider not in self.providers:
            raise ValueError('Unknown integration provider.')
        capabilities = payload.get('capabilities', [])
        if not isinstance(capabilities, list) or not all(isinstance(x, str) and CAPABILITY.fullmatch(x)
                                                        for x in capabilities):
            raise ValueError('Capabilities must be dotted lowercase names.')
        lease_seconds = payload.get('lease_seconds', 120)
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 900:
            raise ValueError('Lease must be 15–900 seconds.')
        with self.c.lock, self.c.store.connect() as db:
            self._expire_leases(db)
            rows = db.execute("SELECT data FROM integration_jobs WHERE status='queued'").fetchall()
            jobs = sorted((json.loads(row[0]) for row in rows),
                          key=lambda job: (-job.get('priority', 50), job['created']))
            job = next((item for item in jobs
                        if (provider is None or item['provider'] == provider) and
                        (not capabilities or item['capability'] in capabilities)), None)
            if job is None:
                return {'job': None}
            token = secrets.token_urlsafe(32)
            job.update(status='running', updated=now(), lease_owner=owner.strip(),
                       lease_until=now() + lease_seconds,
                       lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
                       fence=job.get('fence', 0) + 1)
            self._save_job(db, job)
            _event(db, 'integration_job_claimed',
                   {'message': f"{owner.strip()} claimed {job['capability']}.", 'job_id': job['id'],
                    'provider': job['provider'], 'fence': job['fence']}, job.get('task_id'))
            public = {k: v for k, v in job.items() if k != 'lease_token_hash'}
            return {'job': public, 'lease_token': token}

    @staticmethod
    def _check_lease(job, payload):
        token, fence = payload.get('lease_token'), payload.get('fence')
        if job['status'] != 'running' or job.get('lease_until', 0) <= now():
            raise Conflict('Job lease is not active.')
        if type(fence) is not int or fence != job.get('fence'):
            raise Conflict('Stale fencing token.')
        if not isinstance(token, str) or not secrets.compare_digest(
                hashlib.sha256(token.encode()).hexdigest(), job.get('lease_token_hash') or ''):
            raise Conflict('Invalid job lease token.')

    def renew(self, job_id, payload):
        lease_seconds = payload.get('lease_seconds', 120)
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 900:
            raise ValueError('Lease must be 15–900 seconds.')
        with self.c.lock, self.c.store.connect() as db:
            job = self._load_job(db, job_id)
            self._check_lease(job, payload)
            job.update(lease_until=now() + lease_seconds, updated=now())
            self._save_job(db, job)
            return {k: v for k, v in job.items() if k != 'lease_token_hash'}

    def finish(self, job_id, payload):
        status = payload.get('status')
        if status not in ('succeeded', 'failed'):
            raise ValueError('Finished job status must be succeeded or failed.')
        result = _json_object(payload.get('result'), 'Job result')
        cost = _json_object(payload.get('cost'), 'Job cost', 10_000)
        error = payload.get('error')
        if error is not None and (not isinstance(error, str) or len(error) > 5000):
            raise ValueError('Job error must be at most 5,000 characters.')
        artifacts = payload.get('artifacts', [])
        if not isinstance(artifacts, list) or len(artifacts) > 50:
            raise ValueError('Artifacts must be an array with at most 50 entries.')
        with self.c.lock, self.c.store.connect() as db:
            job = self._load_job(db, job_id)
            self._check_lease(job, payload)
            job.update(status=status, result=result, cost=cost, last_error=error,
                       updated=now(), lease_owner=None, lease_until=None, lease_token_hash=None)
            self._save_job(db, job)
            saved = [self._add_artifact(db, job, item) for item in artifacts]
            _event(db, 'integration_job_finished',
                   {'message': f"{job['capability']} {status}.", 'job_id': job['id'],
                    'provider': job['provider'], 'status': status, 'artifacts': len(saved)},
                   job.get('task_id'))
            public = {k: v for k, v in job.items() if k != 'lease_token_hash'}
            public['artifacts'] = saved
            return public

    def cancel(self, job_id):
        with self.c.lock, self.c.store.connect() as db:
            job = self._load_job(db, job_id)
            if job['status'] in FINAL_JOB_STATES:
                return job
            if job['status'] == 'running':
                raise Conflict('A running adapter must finish or release its lease before cancellation.')
            job.update(status='cancelled', updated=now(), last_error='Cancelled by ACC operator.')
            self._save_job(db, job)
            _event(db, 'integration_job_cancelled', {'message': job['last_error'], 'job_id': job_id},
                   job.get('task_id'))
            return job

    def retry(self, job_id):
        with self.c.lock, self.c.store.connect() as db:
            job = self._load_job(db, job_id)
            if job['status'] != 'failed':
                raise Conflict('Only failed jobs can be retried.')
            job.update(status=self._desired_state(job['provider']), updated=now(), result=None,
                       cost={}, last_error=None)
            self._save_job(db, job)
            _event(db, 'integration_job_retried',
                   {'message': f"{job['capability']} returned to {job['status']}.", 'job_id': job_id},
                   job.get('task_id'))
            return job

    def _add_artifact(self, db, job, payload):
        if not isinstance(payload, dict):
            raise ValueError('Each artifact must be a JSON object.')
        uri, kind = payload.get('uri'), payload.get('kind', 'file')
        if not isinstance(uri, str) or not 1 <= len(uri) <= 2000 or '\0' in uri:
            raise ValueError('Artifact URI must contain 1–2,000 characters.')
        if not isinstance(kind, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', kind):
            raise ValueError('Artifact kind must use lowercase letters, numbers, and hyphens.')
        record = {'id': identifier(), 'job_id': job['id'], 'uri': uri, 'kind': kind,
                  'sha256': payload.get('sha256'), 'metadata': _json_object(payload.get('metadata'),
                                                                            'Artifact metadata', 20_000),
                  'created': now()}
        if record['sha256'] is not None and not re.fullmatch(r'[0-9a-f]{64}', str(record['sha256'])):
            raise ValueError('Artifact sha256 must be 64 lowercase hexadecimal characters.')
        db.execute('INSERT INTO integration_artifacts VALUES (?,?,?,?)',
                   (record['id'], job['id'], record['created'], json.dumps(record)))
        return record

    def on_mode_changed(self):
        with self.c.lock, self.c.store.connect() as db:
            self._expire_leases(db)
            rows = db.execute("SELECT data FROM integration_jobs WHERE status IN ('queued','blocked_offline','waiting_provider')").fetchall()
            changed = 0
            for row in rows:
                job = json.loads(row[0])
                target = self._desired_state(job['provider'])
                if target == job['status']:
                    continue
                job.update(status=target, updated=now())
                self._save_job(db, job)
                changed += 1
            if changed:
                _event(db, 'integration_queue_reconciled',
                       {'message': f'Reconciled {changed} integration job(s) with project mode.',
                        'mode': self.c.controls.mode()})
            return changed

    @staticmethod
    def _memory_dict(row):
        extra = json.loads(row['data'])
        return {key: row[key] for key in ('id', 'memory_key', 'version', 'status', 'kind', 'title',
                                           'body', 'source', 'task_id', 'branch', 'created', 'updated')} | extra

    def propose_memory(self, payload):
        title, body = payload.get('title'), payload.get('body')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ValueError('Memory title must contain 1–200 characters.')
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= 50_000:
            raise ValueError('Memory body must contain 1–50,000 characters.')
        kind = payload.get('kind', 'fact')
        if kind not in MEMORY_KINDS:
            raise ValueError('Unknown memory kind.')
        source = payload.get('source', 'operator')
        if not isinstance(source, str) or not 1 <= len(source.strip()) <= 100:
            raise ValueError('Memory source must contain 1–100 characters.')
        memory_key = payload.get('key')
        if memory_key is None:
            memory_key = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:80]
            memory_key = memory_key or hashlib.sha256(title.encode()).hexdigest()[:16]
        if not isinstance(memory_key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,99}', memory_key):
            raise ValueError('Memory key must use lowercase letters, numbers, dots, underscores, or hyphens.')
        branch = payload.get('branch', 'shared')
        if not isinstance(branch, str) or not 1 <= len(branch) <= 200:
            raise ValueError('Memory branch must contain 1–200 characters.')
        task_id = payload.get('task_id')
        if task_id is not None:
            self.c.store.get(task_id)
        request_id = payload.get('request_id')
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 200):
            raise ValueError('Memory request_id must contain 1–200 characters.')
        tags = payload.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 30 or not all(isinstance(x, str) and 1 <= len(x) <= 50 for x in tags):
            raise ValueError('Memory tags must contain at most 30 short strings.')
        with self.c.lock, self.c.store.connect() as db:
            if request_id:
                existing = db.execute('SELECT * FROM project_memory WHERE request_id=?', (request_id,)).fetchone()
                if existing:
                    item = self._memory_dict(existing)
                    if (item['memory_key'], item['title'], item['body'], item['kind']) != (
                            memory_key, title.strip(), body.strip(), kind):
                        raise Conflict('Memory request ID was already used for different content.')
                    return item
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM project_memory WHERE memory_key=?',
                                 (memory_key,)).fetchone()[0]
            stamp = now()
            item = {'id': identifier(), 'memory_key': memory_key, 'version': version,
                    'status': 'proposed', 'kind': kind, 'title': title.strip(), 'body': body.strip(),
                    'source': source.strip(), 'task_id': task_id, 'branch': branch,
                    'request_id': request_id, 'created': stamp, 'updated': stamp, 'tags': tags,
                    'commit': payload.get('commit')}
            extra = {'tags': tags, 'commit': item['commit']}
            db.execute('''INSERT INTO project_memory
                (id,memory_key,version,status,kind,title,body,source,task_id,branch,request_id,created,updated,data)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (item['id'], memory_key, version, 'proposed', kind, item['title'], item['body'],
                 item['source'], task_id, branch, request_id, stamp, stamp, json.dumps(extra)))
            _event(db, 'memory_proposed',
                   {'message': f"Proposed {kind}: {item['title']}", 'memory_id': item['id'],
                    'memory_key': memory_key, 'version': version}, task_id)
            return item

    def review_memory(self, memory_id, payload):
        status = payload.get('status')
        if status not in ('active', 'rejected'):
            raise ValueError('Memory review status must be active or rejected.')
        reviewer = payload.get('reviewer', 'operator')
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 100:
            raise ValueError('Memory reviewer must contain 1–100 characters.')
        with self.c.lock, self.c.store.connect() as db:
            row = db.execute('SELECT * FROM project_memory WHERE id=?', (memory_id,)).fetchone()
            if row is None:
                raise KeyError(memory_id)
            item = self._memory_dict(row)
            if item['status'] in ('active', 'rejected'):
                if item['status'] != status:
                    raise Conflict('Reviewed memory cannot be changed silently.')
                return item
            stamp = now()
            if status == 'active':
                db.execute("UPDATE project_memory SET status='superseded',updated=? WHERE memory_key=? AND status='active'",
                           (stamp, item['memory_key']))
            extra = {'tags': item.get('tags', []), 'commit': item.get('commit'),
                     'reviewer': reviewer.strip(), 'reviewed_at': stamp}
            db.execute('UPDATE project_memory SET status=?,updated=?,data=? WHERE id=?',
                       (status, stamp, json.dumps(extra), memory_id))
            item.update(status=status, updated=stamp, **extra)
            _event(db, 'memory_reviewed',
                   {'message': f"Memory {status}: {item['title']}", 'memory_id': memory_id,
                    'memory_key': item['memory_key'], 'version': item['version']}, item.get('task_id'))
            return item

    def search_memory(self, filters=None):
        filters = filters or {}
        query = str(filters.get('q', '')).strip().lower()
        kind = str(filters.get('kind', '')).strip()
        status = str(filters.get('status', 'active')).strip()
        task_id = str(filters.get('task_id', '')).strip()
        try:
            limit = int(filters.get('limit', 50) or 50)
        except (TypeError, ValueError):
            raise ValueError('Memory result limit must be an integer.')
        if kind and kind not in MEMORY_KINDS or status not in ('active', 'proposed', 'rejected', 'superseded', 'all'):
            raise ValueError('Invalid memory filter.')
        if not 1 <= limit <= 200:
            raise ValueError('Memory result limit must be 1–200.')
        with self.c.lock, self.c.store.connect() as db:
            rows = db.execute('SELECT * FROM project_memory ORDER BY updated DESC').fetchall()
        results = []
        for row in rows:
            item = self._memory_dict(row)
            if status != 'all' and item['status'] != status:
                continue
            if kind and item['kind'] != kind or task_id and item.get('task_id') != task_id:
                continue
            searchable = json.dumps(item, ensure_ascii=False, default=str).lower()
            if query and query not in searchable:
                continue
            results.append(item)
            if len(results) == limit:
                break
        return {'memory': results, 'count': len(results), 'filters': filters}
