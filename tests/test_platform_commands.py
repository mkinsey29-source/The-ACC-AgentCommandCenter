import unittest

from acc.auth import (
    AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
    Membership, UserState, VerifiedIdentity,
)
from acc.platform.api import PlatformApi
from acc.platform.command_memory import InMemoryCommandRepository
from acc.platform.commands import CommandRequest, IdempotencyConflict, QuotaReservation


class EmptyReads:
    pass

class EmptyEvents:
    pass


class Verifier:
    id = 'openai'
    def verify(self, assertion):
        return VerifiedIdentity('openai', assertion['subject'])


class CommandMutationTests(unittest.TestCase):
    def setUp(self):
        repo = InMemoryAuthRepository()
        repo.put_user(UserState('user-1'))
        identity = VerifiedIdentity('openai', 'subject-1')
        repo.bind_identity(identity, 'user-1')
        repo.put_account(AccountState('acct-1'))
        repo.put_membership(Membership(
            'acct-1', 'user-1', permissions=(
                'project.write','task.write','task.cancel','worker.control')))
        repo.put_entitlements(EntitlementSnapshot('acct-1', features=('acc.web',)))
        self.auth = AuthService(repo, identity_verifiers=(Verifier(),))
        self.token = self.auth.exchange_identity('openai', {'subject':'subject-1'}, 'acct-1')
        self.authorization = 'Bearer ' + self.token
        self.commands = InMemoryCommandRepository()
        self.commands.put_project('acct-1','project-1')
        self.commands.set_quota('acct-1','tasks.active',limit=2)
        self.api = PlatformApi(self.auth, EmptyReads(), EmptyEvents(), self.commands)

    def body(self, operation="op-1", revision=0, **extra):
        value = {
            'operation_id': operation, 'kind':'task.create',
            'expected_revision': revision, 'payload':{'task_id':'task-1'},
        }
        value.update(extra)
        return value

    def test_command_applies_once_and_safe_retry_replays(self):
        body = self.body(quotas=[{"name":"tasks.active","amount":1}])
        first = self.api.command(self.authorization,'acct-1','project-1',body)
        second = self.api.command(self.authorization,'acct-1','project-1',body)
        self.assertEqual(first.status,201)
        self.assertEqual(second.status,200)
        self.assertFalse(first.body['replayed'])
        self.assertTrue(second.body['replayed'])
        self.assertEqual(self.commands.project_revision('acct-1','project-1'),1)
        self.assertEqual(self.commands.quota_used('acct-1','tasks.active'),1)
        self.assertEqual(len(self.commands.audit()),1)
        events=self.commands.read_events('acct-1','project-1',after=0)
        self.assertEqual(len(events.events),1)
        self.assertEqual(events.events[0].data['operation_id'],'op-1')

    def test_same_operation_different_payload_is_409_without_effect(self):
        self.api.command(self.authorization,'acct-1','project-1',self.body())
        changed=self.body(payload={'task_id':'other'})
        response=PlatformApi.handle(self.api.command,self.authorization,'acct-1','project-1',changed)
        self.assertEqual(response.status,409)
        self.assertEqual(response.body['error']['code'],'idempotency_conflict')
        self.assertEqual(self.commands.project_revision('acct-1','project-1'),1)

    def test_stale_revision_is_conflict_and_has_no_side_effects(self):
        self.api.command(self.authorization,'acct-1','project-1',self.body())
        stale=self.body(operation='op-2',revision=0,payload={'task_id':'task-2'})
        response=PlatformApi.handle(self.api.command,self.authorization,'acct-1','project-1',stale)
        self.assertEqual(response.status,409)
        self.assertEqual(response.body['error']['code'],'revision_conflict')
        self.assertEqual(response.body['current_revision'],1)
        self.assertEqual(len(self.commands.audit()),1)

    def test_quota_failure_is_atomic(self):
        body=self.body(quotas=[{'name':'tasks.active','amount':3}])
        response=PlatformApi.handle(self.api.command,self.authorization,'acct-1','project-1',body)
        self.assertEqual(response.status,409)
        self.assertEqual(response.body['error']['code'],'quota_exceeded')
        self.assertEqual(self.commands.project_revision('acct-1','project-1'),0)
        self.assertEqual(self.commands.quota_used('acct-1','tasks.active'),0)
        self.assertEqual(self.commands.audit(),())

    def test_permission_and_entitlement_are_checked_before_mutation(self):
        # Remove task.write after the session was issued.
        self.auth.repository.put_membership(Membership(
            'acct-1','user-1',permissions=('project.write',)))
        response=PlatformApi.handle(self.api.command,self.authorization,'acct-1','project-1',self.body())
        self.assertEqual(response.status,403)
        self.assertEqual(self.commands.project_revision('acct-1','project-1'),0)

    def test_operation_id_is_account_scoped(self):
        # The repository key is (account, operation); same string can exist in another tenant.
        command=CommandRequest('same','acct-x','project-x','project.mode',0,{'mode':'offline'},'user-x')
        self.commands.put_project('acct-x','project-x')
        result=self.commands.execute(command)
        self.assertEqual(result.operation_id,'same')

    def test_invalid_command_body_does_not_touch_repository(self):
        response=PlatformApi.handle(self.api.command,self.authorization,'acct-1','project-1',
                                    {'operation_id':'x','kind':'task.create','expected_revision':True})
        self.assertEqual(response.status,400)
        self.assertEqual(self.commands.project_revision('acct-1','project-1'),0)

    def test_project_mode_and_worker_control_are_revisioned(self):
        mode={'operation_id':'m1','kind':'project.mode','expected_revision':0,'payload':{'mode':'offline'}}
        r=self.api.command(self.authorization,'acct-1','project-1',mode)
        self.assertEqual(r.body['revision'],1)
        pause={'operation_id':'w1','kind':'worker.pause','expected_revision':1,'payload':{'worker_id':'worker-1'}}
        r=self.api.command(self.authorization,'acct-1','project-1',pause)
        self.assertEqual(r.body['revision'],2)

    def test_duplicate_quota_names_rejected_before_execution(self):
        body=self.body(quotas=[{'name':'tasks.active','amount':1},{'name':'tasks.active','amount':1}])
        response=PlatformApi.handle(self.api.command,self.authorization,'acct-1','project-1',body)
        self.assertEqual(response.status,400)
        self.assertEqual(self.commands.project_revision('acct-1','project-1'),0)


if __name__ == '__main__':
    unittest.main()
