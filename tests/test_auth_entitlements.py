import unittest

from acc.auth import (
    AccountState,
    AuthenticationError,
    AuthorizationError,
    AuthError,
    AuthService,
    EntitlementSnapshot,
    IdentityVerificationError,
    InMemoryAuthRepository,
    Membership,
    UserState,
    VerifiedIdentity,
)
from acc.state_authority import policy_for


class Clock:
    def __init__(self, value=1_000_000):
        self.value = value

    def __call__(self):
        return self.value


class AuthEntitlementTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.repo = InMemoryAuthRepository()
        self.identity = VerifiedIdentity('openai', 'subject-123', 'owner@example.test')
        self.repo.put_user(UserState('user-1'))
        self.repo.bind_identity(self.identity, 'user-1')
        self.repo.put_account(AccountState('acct-1'))
        self.repo.put_membership(Membership(
            'acct-1',
            'user-1',
            role='owner',
            permissions=('project.read', 'project.write', 'task.run'),
        ))
        self.repo.put_entitlements(EntitlementSnapshot(
            'acct-1',
            features=('acc.web', 'workers.openai'),
            limits={'projects.active': 10, 'workers.concurrent': 4},
            revision=1,
        ))
        self.auth = AuthService(self.repo, clock=self.clock)

    def token(self):
        return self.auth.create_session(self.identity, 'acct-1')

    def test_session_is_opaque_and_repository_stores_only_digest(self):
        token = self.token()
        self.assertTrue(token.startswith('accs_'))
        self.assertNotIn(token, self.repo.session_digests())
        self.assertEqual(len(self.repo.session_digests()), 1)
        digest = self.repo.session_digests()[0]
        self.assertEqual(len(digest), 64)
        self.assertNotIn('subject-123', digest)

    def test_authentication_resolves_current_membership_and_entitlements(self):
        token = self.token()
        context = self.auth.authenticate(token)
        self.assertEqual(context.membership.role, 'owner')
        self.assertEqual(context.account.account_id, 'acct-1')
        self.assertTrue(context.entitlements.has('acc.web'))
        self.assertEqual(context.entitlements.limit('projects.active'), 10)

    def test_permissions_and_entitlements_are_exact_and_both_required(self):
        token = self.token()
        context = self.auth.authorize(
            token,
            permissions=('project.read', 'task.run'),
            entitlements=('acc.web', 'workers.openai'),
        )
        self.assertEqual(context.session.user_id, 'user-1')
        with self.assertRaisesRegex(AuthorizationError, 'Permission denied'):
            self.auth.authorize(token, permissions=('connector.manage',))
        with self.assertRaisesRegex(AuthorizationError, 'Entitlement required'):
            self.auth.authorize(token, entitlements=('desktop.node',))

    def test_entitlement_change_takes_effect_without_reissuing_token(self):
        token = self.token()
        self.auth.authorize(token, entitlements=('acc.web',))
        self.repo.put_entitlements(EntitlementSnapshot(
            'acct-1',
            features=('workers.openai',),
            limits={'projects.active': 2},
            revision=2,
        ))
        with self.assertRaisesRegex(AuthError, 'Entitlement required'):
            self.auth.authorize(token, entitlements=('acc.web',))
        with self.assertRaisesRegex(AuthError, 'allows 2, requested 3'):
            self.auth.require_limit(token, 'projects.active', 3)

    def test_membership_removal_or_account_suspend_invalidates_existing_token(self):
        token = self.token()
        self.assertTrue(self.repo.remove_membership('acct-1', 'user-1'))
        with self.assertRaisesRegex(AuthError, 'membership is no longer active'):
            self.auth.authenticate(token)

        self.repo.put_membership(Membership('acct-1', 'user-1', permissions=('project.read',)))
        token = self.token()
        self.repo.put_account(AccountState('acct-1', status='suspended', revision=1))
        with self.assertRaisesRegex(AuthError, 'account is not active'):
            self.auth.authenticate(token)

    def test_expiry_and_revocation(self):
        token = self.auth.create_session(self.identity, 'acct-1', ttl_seconds=60)
        self.clock.value += 59
        self.auth.authenticate(token)
        self.clock.value += 1
        with self.assertRaisesRegex(AuthenticationError, 'expired'):
            self.auth.authenticate(token)

        self.clock.value += 1
        token = self.token()
        self.assertTrue(self.auth.revoke(token))
        self.assertFalse(self.auth.revoke(token))
        with self.assertRaisesRegex(AuthenticationError, 'not active'):
            self.auth.authenticate(token)

    def test_cross_account_membership_is_required(self):
        self.repo.put_account(AccountState('acct-2'))
        self.repo.put_entitlements(EntitlementSnapshot('acct-2', features=('acc.web',)))
        with self.assertRaisesRegex(AuthError, 'not a member'):
            self.auth.create_session(self.identity, 'acct-2')

    def test_identity_binding_is_stable(self):
        self.repo.put_user(UserState('user-2'))
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.repo.bind_identity(self.identity, 'user-2')

    def test_user_suspend_invalidates_existing_token(self):
        token = self.token()
        self.repo.put_user(UserState('user-1', status='suspended', revision=1))
        with self.assertRaisesRegex(AuthError, 'user is not active'):
            self.auth.authenticate(token)

    def test_account_scope_is_bound_into_the_session(self):
        token = self.token()
        with self.assertRaisesRegex(AuthorizationError, 'not valid for this account'):
            self.auth.authenticate(token, account_id='acct-2')
        with self.assertRaisesRegex(AuthorizationError, 'not valid for this account'):
            self.auth.authorize(token, account_id='acct-2')

    def test_identity_verifier_is_the_login_trust_boundary(self):
        class Verifier:
            id = 'openai'

            def verify(self, assertion):
                self.last_assertion = assertion
                return VerifiedIdentity('openai', assertion['subject'])

        verifier = Verifier()
        auth = AuthService(self.repo, clock=self.clock, identity_verifiers=(verifier,))
        token = auth.exchange_identity('openai', {'subject': 'subject-123'}, 'acct-1')
        self.assertEqual(auth.authenticate(token).session.user_id, 'user-1')
        self.assertEqual(verifier.last_assertion, {'subject': 'subject-123'})
        with self.assertRaisesRegex(AuthenticationError, 'not configured'):
            auth.exchange_identity('google', {'subject': 'x'}, 'acct-1')

        class RejectingVerifier:
            id = 'google'

            def verify(self, assertion):
                raise IdentityVerificationError('provider rejected assertion')

        rejecting = AuthService(
            self.repo, clock=self.clock, identity_verifiers=(RejectingVerifier(),))
        with self.assertRaisesRegex(AuthenticationError, 'could not be verified'):
            rejecting.exchange_identity('google', {'token': 'redacted'}, 'acct-1')

    def test_entitlement_limits_are_nonnegative_and_dotted(self):
        with self.assertRaises(ValueError):
            EntitlementSnapshot('acct-1', limits={'not valid': 1})
        with self.assertRaises(ValueError):
            EntitlementSnapshot('acct-1', limits={'projects.active': -1})

    def test_m08_preserves_m02_credential_and_account_authority(self):
        self.assertEqual(
            policy_for('credential'),
            {'kind': 'credential', 'authority': 'holder', 'sync': 'never'},
        )
        self.assertEqual(
            policy_for('account'),
            {'kind': 'account', 'authority': 'platform', 'sync': 'read_replica'},
        )
        self.assertFalse(hasattr(self.identity, 'access_token'))
        self.assertFalse(hasattr(self.identity, 'refresh_token'))


if __name__ == '__main__':
    unittest.main()
