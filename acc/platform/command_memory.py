"""Deterministic in-memory reference implementation of the M09 command transaction contract."""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any

from .commands import (
    CommandConflict, CommandRequest, CommandResult, IdempotencyConflict,
    QuotaExceeded, QuotaReservation,
)
from .events import EventBatch, PlatformEvent


class InMemoryCommandRepository:
    """Reference semantics for tests. Production storage must provide equivalent atomicity."""
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._projects: dict[tuple[str, str], dict[str, Any]] = {}
        self._claims: dict[tuple[str, str], tuple[str, CommandResult]] = {}
        self._usage: dict[tuple[str, str], int] = {}
        self._limits: dict[tuple[str, str], int] = {}
        self._audit: list[dict[str, Any]] = []
        self._events: list[PlatformEvent] = []
        self._next_seq = 1

    def put_project(self, account_id, project_id, *, revision=0, mode="online"):
        with self._lock:
            self._projects[(account_id, project_id)] = {
                'revision': revision, 'mode': mode, 'tasks': {}, 'workers': {}}

    def set_quota(self, account_id, name, *, limit, used=0):
        if type(limit) is not int or type(used) is not int or not 0 <= used <= limit:
            raise ValueError('invalid quota state')
        with self._lock:
            self._limits[(account_id, name)] = limit
            self._usage[(account_id, name)] = used

    def execute(self, command: CommandRequest, *, quotas: tuple[QuotaReservation, ...] = ()):
        key = (command.account_id, command.operation_id)
        with self._lock:
            existing = self._claims.get(key)
            if existing is not None:
                fingerprint, result = existing
                if fingerprint != command.fingerprint:
                    raise IdempotencyConflict('Operation ID was already used for another command.')
                return CommandResult(
                    result.operation_id, result.status, result.revision,
                    result.result, replayed=True)

            project_key = (command.account_id, command.project_id)
            project = self._projects.get(project_key)
            if project is None:
                raise ValueError('project not found')
            if project['revision'] != command.expected_revision:
                raise CommandConflict(project['revision'])

            for quota in quotas:
                qkey = (command.account_id, quota.name)
                allowed = self._limits.get(qkey)
                used = self._usage.get(qkey, 0)
                if allowed is None or used + quota.amount > allowed:
                    raise QuotaExceeded('Quota unavailable.')

            # Nothing mutates until every validation/quota check above succeeds.
            candidate = deepcopy(project)
            outcome = self._apply(candidate, command)
            candidate['revision'] += 1
            new_revision = candidate["revision"]

            for quota in quotas:
                qkey = (command.account_id, quota.name)
                self._usage[qkey] = self._usage.get(qkey, 0) + quota.amount
            self._projects[project_key] = candidate
            result = CommandResult(
                command.operation_id, 'applied', new_revision, outcome)
            self._claims[key] = (command.fingerprint, result)
            self._audit.append({
                'operation_id': command.operation_id, 'account_id': command.account_id,
                'project_id': command.project_id, 'actor_user_id': command.actor_user_id,
                'kind': command.kind, 'revision': new_revision, 'at': self.clock(),
            })
            self._events.append(PlatformEvent(
                self._next_seq, command.account_id, command.project_id,
                'command.applied', self.clock(), {
                    'operation_id': command.operation_id, 'kind': command.kind,
                    'revision': new_revision,
                }))
            self._next_seq += 1
            return result

    @staticmethod
    def _apply(project, command):
        payload = command.payload
        if command.kind == 'project.mode':
            mode = payload.get('mode')
            if mode not in ('online', 'offline'):
                raise ValueError('invalid project mode')
            project['mode'] = mode
            return {'mode': mode}
        if command.kind == 'task.create':
            task_id = payload.get('task_id')
            if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
                raise ValueError('invalid task id')
            if task_id in project['tasks']:
                raise ValueError('task already exists')
            project['tasks'][task_id] = {'id': task_id, 'status': 'queued'}
            return {'task_id': task_id, 'status': 'queued'}
        if command.kind == 'task.cancel':
            task_id = payload.get('task_id')
            task = project['tasks'].get(task_id)
            if task is None:
                raise ValueError('task not found')
            task['status'] = 'cancelled'
            return {'task_id': task_id, 'status': 'cancelled'}
        if command.kind in ('worker.pause', 'worker.resume'):
            worker_id = payload.get('worker_id')
            if not isinstance(worker_id, str) or not worker_id.strip():
                raise ValueError('invalid worker id')
            status = 'paused' if command.kind == 'worker.pause' else 'active'
            project['workers'].setdefault(worker_id, {})['status'] = status
            return {'worker_id': worker_id, 'status': status}
        raise ValueError('unsupported command')

    def project_revision(self, account_id, project_id):
        return self._projects[(account_id, project_id)]['revision']

    def quota_used(self, account_id, name):
        return self._usage.get((account_id, name), 0)

    def audit(self):
        return tuple(deepcopy(self._audit))

    def read_events(self, account_id, project_id, *, after, limit=200):
        values = [e for e in self._events
                  if e.account_id == account_id and e.project_id == project_id and e.seq > after]
        page = values[:limit]
        cursor = page[-1].seq if page else after
        return EventBatch(tuple(page), cursor, len(values) > len(page))
