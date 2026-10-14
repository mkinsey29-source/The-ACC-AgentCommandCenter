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
import hashlib
import json
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from .knowledge import checkin_promotes

TTL_SECONDS = 600
KEEP_SECONDS = 3600
MAX_PENDING = 20
SUMMARY_LIMIT = 300


class ApprovalError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class StaleApproval(ApprovalError):
    def __init__(self, message: str = 'What this request would act on has changed since it was made. Ask again.'):
        super().__init__(message, 409)


@dataclass(frozen=True)
class Gate:
    kind: str
    summary: str
    details: dict
    # What the owner is consenting to, fixed when the request is made. ``bind`` joins the retry fingerprint, so a
    # changed target is a new request; ``check`` re-reads the live state at approval time and raises StaleApproval
    # if it no longer matches. With ``atomic`` the check and the action run under the coordinator lock together.
    bind: dict = field(default_factory=dict)
    check: Callable | None = None
    atomic: bool = False


def visible(text) -> str:
    """Escape what a reader cannot see (controls, bidi and other format characters, odd spaces) as \\uXXXX."""
    out = []
    for ch in str(text):
        category = unicodedata.category(ch)
        if category in ('Cc', 'Cf', 'Zl', 'Zp', 'Cs', 'Co', 'Cn') or (category == 'Zs' and ch != ' '):
            out.append(f'\\u{ord(ch):04x}' if ord(ch) <= 0xFFFF else f'\\U{ord(ch):08x}')
        else:
            out.append(ch)
    return ''.join(out)


def argv_display(argv) -> str:
    """The exact argument list as a JSON array: every boundary and quote is visible, invisible characters are escaped."""
    return visible(json.dumps([str(a) for a in argv], ensure_ascii=False))


def redact_url(url) -> str:
    """A push destination without any credentials embedded in it."""
    return re.sub(r'(?<=//)[^/@\s]*@', '', str(url))


def _clip(value, limit=SUMMARY_LIMIT) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _task_title(coordinator, task_id) -> str:
    try:
        return coordinator.store.get(task_id)['title']
    except Exception:
        return str(task_id)


def _items(payload) -> list[dict]:
    items = payload.get('completed_knowledge')
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def effective_status(payload: dict):
    from .knowledge import effective_checkin_status
    return effective_checkin_status(payload)


def _command_fingerprint(coordinator, task_id) -> str:
    """Everything that decides what a start would execute: the task revision, its worker, its argv and that worker's config."""
    task = coordinator.store.get(task_id)
    agent = task.get('agent')
    material = {'task': task_id, 'revision': task.get('revision'), 'agent': agent, 'argv': task.get('argv'),
                'timeout': task.get('timeout_seconds'), 'config': getattr(coordinator, 'agents', {}).get(agent)}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


PREVIEW_FIELDS = ('id', 'head', 'snapshot_id', 'revision', 'repository', 'branch', 'base', 'remote', 'push_url', 'expected_tree')


def _saved_preview(coordinator, task_id):
    try:
        return coordinator.conversation.meta('publish_preview_' + str(task_id))
    except Exception:
        return None


def _publish_gate(coordinator, task_id, payload) -> Gate:
    title = _task_title(coordinator, task_id)
    preview_id = payload.get('preview_id')
    saved = _saved_preview(coordinator, task_id)
    if not isinstance(saved, dict) or not isinstance(preview_id, str) or saved.get('id') != preview_id:
        return Gate('github.publish', f'Push the branch for task “{_clip(title, 120)}” to GitHub (no current preview matches this request)',
                    {'task': task_id, 'title': _clip(title, 200), 'preview': 'No saved preview matches this request; publication '
                     'is refused unless a matching preview exists when you approve.'},
                    bind={'task': task_id, 'preview_id': preview_id if isinstance(preview_id, str) else None, 'matched': False},
                    check=lambda c, tid=task_id, pid=preview_id: _check_preview(c, tid, pid, None))
    bound = {k: saved.get(k) for k in PREVIEW_FIELDS}
    shown = {'task': task_id, 'title': _clip(title, 200), 'preview_id': saved['id'], 'repository': _clip(saved.get('repository'), 200),
             'branch': _clip(saved.get('branch'), 200), 'base': _clip(saved.get('base'), 200), 'remote': _clip(saved.get('remote'), 100),
             'push_destination': _clip(redact_url(saved.get('push_url', '')), 300), 'head': _clip(saved.get('head'), 80),
             'outgoing_commits': len(saved.get('outgoing_commits') or []), 'changed_paths': [_clip(x, 200) for x in (saved.get('changed_paths') or [])[:50]]}
    return Gate('github.publish', f'Push branch {_clip(saved.get("branch"), 80)} of task “{_clip(title, 100)}” to '
                f'{_clip(saved.get("repository"), 100)} (base {_clip(saved.get("base"), 60)}) and create or reuse its pull request',
                shown, bind={'task': task_id, **bound, 'matched': True},
                check=lambda c, tid=task_id, pid=saved['id'], b=bound: _check_preview(c, tid, pid, b))


def _check_preview(coordinator, task_id, preview_id, bound):
    current = _saved_preview(coordinator, task_id)
    if bound is None or not isinstance(current, dict) or current.get('id') != preview_id \
            or any(current.get(k) != bound.get(k) for k in PREVIEW_FIELDS):
        raise StaleApproval('The publication preview changed or is missing since this request was made. Preview again and ask again.')


