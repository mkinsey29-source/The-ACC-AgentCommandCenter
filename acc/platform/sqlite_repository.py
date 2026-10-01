"""SQLite implementation of the durable M09 command and event store.

SQLite's ``BEGIN IMMEDIATE`` obtains the writer reservation before reading command state. This
serializes the revision check, quota reservation, mutation, idempotency result, audit record and
per-project event sequence allocation across repository instances and processes sharing the file.
Command results are retained indefinitely; pruning requires a separately reviewed retry/retention
policy and is intentionally not part of this adapter.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from .commands import (
    CommandConflict, CommandRequest, CommandResult, CommandStateConflict,
    CommandTargetNotFound, IdempotencyConflict, QuotaExceeded,
)
from .events import EventBatch, PlatformEvent


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class SQLiteCommandRepository:
    """Persistent ``PlatformCommandRepository`` and ``PlatformEventSource``.

    The database path must identify a file shared by all service workers. ``:memory:`` is rejected
    because separate processes would not share command claims, revisions or event cursors.
    """

    def __init__(self, database: str | Path, *, clock=time.time, timeout: float = 5.0):
        self.database = str(database)
        if (not self.database.strip() or self.database == ':memory:'
                or self.database.lower().startswith('file:')):
            raise ValueError('SQLite command storage requires a durable database file.')
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.database, timeout=timeout, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = ON')
        self._db.execute('PRAGMA busy_timeout = %d' % max(1, int(timeout * 1000)))
        self._db.execute('PRAGMA journal_mode = WAL')
        self._db.execute('PRAGMA synchronous = FULL')
        self._create_schema()

    def _create_schema(self) -> None:
        self._db.execute('''CREATE TABLE IF NOT EXISTS m09_schema_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton=1),
            version INTEGER NOT NULL)''')
        self._db.execute('INSERT OR IGNORE INTO m09_schema_meta VALUES (1, 1)')
        schema = self._db.execute(
            'SELECT version FROM m09_schema_meta WHERE singleton=1').fetchone()['version']
        if schema != 1:
            raise RuntimeError('Unsupported M09 SQLite schema version.')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS m09_projects (
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 0),
                mode TEXT NOT NULL CHECK (mode IN ('online', 'offline')),
                event_seq INTEGER NOT NULL DEFAULT 0 CHECK (event_seq >= 0),
                PRIMARY KEY (account_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS m09_tasks (
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reserved_json TEXT NOT NULL,
                PRIMARY KEY (account_id, project_id, task_id),
                FOREIGN KEY (account_id, project_id)
                    REFERENCES m09_projects(account_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS m09_workers (
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active', 'paused')),
                PRIMARY KEY (account_id, project_id, worker_id),
                FOREIGN KEY (account_id, project_id)
                    REFERENCES m09_projects(account_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS m09_quota_usage (
                account_id TEXT NOT NULL,
                quota_name TEXT NOT NULL,
                used INTEGER NOT NULL CHECK (used >= 0),
                PRIMARY KEY (account_id, quota_name)
            );
            CREATE TABLE IF NOT EXISTS m09_command_results (
                account_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                revision INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (account_id, operation_id)
            );
            CREATE TABLE IF NOT EXISTS m09_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                revision INTEGER NOT NULL,
                at REAL NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS m09_audit_no_update
                BEFORE UPDATE ON m09_audit
                BEGIN SELECT RAISE(ABORT, 'M09 audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS m09_audit_no_delete
                BEFORE DELETE ON m09_audit
                BEGIN SELECT RAISE(ABORT, 'M09 audit is append-only'); END;
            CREATE TABLE IF NOT EXISTS m09_events (
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                seq INTEGER NOT NULL CHECK (seq > 0),
                kind TEXT NOT NULL,
                at REAL NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY (account_id, project_id, seq),
                FOREIGN KEY (account_id, project_id)
                    REFERENCES m09_projects(account_id, project_id)
            );
        ''')

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _transaction(self):
        """Context manager is defined lazily to keep the commit/rollback path uniform."""
        return _Transaction(self._db)

    # Fixture/bootstrap operations are also transactions so a caller never sees partial rows.
    def put_project(self, account_id: str, project_id: str, *, revision: int = 0,
                    mode: str = 'online') -> None:
        """Provision an initial project row without overwriting durable state."""
        with self._lock, self._transaction():
            self._db.execute('''INSERT INTO m09_projects
                (account_id, project_id, revision, mode, event_seq) VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(account_id, project_id) DO NOTHING''',
                (account_id, project_id, revision, mode))

    def put_worker(self, account_id: str, project_id: str, worker_id: str,
                   *, status: str = 'active') -> None:
        if status not in ('active', 'paused'):
            raise ValueError('invalid worker status')
        with self._lock, self._transaction():
            self._db.execute('''INSERT INTO m09_workers VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, project_id, worker_id)
                DO NOTHING''',
                (account_id, project_id, worker_id, status))

    def set_quota_usage(self, account_id: str, name: str, used: int) -> None:
        if type(used) is not int or used < 0:
            raise ValueError('invalid quota usage')
        with self._lock, self._transaction():
            self._db.execute('''INSERT INTO m09_quota_usage VALUES (?, ?, ?)
                ON CONFLICT(account_id, quota_name) DO UPDATE SET used=excluded.used''',
                (account_id, name, used))

    def execute(self, command: CommandRequest, *, limits: Mapping[str, int | None]) -> CommandResult:
        with self._lock, self._transaction():
            existing = self._db.execute('''SELECT fingerprint, revision, result_json
                FROM m09_command_results WHERE account_id=? AND operation_id=?''',
                (command.account_id, command.operation_id)).fetchone()
            if existing is not None:
                if existing['fingerprint'] != command.fingerprint:
                    raise IdempotencyConflict('Operation ID was already used for another command.')
                return CommandResult(command.operation_id, 'applied', existing['revision'],
                                     json.loads(existing['result_json']), replayed=True)

            project = self._db.execute('''SELECT revision, mode, event_seq FROM m09_projects
                WHERE account_id=? AND project_id=?''',
                (command.account_id, command.project_id)).fetchone()
            if project is None:
                raise CommandTargetNotFound('project')
            if project['revision'] != command.expected_revision:
                raise CommandConflict(project['revision'])

            reservations = {q.name: q.amount for q in command.quotas}
            for name, amount in reservations.items():
                allowed = limits.get(name)
                row = self._db.execute('''SELECT used FROM m09_quota_usage
                    WHERE account_id=? AND quota_name=?''',
                    (command.account_id, name)).fetchone()
                used = row['used'] if row else 0
                if type(allowed) is not int or used + amount > allowed:
                    raise QuotaExceeded('Quota unavailable.')

            outcome, released = self._apply(command, project, reservations)
            for name, amount in reservations.items():
                self._db.execute('''INSERT INTO m09_quota_usage VALUES (?, ?, ?)
                    ON CONFLICT(account_id, quota_name) DO UPDATE
                    SET used=m09_quota_usage.used + excluded.used''',
                    (command.account_id, name, amount))
            for name, amount in released.items():
                self._db.execute('''UPDATE m09_quota_usage SET used=MAX(0, used-?)
                    WHERE account_id=? AND quota_name=?''',
                    (amount, command.account_id, name))

            now = self.clock()
            # Use a time-based high-water mark as well as the stored counter. After a backup
            # restore, the counter may move backwards; a forward-moving wall clock keeps newly
            # appended events beyond cursors observed before the restore. Gaps are valid in M09.
            seq = max(project['event_seq'] + 1, int(now * 1_000_000))
            new_revision = project['revision'] + 1
            changed = self._db.execute('''UPDATE m09_projects
                SET revision=?, mode=?, event_seq=?
                WHERE account_id=? AND project_id=? AND revision=?''',
                (new_revision, outcome.pop('_mode', project['mode']), seq,
                 command.account_id, command.project_id, project['revision']))
            if changed.rowcount != 1:
                # BEGIN IMMEDIATE should make this unreachable, but keep the contract explicit.
                row = self._db.execute('''SELECT revision FROM m09_projects
                    WHERE account_id=? AND project_id=?''',
                    (command.account_id, command.project_id)).fetchone()
                raise CommandConflict(row['revision'] if row else 0)

            result = CommandResult(command.operation_id, 'applied', new_revision, outcome)
            self._db.execute('''INSERT INTO m09_command_results
                (account_id, operation_id, fingerprint, revision, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)''',
                (command.account_id, command.operation_id, command.fingerprint,
                 new_revision, _json(dict(outcome)), now))
            self._db.execute('''INSERT INTO m09_audit
                (operation_id, account_id, project_id, actor_user_id, kind, revision, at)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (command.operation_id, command.account_id, command.project_id,
                 command.actor_user_id, command.kind, new_revision, now))
            event_data = {
                'operation_id': command.operation_id, 'kind': command.kind,
                'actor_user_id': command.actor_user_id, 'revision': new_revision,
                'result': dict(outcome),
            }
            event = PlatformEvent(seq, command.account_id, command.project_id,
                                  'command.applied', now, event_data)
            self._db.execute('''INSERT INTO m09_events
                (account_id, project_id, seq, kind, at, data_json)
                VALUES (?, ?, ?, 'command.applied', ?, ?)''',
                (event.account_id, event.project_id, event.seq, event.at, _json(event.data)))
            return result

    def _apply(self, command, project, reservations):
        kind, payload = command.kind, command.payload
        if kind == 'project.mode':
            if project['mode'] == payload['mode']:
                raise CommandStateConflict('project is already in that mode')
            return {'mode': payload['mode'], '_mode': payload['mode']}, {}
        if kind == 'task.create':
            task_id = payload['task_id']
            if self._db.execute('''SELECT 1 FROM m09_tasks
                WHERE account_id=? AND project_id=? AND task_id=?''',
                (command.account_id, command.project_id, task_id)).fetchone():
                raise CommandStateConflict('task already exists')
            self._db.execute('''INSERT INTO m09_tasks VALUES (?, ?, ?, 'queued', ?)''',
                (command.account_id, command.project_id, task_id, _json(reservations)))
            return {'task_id': task_id, 'status': 'queued'}, {}
        if kind == 'task.cancel':
            task = self._db.execute('''SELECT status, reserved_json FROM m09_tasks
                WHERE account_id=? AND project_id=? AND task_id=?''',
                (command.account_id, command.project_id, payload['task_id'])).fetchone()
            if task is None:
                raise CommandTargetNotFound('task')
            if task['status'] == 'cancelled':
                raise CommandStateConflict('task is already cancelled')
            self._db.execute('''UPDATE m09_tasks SET status='cancelled', reserved_json='{}'
                WHERE account_id=? AND project_id=? AND task_id=?''',
                (command.account_id, command.project_id, payload['task_id']))
            return {'task_id': payload['task_id'], 'status': 'cancelled'}, json.loads(task['reserved_json'])
        if kind in ('worker.pause', 'worker.resume'):
            worker_id = payload['worker_id']
            worker = self._db.execute('''SELECT status FROM m09_workers
                WHERE account_id=? AND project_id=? AND worker_id=?''',
                (command.account_id, command.project_id, worker_id)).fetchone()
            if worker is None:
                raise CommandTargetNotFound('worker')
            target = 'paused' if kind == 'worker.pause' else 'active'
            if worker['status'] == target:
                raise CommandStateConflict('worker is already ' + target)
            self._db.execute('''UPDATE m09_workers SET status=?
                WHERE account_id=? AND project_id=? AND worker_id=?''',
                (target, command.account_id, command.project_id, worker_id))
            return {'worker_id': worker_id, 'status': target}, {}
        raise CommandStateConflict('unsupported command')

    def project_revision(self, account_id: str, project_id: str) -> int:
        with self._lock:
            row = self._db.execute('''SELECT revision FROM m09_projects
            WHERE account_id=? AND project_id=?''', (account_id, project_id)).fetchone()
        if row is None:
            raise CommandTargetNotFound('project')
        return row['revision']

    def project(self, account_id: str, project_id: str) -> dict[str, Any]:
        with self._lock, _ReadTransaction(self._db):
            row = self._db.execute('''SELECT revision, mode FROM m09_projects
                WHERE account_id=? AND project_id=?''', (account_id, project_id)).fetchone()
            if row is None:
                raise CommandTargetNotFound('project')
            tasks = self._db.execute('''SELECT task_id, status, reserved_json FROM m09_tasks
                WHERE account_id=? AND project_id=? ORDER BY task_id''',
                (account_id, project_id)).fetchall()
            workers = self._db.execute('''SELECT worker_id, status FROM m09_workers
                WHERE account_id=? AND project_id=? ORDER BY worker_id''',
                (account_id, project_id)).fetchall()
            return {
                'revision': row['revision'], 'mode': row['mode'],
                'tasks': {t['task_id']: {'id': t['task_id'], 'status': t['status'],
                                         'reserved': json.loads(t['reserved_json'])} for t in tasks},
                'workers': {w['worker_id']: {'id': w['worker_id'], 'status': w['status']}
                            for w in workers},
            }

    def quota_used(self, account_id: str, name: str) -> int:
        with self._lock:
            row = self._db.execute('''SELECT used FROM m09_quota_usage
            WHERE account_id=? AND quota_name=?''', (account_id, name)).fetchone()
        return row['used'] if row else 0

    def audit(self):
        with self._lock:
            rows = self._db.execute('''SELECT operation_id, account_id, project_id,
                actor_user_id, kind, revision, at FROM m09_audit ORDER BY audit_id''').fetchall()
            return tuple(dict(row) for row in rows)

    def read_events(self, account_id: str, project_id: str, *, after: int, limit: int = 200):
        with self._lock, _ReadTransaction(self._db):
            project = self._db.execute('''SELECT event_seq FROM m09_projects
                WHERE account_id=? AND project_id=?''', (account_id, project_id)).fetchone()
            if project is not None and after > project['event_seq']:
                # A cursor ahead of the durable head can happen after restoring an older backup.
                # Ask the client to refetch state from this stream head before reconnecting.
                return EventBatch((), project['event_seq'], reset_required=True,
                                  oldest_available=project['event_seq'] + 1)
            rows = self._db.execute('''SELECT seq, kind, at, data_json FROM m09_events
                WHERE account_id=? AND project_id=? AND seq>? ORDER BY seq LIMIT ?''',
                (account_id, project_id, after, limit + 1)).fetchall()
            has_more = len(rows) > limit
            events = tuple(PlatformEvent(
                row['seq'], account_id, project_id, row['kind'], row['at'],
                json.loads(row['data_json'])) for row in rows[:limit])
            return EventBatch(events, events[-1].seq if events else after, has_more)


class _ReadTransaction:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute('BEGIN')
        return self.db

    def __exit__(self, error_type, error, traceback):
        return _finish_transaction(self.db, error_type)


class _Transaction:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute('BEGIN IMMEDIATE')
        return self.db

    def __exit__(self, error_type, error, traceback):
        return _finish_transaction(self.db, error_type)


def _finish_transaction(db: sqlite3.Connection, error_type) -> bool:
    if error_type is None:
        try:
            db.execute('COMMIT')
        except BaseException:
            # SQLite can leave a transaction active when COMMIT fails (for example,
            # SQLITE_BUSY or an I/O error). Release any lock but preserve the commit error.
            try:
                db.execute('ROLLBACK')
            except BaseException:
                pass
            raise
    else:
        try:
            db.execute('ROLLBACK')
        except BaseException:
            # Returning False preserves the original exception from the with block.
            pass
    return False
