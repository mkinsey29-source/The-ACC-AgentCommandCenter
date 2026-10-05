"""Isolated in-memory staging composition for end-to-end M09 browser tests."""
from __future__ import annotations

import hmac
import os
from copy import deepcopy
from pathlib import Path

from .auth import (AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
                   Membership, SQLiteAuthRepository, UserState, VerifiedIdentity)
from .platform import (AccountProjectView, HostedTransport, OriginPolicy, PlatformApi,
                       ProjectSnapshot)
from .platform.command_memory import InMemoryCommandRepository
from .platform.sqlite_repository import SQLiteCommandRepository, SQLitePlatformReadRepository

STAGING_ACCOUNT_ID = 'staging-account'
STAGING_PROJECT_ID = 'staging-project'
STAGING_USER_ID = 'staging-user'
STAGING_ORIGIN_DEFAULT = 'https://acc-staging-console--memph1510.replit.app'
STAGING_SECRET_HEADER = 'x-acc-staging-secret'
STAGING_SESSION_SECONDS = 900
STAGING_PROJECT_NAME = 'ACC Staging'
# Bump the suffix only for a deliberate fixture change; an applied key is never re-applied.
STAGING_FIXTURE_KEY = 'acc-m09-staging-fixture-v1'
STAGING_PERMISSIONS = ('project.read', 'task.read', 'event.read', 'project.write',
                       'task.write', 'task.cancel', 'worker.control')

# Headers whose repetition is ambiguous; a repeated one is treated as absent (fail closed).
_SINGLE_VALUE_HEADERS = frozenset(('origin', 'authorization', STAGING_SECRET_HEADER))


def _headers(scope) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for key, value in scope.get('headers', ()):
        name = key.decode('latin-1').lower()
        if name in _SINGLE_VALUE_HEADERS and name in result:
            result[name] = None
            continue
        result[name] = value.decode('latin-1')
    return result


class StagingReads:
    def __init__(self, state: InMemoryCommandRepository):
        self.state = state

    def projects(self, account_id):
        if account_id != STAGING_ACCOUNT_ID:
            return []
        p = self.state.project(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID)
        return [AccountProjectView(account_id, STAGING_PROJECT_ID, STAGING_PROJECT_NAME,
                                   p['mode'], p['revision'])]

    def project_snapshot(self, account_id, project_id):
        if (account_id, project_id) != (STAGING_ACCOUNT_ID, STAGING_PROJECT_ID):
            return None
        # One locked deep copy, so revision, tasks and workers come from the same state.
        p = self.state.project(account_id, project_id)
        return ProjectSnapshot(
            AccountProjectView(account_id, project_id, STAGING_PROJECT_NAME, p['mode'], p['revision']),
            tuple(self._public(account_id, project_id, v) for v in p['tasks'].values()),
            tuple(self._public(account_id, project_id, v) for v in p['workers'].values()),
            ())

    def project(self, account_id, project_id):
        if (account_id, project_id) != (STAGING_ACCOUNT_ID, STAGING_PROJECT_ID):
            return None
        p = self.state.project(account_id, project_id)
        return AccountProjectView(account_id, project_id, STAGING_PROJECT_NAME,
                                  p['mode'], p['revision'])

    def tasks(self, account_id, project_id):
        if self.project(account_id, project_id) is None:
            return []
        p = self.state.project(account_id, project_id)
        return [self._public(account_id, project_id, value) for value in p["tasks"].values()]

    def task(self, account_id, project_id, task_id):
        if self.project(account_id, project_id) is None:
            return None
        value = self.state.project(account_id, project_id)["tasks"].get(task_id)
        return None if value is None else self._public(account_id, project_id, value)

    def workers(self, account_id, project_id):
        if self.project(account_id, project_id) is None:
            return []
        p = self.state.project(account_id, project_id)
        return [self._public(account_id, project_id, value) for value in p["workers"].values()]

    def attention(self, account_id, project_id):
        return []

    @staticmethod
    def _public(account_id, project_id, value):
        result = deepcopy(value)
        result.pop('reserved', None)
        result['account_id'] = account_id
        result['project_id'] = project_id
        return result


class StagingVerifier:
    """Staging-only identity verifier: the bootstrap secret maps to one fixed staging identity.

    Composed only by ``StagingApplication``; no production composition registers it.
    """
    id = 'staging'

    def __init__(self, secret: str):
        self._secret = secret.encode('utf-8')

    def __repr__(self) -> str:
        return 'StagingVerifier()'

    def verify(self, assertion):
        presented = assertion.get('secret') if isinstance(assertion, dict) else None
        # Constant-time comparison; the error never contains the presented or expected value.
        if not isinstance(presented, str) or not hmac.compare_digest(
                presented.encode('utf-8'), self._secret):
            raise ValueError('invalid staging assertion')
        return VerifiedIdentity('staging', 'browser-test')