def _start_gate(coordinator, task_id) -> Gate | None:
    try:
        task = coordinator.store.get(task_id)
    except Exception:
        return None  # an unknown task fails in the normal path
    if task.get('agent') != 'local-command':
        return None
    argv = [str(a) for a in task.get('argv') or []]
    shown = argv_display(argv)
    fingerprint = _command_fingerprint(coordinator, task_id)

    def check(c, tid=task_id, expected=fingerprint):
        if _command_fingerprint(c, tid) != expected:
            raise StaleApproval('This task’s command, worker or revision changed after the request was made. Ask again.')
    return Gate('command.start', f'Run this local command now (task “{_clip(task.get("title", ""), 120)}”): {_clip(shown)}',
                {'task': task_id, 'title': _clip(task.get('title', ''), 200), 'revision': task.get('revision'),
                 'agent': task.get('agent'), 'argv': argv, 'argv_json': shown, 'full_command': shown},
                bind={'task': task_id, 'revision': task.get('revision'), 'agent': task.get('agent'), 'fingerprint': fingerprint},
                check=check, atomic=True)


def classify(coordinator, parts: list[str], payload: dict) -> Gate | None:
    """Return the gate a gated caller must pass for this mutating call, or None if it needs none."""
    n = len(parts)
    if parts == ['api', 'tasks']:
        argv = payload.get('argv')
        if isinstance(argv, list) and argv:
            shown = argv_display(argv)
            return Gate('command.create', f'Create a task that runs this local command: {_clip(shown)}',
                        {'title': _clip(payload.get('title', ''), 200), 'argv': [str(a) for a in argv],
                         'argv_json': shown, 'full_command': shown})
    elif parts == ['api', 'archive', 'export']:
        return Gate('files.export', f"Write a task archive and database backup into {_clip(payload.get('directory', ''))}",
                    {'directory': _clip(payload.get('directory', ''))})
    elif parts == ['api', 'github', 'configure']:
        shown = {k: _clip(v, 100) for k, v in payload.items() if k in ('remote', 'source', 'base', 'enabled', 'interval')}
        return Gate('github.configure', 'Change GitHub settings, which decide where reviewed work is pushed: '
                    + (', '.join(f'{k}={v}' for k, v in shown.items()) or 'no fields given'), shown)
    elif n == 4 and parts[:2] == ['api', 'tasks'] and parts[3] == 'publish':
        return _publish_gate(coordinator, parts[2], payload)
    elif n == 4 and parts[:2] == ['api', 'tasks'] and parts[3] == 'start':
        return _start_gate(coordinator, parts[2])
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
    elif parts == ['api', 'knowledge', 'checkin'] and checkin_promotes(payload):
        return Gate('knowledge.checkin', f"Record a check-in that establishes knowledge (its status is {_clip(effective_status(payload), 40)}"
                    f"{', with items marked verified' if any(i.get('status') == 'verified' for i in _items(payload)) else ''}): "
                    f"{_clip(payload.get('title', ''), 150)}",
                    {'title': _clip(payload.get('title', ''), 200), 'status': _clip(effective_status(payload), 40),
                     'verified_items': [_clip(i.get('title', ''), 200) for i in _items(payload) if i.get('status') == 'verified']})
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
            bound = json.dumps(gate.bind, sort_keys=True, default=str)
            fingerprint = (principal_key, gate.kind, tuple(parts), json.dumps(payload, sort_keys=True, default=str), bound)
            for item in self._items.values():
                if item['status'] == 'pending' and item['fingerprint'] == fingerprint:
                    return self.view(item)  # a retry never floods Attention
            if gate.bind:  # the target changed: an older request for the same action must not stay approvable
                for item in self._items.values():
                    if (item['status'] == 'pending' and item['fingerprint'][:4] == fingerprint[:4]
                            and item['fingerprint'][4] != bound):
                        item['status'], item['resolved_at'] = 'superseded', self.clock()
                        self._event('approval_superseded', item, f"Replaced by a newer request: {item['summary']}")
            if sum(1 for i in self._items.values() if i['status'] == 'pending') >= self.max_pending:
                raise ApprovalError('Too many requests are waiting for approval. Ask the owner to answer them first.', 429)
            now = self.clock()
            item = {'id': secrets.token_hex(8), 'kind': gate.kind, 'summary': gate.summary, 'details': gate.details,
                    'requested_by': principal_label, 'status': 'pending', 'created': now, 'expires': now + self.ttl,
                    'resolved_at': None, 'result': None, 'fingerprint': fingerprint,
                    'parts': list(parts), 'payload': copy.deepcopy(payload), 'check': gate.check, 'atomic': gate.atomic}
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
        check = item.get('check')
        try:
            if check and item.get('atomic'):
                # Revalidate and act under one hold of the coordinator lock, so the task cannot change in between.
                with self.coordinator.lock:
                    check(self.coordinator)
                    status, body, _close = self.execute(parts, payload)
            else:
                if check:
                    check(self.coordinator)
                status, body, _close = self.execute(parts, payload)  # outside the lock: it can take a while
        except StaleApproval as exc:
            with self._lock:
                item['status'], item['resolved_at'] = 'stale', self.clock()
                item['result'] = {'status': 409, 'body': {'error': str(exc)}}
                self._event('approval_stale', item, f"Not run, the target changed: {item['summary']}")
            raise
        with self._lock:
            item['result'] = {'status': status, 'body': body}
            item['status'] = 'executed' if status < 400 else 'failed'
            self._event('approval_executed' if status < 400 else 'approval_failed', item,
                        f"{'Done' if status < 400 else 'Failed'}: {item['summary']}")
            return self.view(item)
