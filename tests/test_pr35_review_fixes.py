"""Regression tests for the fresh independent readiness review of PR #35 (2026-10-17) and its re-review (2026-10-18).

Stable IDs follow the reviewer's report: ACC-PR35-20261017-01, -03, -04, -05, -06. Each test fails on 4bf4ebc.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acc.auth.sqlite_repository import SQLiteAuthRepository
from acc.platform.sqlite_repository import SQLiteCommandRepository, _Transaction
from acc.staging import StagingApplication

REPO = Path(__file__).resolve().parent.parent


class SelfRollingConnection:
    """A connection whose COMMIT fails after SQLite has already rolled the transaction back by itself
    (a full disk, an I/O error), as the reviewer observed with a real file-size limit."""

    def __init__(self, real):
        self.real, self.armed, self.closed_by_code = real, False, False

    def execute(self, sql, *args):
        if sql == 'COMMIT' and self.armed:
            self.real.execute('ROLLBACK')
            raise sqlite3.OperationalError('disk I/O error')
        return self.real.execute(sql, *args)

    def close(self):
        self.closed_by_code = True
        return self.real.close()

    def __getattr__(self, name):
        return getattr(self.real, name)


class AlreadyRolledBack(unittest.TestCase):
    """ACC-PR35-20261017-01: a commit failure that SQLite rolled back itself must not retire the connection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repository = SQLiteCommandRepository(Path(self.tmp.name) / 'platform.sqlite3', clock=lambda: 123.0)
        self.addCleanup(lambda: self.repository._db.real.close() if isinstance(self.repository._db, SelfRollingConnection)
                        else self.repository.close())
        self.repository.put_project('acct', 'project')
        self.connection = SelfRollingConnection(self.repository._db)
        self.repository._db = self.connection

    def test_the_commit_error_surfaces_and_the_repository_stays_usable(self):
        self.connection.armed = True
        with self.assertRaisesRegex(sqlite3.OperationalError, 'disk I/O error'):
            self.repository.set_quota_usage('acct', 'tasks.active', 2)
        self.assertFalse(self.connection.closed_by_code, 'the connection was sound and must not be closed')
        self.connection.armed = False
        self.assertEqual(self.repository.quota_used('acct', 'tasks.active'), 0, 'the failed write did not persist')
        self.repository.set_quota_usage('acct', 'tasks.active', 1)
        self.assertEqual(self.repository.quota_used('acct', 'tasks.active'), 1)
        self.assertEqual(self.repository.project('acct', 'project')['revision'], 0)

    def test_a_failed_command_leaves_nothing_half_written_and_the_retry_applies_once(self):
        from acc.platform import CommandRequest
        command = CommandRequest('op-1', 'acct', 'project', 'task.create', 0, {'task_id': 't1'}, 'u1')
        self.connection.armed = True
        with self.assertRaises(sqlite3.OperationalError):
            self.repository.execute(command, limits={'tasks.active': 2})
        self.connection.armed = False
        self.assertEqual(self.repository.project('acct', 'project')['revision'], 0)
        self.assertEqual(self.repository.read_events('acct', 'project', after=0, limit=10).events, ())
        result = self.repository.execute(command, limits={'tasks.active': 2})
        self.assertEqual((result.revision, result.replayed), (1, False))
        self.assertTrue(self.repository.execute(command, limits={'tasks.active': 2}).replayed)

    def test_a_body_error_after_sqlite_rolled_back_does_not_close_the_connection(self):
        class NothingOpen:
            in_transaction = False
            closed = False

            def execute(self, statement):
                if statement == 'ROLLBACK':
                    raise sqlite3.OperationalError('cannot rollback - no transaction is active')

            def close(self):
                self.closed = True
        connection = NothingOpen()
        with self.assertRaisesRegex(ValueError, 'body failure'):
            with _Transaction(connection):
                raise ValueError('body failure')
        self.assertFalse(connection.closed, 'nothing was open, so nothing needed rolling back or closing')

    def test_a_rollback_that_was_needed_and_failed_still_closes_the_connection(self):
        class Stuck:
            in_transaction = True
            closed = False

            def execute(self, statement):
                if statement in ('COMMIT', 'ROLLBACK'):
                    raise sqlite3.OperationalError(statement.lower() + ' failure')

            def close(self):
                self.closed = True
        for failing_body in (False, True):
            connection = Stuck()
            with self.assertRaises((sqlite3.OperationalError, ValueError)):
                with _Transaction(connection):
                    if failing_body:
                        raise ValueError('body failure')
            self.assertTrue(connection.closed, f'failing_body={failing_body}')

    def test_an_active_transaction_is_still_rolled_back(self):
        self.repository._db = self.connection.real
        self.repository._db.set_authorizer(
            lambda action, arg1, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT' else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):
            self.repository.set_quota_usage('acct', 'tasks.active', 2)
        # Python 3.10 cannot clear an authorizer with None (3.11+ can); install a permissive one instead.
        self.repository._db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertFalse(self.repository._db.in_transaction)
        self.assertEqual(self.repository.quota_used('acct', 'tasks.active'), 0)


class AuthConstructorCleanup(unittest.TestCase):
    """ACC-PR35-20261017-04: a constructor failure at any step releases the connection."""

    def test_a_file_that_is_not_a_database_closes_the_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'auth.sqlite3'
            path.write_bytes(b'this is not a sqlite database, only text' * 40)
            connections = []
            real_connect = sqlite3.connect

            def recording_connect(*args, **kwargs):
                connections.append(real_connect(*args, **kwargs))
                return connections[-1]
            with mock.patch('acc.auth.sqlite_repository.sqlite3.connect', side_effect=recording_connect):
                with self.assertRaises(sqlite3.DatabaseError):
                    SQLiteAuthRepository(path)
            self.assertEqual(len(connections), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')

    def test_a_failing_pragma_closes_the_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            connections = []
            real_connect = sqlite3.connect

            def failing_connect(*args, **kwargs):
                real = real_connect(*args, **kwargs)
                connections.append(real)

                class Wrapper:
                    def __getattr__(self, name):
                        return getattr(real, name)

                    def execute(self, sql, *rest):
                        if sql.startswith('PRAGMA foreign_keys'):
                            raise sqlite3.OperationalError('pragma refused')
                        return real.execute(sql, *rest)

                    def close(self):
                        real.close()
                return Wrapper()
            with mock.patch('acc.auth.sqlite_repository.sqlite3.connect', side_effect=failing_connect):
                with self.assertRaisesRegex(sqlite3.OperationalError, 'pragma refused'):
                    SQLiteAuthRepository(Path(directory) / 'auth.sqlite3')
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')


class AuthConstructorInterrupt(unittest.TestCase):
    """ACC-PR35R-20261018-04: the constructor cleanup must also run for KeyboardInterrupt (a BaseException)."""

    def test_an_interrupt_during_schema_creation_closes_the_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            connections = []
            real_connect = sqlite3.connect

            def recording_connect(*args, **kwargs):
                connections.append(real_connect(*args, **kwargs))
                return connections[-1]
            with mock.patch('acc.auth.sqlite_repository.sqlite3.connect', side_effect=recording_connect), \
                    mock.patch.object(SQLiteAuthRepository, '_create_schema', side_effect=KeyboardInterrupt()):
                with self.assertRaises(KeyboardInterrupt):
                    SQLiteAuthRepository(Path(directory) / 'auth.sqlite3')
            self.assertEqual(len(connections), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')


class StagingClose(unittest.TestCase):
    """ACC-PR35-20261017-06: one failing closer must not leave the others open."""

    def app(self):
        app = StagingApplication(origin='https://console.example.test', bootstrap_secret='x' * 40)
        app._closers.clear()
        return app

    def test_every_closer_runs_and_the_first_failure_is_raised(self):
        app = self.app()
        calls = []

        def closer(name, fail=None):
            def run():
                calls.append(name)
                if fail:
                    raise fail
            return run
        app._closers.extend([closer('auth'), closer('commands', RuntimeError('first')), closer('reads', RuntimeError('second'))])
        with self.assertRaisesRegex(RuntimeError, 'second'):  # closers run last-in first-out; the first failure is 'reads'
            app.close()
        self.assertEqual(calls, ['reads', 'commands', 'auth'])
        app.close()  # nothing left: a second close is a no-op
        self.assertEqual(calls, ['reads', 'commands', 'auth'])

    def test_a_clean_close_still_returns_quietly(self):
        app = self.app()
        called = []
        app._closers.append(lambda: called.append(1))
        app.close()
        self.assertEqual(called, [1])

    def test_an_interrupt_in_one_closer_does_not_strand_the_others(self):
        """ACC-PR35R-20261018-01: KeyboardInterrupt/SystemExit are not Exceptions; every closer must still run."""
        app = self.app()
        calls = []

        def interrupted():
            calls.append('commands')
            raise KeyboardInterrupt()
        app._closers.extend([lambda: calls.append('auth'), interrupted])
        with self.assertRaises(KeyboardInterrupt):
            app.close()
        self.assertEqual(calls, ['commands', 'auth'])
        app.close()  # nothing left pending
        self.assertEqual(calls, ['commands', 'auth'])

    def test_a_cleanup_failure_does_not_replace_the_startup_error(self):
        """ACC-PR35R-20261018-02: the caller must see why startup failed, not a secondary close failure."""
        with tempfile.TemporaryDirectory() as directory:
            real_close = SQLiteCommandRepository.close

            def failing_close(self):
                real_close(self)  # release the connection for real, then report a failure
                raise RuntimeError('close boom')
            with mock.patch.object(SQLiteCommandRepository, 'put_worker', side_effect=ValueError('startup boom')), \
                    mock.patch.object(SQLiteCommandRepository, 'close', failing_close):
                with self.assertRaisesRegex(ValueError, 'startup boom'):
                    StagingApplication(origin='https://console.example.test', bootstrap_secret='x' * 40,
                                       database=Path(directory) / 'staging.sqlite3')


class ParityAndDocs(unittest.TestCase):
    """ACC-PR35-20261017-03 and -05."""

    def test_the_parity_suite_cross_checks_the_missing_ceiling_row(self):
        from test_platform_command_parity import SUITE
        self.assertTrue(any(row[5] == {} for row in SUITE), 'the parity suite needs a row with no quota ceiling')

    def test_the_missing_ceiling_is_refused_by_both_stores_through_the_shared_rules(self):
        from acc.platform import CommandRequest, InMemoryCommandRepository, QuotaExceeded
        with tempfile.TemporaryDirectory() as directory:
            stores = [InMemoryCommandRepository(clock=lambda: 1.0),
                      SQLiteCommandRepository(Path(directory) / 'p.sqlite3', clock=lambda: 1.0)]
            try:
                for store in stores:
                    store.put_project('acct', 'p')
                    with self.assertRaises(QuotaExceeded):
                        store.execute(CommandRequest('op', 'acct', 'p', 'task.create', 0, {'task_id': 't'}, 'u'), limits={})
                    self.assertEqual(store.project('acct', 'p')['revision'], 0)
            finally:
                stores[1].close()

    def test_the_events_document_no_longer_says_staging_is_unchanged(self):
        text = (REPO / 'docs' / 'M09-PLATFORM-API-EVENTS.md').read_text()
        self.assertNotIn('remains separate and was not changed by this adapter', text)
        self.assertIn('ACC_STAGING_DATABASE', text)

    def test_no_staging_text_still_claims_the_durable_mode_is_in_memory(self):
        """ACC-PR35R-20261018-03."""
        runtime = (REPO / 'docs' / 'M09-STAGING-RUNTIME.md').read_text()
        self.assertNotIn('reads from that same\nin-memory state', runtime)
        self.assertNotIn('Isolated in-memory staging composition', (REPO / 'acc' / 'staging.py').read_text())


if __name__ == '__main__':
    unittest.main()
