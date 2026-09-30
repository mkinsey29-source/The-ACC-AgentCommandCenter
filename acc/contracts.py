"""Stable provider and connector contracts for the ACC platform.

These interfaces are intentionally small. Implementations may expose richer methods, but
downstream modules should depend only on these contracts so providers can be replaced without
changing the task/project model.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


CAPABILITY_RE = re.compile(r'[a-z][a-z0-9-]{0,39}(?:\.[a-z][a-z0-9-]{0,39}){0,3}\Z')
PROVIDER_ID_RE = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
CONNECTOR_FACETS = frozenset(('app', 'capability', 'workspace', 'knowledge', 'execution_node'))


def capability_name(value: Any, label: str = 'capability') -> str:
    """Return one validated dotted capability name."""
    if not isinstance(value, str) or not CAPABILITY_RE.fullmatch(value):
        raise ValueError(label + ' must be a dotted lowercase name.')
    return value


def provider_id(value: Any, label: str = 'provider id') -> str:
    if not isinstance(value, str) or not PROVIDER_ID_RE.fullmatch(value):
        raise ValueError(label + ' must use lowercase letters, numbers, and hyphens.')
    return value


def capability_list(values: Any, label: str = 'capabilities', limit: int = 100) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or len(values) > limit:
        raise ValueError(f'{label} must be an array of at most {limit} dotted lowercase names.')
    result = []
    for value in values:
        result.append(capability_name(value, label))
    return tuple(dict.fromkeys(result))


@runtime_checkable
class WorkerProvider(Protocol):
    """Runs work for ACC. Provider branding never leaks into the core task model."""
    id: str

    def capabilities(self) -> Sequence[str]: ...
    def status(self) -> Mapping[str, Any]: ...
    def start(self, task: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def cancel(self, run_id: str) -> Mapping[str, Any]: ...


@runtime_checkable
class DecisionProvider(Protocol):
    """Chooses among already-valid bounded options; it does not establish facts."""
    id: str

    def decide(self, question: str, options: Sequence[str],
               evidence: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class ExecutionNode(Protocol):
    """Owns local/remote execution resources such as a desktop host."""
    id: str

    def status(self) -> Mapping[str, Any]: ...
    def reserve(self, resource: str, owner: str) -> Mapping[str, Any]: ...
    def release(self, lease_id: str) -> None: ...


@runtime_checkable
class CapabilityConnector(Protocol):
    """Invokes a tool/engine lane, often with execution-node/resource-lease semantics."""
    id: str

    def capabilities(self) -> Sequence[str]: ...
    def status(self) -> Mapping[str, Any]: ...
    def invoke(self, capability: str, payload: Mapping[str, Any],
               context: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class WorkspaceProvider(Protocol):
    """Stores user-facing project artifacts/workspaces; never authoritative ACC task state."""
    id: str

    def status(self) -> Mapping[str, Any]: ...
    def put(self, project_id: str, artifact: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def get(self, uri: str) -> Mapping[str, Any]: ...


@runtime_checkable
class AppConnector(Protocol):
    """Connects an account/SaaS service such as mail, calendar, Drive, or a business system."""
    id: str

    def capabilities(self) -> Sequence[str]: ...
    def status(self) -> Mapping[str, Any]: ...
    def invoke(self, capability: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class KnowledgeProvider(Protocol):
    """Persists/retrieves ACC knowledge records behind the common knowledge lifecycle."""
    id: str

    def status(self) -> Mapping[str, Any]: ...
    def search(self, project_id: str, query: str, scopes: Sequence[str]) -> Sequence[Mapping[str, Any]]: ...
    def write(self, project_id: str, record: Mapping[str, Any]) -> Mapping[str, Any]: ...
