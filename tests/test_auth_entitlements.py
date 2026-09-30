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

    def token(self, **kwargs):
        return self.auth._issue_session(self.identity, 'acct-1', **kwargs)

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
            account_id='acct-1',
        )
        self.assertEqual(context.session.user_id, 'user-1')
        with self.assertRaisesRegex(AuthorizationError, 'Permission denied'):
            self.auth.authorize(token, permissions=('connector.manage',), account_id='acct-1')
        with self.assertRaisesRegex(AuthorizationError, 'Entitlement required'):
            self.auth.authorize(token, entitlements=('desktop.node',), account_id='acct-1')

    def test_entitlement_change_takes_effect_without_reissuing_token(self):
        token = self.token()
        self.auth.authorize(token, entitlements=('acc.web',), account_id='acct-1')
        self.repo.put_entitlements(EntitlementSnapshot(
            'acct-1',
            features=('workers.openai',),
            limits={'projects.active': 2},
            revision=2,
        ))
        with self.assertRaisesRegex(AuthError, 'Entitlement required'):
            self.auth.authorize(token, entitlements=('acc.web',), account_id='acct-1')
        with self.assertRaisesRegex(AuthError, 'allows 2, requested 3'):
            self.auth.require_limit(token, 'projects.active', 3, account_id='acct-1')

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
        token = self.token(ttl_seconds=60)
        self.clock.value += 59
        self.auth.authenticate(token)
        self.clock.value += 1
        with self.assertRaisesRegex(AuthenticationError, 'not active'):
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
        with self.assertRaisesRegex(AuthError, 'not available to this user'):
            self.auth._issue_session(self.identity, 'acct-2')

    def test_identity_binding_is_stable(self):
        self.repo.put_user(UserState('user-2'))
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.repo.bind_identity(self.identity, 'user-2')

    def test_user_suspend_invalidates_existing_token(self):
        token = self.token()
        self.repo.put_user(UserState('user-1', status='suspended', revision=1))
        with self.assertRaisesRegex(AuthenticationError, 'user is not active'):
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



