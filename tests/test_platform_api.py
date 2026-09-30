import unittest

from acc.auth import (
    AccountState,
    AuthService,
    EntitlementSnapshot,
    InMemoryAuthRepository,
    Membership,
    UserState,
    VerifiedIdentity,
)
from acc.platform import (
    AccountProjectView,
    ApiError,
    EventBatch,
    PlatformApi,
    PlatformEvent,
)


class Verifier:
    id = 'openai'

    def verify(self, assertion):
        return VerifiedIdentity('openai', assertion['subject'])


class Reads:
    def __init__(self):
        self.projects_by_account = {
            'acct-1': [AccountProjectView('acct-1', 'project-1', 'Alpha')],
            'acct-2': [AccountProjectView('acct-2', 'project-2', 'Beta')],
        }

    def projects(self, account_id):
        return list(self.projects_by_account.get(account_id, ()))

    def project(self, account_id, project_id):
        return next(
            (p for p in self.projects(account_id) if p.project_id == project_id), None)

    def tasks(self, account_id, project_id):
        return [{
            'id': 'task-1',
            'account_id': account_id,
            'project_id': project_id,
            'title': 'Hosted API task',
            'status': 'queued',
        }]

    def task(self, account_id, project_id, task_id):
        return next(
            (task for task in self.tasks(account_id, project_id) if task['id'] == task_id), None)

    def workers(self, account_id, project_id):
        return [{
            'id': 'worker-1',
            'account_id': account_id,
            'project_id': project_id,
            'status': 'idle',
        }]

    def attention(self, account_id, project_id):
        return [{
            'id': 'attention-1',
            'account_id': account_id,
            'project_id': project_id,
            'severity': 'warning',
        }]


class Events:
    def __init__(self):
        self.bad_account = False
        self.bad_cursor = False
        self.empty_has_more = False

    def read_events(self, account_id, project_id, *, after, limit=200):
        if self.bad_cursor:
            return EventBatch((), after + 1)
        if self.empty_has_more:
            return EventBatch((), after, True)
        owner = 'acct-2' if self.bad_account else account_id
        events = (
            PlatformEvent(after + 1, owner, project_id, 'task.changed', 100.0, {'status': 'working'}),
            PlatformEvent(after + 2, owner, project_id, 'worker.changed', 101.0, {'status': 'busy'}),
        )
        return EventBatch(events, after + 2, False)


class PlatformApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_repo = InMemoryAuthRepository()
        self.auth_repo.put_user(UserState('user-1'))
        self.auth_repo.put_user(UserState('user-2'))
        self.auth_repo.bind_identity(VerifiedIdentity('openai', 'subject-1'), 'user-1')
        self.auth_repo.bind_identity(VerifiedIdentity('openai', 'subject-2'), 'user-2')
        for account, user in (('acct-1', 'user-1'), ('acct-2', 'user-2')):
            self.auth_repo.put_account(AccountState(account))
            self.auth_repo.put_membership(Membership(
                account, user, permissions=('project.read', 'task.read', 'event.read')))
            self.auth_repo.put_entitlements(EntitlementSnapshot(
                account, features=('acc.web',)))
        self.auth = AuthService(self.auth_repo, identity_verifiers=(Verifier(),))
        self.token1 = self.auth.exchange_identity('openai', {'subject': 'subject-1'}, 'acct-1')
        self.token2 = self.auth.exchange_identity('openai', {'subject': 'subject-2'}, 'acct-2')
        self.reads = Reads()
        self.events = Events()
        self.api = PlatformApi(self.auth, self.reads, self.events)

    def authz(self, token):
        return 'Bearer ' + token

    def test_list_projects_is_account_scoped(self):
        response = self.api.list_projects(self.authz(self.token1), 'acct-1')
        self.assertEqual(response.status, 200)
        self.assertEqual([p['project_id'] for p in response.body['projects']], ['project-1'])
        with self.assertRaises(ApiError) as caught:
            self.api.list_projects(self.authz(self.token1), 'acct-2')
        self.assertEqual(caught.exception.status, 403)

    def test_project_state_returns_scoped_tasks_workers_attention(self):
        response = self.api.project_state(
            self.authz(self.token1), 'acct-1', 'project-1')
        self.assertEqual(response.body['project']['project_id'], 'project-1')
        self.assertEqual(response.body['tasks'][0]['account_id'], 'acct-1')
        self.assertEqual(response.body['workers'][0]['project_id'], 'project-1')
        self.assertEqual(response.body['attention'][0]['account_id'], 'acct-1')

    def test_task_read_requires_task_permission(self):
        response = self.api.task(
            self.authz(self.token1), 'acct-1', 'project-1', 'task-1')
        self.assertEqual(response.body['task']['id'], 'task-1')
        self.auth_repo.put_membership(Membership(
            'acct-1', 'user-1', permissions=('project.read', 'event.read')))
        with self.assertRaises(ApiError) as caught:
            self.api.task(self.authz(self.token1), 'acct-1', 'project-1', 'task-1')
        self.assertEqual(caught.exception.status, 403)

    def test_authentication_and_authorization_map_to_401_and_403(self):
        response = PlatformApi.handle(self.api.list_projects, 'bad', 'acct-1')
        self.assertEqual(response.status, 401)
        self.assertEqual(response.body['error']['code'], 'authentication_required')

        response = PlatformApi.handle(
            self.api.list_projects, self.authz(self.token1), 'acct-2')
        self.assertEqual(response.status, 403)
        self.assertEqual(response.body['error']['code'], 'access_denied')

    def test_missing_web_entitlement_is_403(self):
        self.auth_repo.put_entitlements(EntitlementSnapshot('acct-1'))
        response = PlatformApi.handle(
            self.api.list_projects, self.authz(self.token1), 'acct-1')
        self.assertEqual(response.status, 403)

    def test_repository_cross_account_record_fails_closed(self):
        self.reads.projects_by_account['acct-1'] = [
            AccountProjectView('acct-2', 'project-2', 'Wrong tenant')]
        response = PlatformApi.handle(
            self.api.list_projects, self.authz(self.token1), 'acct-1')
        self.assertEqual(response.status, 500)
        self.assertEqual(response.body['error']['code'], 'invalid_repository_state')

    def test_repository_cross_project_task_fails_closed(self):
        self.reads.tasks = lambda account_id, project_id: [{
            'id': 'task-x',
            'account_id': account_id,
            'project_id': 'project-other',
        }]
        response = PlatformApi.handle(
            self.api.project_state, self.authz(self.token1), 'acct-1', 'project-1')
        self.assertEqual(response.status, 500)

    def test_event_cursor_reconnect_is_strict_and_ordered(self):
        response = self.api.events_after(
            self.authz(self.token1), 'acct-1', 'project-1', 40)
        self.assertEqual([e['seq'] for e in response.body['events']], [41, 42])
        self.assertEqual(response.body['cursor'], 42)

        response = self.api.events_after(
            self.authz(self.token1), 'acct-1', 'project-1', response.body['cursor'])
        self.assertEqual([e['seq'] for e in response.body['events']], [43, 44])

    def test_bad_event_cursor_is_400(self):
        for cursor in (-1, True, '7'):
            with self.subTest(cursor=cursor):
                response = PlatformApi.handle(
                    self.api.events_after,
                    self.authz(self.token1), 'acct-1', 'project-1', cursor)
                self.assertEqual(response.status, 400)

    def test_cross_tenant_event_fails_closed(self):
        self.events.bad_account = True
        response = PlatformApi.handle(
            self.api.events_after,
            self.authz(self.token1), 'acct-1', 'project-1', 0)
        self.assertEqual(response.status, 500)
        self.assertEqual(response.body['error']['code'], 'invalid_event_source')

    def test_empty_event_batch_cannot_advance_cursor(self):
        self.events.bad_cursor = True
        response = PlatformApi.handle(
            self.api.events_after,
            self.authz(self.token1), 'acct-1', 'project-1', 5)
        self.assertEqual(response.status, 500)

    def test_empty_batch_cannot_claim_has_more(self):
        self.events.empty_has_more = True
        response = PlatformApi.handle(
            self.api.events_after,
            self.authz(self.token1), 'acct-1', 'project-1', 5)
        self.assertEqual(response.status, 500)

    def test_revoked_session_is_401(self):
        self.assertTrue(self.auth.revoke(self.token1))
        response = PlatformApi.handle(
            self.api.list_projects, self.authz(self.token1), 'acct-1')
        self.assertEqual(response.status, 401)

    def test_repository_not_found_is_404_without_cross_tenant_probe(self):
        response = PlatformApi.handle(
            self.api.project_state, self.authz(self.token1), 'acct-1', 'missing')
        self.assertEqual(response.status, 404)
        response = PlatformApi.handle(
            self.api.project_state, self.authz(self.token1), 'acct-2', 'project-2')
        self.assertEqual(response.status, 403)


if __name__ == '__main__':
    unittest.main()
