import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from acc.auth import (
    AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
    Membership, UserState, VerifiedIdentity,
)
from acc.platform.api import PlatformApi
from acc.platform.command_memory import InMemoryCommandRepository
from acc.platform.commands import (
    CommandRequest, IdempotencyConflict, QuotaExceeded,
)


class EmptyReads:
    pass

class EmptyEvents:
    pass


class Verifier:
    id = 'openai'
    def verify(self, assertion):
        return VerifiedIdentity('openai', assertion['subject'])


ALL_COMMAND_PERMISSIONS = ('project.write', 'task.write', 'task.cancel', 'worker.control')


class Fixture:
    """Two accounts, two users in acct-1, one user in acct-2, projects in both accounts."""

    def __init__(self, *, tasks_active=2):
        repo = InMemoryAuthRepository()
        self.repo = repo
        for user, subject in (('user-1', 'subject-1'), ('user-2', 'subject-2'),
                              ('user-3', 'subject-3')):
            repo.put_user(UserState(user))
            repo.bind_identity(VerifiedIdentity('openai', subject), user)
        for account in ('acct-1', 'acct-2'):
            repo.put_account(AccountState(account))
            repo.put_entitlements(EntitlementSnapshot(
                account, features=('acc.web',), limits={'tasks.active': tasks_active}))
        repo.put_membership(Membership('acct-1', 'user-1', permissions=ALL_COMMAND_PERMISSIONS))
        repo.put_membership(Membership('acct-1', 'user-2', permissions=ALL_COMMAND_PERMISSIONS))
        repo.put_membership(Membership('acct-2', 'user-3', permissions=ALL_COMMAND_PERMISSIONS))
        self.auth = AuthService(repo, identity_verifiers=(Verifier(),))
        self.a1 = 'Bearer ' + self.auth.exchange_identity('openai', {'subject': 'subject-1'}, 'acct-1')
        self.a1_user2 = 'Bearer ' + self.auth.exchange_identity(
            'openai', {'subject': 'subject-2'}, 'acct-1')
        self.a2 = 'Bearer ' + self.auth.exchange_identity('openai', {'subject': 'subject-3'}, 'acct-2')
        self.commands = InMemoryCommandRepository(clock=lambda: 1000.0)
        for account in ('acct-1', 'acct-2'):
            for project in ('project-1', 'project-2'):
                self.commands.put_project(account, project)
        self.api = PlatformApi(self.auth, EmptyReads(), EmptyEvents(), self.commands)

    def call(self, body, *, authorization=None, account='acct-1', project='project-1'):
        return PlatformApi.handle(
            self.api.command, authorization or self.a1, account, project, body)

    def effects(self, account='acct-1', project='project-1'):
        return (self.commands.project_revision(account, project),
                self.commands.quota_used(account, 'tasks.active'),
                len(self.commands.audit()),
                len(self.commands.read_events(account, project, after=0).events))


_DEFAULT = object()


def body(operation='op-1', revision=0, kind='task.create', payload=_DEFAULT):
    return {
        'operation_id': operation, 'kind': kind, 'expected_revision': revision,
        'payload': {'task_id': 'task-1'} if payload is _DEFAULT else payload,
    }


