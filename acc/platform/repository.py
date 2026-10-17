"""Read-model boundary for the hosted ACC Platform API."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..auth.models import _bounded_id


@dataclass(frozen=True)
class AccountProjectView:
    account_id: str
    project_id: str
    name: str
    mode: str = 'online'
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        object.__setattr__(self, 'project_id', _bounded_id(self.project_id, 'project_id'))
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 300:
            raise ValueError('project name must contain 1-300 characters.')
        object.__setattr__(self, 'name', self.name.strip())
        if self.mode not in ('online', 'offline'):
            raise ValueError('project mode must be online or offline.')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('project revision must be a nonnegative integer.')


@dataclass(frozen=True)
class ProjectSnapshot:
    """One project's state as of a single committed point in time."""
    project: AccountProjectView
    tasks: tuple[Mapping[str, Any], ...]
    workers: tuple[Mapping[str, Any], ...]
    attention: tuple[Mapping[str, Any], ...]


class PlatformReadRepository(Protocol):
    """Account-scoped read model consumed by M09.

    ``project_snapshot`` must return every part from the same committed state: no command or
    fixture write may be visible in one part and missing from another, so ``project.revision``
    describes exactly the returned tasks and workers. ``None`` means the project does not exist.
    """
    def project_snapshot(self, account_id: str, project_id: str) -> ProjectSnapshot | None: ...
    def projects(self, account_id: str) -> list[AccountProjectView]: ...
    def project(self, account_id: str, project_id: str) -> AccountProjectView | None: ...
    def tasks(self, account_id: str, project_id: str) -> list[Mapping[str, Any]]: ...
    def task(self, account_id: str, project_id: str, task_id: str) -> Mapping[str, Any] | None: ...
    def workers(self, account_id: str, project_id: str) -> list[Mapping[str, Any]]: ...
    def attention(self, account_id: str, project_id: str) -> list[Mapping[str, Any]]: ...


def safe_public_record(
    record: Mapping[str, Any],
    *,
    account_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Copy a hosted record only after enforcing tenant/project ownership."""
    if not isinstance(record, Mapping):
        raise ValueError('Platform repository returned a non-object record.')
    value = dict(record)
    if value.get('account_id') != account_id:
        raise ValueError('Platform repository returned a cross-account record.')
    if project_id is not None and value.get('project_id') != project_id:
        raise ValueError('Platform repository returned a cross-project record.')
    return value
