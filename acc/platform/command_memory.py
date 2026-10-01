"""Deterministic in-memory reference implementation of the M09 command transaction contract."""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any, Mapping

from .commands import (
    CommandConflict, CommandRequest, CommandResult, CommandStateConflict,
    CommandTargetNotFound, IdempotencyConflict, QuotaExceeded,
)
from .events import EventBatch, PlatformEvent


class InMemoryCommandRepository:
    """Reference semantics for tests; also a ``PlatformEventSource`` for its own events.

    One lock makes each ``execute`` a single serializable transaction. Production storage must
    provide the same atomicity (see ``PlatformCommandRepository``).
    """

    def __init__(self, *, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._projects: dict[tuple[str, str], dict[str, Any]] = {}
        self._claims: dict[tuple[str, str], tuple[str, CommandResult]] = {}
        self._usage: dict[tuple[str, str], int] = {}
        self._audit: list[dict[str, Any]] = []
        self._events: list[PlatformEvent] = []
        self._next_seq = 1

    # --- fixtures -----------------------------------------------------------------------------
    def put_project(self, account_id, project_id, *, revision=0, mode='online'):
        with self._lock:
            self._projects[(account_id, project_id)] = {
                'revision': revision, 'mode': mode, 'tasks': {}, 'workers': {}}

    def put_worker(self, account_id, project_id, worker_id, *, status='active'):
        if status not in ('active', 'paused'):
            raise ValueError('invalid worker status')
        with self._lock:
            self._projects[(account_id, project_id)]['workers'][worker_id] = {
                'id': worker_id, 'status': status}

    def set_quota_usage(self, account_id, name, used):
        if type(used) is not int or used < 0:
            raise ValueError('invalid quota usage')
        with self._lock:
            self._usage[(account_id, name)] = used

    # --- transaction --------------------------------------------------------------------------
    def execute(self, command: CommandRequest, *, limits: Mapping[str, int | None]):
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
                raise CommandTargetNotFound('project')
            if project['revision'] != command.expected_revision:
                raise CommandConflict(project['revision'])

            reservations = command.quotas
            for quota in reservations:
                allowed = limits.get(quota.name)
                used = self._usage.get((command.account_id, quota.name), 0)
                if type(allowed) is not int or used + quota.amount > allowed:
                    raise QuotaExceeded('Quota unavailable.')

            # Nothing is written until every check and the transition have succeeded.
            candidate = deepcopy(project)
            outcome, released = self._apply(candidate, command, reservations)
            candidate['revision'] += 1
            new_revision = candidate['revision']
            result = CommandResult(command.operation_id, 'applied', new_revision, outcome)
            now = self.clock()

            for quota in reservations:
                qkey = (command.account_id, quota.name)
                self._usage[qkey] = self._usage.get(qkey, 0) + quota.amount
            for name, amount in released.items():
                qkey = (command.account_id, name)
                self._usage[qkey] = max(0, self._usage.get(qkey, 0) - amount)
            self._projects[project_key] = candidate
            self._claims[key] = (command.fingerprint, result)
            self._audit.append({
                'operation_id': command.operation_id, 'account_id': command.account_id,
                'project_id': command.project_id, 'actor_user_id': command.actor_user_id,
                'kind': command.kind, 'revision': new_revision, 'at': now,
            })
            self._events.append(PlatformEvent(
                self._next_seq, command.account_id, command.project_id, 'command.applied', now, {
                    'operation_id': command.operation_id, 'kind': command.kind,
                    'actor_user_id': command.actor_user_id, 'revision': new_revision,
                    'result': dict(outcome),
                }))
            self._next_seq += 1
            return result

    @staticmethod
    def _apply(project, command, reservations):
        """Apply one validated transition to ``project`` (a private copy).

        Returns ``(result, released_quota)``. Raises before any write to shared state.
        """
        payload = command.payload
        kind = command.kind
        if kind == 'project.mode':
            if project['mode'] == payload['mode']:
                raise CommandStateConflict('project is already in that mode')
            project['mode'] = payload['mode']
            return {'mode': payload['mode']}, {}
        if kind == 'task.create':
            task_id = payload['task_id']
            if task_id in project['tasks']:
                raise CommandStateConflict('task already exists')
            project['tasks'][task_id] = {
                'id': task_id, 'status': 'queued',
                'reserved': {q.name: q.amount for q in reservations},
            }
            return {'task_id': task_id, 'status': 'queued'}, {}
        if kind == 'task.cancel':
            task = project['tasks'].get(payload['task_id'])
            if task is None:
                raise CommandTargetNotFound('task')
            if task['status'] == 'cancelled':
                raise CommandStateConflict('task is already cancelled')
            task['status'] = 'cancelled'
            released = dict(task.get('reserved', {}))
            task['reserved'] = {}
            return {'task_id': task['id'], 'status': 'cancelled'}, released
        if kind in ('worker.pause', 'worker.resume'):
            worker = project['workers'].get(payload['worker_id'])
            if worker is None:
                raise CommandTargetNotFound('worker')
            target = 'paused' if kind == 'worker.pause' else 'active'
            if worker['status'] == target:
                raise CommandStateConflict('worker is already ' + target)
            worker['status'] = target
            return {'worker_id': worker['id'], 'status': target}, {}
        raise CommandStateConflict('unsupported command')

    # --- read helpers -------------------------------------------------------------------------
    def project_revision(self, account_id, project_id):
        with self._lock:
            return self._projects[(account_id, project_id)]['revision']

    def project(self, account_id, project_id):
        with self._lock:
            return deepcopy(self._projects[(account_id, project_id)])

    def quota_used(self, account_id, name):
        with self._lock:
            return self._usage.get((account_id, name), 0)

    def audit(self):
        with self._lock:
            return tuple(deepcopy(self._audit))

    def read_events(self, account_id, project_id, *, after, limit=200):
        with self._lock:
            values = [e for e in self._events
                      if e.account_id == account_id and e.project_id == project_id
                      and e.seq > after]
        page = values[:limit]
        cursor = page[-1].seq if page else after
        return EventBatch(tuple(page), cursor, len(values) > len(page))
