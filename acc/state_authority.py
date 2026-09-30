"""ACC state-authority and synchronization contract.

This module defines policy only. It deliberately does not pretend the current local prototype
already has a hosted synchronization service.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping


STATE_AUTHORITY_VERSION = 1

# Shared cloud-connected state is platform-authoritative. Local-only secrets and machine resource
# facts remain node-authoritative. The Desktop keeps durable replicas for offline continuity.
STATE_POLICIES = {
    # Nodes may cache account/entitlement state but never mutate it offline.
    'account': ('platform', 'read_replica'),
    'project': ('platform', 'offline_queue'),
    'workstream': ('platform', 'offline_queue'),
    'task': ('platform', 'offline_queue'),
    'run': ('platform', 'offline_queue'),
    'decision': ('platform', 'offline_queue'),
    'review': ('platform', 'offline_queue'),
    'artifact_metadata': ('platform', 'offline_queue'),
    'knowledge_metadata': ('platform', 'offline_queue'),
    'usage': ('platform', 'offline_queue'),
    'event': ('platform', 'append_only'),
    # Writer/branch/resource leases are granted online only. An offline node may keep working under
    # a lease it already holds until expiry, but cannot acquire, renew, or transfer one offline;
    # queued lease grants could otherwise produce two writers after reconnect.
    'lease': ('platform', 'online_only'),
    'execution_node': ('node', 'status_only'),
    'local_resource': ('node', 'status_only'),
    # A credential stays with the component that uses it: the node's OS store for local tools, the
    # platform's secret store for platform-run connectors (e.g. a zero-install user's cloud mail
    # grant). It is never copied between platform and nodes by the sync protocol.
    'credential': ('holder', 'never'),
}

CONFLICT_POLICY = {
    'entity_mutation': 'optimistic_revision',
    'event': 'deduplicate_operation_id',
    'credential': 'never_replicate',
    'local_resource': 'node_truth',
    'lease': 'online_grant_only',
}


def policy_for(kind: str) -> dict:
    if kind not in STATE_POLICIES:
        raise ValueError('Unknown ACC state kind.')
    authority, sync = STATE_POLICIES[kind]
    return {'kind': kind, 'authority': authority, 'sync': sync}


def policy_snapshot() -> dict:
    return {
        'version': STATE_AUTHORITY_VERSION,
        'connected_system_of_record': 'platform',
        'local_only_system_of_record': 'local_store',
        'desktop_offline': 'durable_replica_with_queued_mutations',
        'policies': [policy_for(kind) for kind in STATE_POLICIES],
        'conflicts': dict(CONFLICT_POLICY),
    }


def sync_envelope(kind: str, entity_id: str, base_revision: int,
                  mutation: Mapping[str, Any], operation_id: str | None = None) -> dict:
    """Build an idempotent optimistic-concurrency envelope for a future sync transport."""
    policy = policy_for(kind)
    if policy['sync'] in ('never', 'status_only', 'read_replica', 'online_only'):
        raise ValueError(kind + ' is not eligible for replicated mutation envelopes.')
    if not isinstance(entity_id, str) or not entity_id.strip() or len(entity_id) > 200:
        raise ValueError('entity_id must be a nonempty string up to 200 characters.')
    if type(base_revision) is not int or base_revision < 0:
        raise ValueError('base_revision must be a nonnegative integer.')
    if not isinstance(mutation, Mapping):
        raise ValueError('mutation must be a JSON object.')
    encoded = json.dumps(dict(mutation), separators=(',', ':'), ensure_ascii=False)
    if len(encoded.encode('utf-8')) > 100_000:
        raise ValueError('mutation is too large.')
    op = uuid.uuid4().hex if operation_id is None else operation_id
    if not isinstance(op, str) or not 1 <= len(op) <= 200 or '\0' in op:
        raise ValueError('operation_id must contain 1-200 characters.')
    return {
        'contract_version': STATE_AUTHORITY_VERSION,
        'operation_id': op,
        'kind': kind,
        'entity_id': entity_id.strip(),
        'base_revision': base_revision,
        'mutation': json.loads(encoded),
    }
