import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from acc.auth import (
    AccountState, AuthenticationError, AuthorizationError, AuthService, AuthStateError,
    EntitlementSnapshot, Membership, SQLiteAuthRepository, UserState, VerifiedIdentity,
)
from acc.platform import PlatformApi


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

    def test_invalid_stored_auth_state_is_an_opaque_500_not_a_client_400(self):
        class Reads:
            def projects(self, account_id):
                return []

        token = self.auth._issue_session(self.identity, 'acct-1')
        api = PlatformApi(self.auth, Reads(), None)
        self.assertEqual(PlatformApi.handle(
            api.list_projects, 'Bearer ' + token, 'acct-1').status, 200)
        for column, value in (('permissions_json', '{not json'), ('role', 'superadmin')):
            with self.subTest(column=column), sqlite3.connect(self.path) as db:
                original = db.execute(f'SELECT {column} FROM m08_memberships').fetchone()[0]
                db.execute(f'UPDATE m08_memberships SET {column}=?', (value,))
                db.commit()
                with self.assertRaises(AuthStateError):
                    self.auth.authenticate(token)
                response = PlatformApi.handle(api.list_projects, 'Bearer ' + token, 'acct-1')
                self.assertEqual((response.status, response.body['error']['code']),
                                 (500, 'internal_error'))
                db.execute(f'UPDATE m08_memberships SET {column}=?', (original,))
                db.commit()

    def test_reopening_a_current_database_does_not_need_the_writer_lock(self):
        self.repo.close()
        blocker = sqlite3.connect(self.path, isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
        try:
            self.repo = SQLiteAuthRepository(self.path, timeout=0.2)
        finally:
            blocker.execute('ROLLBACK')
            blocker.close()
        self.assertEqual(self.repo.resolve_identity(self.identity), 'user-1')

    def test_schema_version_can_advance_and_an_unknown_version_is_refused(self):
        self.repo.close()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE m08_schema_meta SET version=2')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported M08 SQLite schema version'):
            SQLiteAuthRepository(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE m08_schema_meta SET version=1')
        self.repo = SQLiteAuthRepository(self.path)

    def test_failed_rollback_closes_connection_and_preserves_original_error(self):
        class BrokenEnd:
            """Connection proxy whose COMMIT and ROLLBACK both fail, as on an I/O error."""
            def __init__(self, db):
                self.db, self.closed = db, False

            def execute(self, sql, *args):
                if sql in ('COMMIT', 'ROLLBACK'):
                    raise sqlite3.OperationalError(sql.lower() + ' failure')
                return self.db.execute(sql, *args)

            def close(self):
                self.closed = True
                self.db.close()

            def __getattr__(self, name):
                return getattr(self.db, name)

        broken = BrokenEnd(self.repo._db)
        self.repo._db = broken
        with self.assertRaisesRegex(sqlite3.OperationalError, 'commit failure'):
            self.repo.put_user(UserState('user-2'))
        self.assertTrue(broken.closed)
        self.repo = SQLiteAuthRepository(self.path)  # a fresh connection sees no partial write
        self.assertIsNone(self.repo.user('user-2'))

    def test_commit_failure_rolls_back_and_the_connection_stays_usable(self):
        token = self.auth._issue_session(self.identity, 'acct-1')
        db = self.repo._db
        db.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
                          if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT'
                          else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):
            self.repo.put_user(UserState('user-2'))
        db.set_authorizer(None)
        self.assertFalse(db.in_transaction)
        self.assertIsNone(self.repo.user('user-2'))
        self.repo.put_user(UserState('user-3'))
        self.assertEqual(self.auth.authenticate(token).session.user_id, 'user-1')

    def test_newer_schema_is_refused_without_recreating_missing_tables(self):
        self.repo.close()
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('DROP TABLE m08_sessions')
            db.execute('UPDATE m08_schema_meta SET version=2')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported M08 SQLite schema version'):
            SQLiteAuthRepository(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='m08_sessions'").fetchone())
        self.repo = SQLiteAuthRepository(Path(self.temp.name) / 'fresh.sqlite3')


    def test_database_from_before_the_provisioning_table_gains_it_on_open(self):
        token = self.auth._issue_session(self.identity, 'acct-1')
        self.repo.close()
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE m08_provisioning')
        self.repo = SQLiteAuthRepository(self.path)
        self.auth = AuthService(self.repo, clock=Clock())
        self.assertEqual(self.auth.authenticate(token).session.user_id, 'user-1')
        with sqlite3.connect(self.path) as db:
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='m08_provisioning'").fetchone())
            self.assertEqual(db.execute('SELECT version FROM m08_schema_meta').fetchone()[0], 1)


class ProvisionOnceTests(unittest.TestCase):
    """Startup fixtures apply once per store and never overwrite or resurrect durable state."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def repositories(self):
        from acc.auth import InMemoryAuthRepository
        self._stores = getattr(self, '_stores', 0) + 1  # a fresh file per call
        sqlite_repo = SQLiteAuthRepository(Path(self.temp.name) / f'auth-{self._stores}.sqlite3')
        self.addCleanup(sqlite_repo.close)
        return (('memory', InMemoryAuthRepository()), ('sqlite', sqlite_repo))

    @staticmethod
    def fixture(repo, key='fixture-v1', subject='subject-1'):
        return repo.provision_once(
            key,
            users=(UserState('user-1'),),
            accounts=(AccountState('acct-1'),),
            identities=((VerifiedIdentity('openai', subject), 'user-1'),),
            memberships=(Membership('acct-1', 'user-1', 'owner', ('project.read',)),),
            entitlements=(EntitlementSnapshot('acct-1', ('acc.web',), {'tasks.active': 5}),),
        )

    def test_applies_once_then_leaves_later_changes_alone(self):
        for name, repo in self.repositories():
            with self.subTest(repository=name):
                self.assertTrue(self.fixture(repo))
                self.assertEqual(repo.resolve_identity(VerifiedIdentity('openai', 'subject-1')),
                                 'user-1')
                repo.put_user(UserState('user-1', status='suspended'))
                self.assertTrue(repo.remove_membership('acct-1', 'user-1'))
                self.assertFalse(self.fixture(repo))
                self.assertEqual(repo.user('user-1').status, 'suspended')
                self.assertIsNone(repo.membership('acct-1', 'user-1'))

    def test_never_overwrites_records_that_already_exist(self):
        for name, repo in self.repositories():
            with self.subTest(repository=name):
                repo.put_user(UserState('user-1', status='suspended', revision=4))
                repo.put_account(AccountState('acct-1'))
                repo.put_entitlements(EntitlementSnapshot('acct-1', (), {}, revision=9))
                self.assertTrue(self.fixture(repo))
                self.assertEqual((repo.user('user-1').status, repo.user('user-1').revision),
                                 ('suspended', 4))
                self.assertEqual(repo.entitlements('acct-1').revision, 9)

    def test_identity_conflict_applies_nothing_and_records_no_key(self):
        for name, repo in self.repositories():
            with self.subTest(repository=name):
                repo.put_user(UserState('user-9'))
                repo.bind_identity(VerifiedIdentity('openai', 'taken'), 'user-9')
                with self.assertRaisesRegex(ValueError, 'already bound'):
                    self.fixture(repo, subject='taken')
                self.assertIsNone(repo.account('acct-1'))
                self.assertTrue(self.fixture(repo, subject='free'))  # the key was not consumed


    def test_refused_fixture_changes_nothing_in_either_repository(self):
        cases = (
            ('identity for a missing user', dict(
                users=(UserState('user-1'),), accounts=(AccountState('acct-1'),),
                identities=((VerifiedIdentity('openai', 'subject-x'), 'ghost'),))),
            ('duplicate identity assigned to different users', dict(
                users=(UserState('user-1'), UserState('user-2')),
                accounts=(AccountState('acct-1'),),
                identities=((VerifiedIdentity('openai', 'subject-x'), 'user-1'),
                            (VerifiedIdentity('openai', 'subject-x'), 'user-2')))),
            ('membership for a missing account', dict(
                users=(UserState('user-1'),), memberships=(Membership('ghost-acct', 'user-1'),))),
            ('entitlements for a missing account', dict(
                users=(UserState('user-1'),),
                entitlements=(EntitlementSnapshot('ghost-acct', ('acc.web',)),))),
        )
        for number, (label, fixture) in enumerate(cases):
            key = f'fixture-{number}'
            for name, repo in self.repositories():
                with self.subTest(case=label, repository=name):
                    with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                        repo.provision_once(key, **fixture)
                    self.assertIsNone(repo.user('user-1'))
                    self.assertIsNone(repo.account('acct-1'))
                    self.assertTrue(repo.provision_once(key))  # the key was not consumed


if __name__ == '__main__':
    unittest.main()
