"""Permission gates for remote-driven callers.

Three kinds of caller reach the API. The owner at this computer (local control token) is never gated. A
paired device (cookie) and the remote-driven orchestrator (the separate agent token, used by the MCP
bridge) are gated: a small set of operations that run commands, write outside the project, push to
GitHub, retarget those pushes, or accept shared memory does not run. Instead it becomes an approval
request that appears in Attention. The owner approves or denies at the computer; approval runs exactly
the request that was stored, once. A gated caller can never approve, deny, or change this policy.

Everything else, including starting configured AI workers, is not gated: that is the product, and it is
bounded by worktrees, snapshots and review. See docs/REMOTE-ACCESS.md.
"""
from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

TTL_SECONDS = 600
KEEP_SECONDS = 3600
MAX_PENDING = 20
SUMMARY_LIMIT = 300


class ApprovalError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Gate:
    kind: str
    summary: str
    details: dict


def _clip(value, limit=SUMMARY_LIMIT) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _task_title(coordinator, task_id) -> str:
    try:
        return coordinator.store.get(task_id)['title']
    except Exception:
        return str(task_id)


def classify(coordinator, parts: list[str], payload: dict) -> Gate | None:
    """Return the gate a gated caller must pass for this mutating call, or None if it needs none."""
    n = len(parts)
    if parts == ['api', 'tasks']:
        argv = payload.get('argv')
        if isinstance(argv, list) and argv:
            command = ' '.join(str(a) for a in argv)
            return Gate('command.create', f'Create a task that runs this local command: {_clip(command)}',
                        {'title': _clip(payload.get('title', ''), 200), 'argv': [_clip(a, 200) for a in argv[:20]]})
    elif parts == ['api', 'archive', 'export']:
        return Gate('files.export', f"Write a task archive and database backup into {_clip(payload.get('directory', ''))}",
                    {'directory': _clip(payload.get('directory', ''))})
    elif parts == ['api', 'github', 'configure']:
        shown = {k: _clip(v, 100) for k, v in payload.items() if k in ('remote', 'source', 'base', 'enabled', 'interval')}
        return Gate('github.configure', 'Change GitHub settings, which decide where reviewed work is pushed: '
                    + (', '.join(f'{k}={v}' for k, v in shown.items()) or 'no fields given'), shown)
    elif n == 4 and parts[:2] == ['api', 'tasks'] and parts[3] == 'publish':
        title = _task_title(coordinator, parts[2])
        return Gate('github.publish', f'Push the branch for task “{_clip(title, 120)}” to GitHub and create or reuse its pull request',
                    {'task': parts[2], 'title': _clip(title, 200)})
    elif n == 4 and parts[:2] == ['api', 'tasks'] and parts[3] == 'recover':
        title = _task_title(coordinator, parts[2])
        return Gate('task.recover', f'Confirm the previous worker’s process tree for task “{_clip(title, 120)}” was inspected, and release it',
                    {'task': parts[2], 'title': _clip(title, 200)})
    elif parts == ['api', 'knowledge', 'transition']:
        return Gate('knowledge.transition', f"Rewrite vault note {_clip(payload.get('path', ''), 150)} with status {_clip(payload.get('status', ''), 40)}",
                    {'path': _clip(payload.get('path', ''), 200), 'status': _clip(payload.get('status', ''), 40)})
    elif parts == ['api', 'knowledge', 'rebuttal']:
        return Gate('knowledge.rebuttal', f"Mark vault note {_clip(payload.get('original_path', ''), 150)} as "
                    f"{_clip(payload.get('original_status', 'disproven'), 40)} and add a verified correction",
                    {'path': _clip(payload.get('original_path', ''), 200), 'title': _clip(payload.get('title', ''), 200)})
    elif parts == ['api', 'knowledge', 'note'] and payload.get('status') in ('verified', 'active'):
        return Gate('knowledge.note', f"Add a vault note already marked {payload['status']}: {_clip(payload.get('title', ''), 150)}",
                    {'title': _clip(payload.get('title', ''), 200), 'status': payload['status']})
    elif n == 4 and parts[:2] == ['api', 'memory'] and parts[3] == 'review' and payload.get('status') == 'active':
        return Gate('memory.accept', f'Accept shared project memory {_clip(parts[2], 60)} so every agent treats it as established',
                    {'memory': _clip(parts[2], 60)})
    return None


