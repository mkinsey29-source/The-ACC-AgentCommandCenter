"""Account-scoped event cursor contracts for M09."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..auth.models import _bounded_id


class EventCursorError(ValueError):
    pass


def event_cursor(value: object) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise EventCursorError('Event cursor must be a nonnegative integer.')
    return value


@dataclass(frozen=True)
class PlatformEvent:
    seq: int
    account_id: str
    project_id: str
    kind: str
    at: float
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.seq) is not int or self.seq <= 0:
            raise ValueError('event seq must be a positive integer.')
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        object.__setattr__(self, 'project_id', _bounded_id(self.project_id, 'project_id'))
        if not isinstance(self.kind, str) or not self.kind.strip() or len(self.kind) > 120:
            raise ValueError('event kind must contain 1-120 characters.')
        object.__setattr__(self, 'kind', self.kind.strip())
        if not isinstance(self.at, (int, float)) or isinstance(self.at, bool) or self.at < 0:
            raise ValueError('event timestamp must be nonnegative.')
        if not isinstance(self.data, Mapping):
            raise ValueError('event data must be an object.')


@dataclass(frozen=True)
class EventBatch:
    events: tuple[PlatformEvent, ...]
    cursor: int
    has_more: bool = False

    def __post_init__(self) -> None:
        if type(self.cursor) is not int or self.cursor < 0:
            raise ValueError('event batch cursor must be nonnegative.')
        previous = -1
        for event in self.events:
            if event.seq <= previous:
                raise ValueError('event batch must be strictly ordered by seq.')
            previous = event.seq
        if self.events and self.cursor != self.events[-1].seq:
            raise ValueError('event batch cursor must equal the last event seq.')


class PlatformEventSource(Protocol):
    def read_events(
        self,
        account_id: str,
        project_id: str,
        *,
        after: int,
        limit: int = 200,
    ) -> EventBatch: ...


def validate_event_batch(
    batch: EventBatch,
    *,
    account_id: str,
    project_id: str,
    after: int,
) -> EventBatch:
    after = event_cursor(after)
    if not isinstance(batch, EventBatch):
        raise ValueError('Event source returned an invalid batch.')
    previous = after
    for event in batch.events:
        if event.account_id != account_id or event.project_id != project_id:
            raise ValueError('Event source returned a cross-tenant event.')
        if event.seq <= previous:
            raise ValueError('Event source returned a replay/out-of-order event.')
        previous = event.seq
    if not batch.events and batch.cursor != after:
        raise ValueError('Empty event batch must preserve the requested cursor.')
    return batch
