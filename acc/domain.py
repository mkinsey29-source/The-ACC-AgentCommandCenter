"""Provider-neutral ACC domain validation and task contract normalization."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .contracts import capability_list, capability_name


RISK_LEVELS = frozenset(('low', 'medium', 'high'))
DATA_CLASSIFICATIONS = frozenset(('public', 'internal', 'confidential'))
WORKSPACE_SCOPES = frozenset(('project', 'isolated_repository'))


def _json_object(value: Any, label: str, limit: int = 100_000) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(label + ' must be a JSON object.')
    encoded = json.dumps(dict(value), separators=(',', ':'), ensure_ascii=False)
    if len(encoded.encode('utf-8')) > limit:
        raise ValueError(label + ' is too large.')
    return json.loads(encoded)


def _string_list(value: Any, label: str, limit: int = 100) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'{label} must be an array of at most {limit} strings.')
    if not all(isinstance(item, str) and item.strip() and '\0' not in item for item in value):
        raise ValueError(label + ' must contain nonempty strings.')
    return list(dict.fromkeys(item.strip() for item in value))


def _capability_field(value: Any, label: str) -> list[str]:
    return list(capability_list(value, label))


def normalize_task_fields(payload: Mapping[str, Any]) -> dict:
    """Normalize the provider-neutral fields shared by coding and non-coding work.

    Existing task callers remain valid because every newly added field has a conservative default.
    """
    area = payload.get('task_area', 'general')
    capability_name(area, 'task_area')

    risk = payload.get('risk', 'medium')
    if risk not in RISK_LEVELS:
        raise ValueError('risk must be low, medium, or high.')

    priority = payload.get('priority', 50)
    if type(priority) is not int or not 0 <= priority <= 100:
        raise ValueError('priority must be an integer from 0 to 100.')

    classification = payload.get('data_classification', 'internal')
    if classification not in DATA_CLASSIFICATIONS:
        raise ValueError('data_classification must be public, internal, or confidential.')

    workspace_scope = payload.get('workspace_scope', 'project')
    if workspace_scope not in WORKSPACE_SCOPES:
        raise ValueError('workspace_scope must be project or isolated_repository.')

    workstream_id = payload.get('workstream_id')
    if workstream_id is not None and (
            not isinstance(workstream_id, str) or not workstream_id.strip()
            or len(workstream_id) > 200 or '\0' in workstream_id):
        raise ValueError('workstream_id must be a nonempty string up to 200 characters.')

    return {
        'task_area': area,
        'workstream_id': workstream_id.strip() if isinstance(workstream_id, str) else None,
        'required_capabilities': _capability_field(
            payload.get('required_capabilities', []), 'required_capabilities'),
        'permissions': _capability_field(payload.get('permissions', []), 'permissions'),
        'expected_artifacts': _capability_field(
            payload.get('expected_artifacts', []), 'expected_artifacts'),
        'resource_requirements': _capability_field(
            payload.get('resource_requirements', []), 'resource_requirements'),
        'inputs': _json_object(payload.get('inputs'), 'inputs'),
        'acceptance_requirements': _string_list(
            payload.get('acceptance_requirements', []), 'acceptance_requirements'),
        'risk': risk,
        'priority': priority,
        'depends_on': _string_list(payload.get('depends_on', []), 'depends_on'),
        'data_classification': classification,
        'workspace_scope': workspace_scope,
    }


def task_contract_view(task: Mapping[str, Any]) -> dict:
    """Return contract fields with defaults for pre-spine persisted tasks."""
    return normalize_task_fields(task)