class Approvals:
    """Pending and answered approval requests. In memory: a restart drops them and the caller asks again."""

    def __init__(self, coordinator, execute: Callable[[list[str], dict], tuple[int, object, bool]], *,
                 clock=time.time, ttl: int = TTL_SECONDS, max_pending: int = MAX_PENDING):
        self.coordinator, self.execute, self.clock = coordinator, execute, clock
        self.ttl, self.max_pending = ttl, max_pending
        self._items: dict[str, dict] = {}
        self._lock = threading.RLock()

    # --- bookkeeping ------------------------------------------------------------------------
    def _event(self, kind: str, item: dict, message: str) -> None:
        try:
            self.coordinator.store.event(kind, {'message': message, 'approval_id': item['id'],
                                                'gate': item['kind'], 'requested_by': item['requested_by']})
        except Exception:
            pass  # an audit-event failure must not decide an approval

    def _expire(self) -> None:
        now = self.clock()
        for item in self._items.values():
            if item['status'] == 'pending' and item['expires'] <= now:
                item['status'] = 'expired'
                item['resolved_at'] = now
                self._event('approval_expired', item, f"Approval request expired: {item['summary']}")
        for key in [k for k, v in self._items.items()
                    if v['status'] != 'pending' and v['resolved_at'] is not None and now - v['resolved_at'] > KEEP_SECONDS]:
            del self._items[key]

    @staticmethod
    def view(item: dict) -> dict:
        shown = {k: item[k] for k in ('id', 'kind', 'summary', 'details', 'requested_by', 'status', 'created', 'expires')}
        if item.get('result') is not None:
            shown['result'] = item['result']
        return shown

    # --- gated caller ------------------------------------------------------------------------
    def request(self, principal_key: str, principal_label: str, gate: Gate, parts: list[str], payload: dict) -> dict:
        with self._lock:
            self._expire()
            fingerprint = (principal_key, gate.kind, tuple(parts), json.dumps(payload, sort_keys=True, default=str))
            for item in self._items.values():
                if item['status'] == 'pending' and item['fingerprint'] == fingerprint:
                    return self.view(item)  # a retry never floods Attention
            if sum(1 for i in self._items.values() if i['status'] == 'pending') >= self.max_pending:
                raise ApprovalError('Too many requests are waiting for approval. Ask the owner to answer them first.', 429)
            now = self.clock()
            item = {'id': secrets.token_hex(8), 'kind': gate.kind, 'summary': gate.summary, 'details': gate.details,
                    'requested_by': principal_label, 'status': 'pending', 'created': now, 'expires': now + self.ttl,
                    'resolved_at': None, 'result': None, 'fingerprint': fingerprint,
                    'parts': list(parts), 'payload': copy.deepcopy(payload)}
            self._items[item['id']] = item
            self._event('approval_requested', item, f'{principal_label} needs approval: {gate.summary}')
            return self.view(item)

    def get(self, approval_id) -> dict | None:
        with self._lock:
            self._expire()
            item = self._items.get(approval_id) if isinstance(approval_id, str) else None
            return self.view(item) if item else None

    def pending(self) -> list[dict]:
        with self._lock:
            self._expire()
            return [self.view(i) for i in sorted(self._items.values(), key=lambda i: i['created']) if i['status'] == 'pending']

    # --- the owner ----------------------------------------------------------------------------
    def resolve(self, approval_id, approve: bool) -> dict:
        """Owner only (the caller enforces it). Approving runs the stored request once, as the owner."""
        with self._lock:
            self._expire()
            item = self._items.get(approval_id) if isinstance(approval_id, str) else None
            if item is None:
                raise ApprovalError('Unknown approval request.', 404)
            if item['status'] != 'pending':
                raise ApprovalError('This request was already answered or has expired.', 409)
            item['status'] = 'approved' if approve else 'denied'  # claimed: no second approval can run it
            item['resolved_at'] = self.clock()
            parts, payload = item['parts'], item['payload']
            if not approve:
                self._event('approval_denied', item, f"Denied: {item['summary']}")
                return self.view(item)
            self._event('approval_approved', item, f"Approved: {item['summary']}")
        status, body, _close = self.execute(parts, payload)  # outside the lock: it can take a while
        with self._lock:
            item['result'] = {'status': status, 'body': body}
            item['status'] = 'executed' if status < 400 else 'failed'
            self._event('approval_executed' if status < 400 else 'approval_failed', item,
                        f"{'Done' if status < 400 else 'Failed'}: {item['summary']}")
            return self.view(item)
