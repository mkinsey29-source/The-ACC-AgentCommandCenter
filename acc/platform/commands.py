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


def operation_id(value: object) -> str:
    return _bounded_id(value, 'operation_id')


def expected_revision(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError('expected_revision must be a nonnegative integer.')
    return value


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
        if self.kind not in COMMAND_KINDS:
            raise ValueError('Unsupported command kind.')
        object.__setattr__(self, 'expected_revision', expected_revision(self.expected_revision))
        object.__setattr__(self, 'payload', _json_object(self.payload, 'command payload', 50_000))
        object.__setattr__(self, 'actor_user_id', _bounded_id(self.actor_user_id, 'actor_user_id'))

    @property
    def fingerprint(self) -> str:
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
        if self.status not in ('applied', 'conflict', 'rejected'):
            raise ValueError('Unsupported command result status.')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('command result revision must be nonnegative.')
        object.__setattr__(self, 'result', _json_object(self.result, 'command result', 50_000))


class CommandConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__('Command revision conflict.')


class IdempotencyConflict(Exception):
    pass


class QuotaExceeded(Exception):
    pass


class PlatformCommandRepository(Protocol):
    """Durable atomic command boundary. Implementations claim idempotency, compare revision,
    reserve quotas, mutate state, append audit/event records, save the result, and commit all
    of those effects in one transaction. Same operation+fingerprint replays the original result;
    the same operation ID with another fingerprint raises IdempotencyConflict."""

    def execute(
        self,
        command: CommandRequest,
        *,
        quotas: tuple[QuotaReservation, ...] = (),
    ) -> CommandResult: ...