class StagingApplication:
    """ASGI wrapper adding staging-only health and session bootstrap routes.

    Without ``database`` all state is process memory and a restart resets it. With ``database``
    (an absolute SQLite file path) auth, sessions, commands, events and project reads are durable
    in that one file: sessions, revocations, state and event cursors survive restarts. WebSocket
    tickets stay process-local by design, so run one worker either way.
    """
    def __init__(self, *, origin: str, bootstrap_secret: str, database: str | Path | None = None):
        if not isinstance(bootstrap_secret, str) or len(bootstrap_secret) < 24:
            raise ValueError('staging bootstrap secret must contain at least 24 characters')
        if database is not None and not Path(database).is_absolute():
            raise ValueError('staging database must be an absolute file path')
        self.database = None if database is None else str(database)
        self._closers = []
        try:
            if database is None:
                auth_repo = InMemoryAuthRepository()
                self.state = InMemoryCommandRepository()
                self.state.put_project(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID)
                self.reads = StagingReads(self.state)
            else:
                auth_repo = SQLiteAuthRepository(database)
                self._closers.append(auth_repo.close)
                self.state = SQLiteCommandRepository(database)
                self._closers.append(self.state.close)
                # Insert-if-missing: a restart never resets the project's revision or state.
                self.state.put_project(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID,
                                       name=STAGING_PROJECT_NAME)
                self.reads = SQLitePlatformReadRepository(self.state)
            _provision_staging_fixture(auth_repo)
            self.state.put_worker(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID, 'worker-1')
        except BaseException:
            self.close()
            raise
        self.auth = AuthService(auth_repo, identity_verifiers=(StagingVerifier(bootstrap_secret),),
                                default_ttl_seconds=STAGING_SESSION_SECONDS)
        self.api = PlatformApi(self.auth, self.reads, self.state, self.state)
        self.transport = HostedTransport(self.api, origins=OriginPolicy(frozenset((origin,))))

    def close(self) -> None:
        """Close durable storage connections; a no-op for in-memory staging."""
        while self._closers:
            self._closers.pop()()

    def __repr__(self) -> str:
        return 'StagingApplication()'

    async def __call__(self, scope, receive, send):
        # /health is deliberately public (hosting health checks send no Origin) and returns only
        # fixed, non-secret identifiers.
        if scope.get('type') == 'http' and scope.get('path') == '/health':
            if scope.get('method', '').upper() not in ('GET', 'HEAD'):
                await self.transport._send_json(
                    send, 404, {'error': {'code': 'not_found', 'message': 'Not found.'}})
                return
            await self.transport._send_json(send, 200, {
                'status':'ok','service':'acc-m09-staging',
                'account_id':STAGING_ACCOUNT_ID,'project_id':STAGING_PROJECT_ID,
            })
            return
        if scope.get('type') == 'http' and scope.get('path') == '/staging/session':
            await self._session(scope, receive, send)
            return
        await self.transport(scope, receive, send)

    async def _session(self, scope, receive, send):
        headers = _headers(scope)
        origin = headers.get('origin')
        if not self.transport.origins.allows(origin):
            await self.transport._send_json(
                send, 403, {'error': {'code': 'origin_denied', 'message': 'Origin denied.'}})
            return
        method = scope.get('method', '').upper()
        if method == 'OPTIONS':
            # CORS preflight: the custom secret header makes every browser bootstrap preflighted.
            await self._preflight(send, origin)
            return
        if method != 'POST':
            await self.transport._send_json(
                send, 404, {'error': {'code': 'not_found', 'message': 'Not found.'}}, origin=origin)
            return
        # The secret is accepted only from this header: never from the query string or body.
        secret = headers.get(STAGING_SECRET_HEADER)
        try:
            token = self.auth.exchange_identity(
                'staging', {'secret': secret}, STAGING_ACCOUNT_ID,
                ttl_seconds=STAGING_SESSION_SECONDS)
        except Exception:
            await self.transport._send_json(send, 401, {
                'error': {'code': 'authentication_required', 'message': 'Authentication required.'},
            }, origin=origin)
            return
        await self.transport._send_json(send, 201, {
            'access_token': token, 'token_type': 'Bearer', 'expires_in': STAGING_SESSION_SECONDS,
            'account_id': STAGING_ACCOUNT_ID, 'project_id': STAGING_PROJECT_ID,
        }, origin=origin)

    @staticmethod
    async def _preflight(send, origin):
        headers = [
            (b'access-control-allow-origin', origin.encode('ascii')),
            (b'vary', b'Origin'),
            (b'access-control-allow-methods', b'POST, OPTIONS'),
            (b'access-control-allow-headers', b'X-ACC-Staging-Secret, Content-Type'),
            (b'access-control-max-age', b'600'),
            (b'cache-control', b'no-store'),
            (b'content-length', b'0'),
        ]
        await send({'type': 'http.response.start', 'status': 204, 'headers': headers})
        await send({'type': 'http.response.body', 'body': b''})


def _provision_staging_fixture(auth_repo) -> bool:
    """Seed the staging tenant once per store; later starts keep whatever state it now holds."""
    return auth_repo.provision_once(
        STAGING_FIXTURE_KEY,
        users=(UserState(STAGING_USER_ID),),
        accounts=(AccountState(STAGING_ACCOUNT_ID),),
        identities=((VerifiedIdentity('staging', 'browser-test'), STAGING_USER_ID),),
        memberships=(Membership(STAGING_ACCOUNT_ID, STAGING_USER_ID, role='owner',
                                permissions=STAGING_PERMISSIONS),),
        entitlements=(EntitlementSnapshot(STAGING_ACCOUNT_ID, features=('acc.web',),
                                          limits={'tasks.active': 25}),),
    )


def build_staging_app():
    origin = os.environ.get('ACC_STAGING_ORIGIN', STAGING_ORIGIN_DEFAULT)
    secret = os.environ.get('ACC_STAGING_BOOTSTRAP_SECRET')
    if not secret:
        raise RuntimeError('ACC_STAGING_BOOTSTRAP_SECRET is required.')
    # Optional: an absolute SQLite path makes staging durable; unset keeps it in memory.
    database = os.environ.get('ACC_STAGING_DATABASE')
    return StagingApplication(origin=origin, bootstrap_secret=secret, database=database)
