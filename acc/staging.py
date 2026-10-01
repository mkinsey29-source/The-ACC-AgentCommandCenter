"""Isolated in-memory staging composition for end-to-end M09 browser tests."""
from __future__ import annotations

import os
from copy import deepcopy

from .auth import (AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
                   Membership, UserState, VerifiedIdentity)
from .platform import AccountProjectView, HostedTransport, OriginPolicy, PlatformApi
from .platform.command_memory import InMemoryCommandRepository

STAGING_ACCOUNT_ID = 'staging-account'
STAGING_PROJECT_ID = 'staging-project'
STAGING_USER_ID = 'staging-user'
STAGING_ORIGIN_DEFAULT = 'https://acc-staging-console--memph1510.replit.app'


class StagingReads:
    def __init__(self, state: InMemoryCommandRepository):
        self.state = state

    def projects(self, account_id):
        if account_id != STAGING_ACCOUNT_ID:
            return []
        p = self.state.project(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID)
        return [AccountProjectView(account_id, STAGING_PROJECT_ID, "ACC Staging",
                                   p['mode'], p['revision'])]

    def project(self, account_id, project_id):
        if (account_id, project_id) != (STAGING_ACCOUNT_ID, STAGING_PROJECT_ID):
            return None
        p = self.state.project(account_id, project_id)
        return AccountProjectView(account_id, project_id, "ACC Staging",
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
    id = 'staging'
    def __init__(self, secret):
        self.secret = secret
    def verify(self, assertion):
        if not isinstance(assertion, dict) or assertion.get('secret') != self.secret:
            raise ValueError('invalid staging assertion')
        return VerifiedIdentity('staging', 'browser-test')


class StagingApplication:
    """ASGI wrapper adding staging-only health and session bootstrap routes."""
    def __init__(self, *, origin: str, bootstrap_secret: str):
        if not bootstrap_secret or len(bootstrap_secret) < 24:
            raise ValueError('staging bootstrap secret must contain at least 24 characters')
        auth_repo = InMemoryAuthRepository()
        auth_repo.put_user(UserState(STAGING_USER_ID))
        identity = VerifiedIdentity('staging', 'browser-test')
        auth_repo.bind_identity(identity, STAGING_USER_ID)
        auth_repo.put_account(AccountState(STAGING_ACCOUNT_ID))
        auth_repo.put_membership(Membership(
            STAGING_ACCOUNT_ID, STAGING_USER_ID, role="owner", permissions=(
                'project.read','task.read','event.read','project.write',
                'task.write','task.cancel','worker.control')))
        auth_repo.put_entitlements(EntitlementSnapshot(
            STAGING_ACCOUNT_ID, features=('acc.web',), limits={'tasks.active': 25}))
        self.auth = AuthService(auth_repo, identity_verifiers=(StagingVerifier(bootstrap_secret),),
                                default_ttl_seconds=900)
        self.bootstrap_secret = bootstrap_secret
        self.state = InMemoryCommandRepository()
        self.state.put_project(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID)
        self.state.put_worker(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID, 'worker-1')
        self.reads = StagingReads(self.state)
        self.api = PlatformApi(self.auth, self.reads, self.state, self.state)
        self.transport = HostedTransport(self.api, origins=OriginPolicy(frozenset((origin,))))

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http' and scope.get('path') == '/health':
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
        if scope.get('method','').upper() != 'POST':
            await self.transport._send_json(send, 404, {'error':{'code':'not_found','message':'Not found.'}})
            return
        headers = {}
        for key, value in scope.get('headers', ()):
            headers[key.decode('latin-1').lower()] = value.decode('latin-1')
        if not self.transport.origins.allows(headers.get('origin')):
            await self.transport._send_json(send,403,{'error':{'code':'origin_denied','message':'Origin denied.'}})
            return
        secret = headers.get('x-acc-staging-secret')
        try:
            token = self.auth.exchange_identity('staging', {'secret':secret}, STAGING_ACCOUNT_ID,
                                                ttl_seconds=900)
        except Exception:
            await self.transport._send_json(send,401,{'error':{'code':'authentication_required','message':'Authentication required.'}},origin=headers.get('origin'))
            return
        await self.transport._send_json(send,201,{
            'access_token':token,'token_type':'Bearer','expires_in':900,
            'account_id':STAGING_ACCOUNT_ID,'project_id':STAGING_PROJECT_ID,
        },origin=headers.get("origin"))


def build_staging_app():
    origin = os.environ.get('ACC_STAGING_ORIGIN', STAGING_ORIGIN_DEFAULT)
    secret = os.environ.get('ACC_STAGING_BOOTSTRAP_SECRET')
    if not secret:
        raise RuntimeError('ACC_STAGING_BOOTSTRAP_SECRET is required.')
    return StagingApplication(origin=origin, bootstrap_secret=secret)
