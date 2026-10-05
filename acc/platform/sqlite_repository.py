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

from ..auth.models import _bounded_id
from . import command_rules as rules
from .commands import CommandConflict, CommandRequest, CommandResult, CommandTargetNotFound
from .events import EventBatch, PlatformEvent
from .repository import AccountProjectView, ProjectSnapshot


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class _SQLiteProjectState:
    """``CommandState`` read from the open command transaction."""

    def __init__(self, db, account_id, project_id, mode):
        self._db, self._account_id, self._project_id, self._mode = db, account_id, project_id, mode

    def mode(self):
        return self._mode

    def task(self, task_id):
        row = self._db.execute('''SELECT status, reserved_json FROM m09_tasks
            WHERE account_id=? AND project_id=? AND task_id=?''',
            (self._account_id, self._project_id, task_id)).fetchone()
        if row is None:
            return None
        return {'status': row['status'], 'reserved': json.loads(row['reserved_json'])}

    def worker_status(self, worker_id):
        row = self._db.execute('''SELECT status FROM m09_workers
            WHERE account_id=? AND project_id=? AND worker_id=?''',
            (self._account_id, self._project_id, worker_id)).fetchone()
        return None if row is None else row['status']


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
        try:
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA foreign_keys = ON')
            self._db.execute('PRAGMA busy_timeout = %d' % max(1, int(timeout * 1000)))
            self._enable_wal(timeout)
            self._db.execute('PRAGMA synchronous = FULL')
            self._create_schema()
        except BaseException:
            self._db.close()
            raise

    def _enable_wal(self, timeout: float) -> None:
        # Switching a rollback-journal file to WAL needs an exclusive lock and SQLite does not apply
        # busy_timeout to it, so workers opening the same file concurrently retry until the timeout.
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                if self._db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                    self._db.execute('PRAGMA journal_mode = WAL')
                return
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def _create_schema(self) -> None:
        self._db.execute('''CREATE TABLE IF NOT EXISTS m09_schema_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton=1),
            version INTEGER NOT NULL)''')
        row = self._db.execute('SELECT version FROM m09_schema_meta WHERE singleton=1').fetchone()
        if row is None:
            self._db.execute('INSERT OR IGNORE INTO m09_schema_meta VALUES (1, 2)')
            row = self._db.execute(
                'SELECT version FROM m09_schema_meta WHERE singleton=1').fetchone()
        schema = row['version']
        if schema not in (1, 2):
            raise RuntimeError('Unsupported M09 SQLite schema version.')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS m09_projects (
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
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
        if self._needs_v2_migration():
            # Re-check under the writer reservation: another worker may have migrated the file
            # between the unlocked check and BEGIN IMMEDIATE.
            with self._transaction() as db:
                if self._needs_v2_migration():
                    columns = {row['name'] for row in db.execute('PRAGMA table_info(m09_projects)')}
                    if 'name' not in columns:
                        db.execute("ALTER TABLE m09_projects ADD COLUMN name TEXT NOT NULL DEFAULT ''")
                    db.execute("UPDATE m09_projects SET name=project_id WHERE name=''")
                    db.execute('UPDATE m09_schema_meta SET version=2 WHERE singleton=1')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS m09_attention (
                account_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                attention_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (account_id, project_id, attention_id),
                FOREIGN KEY (account_id, project_id)
                    REFERENCES m09_projects(account_id, project_id)
            );
        ''')

    def _needs_v2_migration(self) -> bool:
        version = self._db.execute(
            'SELECT version FROM m09_schema_meta WHERE singleton=1').fetchone()['version']
        columns = {row['name'] for row in self._db.execute('PRAGMA table_info(m09_projects)')}
        return version != 2 or 'name' not in columns

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _transaction(self):
        """Context manager is defined lazily to keep the commit/rollback path uniform."""
        return _Transaction(self._db)

    # Fixture/bootstrap operations are also transactions so a caller never sees partial rows.
    def put_project(self, account_id: str, project_id: str, *, revision: int = 0,
                    mode: str = 'online', name: str | None = None) -> None:
        """Provision an initial project row without overwriting durable state."""
        project = AccountProjectView(
            account_id, project_id, project_id if name is None else name, mode, revision)
        with self._lock, self._transaction():
            self._db.execute('''INSERT INTO m09_projects
                (account_id, project_id, name, revision, mode, event_seq) VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(account_id, project_id) DO NOTHING''',
                (project.account_id, project.project_id, project.name,
                 project.revision, project.mode))

    def put_attention(self, account_id: str, project_id: str, attention_id: str,
                      record: Mapping[str, Any]) -> None:
        """Persist one account/project-scoped attention projection record."""
        account_id = _bounded_id(account_id, 'account_id')
        project_id = _bounded_id(project_id, 'project_id')
        attention_id = _bounded_id(attention_id, 'attention_id')
        if not isinstance(record, Mapping):
            raise ValueError('attention record must be an object')
        value = dict(record)
        if value.get('account_id') != account_id or value.get('project_id') != project_id:
            raise ValueError('attention record scope does not match its key')
        if value.get('id') != attention_id:
            raise ValueError('attention record id does not match its key')
        with self._lock, self._transaction():
            self._db.execute('''INSERT INTO m09_attention VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, project_id, attention_id)
                DO UPDATE SET record_json=excluded.record_json''',
                (account_id, project_id, attention_id, _json(value)))

    def delete_attention(self, account_id: str, project_id: str, attention_id: str) -> bool:
        account_id = _bounded_id(account_id, 'account_id')
        project_id = _bounded_id(project_id, 'project_id')
        attention_id = _bounded_id(attention_id, 'attention_id')
        with self._lock, self._transaction():
            result = self._db.execute('''DELETE FROM m09_attention
                WHERE account_id=? AND project_id=? AND attention_id=?''',
                (account_id, project_id, attention_id))
            return result.rowcount == 1

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
        account_id, project_id = command.account_id, command.project_id
        with self._lock, self._transaction():
            existing = self._db.execute('''SELECT fingerprint, revision, result_json
                FROM m09_command_results WHERE account_id=? AND operation_id=?''',
                (account_id, command.operation_id)).fetchone()
            if existing is not None:
                return rules.replay(command, existing['fingerprint'], existing['revision'],
                                    json.loads(existing['result_json']))

            project = self._db.execute('''SELECT revision, mode, event_seq FROM m09_projects
                WHERE account_id=? AND project_id=?''', (account_id, project_id)).fetchone()
            change = rules.decide(
                command,
                revision=None if project is None else project['revision'],
                state=_SQLiteProjectState(self._db, account_id, project_id,
                                          None if project is None else project['mode']),
                quota_used=lambda name: self._quota_used(account_id, name),
                limits=limits)

            if change.task is not None:
                if change.task.created:
                    self._db.execute('''INSERT INTO m09_tasks VALUES (?, ?, ?, ?, ?)''',
                        (account_id, project_id, change.task.task_id, change.task.status,
                         _json(dict(change.task.reserved))))
                else:
                    self._db.execute('''UPDATE m09_tasks SET status=?, reserved_json=?
                        WHERE account_id=? AND project_id=? AND task_id=?''',
                        (change.task.status, _json(dict(change.task.reserved)),
                         account_id, project_id, change.task.task_id))
            if change.worker is not None:
                self._db.execute('''UPDATE m09_workers SET status=?
                    WHERE account_id=? AND project_id=? AND worker_id=?''',
                    (change.worker.status, account_id, project_id, change.worker.worker_id))
            for name, amount in change.reserve.items():
                self._db.execute('''INSERT INTO m09_quota_usage VALUES (?, ?, ?)
                    ON CONFLICT(account_id, quota_name) DO UPDATE
                    SET used=m09_quota_usage.used + excluded.used''',
                    (account_id, name, amount))
            for name, amount in change.release.items():
                self._db.execute('''UPDATE m09_quota_usage SET used=?
                    WHERE account_id=? AND quota_name=?''',
                    (rules.released_usage(self._quota_used(account_id, name), amount),
                     account_id, name))

            now = self.clock()
            # Use a time-based high-water mark as well as the stored counter. After a backup
            # restore, the counter may move backwards; a forward-moving wall clock keeps newly
            # appended events beyond cursors observed before the restore. Gaps are valid in M09.
            seq = max(project['event_seq'] + 1, int(now * 1_000_000))
            changed = self._db.execute('''UPDATE m09_projects
                SET revision=?, mode=?, event_seq=?
                WHERE account_id=? AND project_id=? AND revision=?''',
                (change.revision, change.mode or project['mode'], seq,
                 account_id, project_id, project['revision']))
            if changed.rowcount != 1:
                # BEGIN IMMEDIATE should make this unreachable, but keep the contract explicit.
                row = self._db.execute('''SELECT revision FROM m09_projects
                    WHERE account_id=? AND project_id=?''', (account_id, project_id)).fetchone()
                raise CommandConflict(row['revision'] if row else 0)

            result = CommandResult(command.operation_id, 'applied', change.revision, change.result)
            self._db.execute('''INSERT INTO m09_command_results
                (account_id, operation_id, fingerprint, revision, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)''',
                (account_id, command.operation_id, command.fingerprint,
                 change.revision, _json(change.result), now))
            audit = rules.audit_record(command, change.revision, now)
            self._db.execute('''INSERT INTO m09_audit
                (operation_id, account_id, project_id, actor_user_id, kind, revision, at)
                VALUES (:operation_id, :account_id, :project_id, :actor_user_id, :kind,
                        :revision, :at)''', audit)
            # Building the event validates the sequence and clock value before it is stored.
            event = PlatformEvent(seq, account_id, project_id, 'command.applied', now,
                                  rules.event_data(command, change.revision, change.result))
            self._db.execute('''INSERT INTO m09_events
                (account_id, project_id, seq, kind, at, data_json)
                VALUES (?, ?, ?, 'command.applied', ?, ?)''',
                (event.account_id, event.project_id, event.seq, event.at, _json(event.data)))
            return result

    def _quota_used(self, account_id: str, name: str) -> int:
        row = self._db.execute('''SELECT used FROM m09_quota_usage
            WHERE account_id=? AND quota_name=?''', (account_id, name)).fetchone()
        return row['used'] if row else 0

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
            if project is not None and after > 0 and (
                    after > project['event_seq'] or self._db.execute('''SELECT 1 FROM m09_events
                        WHERE account_id=? AND project_id=? AND seq=?''',
                        (account_id, project_id, after)).fetchone() is None):
                # Events are never pruned, so a valid cursor is 0 or the seq of an event in this
                # stream. A cursor ahead of the head, or naming an event this store does not have,
                # means the client applied history that is not here (for example events lost by
                # restoring an older backup, even if later commands have since moved the head past
                # that cursor). Ask the client to refetch state from this head before reconnecting.
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


class SQLitePlatformReadRepository:
    """Durable M09 read projection backed by the command store's SQLite database."""

    def __init__(self, store: SQLiteCommandRepository):
        self.store = store

    def projects(self, account_id: str) -> list[AccountProjectView]:
        with self.store._lock:
            rows = self.store._db.execute('''SELECT account_id, project_id, name, mode, revision
                FROM m09_projects WHERE account_id=? ORDER BY project_id''', (account_id,)).fetchall()
        return [AccountProjectView(row['account_id'], row['project_id'], row['name'],
                                   row['mode'], row['revision']) for row in rows]

    def project_snapshot(self, account_id: str, project_id: str) -> ProjectSnapshot | None:
        # A deferred read transaction pins one WAL snapshot for all four queries. Under WAL it
        # does not block writers on other connections; same-process commands wait on the lock.
        with self.store._lock, _ReadTransaction(self.store._db) as db:
            project = self._project(db, account_id, project_id)
            if project is None:
                return None
            return ProjectSnapshot(
                project,
                tuple(self._tasks(db, account_id, project_id)),
                tuple(self._workers(db, account_id, project_id)),
                tuple(self._attention(db, account_id, project_id)))

    def project(self, account_id: str, project_id: str) -> AccountProjectView | None:
        with self.store._lock:
            return self._project(self.store._db, account_id, project_id)

    def tasks(self, account_id: str, project_id: str) -> list[Mapping[str, Any]]:
        with self.store._lock:
            return self._tasks(self.store._db, account_id, project_id)

    def task(self, account_id: str, project_id: str, task_id: str) -> Mapping[str, Any] | None:
        with self.store._lock:
            row = self.store._db.execute('''SELECT status FROM m09_tasks
                WHERE account_id=? AND project_id=? AND task_id=?''',
                (account_id, project_id, task_id)).fetchone()
        if row is None:
            return None
        return {'id': task_id, 'account_id': account_id, 'project_id': project_id,
                'status': row['status']}

    def workers(self, account_id: str, project_id: str) -> list[Mapping[str, Any]]:
        with self.store._lock:
            return self._workers(self.store._db, account_id, project_id)

    def attention(self, account_id: str, project_id: str) -> list[Mapping[str, Any]]:
        with self.store._lock:
            return self._attention(self.store._db, account_id, project_id)

    @staticmethod
    def _project(db, account_id: str, project_id: str) -> AccountProjectView | None:
        row = db.execute('''SELECT account_id, project_id, name, mode, revision
            FROM m09_projects WHERE account_id=? AND project_id=?''',
            (account_id, project_id)).fetchone()
        return None if row is None else AccountProjectView(
            row['account_id'], row['project_id'], row['name'], row['mode'], row['revision'])

    @staticmethod
    def _tasks(db, account_id: str, project_id: str) -> list[Mapping[str, Any]]:
        rows = db.execute('''SELECT task_id, status FROM m09_tasks
            WHERE account_id=? AND project_id=? ORDER BY task_id''',
            (account_id, project_id)).fetchall()
        # Quota reservations are internal bookkeeping; like StagingReads, never expose them.
        return [{'id': row['task_id'], 'account_id': account_id, 'project_id': project_id,
                 'status': row['status']} for row in rows]

    @staticmethod
    def _workers(db, account_id: str, project_id: str) -> list[Mapping[str, Any]]:
        rows = db.execute('''SELECT worker_id, status FROM m09_workers
            WHERE account_id=? AND project_id=? ORDER BY worker_id''',
            (account_id, project_id)).fetchall()
        return [{'id': row['worker_id'], 'account_id': account_id,
                 'project_id': project_id, 'status': row['status']} for row in rows]

    @staticmethod
    def _attention(db, account_id: str, project_id: str) -> list[Mapping[str, Any]]:
        rows = db.execute('''SELECT record_json FROM m09_attention
            WHERE account_id=? AND project_id=? ORDER BY attention_id''',
            (account_id, project_id)).fetchall()
        return [json.loads(row['record_json']) for row in rows]


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
                # The transaction state is now unknown; never reuse this connection.
                try:
                    db.close()
                except BaseException:
                    pass
            raise
    else:
        try:
            db.execute('ROLLBACK')
        except BaseException:
            # Returning False preserves the original exception from the with block.
            # The transaction state is now unknown; never reuse this connection.
            try:
                db.close()
            except BaseException:
                pass
    return False