class CommandMutationTests(unittest.TestCase):
    """The implementation's original tests, updated to the server-derived quota contract."""

    def setUp(self):
        self.f = Fixture()
        self.api, self.commands = self.f.api, self.f.commands

    def test_command_applies_once_and_safe_retry_replays(self):
        first = self.f.call(body())
        second = self.f.call(body())
        self.assertEqual(first.status, 201)
        self.assertEqual(second.status, 200)
        self.assertFalse(first.body['replayed'])
        self.assertTrue(second.body['replayed'])
        self.assertEqual(second.body['revision'], first.body['revision'])
        self.assertEqual(self.f.effects(), (1, 1, 1, 1))
        events = self.commands.read_events('acct-1', 'project-1', after=0)
        self.assertEqual(events.events[0].data['operation_id'], 'op-1')

    def test_same_operation_different_payload_is_409_without_effect(self):
        self.f.call(body())
        response = self.f.call(body(payload={'task_id': 'other'}))
        self.assertEqual(response.status, 409)
        self.assertEqual(response.body['error']['code'], 'idempotency_conflict')
        self.assertEqual(self.f.effects(), (1, 1, 1, 1))

    def test_stale_revision_is_conflict_and_has_no_side_effects(self):
        self.f.call(body())
        response = self.f.call(body(operation='op-2', revision=0, payload={'task_id': 'task-2'}))
        self.assertEqual(response.status, 409)
        self.assertEqual(response.body['error']['code'], 'revision_conflict')
        self.assertEqual(response.body['current_revision'], 1)
        self.assertEqual(self.f.effects(), (1, 1, 1, 1))

    def test_quota_failure_is_atomic(self):
        self.commands.set_quota_usage('acct-1', 'tasks.active', 2)
        response = self.f.call(body())
        self.assertEqual(response.status, 409)
        self.assertEqual(response.body['error']['code'], 'quota_exceeded')
        self.assertEqual(self.f.effects(), (0, 2, 0, 0))

    def test_permission_and_entitlement_are_checked_before_mutation(self):
        self.f.repo.put_membership(Membership('acct-1', 'user-1', permissions=('project.write',)))
        response = self.f.call(body())
        self.assertEqual(response.status, 403)
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))

    def test_operation_id_is_account_scoped(self):
        self.assertEqual(self.f.call(body(operation='same')).status, 201)
        other = self.f.call(body(operation='same'), authorization=self.f.a2, account='acct-2')
        self.assertEqual(other.status, 201)
        self.assertFalse(other.body['replayed'])
        self.assertEqual(self.f.effects('acct-2'), (1, 1, 2, 1))

    def test_invalid_command_body_does_not_touch_repository(self):
        response = self.f.call(body(revision=True))
        self.assertEqual(response.status, 400)
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))

    def test_project_mode_and_worker_control_are_revisioned(self):
        self.commands.put_worker('acct-1', 'project-1', 'worker-1')
        r = self.f.call(body('m1', 0, 'project.mode', {'mode': 'offline'}))
        self.assertEqual(r.body['revision'], 1)
        r = self.f.call(body('w1', 1, 'worker.pause', {'worker_id': 'worker-1'}))
        self.assertEqual(r.body['revision'], 2)
        r = self.f.call(body('w2', 2, 'worker.resume', {'worker_id': 'worker-1'}))
        self.assertEqual((r.status, r.body['result']['status']), (201, 'active'))

    def test_client_quota_field_is_rejected(self):
        value = body()
        value['quotas'] = [{'name': 'tasks.active', 'amount': 1}]
        response = self.f.call(value)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))


