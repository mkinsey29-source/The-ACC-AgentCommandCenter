"""Storage-neutral M09 command rules.

Every ``PlatformCommandRepository`` calls these functions so the business rules (revision
compare-and-swap, quota ceilings, task/worker/project transitions, replay identity, and the shape
of audit and event records) are written once. A store only supplies reads of its current state
inside its transaction and writes the returned ``Transition`` back; it never decides an outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .commands import (
    CommandConflict, CommandRequest, CommandResult, CommandStateConflict,
    CommandTargetNotFound, IdempotencyConflict, QuotaExceeded,
)


class CommandState(Protocol):
    """Reads of one project's state, made inside the store's command transaction."""

    def mode(self) -> str: ...

    def task(self, task_id: str) -> Mapping[str, Any] | None:
        """``{'status': str, 'reserved': {quota_name: amount}}`` or ``None``."""

    def worker_status(self, worker_id: str) -> str | None: ...


@dataclass(frozen=True)
class TaskWrite:
    task_id: str
    status: str
    reserved: Mapping[str, int]
    created: bool


@dataclass(frozen=True)
class WorkerWrite:
    worker_id: str
    status: str


@dataclass(frozen=True)
class Transition:
    """Everything one applied command changes; the store writes all of it or none of it."""

    result: dict[str, Any]
    revision: int
    mode: str | None = None
    task: TaskWrite | None = None
    worker: WorkerWrite | None = None
    reserve: Mapping[str, int] = field(default_factory=dict)
    release: Mapping[str, int] = field(default_factory=dict)


def replay(command: CommandRequest, fingerprint: str, revision: int,
           result: Mapping[str, Any]) -> CommandResult:
    """Return the stored result for a retried operation ID, or refuse a different command."""
    if fingerprint != command.fingerprint:
        raise IdempotencyConflict('Operation ID was already used for another command.')
    return CommandResult(command.operation_id, 'applied', revision, result, replayed=True)


def decide(command: CommandRequest, *, revision: int | None, state: CommandState,
           quota_used: Callable[[str], int],
           limits: Mapping[str, int | None]) -> Transition:
    """Validate ``command`` against the current state and return its transition.

    ``revision`` is the project's current revision, or ``None`` when the project does not exist.
    Raises before the store writes anything.
    """
    if revision is None:
        raise CommandTargetNotFound('project')
    if revision != command.expected_revision:
        raise CommandConflict(revision)

    reserve = {q.name: q.amount for q in command.quotas}
    for name, amount in reserve.items():
        allowed = limits.get(name)
        if allowed is not None and quota_used(name) + amount > allowed:
            raise QuotaExceeded('Quota unavailable.')

    payload, kind, new_revision = command.payload, command.kind, revision + 1
    if kind == 'project.mode':
        if state.mode() == payload['mode']:
            raise CommandStateConflict('project is already in that mode')
        return Transition({'mode': payload['mode']}, new_revision, mode=payload['mode'])
    if kind == 'task.create':
        task_id = payload['task_id']
        if state.task(task_id) is not None:
            raise CommandStateConflict('task already exists')
        return Transition({'task_id': task_id, 'status': 'queued'}, new_revision,
                          task=TaskWrite(task_id, 'queued', reserve, created=True),
                          reserve=reserve)
    if kind == 'task.cancel':
        task_id = payload['task_id']
        task = state.task(task_id)
        if task is None:
            raise CommandTargetNotFound('task')
        if task['status'] == 'cancelled':
            raise CommandStateConflict('task is already cancelled')
        return Transition({'task_id': task_id, 'status': 'cancelled'}, new_revision,
                          task=TaskWrite(task_id, 'cancelled', {}, created=False),
                          release=dict(task.get('reserved') or {}))
    if kind in ('worker.pause', 'worker.resume'):
        worker_id = payload['worker_id']
        status = state.worker_status(worker_id)
        if status is None:
            raise CommandTargetNotFound('worker')
        target = 'paused' if kind == 'worker.pause' else 'active'
        if status == target:
            raise CommandStateConflict('worker is already ' + target)
        return Transition({'worker_id': worker_id, 'status': target}, new_revision,
                          worker=WorkerWrite(worker_id, target))
    raise CommandStateConflict('unsupported command')


def released_usage(used: int, amount: int) -> int:
    """Quota usage after releasing ``amount``; never below zero."""
    return max(0, used - amount)


def audit_record(command: CommandRequest, revision: int, at: float) -> dict[str, Any]:
    return {
        'operation_id': command.operation_id, 'account_id': command.account_id,
        'project_id': command.project_id, 'actor_user_id': command.actor_user_id,
        'kind': command.kind, 'revision': revision, 'at': at,
    }


def event_data(command: CommandRequest, revision: int,
               result: Mapping[str, Any]) -> dict[str, Any]:
    """Data of the ``command.applied`` Platform event."""
    return {
        'operation_id': command.operation_id, 'kind': command.kind,
        'actor_user_id': command.actor_user_id, 'revision': revision,
        'result': dict(result),
    }
