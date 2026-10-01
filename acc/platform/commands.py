"""Remote command/mutation contracts for M09."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..auth.models import _bounded_id
from ..contracts import capability_name
from ..domain import _json_object


COMMAND_KINDS = {
    'task.create': ('task.write',),
    'task.cancel': ('task.cancel',),
    'project.mode': ('project.write',),
    'worker.pause': ('worker.control',),
    'worker.resume': ('worker.control',),
}

# Quota consumption is owned by the server and derived from command semantics. Clients never name
# quotas or amounts. The ceiling for each name is the account's live M08 entitlement limit.
# ``tasks.active`` counts non-cancelled tasks: task.create reserves 1, and task.cancel releases
# exactly what that task reserved.
COMMAND_QUOTAS: dict[str, tuple[tuple[str, int], ...]] = {
    'task.create': (('tasks.active', 1),),
    'task.cancel': (),
    'project.mode': (),
    'worker.pause': (),
    'worker.resume': (),
}

PROJECT_MODES = ('online', 'offline')

# Exact payload schema per command kind: every field is required and no other field is accepted,
# so an unknown field cannot silently acquire meaning in a later version.
_PAYLOAD_FIELDS = {
    'task.create': ('task_id',),
    'task.cancel': ('task_id',),
    'project.mode': ('mode',),
    'worker.pause': ('worker_id',),
    'worker.resume': ('worker_id',),
}


def operation_id(value: object) -> str:
    return _bounded_id(value, 'operation_id')


def expected_revision(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError('expected_revision must be a nonnegative integer.')
    return value


def command_payload(kind: str, value: object) -> dict[str, Any]:
    """Validate and normalize one command payload against its exact schema."""
    payload = _json_object(value, 'command payload', 50_000)
    fields = _PAYLOAD_FIELDS[kind]
    if set(payload) != set(fields):
        raise ValueError('command payload fields do not match the command schema.')
    if kind == 'project.mode':
        if payload['mode'] not in PROJECT_MODES:
            raise ValueError('invalid project mode.')
        return {'mode': payload['mode']}
    field = fields[0]
    return {field: _bounded_id(payload[field], field)}


@dataclass(frozen=True)
class CommandRequest:
    operation_id: str
    account_id: str
    project_id: str
    kind: str
    expected_revision: int
    payload: Mapping[str, Any]
    actor_user_id: str

    def __post_init__(self):
        object.__setattr__(self, 'operation_id', operation_id(self.operation_id))
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        object.__setattr__(self, 'project_id', _bounded_id(self.project_id, 'project_id'))
        if not isinstance(self.kind, str) or self.kind not in COMMAND_KINDS:
            raise ValueError('Unsupported command kind.')
        object.__setattr__(self, 'expected_revision', expected_revision(self.expected_revision))
        object.__setattr__(self, 'payload', command_payload(self.kind, self.payload))
        object.__setattr__(self, 'actor_user_id', _bounded_id(self.actor_user_id, 'actor_user_id'))

    @property
    def quotas(self) -> tuple['QuotaReservation', ...]:
        """Server-derived quota reservations for this command."""
        return tuple(QuotaReservation(name, amount) for name, amount in COMMAND_QUOTAS[self.kind])

    @property
    def fingerprint(self) -> str:
        """Identity of the request for idempotent replay.

        It includes the actor, so a retry of the same account-scoped operation ID by a different
        user is an idempotency conflict, never a replay of someone else's result.
        """
        body = {
            'account_id': self.account_id,
            'project_id': self.project_id,
            'kind': self.kind,
            'expected_revision': self.expected_revision,
            'payload': dict(self.payload),
            'actor_user_id': self.actor_user_id,
        }
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class QuotaReservation:
    name: str
    amount: int

    def __post_init__(self):
        object.__setattr__(self, 'name', capability_name(self.name, 'quota name'))
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError('quota reservation amount must be a positive integer.')


@dataclass(frozen=True)
class CommandResult:
    operation_id: str
    status: str
    revision: int
    result: Mapping[str, Any]
    replayed: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'operation_id', operation_id(self.operation_id))
        if self.status != 'applied':
            raise ValueError('Unsupported command result status.')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('command result revision must be nonnegative.')
        object.__setattr__(self, 'result', _json_object(self.result, 'command result', 50_000))


class CommandConflict(Exception):
    """expected_revision does not equal the project's current revision."""
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__('Command revision conflict.')


class IdempotencyConflict(Exception):
    """The (account_id, operation_id) was already applied with a different fingerprint."""


class QuotaExceeded(Exception):
    """A server-derived reservation would exceed the M08 ceiling, or no ceiling is configured."""


class CommandTargetNotFound(Exception):
    """The project, task or worker does not exist in this account/project."""


class CommandStateConflict(Exception):
    """The target exists but the transition is not valid from its current state."""


class PlatformCommandRepository(Protocol):
    """Durable atomic command boundary.

    ``execute`` runs as ONE transaction, in this order, and commits all of it or none of it:

    1. Idempotency: look up ``(command.account_id, command.operation_id)``. If a result exists and
       its fingerprint equals ``command.fingerprint``, return it with ``replayed=True`` and make no
       other change. A different fingerprint raises ``IdempotencyConflict``.
    2. Target: the ``(account_id, project_id)`` project must exist, else ``CommandTargetNotFound``.
    3. Revision: the project's revision must equal ``command.expected_revision`` (compare-and-swap),
       else ``CommandConflict(current_revision)``.
    4. Quota: for each ``command.quotas`` entry, ``used + amount <= limits[name]``, where ``limits``
       holds the live M08 ceiling (``None`` or missing means not configured). Otherwise
       ``QuotaExceeded``.
    5. Mutation: validate the transition (``CommandTargetNotFound`` / ``CommandStateConflict``) and
       apply it; the project revision increases by exactly 1.
    6. Record exactly one audit record, one Platform event (``command.applied``) allocated from the
       same per-deployment sequence the event source serves, and the command result keyed by
       ``(account_id, operation_id)`` with its fingerprint.

    Any error in steps 2-6 commits nothing, including no idempotency claim, so the operation ID
    can be retried. Any other exception is a repository fault and surfaces as an opaque 500.
    """

    def execute(
        self,
        command: CommandRequest,
        *,
        limits: Mapping[str, int | None],
    ) -> CommandResult: ...
