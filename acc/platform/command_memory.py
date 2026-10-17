"""Deterministic in-memory reference implementation of the M09 command transaction contract."""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any, Mapping

from . import command_rules as rules
from .commands import CommandRequest, CommandResult
from .events import EventBatch, PlatformEvent


class _ProjectState:
    """``CommandState`` over one in-memory project record."""

    def __init__(self, project):
        self._project = project

    def mode(self):
        return self._project['mode']

    def task(self, task_id):
        return self._project['tasks'].get(task_id)

    def worker_status(self, worker_id):
        worker = self._project['workers'].get(worker_id)
        return None if worker is None else worker['status']


class InMemoryCommandRepository:
    """In-memory store for tests; also a ``PlatformEventSource`` for its own events.

    The rules come from ``command_rules``; this class only holds state.

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
                return rules.replay(command, fingerprint, result.revision, result.result)

            project_key = (command.account_id, command.project_id)
            project = self._projects.get(project_key)
            # Nothing is written until every check and the transition have succeeded.
            change = rules.decide(
                command,
                revision=None if project is None else project['revision'],
                state=_ProjectState(project),
                quota_used=lambda name: self._usage.get((command.account_id, name), 0),
                limits=limits)
            result = CommandResult(command.operation_id, 'applied', change.revision, change.result)
            now = self.clock()
            event = PlatformEvent(
                self._next_seq, command.account_id, command.project_id, 'command.applied', now,
                rules.event_data(command, change.revision, change.result))

            candidate = deepcopy(project)
            candidate['revision'] = change.revision
            if change.mode is not None:
                candidate['mode'] = change.mode
            if change.task is not None:
                candidate['tasks'][change.task.task_id] = {
                    'id': change.task.task_id, 'status': change.task.status,
                    'reserved': dict(change.task.reserved)}
            if change.worker is not None:
                candidate['workers'][change.worker.worker_id]['status'] = change.worker.status
            for name, amount in change.reserve.items():
                qkey = (command.account_id, name)
                self._usage[qkey] = self._usage.get(qkey, 0) + amount
            for name, amount in change.release.items():
                qkey = (command.account_id, name)
                self._usage[qkey] = rules.released_usage(self._usage.get(qkey, 0), amount)
            self._projects[project_key] = candidate
            self._claims[key] = (command.fingerprint, result)
            self._audit.append(rules.audit_record(command, change.revision, now))
            self._events.append(event)
            self._next_seq += 1
            return result

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
