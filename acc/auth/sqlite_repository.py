"""Durable SQLite adapter for M08 account and session state.

Only ACC account records, stable identity links, and session-token digests are stored here.
Provider credentials and the plaintext session bearer are never persisted.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .models import (
    AccountState, EntitlementSnapshot, Membership, SessionRecord, UserState,
    VerifiedIdentity, _bounded_id,
)
from .repository import AuthSnapshot


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


_TABLES = ('m08_schema_meta', 'm08_users', 'm08_accounts', 'm08_identities', 'm08_memberships',
           'm08_entitlements', 'm08_sessions')


class AuthStateError(RuntimeError):
    """Stored auth state could not be decoded into valid records.

    Deliberately not a ``ValueError``: callers map ``ValueError`` to a client 400, but corrupt or
    no-longer-valid stored state is a server fault and must surface as an opaque 500 (fail closed).
    """


@contextmanager
def _decoding():
    try:
        yield
    except (ValueError, TypeError, KeyError) as exc:
        raise AuthStateError('Stored M08 auth state is invalid.') from exc


def _finish(db: sqlite3.Connection, error: BaseException | None) -> None:
    """Commit, or roll back while preserving the original error; never reuse a stuck connection."""
    if error is None:
        try:
            db.execute('COMMIT')
            return
        except BaseException as commit_error:
            error = commit_error
    try:
        if db.in_transaction:
            db.execute('ROLLBACK')
    except BaseException:
        # Transaction state is now unknown: close so no later call runs inside it.
        db.close()
    raise error


class SQLiteAuthRepository:
    """Persistent ``AuthRepository`` requiring a file shared by service workers."""

    def __init__(self, database: str | Path, *, timeout: float = 5.0):
        self.database = str(database)
        if (not self.database.strip() or self.database == ':memory:'
                or self.database.lower().startswith('file:')):
            raise ValueError('SQLite auth storage requires a durable database file.')
        if timeout <= 0:
            raise ValueError('timeout must be positive.')
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.database, timeout=timeout, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.execute('PRAGMA busy_timeout=%d' % max(1, int(timeout * 1000)))
        self._enable_wal(timeout)
        self._db.execute('PRAGMA synchronous=FULL')
        try:
            self._create_schema()
        except Exception:
            self._db.close()
            raise

    def _enable_wal(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self._db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                    self._db.execute('PRAGMA journal_mode=WAL')
                return
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def _schema_is_current(self) -> bool:
        tables = {row[0] for row in self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'm08_%'")}
        if not set(_TABLES) <= tables:
            return False
        row = self._db.execute('SELECT version FROM m08_schema_meta WHERE singleton=1').fetchone()
        if row is not None and row['version'] != 1:
            raise RuntimeError('Unsupported M08 SQLite schema version.')
        return row is not None

    def _create_schema(self) -> None:
        # A current database opens read-only, so a worker can start while another holds the writer
        # lock. Otherwise serialize first-open races and keep metadata plus all tables atomic.
        if self._schema_is_current():
            return
        self._db.execute('BEGIN IMMEDIATE')
        try:
            self._db.execute('''CREATE TABLE IF NOT EXISTS m08_schema_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                version INTEGER NOT NULL CHECK(version>=1))''')
            self._db.execute('INSERT OR IGNORE INTO m08_schema_meta VALUES (1, 1)')
            version = self._db.execute(
                'SELECT version FROM m08_schema_meta WHERE singleton=1').fetchone()['version']
            if version != 1:
                raise RuntimeError('Unsupported M08 SQLite schema version.')
            statements = (
                '''CREATE TABLE IF NOT EXISTS m08_users (
                    user_id TEXT PRIMARY KEY, status TEXT NOT NULL
                        CHECK(status IN ('active','suspended','closed')),
                    revision INTEGER NOT NULL CHECK(revision>=0))''',
                '''CREATE TABLE IF NOT EXISTS m08_accounts (
                    account_id TEXT PRIMARY KEY, status TEXT NOT NULL
                        CHECK(status IN ('active','suspended','closed')),
                    revision INTEGER NOT NULL CHECK(revision>=0))''',
                '''CREATE TABLE IF NOT EXISTS m08_identities (
                    provider TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL,
                    PRIMARY KEY(provider, subject),
                    FOREIGN KEY(user_id) REFERENCES m08_users(user_id))''',
                '''CREATE TABLE IF NOT EXISTS m08_memberships (
                    account_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
                    permissions_json TEXT NOT NULL,
                    PRIMARY KEY(account_id,user_id),
                    FOREIGN KEY(account_id) REFERENCES m08_accounts(account_id),
                    FOREIGN KEY(user_id) REFERENCES m08_users(user_id))''',
                '''CREATE TABLE IF NOT EXISTS m08_entitlements (
                    account_id TEXT PRIMARY KEY, features_json TEXT NOT NULL,
                    limits_json TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0),
                    FOREIGN KEY(account_id) REFERENCES m08_accounts(account_id))''',
                '''CREATE TABLE IF NOT EXISTS m08_sessions (
                    token_digest TEXT PRIMARY KEY CHECK(length(token_digest)=64),
                    session_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at>issued_at),
                    identity_provider TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                        CHECK(revoked IN (0,1)),
                    FOREIGN KEY(account_id) REFERENCES m08_accounts(account_id),
                    FOREIGN KEY(user_id) REFERENCES m08_users(user_id))''',
            )
            for statement in statements:
                self._db.execute(statement)
        except BaseException as error:
            _finish(self._db, error)
        _finish(self._db, None)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _write(self, callback):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                result = callback()
            except BaseException as error:
                _finish(self._db, error)
            _finish(self._db, None)
            return result

    def put_user(self, user: UserState) -> None:
        self._write(lambda: self._db.execute('''INSERT INTO m08_users VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,revision=excluded.revision''',
            (user.user_id, user.status, user.revision)))

    def bind_identity(self, identity: VerifiedIdentity, user_id: str) -> None:
        user_id = _bounded_id(user_id, 'user_id')

        def write():
            if self._db.execute('SELECT 1 FROM m08_users WHERE user_id=?', (user_id,)).fetchone() is None:
                raise ValueError('ACC user must exist before an identity can be bound.')
            current = self._db.execute(
                'SELECT user_id FROM m08_identities WHERE provider=? AND subject=?',
                (identity.provider, identity.subject)).fetchone()
            if current is not None and current['user_id'] != user_id:
                raise ValueError('Identity is already bound to another ACC user.')
            self._db.execute('INSERT OR IGNORE INTO m08_identities VALUES (?,?,?)',
                             (identity.provider, identity.subject, user_id))
        self._write(write)

    def put_account(self, account: AccountState) -> None:
        self._write(lambda: self._db.execute('''INSERT INTO m08_accounts VALUES (?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET status=excluded.status,revision=excluded.revision''',
            (account.account_id, account.status, account.revision)))

    def put_membership(self, membership: Membership) -> None:
        self._write(lambda: self._db.execute('''INSERT INTO m08_memberships VALUES (?,?,?,?)
            ON CONFLICT(account_id,user_id) DO UPDATE SET role=excluded.role,
            permissions_json=excluded.permissions_json''',
            (membership.account_id, membership.user_id, membership.role,
             _json(membership.permissions))))

    def put_entitlements(self, entitlements: EntitlementSnapshot) -> None:
        self._write(lambda: self._db.execute('''INSERT INTO m08_entitlements VALUES (?,?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET features_json=excluded.features_json,
            limits_json=excluded.limits_json,revision=excluded.revision''',
            (entitlements.account_id, _json(entitlements.features),
             _json(dict(entitlements.limits)), entitlements.revision)))

    def remove_membership(self, account_id: str, user_id: str) -> bool:
        account_id = _bounded_id(account_id, 'account_id')
        user_id = _bounded_id(user_id, 'user_id')
        return self._write(lambda: self._db.execute(
            'DELETE FROM m08_memberships WHERE account_id=? AND user_id=?',
            (account_id, user_id)).rowcount == 1)

    def resolve_identity(self, identity: VerifiedIdentity) -> str | None:
        with self._lock:
            row = self._db.execute('SELECT user_id FROM m08_identities WHERE provider=? AND subject=?',
                                   (identity.provider, identity.subject)).fetchone()
            return None if row is None else row['user_id']

    @staticmethod
    def _user(row) -> UserState | None:
        return None if row is None or row['user_id'] is None else UserState(
            row['user_id'], row['user_status'], row['user_revision'])

    @staticmethod
    def _account(row) -> AccountState | None:
        return None if row is None or row['account_id'] is None else AccountState(
            row['account_id'], row['account_status'], row['account_revision'])

    @staticmethod
    def _membership(row) -> Membership | None:
        return None if row is None or row['membership_account_id'] is None else Membership(
            row['membership_account_id'], row['membership_user_id'], row['role'],
            tuple(json.loads(row['permissions_json'])))

    @staticmethod
    def _entitlements(row) -> EntitlementSnapshot | None:
        return None if row is None or row['entitlement_account_id'] is None else EntitlementSnapshot(
            row['entitlement_account_id'], tuple(json.loads(row['features_json'])),
            json.loads(row['limits_json']), row['entitlement_revision'])

    @staticmethod
    def _session(row) -> SessionRecord | None:
        if row is None or row['token_digest'] is None:
            return None
        return SessionRecord(row['session_id'], row['session_account_id'], row['session_user_id'],
                             row['issued_at'], row['expires_at'], row['identity_provider'],
                             bool(row['revoked']))

    def _select_session(self, token_digest: str):
        return self._db.execute('''SELECT s.token_digest,s.session_id,
            s.account_id AS session_account_id,s.user_id AS session_user_id,s.issued_at,
            s.expires_at,s.identity_provider,s.revoked,
            u.user_id,u.status AS user_status,u.revision AS user_revision,
            a.account_id,a.status AS account_status,a.revision AS account_revision,
            m.account_id AS membership_account_id,m.user_id AS membership_user_id,
            m.role,m.permissions_json,
            e.account_id AS entitlement_account_id,e.features_json,e.limits_json,
            e.revision AS entitlement_revision
            FROM m08_sessions s
            LEFT JOIN m08_users u ON u.user_id=s.user_id
            LEFT JOIN m08_accounts a ON a.account_id=s.account_id
            LEFT JOIN m08_memberships m ON m.account_id=s.account_id AND m.user_id=s.user_id
            LEFT JOIN m08_entitlements e ON e.account_id=s.account_id
            WHERE s.token_digest=?''', (token_digest,)).fetchone()

    def auth_snapshot(self, token_digest: str) -> AuthSnapshot | None:
        with self._lock:
            row = self._select_session(token_digest)
        with _decoding():
            session = self._session(row)
            if session is None:
                return None
            return AuthSnapshot(session, self._user(row), self._account(row),
                                self._membership(row), self._entitlements(row))

    def user(self, user_id: str) -> UserState | None:
        with self._lock:
            row = self._db.execute('SELECT user_id,status,revision FROM m08_users WHERE user_id=?',
                                   (user_id,)).fetchone()
        with _decoding():
            return None if row is None else UserState(row['user_id'], row['status'], row['revision'])

    def account(self, account_id: str) -> AccountState | None:
        with self._lock:
            row = self._db.execute('SELECT account_id,status,revision FROM m08_accounts WHERE account_id=?',
                                   (account_id,)).fetchone()
        with _decoding():
            return None if row is None else AccountState(row['account_id'], row['status'], row['revision'])

    def membership(self, account_id: str, user_id: str) -> Membership | None:
        with self._lock:
            row = self._db.execute('''SELECT account_id,user_id,role,permissions_json
                FROM m08_memberships WHERE account_id=? AND user_id=?''', (account_id, user_id)).fetchone()
        with _decoding():
            return None if row is None else Membership(row['account_id'], row['user_id'], row['role'],
                                                        tuple(json.loads(row['permissions_json'])))

    def entitlements(self, account_id: str) -> EntitlementSnapshot | None:
        with self._lock:
            row = self._db.execute('''SELECT account_id,features_json,limits_json,revision
                FROM m08_entitlements WHERE account_id=?''', (account_id,)).fetchone()
        with _decoding():
            return None if row is None else EntitlementSnapshot(
                row['account_id'], tuple(json.loads(row['features_json'])),
                json.loads(row['limits_json']), row['revision'])

    def session(self, token_digest: str) -> SessionRecord | None:
        with self._lock:
            row = self._db.execute('''SELECT token_digest,session_id,account_id AS session_account_id,
                user_id AS session_user_id,issued_at,expires_at,identity_provider,revoked
                FROM m08_sessions WHERE token_digest=?''', (token_digest,)).fetchone()
        with _decoding():
            return self._session(row)

    def save_session(self, token_digest: str, session: SessionRecord) -> None:
        if (not isinstance(token_digest, str) or len(token_digest) != 64
                or any(char not in '0123456789abcdef' for char in token_digest)):
            raise ValueError('Session key must be a lowercase SHA-256 digest.')
        self._write(lambda: self._db.execute('''INSERT INTO m08_sessions
            (token_digest,session_id,account_id,user_id,issued_at,expires_at,identity_provider,revoked)
            VALUES (?,?,?,?,?,?,?,?)''',
            (token_digest, session.session_id, session.account_id, session.user_id,
             session.issued_at, session.expires_at, session.identity_provider, int(session.revoked))))

    def revoke_session(self, token_digest: str) -> bool:
        return self._write(lambda: self._db.execute('''UPDATE m08_sessions SET revoked=1
            WHERE token_digest=? AND revoked=0''', (token_digest,)).rowcount == 1)

    def session_digests(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(row['token_digest'] for row in self._db.execute(
                'SELECT token_digest FROM m08_sessions ORDER BY token_digest'))