class CommandAdversarialTests(unittest.TestCase):
    """Independent review tests: idempotency, revisions, quotas, isolation, validation, audit."""

    def setUp(self):
        self.f = Fixture()
        self.commands = self.f.commands

    # --- idempotency --------------------------------------------------------------------------
    def test_replay_after_state_moved_on_returns_original_without_effects(self):
        self.f.call(body())
        self.f.call(body('op-2', 1, 'project.mode', {'mode': 'offline'}))
        before = self.f.effects()
        replay = self.f.call(body())
        self.assertEqual((replay.status, replay.body['revision'], replay.body['replayed']),
                         (200, 1, True))
        self.assertEqual(self.f.effects(), before)

    def test_other_user_same_operation_id_is_conflict_not_replay(self):
        self.f.call(body())
        response = self.f.call(body(), authorization=self.f.a1_user2)
        self.assertEqual(response.status, 409)
        self.assertEqual(response.body['error']['code'], 'idempotency_conflict')
        self.assertNotIn('result', response.body)

    def test_same_operation_id_other_project_is_conflict(self):
        self.f.call(body())
        response = self.f.call(body(), project='project-2')
        self.assertEqual(response.body['error']['code'], 'idempotency_conflict')
        self.assertEqual(self.f.effects('acct-1', 'project-2')[0], 0)

    def test_failed_operations_do_not_claim_the_operation_id(self):
        # Stale revision, quota, missing target and invalid state all leave the ID reusable.
        self.assertEqual(self.f.call(body(revision=5)).status, 409)
        self.assertEqual(self.f.call(body('op-c', 0, 'task.cancel', {'task_id': 'nope'})).status, 404)
        self.commands.set_quota_usage('acct-1', 'tasks.active', 2)
        self.assertEqual(self.f.call(body()).status, 409)
        self.commands.set_quota_usage('acct-1', 'tasks.active', 0)
        self.assertEqual(self.f.call(body()).status, 201)
        self.assertEqual(self.f.call(body('op-c', 1, 'task.cancel', {'task_id': 'task-1'})).status, 201)

    def test_replay_still_requires_current_permission(self):
        self.f.call(body())
        self.f.repo.put_membership(Membership('acct-1', 'user-1', permissions=('project.write',)))
        self.assertEqual(self.f.call(body()).status, 403)

    def test_concurrent_same_operation_applies_exactly_once(self):
        barrier = threading.Barrier(16)
        def run(_):
            barrier.wait()
            return self.f.call(body())
        with ThreadPoolExecutor(16) as pool:
            responses = list(pool.map(run, range(16)))
        statuses = sorted(r.status for r in responses)
        self.assertEqual(statuses.count(201), 1)
        self.assertEqual(statuses.count(200), 15)
        self.assertEqual(self.f.effects(), (1, 1, 1, 1))

    def test_concurrent_different_operations_same_revision_one_wins(self):
        barrier = threading.Barrier(16)
        def run(i):
            barrier.wait()
            return self.f.call(body(f'op-{i}', 0, payload={'task_id': f'task-{i}'}))
        with ThreadPoolExecutor(16) as pool:
            responses = list(pool.map(run, range(16)))
        codes = [r.body.get('error', {}).get('code') for r in responses]
        self.assertEqual([r.status for r in responses].count(201), 1)
        self.assertEqual(codes.count('revision_conflict'), 15)
        self.assertEqual(self.f.effects(), (1, 1, 1, 1))

    def test_concurrent_quota_never_overshoots(self):
        # Each request targets the revision it read; retry on revision conflict like a client.
        barrier = threading.Barrier(12)
        def run(i):
            barrier.wait()
            for _ in range(200):
                revision = self.commands.project_revision('acct-1', 'project-1')
                r = self.f.call(body(f'op-{i}', revision, payload={'task_id': f'task-{i}'}))
                if r.body.get('error', {}).get('code') != 'revision_conflict':
                    return r
            return r
        with ThreadPoolExecutor(12) as pool:
            responses = list(pool.map(run, range(12)))
        self.assertEqual([r.status for r in responses].count(201), 2)
        self.assertEqual(
            [r.body['error']['code'] for r in responses if r.status != 201].count('quota_exceeded'),
            10)
        self.assertEqual(self.commands.quota_used('acct-1', 'tasks.active'), 2)
        self.assertEqual(len(self.commands.audit()), 2)

    # --- revisions ----------------------------------------------------------------------------
    def test_expected_revision_must_be_exact_int(self):
        for value in (True, False, '0', 0.0, None, -1, [0]):
            self.assertEqual(self.f.call(body(revision=value)).status, 400, value)
        missing = body()
        del missing['expected_revision']
        self.assertEqual(self.f.call(missing).status, 400)
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))

    def test_future_revision_is_conflict(self):
        response = self.f.call(body(revision=7))
        self.assertEqual((response.status, response.body['current_revision']), (409, 0))

    # --- quotas -------------------------------------------------------------------------------
    def test_quota_ceiling_is_the_live_m08_limit(self):
        self.f.repo.put_entitlements(EntitlementSnapshot(
            'acct-1', features=('acc.web',), limits={'tasks.active': 1}))
        self.assertEqual(self.f.call(body()).status, 201)
        second = self.f.call(body('op-2', 1, payload={'task_id': 'task-2'}))
        self.assertEqual(second.body['error']['code'], 'quota_exceeded')

    def test_missing_m08_limit_fails_closed(self):
        self.f.repo.put_entitlements(EntitlementSnapshot('acct-1', features=('acc.web',)))
        response = self.f.call(body())
        self.assertEqual(response.body['error']['code'], 'quota_exceeded')
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))
        # Commands without a quota still work without the limit.
        ok = self.f.call(body('m', 0, 'project.mode', {'mode': 'offline'}))
        self.assertEqual(ok.status, 201)

    def test_cancel_releases_reservation_once(self):
        self.f.call(body())
        self.f.call(body('c1', 1, 'task.cancel', {'task_id': 'task-1'}))
        self.assertEqual(self.commands.quota_used('acct-1', 'tasks.active'), 0)
        again = self.f.call(body('c2', 2, 'task.cancel', {'task_id': 'task-1'}))
        self.assertEqual(again.body['error']['code'], 'state_conflict')
        self.assertEqual(self.commands.quota_used('acct-1', 'tasks.active'), 0)

    def test_quota_usage_is_per_account(self):
        self.f.call(body())
        self.f.call(body('op-2', 1, payload={'task_id': 'task-2'}))
        self.assertEqual(self.commands.quota_used('acct-1', 'tasks.active'), 2)
        other = self.f.call(body(), authorization=self.f.a2, account='acct-2')
        self.assertEqual(other.status, 201)
        self.assertEqual(self.commands.quota_used('acct-2', 'tasks.active'), 1)

    # --- tenant isolation ---------------------------------------------------------------------
    def test_cross_account_route_is_denied(self):
        response = self.f.call(body(), account='acct-2')
        self.assertEqual(response.status, 403)
        self.assertEqual(self.f.effects('acct-2'), (0, 0, 0, 0))

    def test_unknown_project_is_404(self):
        self.assertEqual(self.f.call(body(), project='project-9').status, 404)

    def test_task_and_worker_ids_do_not_cross_projects(self):
        self.f.call(body())
        self.commands.put_worker('acct-1', 'project-1', 'worker-1')
        cancel = self.f.call(body('c', 0, 'task.cancel', {'task_id': 'task-1'}), project='project-2')
        pause = self.f.call(body('p', 0, 'worker.pause', {'worker_id': 'worker-1'}),
                            project='project-2')
        self.assertEqual((cancel.status, pause.status), (404, 404))
        self.assertEqual(self.f.effects('acct-1', 'project-2')[0], 0)

    def test_permission_is_per_kind(self):
        self.f.repo.put_membership(Membership('acct-1', 'user-1', permissions=('task.write',)))
        self.assertEqual(self.f.call(body()).status, 201)
        for kind, payload in (('task.cancel', {'task_id': 'task-1'}),
                              ('project.mode', {'mode': 'offline'}),
                              ('worker.pause', {'worker_id': 'w'})):
            self.assertEqual(self.f.call(body('x-' + kind, 1, kind, payload)).status, 403, kind)

    def test_entitlement_and_account_loss_stop_mutations(self):
        self.f.repo.put_entitlements(EntitlementSnapshot(
            'acct-1', features=(), limits={'tasks.active': 2}))
        self.assertEqual(self.f.call(body()).status, 403)
        self.f.repo.put_entitlements(EntitlementSnapshot(
            'acct-1', features=('acc.web',), limits={'tasks.active': 2}))
        self.f.repo.put_account(AccountState('acct-1', status='suspended'))
        self.assertEqual(self.f.call(body()).status, 403)
        self.f.repo.put_account(AccountState('acct-1'))
        self.f.auth.revoke(self.f.a1[len('Bearer '):])
        self.assertEqual(self.f.call(body()).status, 401)
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))

    # --- command-specific validation ----------------------------------------------------------
    def test_payload_schemas_are_exact(self):
        bad = [
            ('task.create', {}), ('task.create', {'task_id': 'x', 'status': 'done'}),
            ('task.create', {'task_id': ''}), ('task.create', {'task_id': 'x' * 201}),
            ('task.create', {'task_id': 5}), ('task.create', {'task_id': ['x']}),
            ('task.cancel', {'task_id': {'a': 1}}), ('project.mode', {'mode': 'root'}),
            ('project.mode', {'mode': 'offline', 'force': True}),
            ('worker.pause', {'worker_id': None}), ('worker.pause', {'task_id': 'x'}),
            ('task.create', []), ('task.create', 'x'), ('task.create', None),
            ('task.create', {'task_id': 'x', 'blob': 'y' * 60_000}),
        ]
        for kind, payload in bad:
            self.assertEqual(self.f.call(body('op', 0, kind, payload)).status, 400, (kind, payload))
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))

    def test_body_schema_is_exact(self):
        extra = body()
        extra['kind_override'] = 'project.mode'
        for value in (extra, [], 'x', None, 5, {'kind': 'task.create'},
                      body(kind='task.delete'), body(kind=['task.create']),
                      body(operation=''), body(operation=7), body(operation='x' * 201)):
            self.assertEqual(self.f.call(value).status, 400, value)
        self.assertEqual(self.f.effects(), (0, 0, 0, 0))

    def test_state_transitions(self):
        self.commands.put_worker('acct-1', 'project-1', 'worker-1')
        self.assertEqual(self.f.call(body('a', 0, 'project.mode', {'mode': 'online'}))
                         .body['error']['code'], 'state_conflict')
        self.assertEqual(self.f.call(body('b', 0, 'worker.resume', {'worker_id': 'worker-1'}))
                         .body['error']['code'], 'state_conflict')
        self.assertEqual(self.f.call(body('c', 0, 'worker.pause', {'worker_id': 'ghost'})).status, 404)
        self.f.call(body())
        dup = self.f.call(body('d', 1, 'task.create', {'task_id': 'task-1'}))
        self.assertEqual(dup.body['error']['code'], 'state_conflict')
        self.assertEqual(self.f.effects(), (1, 1, 1, 1))
        self.assertNotIn('ghost', self.commands.project('acct-1', 'project-1')['workers'])

    # --- audit / events -----------------------------------------------------------------------
    def test_audit_and_event_identify_the_operation_without_secrets(self):
        self.f.call(body())
        (audit,) = self.commands.audit()
        (event,) = self.commands.read_events('acct-1', 'project-1', after=0).events
        self.assertEqual(audit, {
            'operation_id': 'op-1', 'account_id': 'acct-1', 'project_id': 'project-1',
            'actor_user_id': 'user-1', 'kind': 'task.create', 'revision': 1, 'at': 1000.0})
        self.assertEqual((event.account_id, event.project_id, event.kind, event.at),
                         ('acct-1', 'project-1', 'command.applied', 1000.0))
        self.assertEqual(event.data['actor_user_id'], 'user-1')
        self.assertEqual(event.data['revision'], 1)
        token = self.f.a1[len('Bearer '):]
        self.assertNotIn(token, repr(self.commands.audit()) + repr(event))

    def test_repository_fault_is_opaque_500_without_effects(self):
        class Broken(InMemoryCommandRepository):
            def execute(self, command, *, limits):
                raise ValueError('driver said: row acct-1 secret')
        self.f.api.commands = Broken()
        response = self.f.call(body())
        self.assertEqual(response.status, 500)
        self.assertNotIn('secret', repr(response.body))

    def test_repository_direct_contract(self):
        cmd = CommandRequest('same', 'acct-x', 'p', 'task.create', 0, {'task_id': 't'}, 'u')
        self.commands.put_project('acct-x', 'p')
        with self.assertRaises(QuotaExceeded):
            self.commands.execute(cmd, limits={})
        self.commands.execute(cmd, limits={'tasks.active': 1})
        other = CommandRequest('same', 'acct-x', 'p', 'task.create', 0, {'task_id': 't'}, 'u2')
        with self.assertRaises(IdempotencyConflict):
            self.commands.execute(other, limits={'tasks.active': 1})


if __name__ == '__main__':
    unittest.main()
