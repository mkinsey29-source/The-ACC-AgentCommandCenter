"""Connector descriptors and registry for optional ACC capability packs."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

_UNSET = object()

from .contracts import CONNECTOR_FACETS, capability_list, provider_id


@dataclass(frozen=True)
class ConnectorSpec:
    id: str
    name: str
    facets: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    requires_execution_node: bool = False
    requires_resource_lease: bool = False
    implemented: bool = False

    def __post_init__(self):
        provider_id(self.id, 'connector id')
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 120:
            raise ValueError('connector name must be 1-120 characters.')
        facets = tuple(dict.fromkeys(self.facets))
        if not facets or any(item not in CONNECTOR_FACETS for item in facets):
            raise ValueError('connector facets must use documented ACC facet names.')
        object.__setattr__(self, 'facets', facets)
        object.__setattr__(self, 'capabilities', capability_list(self.capabilities, 'connector capabilities'))


@dataclass
class ConnectorState:
    spec: ConnectorSpec
    configured: bool = False
    healthy: bool = False
    enabled: bool = False
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            'id': self.spec.id,
            'name': self.spec.name,
            'facets': list(self.spec.facets),
            'capabilities': list(self.spec.capabilities),
            'implemented': self.spec.implemented,
            'requires_execution_node': self.spec.requires_execution_node,
            'requires_resource_lease': self.spec.requires_resource_lease,
            'configured': self.configured,
            'healthy': self.healthy,
            'enabled': self.enabled,
            'last_error': self.last_error,
            'metadata': dict(self.metadata),
        }


class ConnectorRegistry:
    """Tracks connector capability declarations separately from live connection health."""

    def __init__(self, specs: Iterable[ConnectorSpec] = ()):
        self._items: dict[str, ConnectorState] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ConnectorSpec) -> ConnectorState:
        if not isinstance(spec, ConnectorSpec):
            raise TypeError('register expects ConnectorSpec.')
        if spec.id in self._items:
            raise ValueError('Connector already registered: ' + spec.id)
        state = ConnectorState(spec)
        self._items[spec.id] = state
        return state

    def update_state(self, connector_id: str, *, configured: bool | None = None,
                     healthy: bool | None = None, enabled: bool | None = None,
                     last_error: Any = _UNSET, metadata: dict[str, Any] | None = None) -> dict:
        """Apply one connector state transition atomically.

        Every argument is validated against the complete prospective state before anything is
        mutated, so a rejected call leaves the connector unchanged. ``last_error`` is kept unless
        passed explicitly (``None`` clears it).
        """
        state = self._items.get(connector_id)
        if state is None:
            raise KeyError(connector_id)
        next_configured = state.configured if configured is None else bool(configured)
        next_healthy = state.healthy if healthy is None else bool(healthy)
        next_enabled = state.enabled if enabled is None else bool(enabled)
        if next_enabled and not next_configured:
            raise ValueError('A connector cannot be enabled before it is configured.')
        next_error = state.last_error if last_error is _UNSET else last_error
        if next_error is not None and (not isinstance(next_error, str) or len(next_error) > 1000):
            raise ValueError('last_error must be a string up to 1000 characters.')
        next_metadata = state.metadata
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError('metadata must be a JSON object.')
            try:
                encoded = json.dumps(metadata, separators=(',', ':'), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError('metadata must be a JSON object.') from exc
            if len(encoded) > 20_000:
                raise ValueError('metadata is too large.')
            next_metadata = json.loads(encoded)
        state.configured = next_configured
        state.healthy = next_healthy
        state.enabled = next_enabled
        state.last_error = next_error
        state.metadata = next_metadata
        return state.snapshot()

    def get(self, connector_id: str) -> dict:
        if connector_id not in self._items:
            raise KeyError(connector_id)
        return self._items[connector_id].snapshot()

    def snapshot(self) -> list[dict]:
        return [self._items[key].snapshot() for key in sorted(self._items)]

    def eligible(self, capability: str) -> list[dict]:
        capability_list([capability], 'capability')
        return [item for item in self.snapshot()
                if item['implemented'] and item['enabled'] and item['healthy']
                and capability in item['capabilities']]


SUGGESTED_CONNECTORS = (
    ConnectorSpec('acc-managed', 'ACC Managed Workspace',
                  ('workspace', 'knowledge'),
                  ('artifact.read', 'artifact.write', 'knowledge.read', 'knowledge.write'),
                  implemented=False),
    ConnectorSpec('local-folder', 'Local Folder',
                  ('workspace', 'knowledge'),
                  ('artifact.read', 'artifact.write', 'knowledge.read', 'knowledge.write'),
                  requires_execution_node=True, implemented=False),
    ConnectorSpec('obsidian', 'Obsidian',
                  ('workspace', 'knowledge'),
                  ('knowledge.read', 'knowledge.write', 'knowledge.link'),
                  requires_execution_node=True, implemented=False),
    ConnectorSpec('google-drive', 'Google Drive',
                  ('app', 'workspace', 'knowledge'),
                  ('artifact.read', 'artifact.write', 'docs.read', 'docs.write',
                   'knowledge.read', 'knowledge.write'),
                  implemented=False),
    ConnectorSpec('blender', 'Blender',
                  ('capability',),
                  ('blender.edit', 'blender.render', 'blender.export'),
                  requires_execution_node=True, requires_resource_lease=True, implemented=False),
    ConnectorSpec('unity', 'Unity',
                  ('capability',),
                  ('unity.edit', 'unity.build', 'unity.test'),
                  requires_execution_node=True, requires_resource_lease=True, implemented=False),
    ConnectorSpec('unreal', 'Unreal Engine',
                  ('capability',),
                  ('unreal.edit', 'unreal.build', 'unreal.package'),
                  requires_execution_node=True, requires_resource_lease=True, implemented=False),
)
