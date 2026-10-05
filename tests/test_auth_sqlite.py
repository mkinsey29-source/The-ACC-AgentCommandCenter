import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from acc.auth import (
    AccountState, AuthenticationError, AuthorizationError, AuthService, EntitlementSnapshot,
    Membership, SQLiteAuthRepository, UserState, VerifiedIdentity,
)


class Clock:
    def __call__(self):
        return 1_000_000


class SQLiteAuthRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'auth.sqlite3'
        self.repo = SQLiteAuthRepository(self.path)
        self.identity = VerifiedIdentity('openai', 'subject-1', 'display@example.test')
        self.repo.put_user(UserState('user-1'))
        self.repo.bind_identity(self.identity, 'user-1')
        self.repo.put_account(AccountState('acct-1'))
        self.repo.put_membership(Membership(
            'acct-1', 'user-1', 'owner', ('project.read', 'task.run')))
        self.repo.put_entitlements(EntitlementSnapshot(
            'acct-1', ('acc.web',), {'projects.active': 5}, revision=2))
        self.auth = AuthService(self.repo, clock=Clock())

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_session_and_identity_survive_repository_restart_without_plaintext(self):
        token = self.auth._issue_session(self.identity, 'acct-1')
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.repo.close()
        self.repo = SQLiteAuthRepository(self.path)
        self.auth = AuthService(self.repo, clock=Clock())
        context = self.auth.authenticate(token, account_id='acct-1')
        self.assertEqual(context.session.user_id, 'user-1')
        self.assertEqual(context.entitlements.revision, 2)
        self.assertEqual(self.repo.resolve_identity(self.identity), 'user-1')
        self.assertEqual(self.repo.session_digests(), (digest,))
        with sqlite3.connect(self.path) as db:
            persisted = '\n'.join(str(row) for row in db.execute(
                "SELECT token_digest,session_id FROM m08_sessions"))
            self.assertNotIn(token, persisted)
            self.assertEqual(db.execute('SELECT count(*) FROM m08_sessions').fetchone()[0], 1)

    def test_digest_is_insert_only_and_revoke_survives_restart(self):
        token = self.auth._issue_session(self.identity, 'acct-1')
        digest = hashlib.sha256(token.encode()).hexdigest()
        record = self.repo.session(digest)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save_session(digest, record)
        self.assertTrue(self.repo.revoke_session(digest))
        self.assertFalse(self.repo.revoke_session(digest))
        self.repo.close()
        self.repo = SQLiteAuthRepository(self.path)
        self.auth = AuthService(self.repo, clock=Clock())
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate(token)

    def test_identity_cannot_be_rebound_and_foreign_provisioning_is_refused(self):
        self.repo.put_user(UserState('user-2'))
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.repo.bind_identity(self.identity, 'user-2')
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.put_membership(Membership('missing-account', 'user-1'))

    def test_authenticate_uses_one_snapshot_read(self):
        token = self.auth._issue_session(self.identity, 'acct-1')
        original = self.repo.auth_snapshot
        calls = []
        self.repo.auth_snapshot = lambda digest: (calls.append(digest) or original(digest))
        self.repo.session = lambda *_: self.fail('authenticate must not do a separate session read')
        context = self.auth.authenticate(token)
        self.assertEqual(context.account.account_id, 'acct-1')
        self.assertEqual(len(calls), 1)

    def test_snapshot_detects_live_membership_and_entitlement_changes(self):
        token = self.auth._issue_session(self.identity, 'acct-1')
        self.repo.put_entitlements(EntitlementSnapshot('acct-1', features=(), revision=3))
        with self.assertRaisesRegex(AuthorizationError, 'Entitlement required'):
            self.auth.authorize(token, account_id='acct-1', entitlements=('acc.web',))
        self.repo.remove_membership('acct-1', 'user-1')
        with self.assertRaisesRegex(AuthorizationError, 'membership is no longer active'):
            self.auth.authenticate(token)


if __name__ == '__main__':
    unittest.main()