class AuthSecurityReviewTests(unittest.TestCase):
    """Defects found in the independent security review of PR #29."""

    def setUp(self):
        self.clock = Clock()
        self.repo = InMemoryAuthRepository()
        self.repo.put_user(UserState('user-1'))
        self.repo.bind_identity(VerifiedIdentity('openai', 'subject-123'), 'user-1')
        for account in ('acct-1', 'acct-2'):
            self.repo.put_account(AccountState(account))
            self.repo.put_entitlements(EntitlementSnapshot(
                account, features=('acc.web',), limits={'projects.active': 10}))
        self.repo.put_membership(Membership('acct-1', 'user-1', permissions=('project.read',)))
        self.repo.put_account(AccountState('acct-3'))

        class Verifier:
            id = 'openai'

            def verify(inner, assertion):
                return VerifiedIdentity('openai', assertion['subject'])

        self.auth = AuthService(self.repo, clock=self.clock, identity_verifiers=(Verifier(),))

    def login(self, account='acct-1', subject='subject-123', **kwargs):
        return self.auth.exchange_identity('openai', {'subject': subject}, account, **kwargs)

    def test_package_imports_and_verifier_protocol_is_runtime_checkable(self):
        from acc.auth.identity import IdentityVerifier
        self.assertTrue(issubclass(IdentityVerificationError, ValueError))
        self.assertIsInstance(self.auth.identity_verifiers['openai'], IdentityVerifier)

    def test_no_public_path_turns_an_unverified_identity_into_a_session(self):
        self.assertFalse(hasattr(self.auth, 'create_session'))

    def test_subclassed_identity_cannot_smuggle_extra_state(self):
        class Leaky(VerifiedIdentity):
            pass

        class Verifier:
            id = 'leaky'

            def verify(inner, assertion):
                return Leaky('leaky', 'subject-123')

        self.repo.bind_identity(VerifiedIdentity('leaky', 'subject-123'), 'user-1')
        auth = AuthService(self.repo, clock=self.clock, identity_verifiers=(Verifier(),))
        with self.assertRaises(AuthenticationError):
            auth.exchange_identity('leaky', {}, 'acct-1')

    def test_unexpected_verifier_failure_fails_closed_without_leaking(self):
        class Verifier:
            id = 'broken'

            def verify(inner, assertion):
                raise KeyError('provider-secret-value')

        auth = AuthService(self.repo, clock=self.clock, identity_verifiers=(Verifier(),))
        with self.assertRaises(AuthenticationError) as caught:
            auth.exchange_identity('broken', {}, 'acct-1')
        self.assertNotIn('provider-secret-value', str(caught.exception))
        for bad_provider in (None, ['openai'], 7):
            with self.subTest(provider=bad_provider), self.assertRaises(AuthenticationError):
                self.auth.exchange_identity(bad_provider, {'subject': 'subject-123'}, 'acct-1')
        with self.assertRaises(AuthenticationError):
            self.auth.exchange_identity('openai', 'not-a-mapping', 'acct-1')

    def test_login_does_not_reveal_which_accounts_exist(self):
        messages = set()
        for account in ('acct-2', 'acct-3', 'acct-does-not-exist'):
            with self.subTest(account=account), self.assertRaises(AuthorizationError) as caught:
                self.login(account)
            messages.add(str(caught.exception))
        self.repo.put_account(AccountState('acct-2', status='suspended', revision=1))
        with self.assertRaises(AuthorizationError) as caught:
            self.login('acct-2')
        messages.add(str(caught.exception))
        self.assertEqual(len(messages), 1)

    def test_session_failures_are_indistinguishable(self):
        token = self.login(ttl_seconds=60)
        self.clock.value += 60
        with self.assertRaises(AuthenticationError) as expired:
            self.auth.authenticate(token)
        with self.assertRaises(AuthenticationError) as unknown:
            self.auth.authenticate('accs_' + 'x' * 43)
        self.assertEqual(str(expired.exception), str(unknown.exception))

    def test_account_scoped_checks_require_and_enforce_the_tenant(self):
        token = self.login()
        with self.assertRaises(TypeError):
            self.auth.authorize(token, permissions=('project.read',))
        with self.assertRaises(TypeError):
            self.auth.require_limit(token, 'projects.active', 1)
        with self.assertRaises(AuthorizationError):
            self.auth.require_limit(token, 'projects.active', 1, account_id='acct-2')
        with self.assertRaises(AuthorizationError):
            self.auth.authorize(token, account_id='acct-2')
        self.auth.require_limit(token, 'projects.active', 10, account_id='acct-1')

    def test_suspended_or_closed_user_is_an_authentication_failure(self):
        token = self.login()
        self.repo.put_user(UserState('user-1', status='closed', revision=1))
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate(token)
        with self.assertRaises(AuthenticationError):
            self.login()

    def test_repository_returning_another_tenants_records_is_rejected(self):
        token = self.login()
        original = self.repo.entitlements
        self.repo.entitlements = lambda account_id: original('acct-2')
        with self.assertRaises(AuthorizationError):
            self.auth.authenticate(token, account_id='acct-1')
        self.repo.entitlements = original
        membership = self.repo.membership
        self.repo.membership = lambda account_id, user_id: Membership(
            'acct-2', user_id, permissions=('project.read',))
        with self.assertRaises(AuthorizationError):
            self.auth.authenticate(token)
        self.repo.membership = membership

    def test_invalid_ttl_is_a_request_error_not_an_auth_failure(self):
        for ttl in (59, 86401, True, 3600.0):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError) as caught:
                self.login(ttl_seconds=ttl)
            self.assertNotIsInstance(caught.exception, AuthError)

    def test_auth_errors_are_not_generic_value_errors(self):
        self.assertFalse(issubclass(AuthError, ValueError))

    def test_session_digest_cannot_be_overwritten(self):
        token = self.login()
        digest = self.repo.session_digests()[0]
        record = self.repo.session(digest)
        with self.assertRaises(ValueError):
            self.repo.save_session(digest, record)
        self.assertEqual(self.auth.authenticate(token).session, record)

    def test_malformed_tokens_are_authentication_failures(self):
        for token in (None, 7, '', 'Bearer accs_x', 'accs_' + 'a' * 600, b'accs_x'):
            with self.subTest(token=token), self.assertRaises(AuthenticationError):
                self.auth.authenticate(token)


if __name__ == '__main__':
    unittest.main()
