"""Durable staging must close the stores it owns when the ASGI lifespan shuts down.

Finding: https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/pull/38#discussion_r4303886924
``StagingApplication`` opens two SQLite connections (auth, and commands/reads) and registers their closers, but
lifespan handling was delegated to ``HostedTransport``, which acknowledged shutdown without calling ``close()``.
The tests below drive the real ``lifespan.shutdown`` path and never call ``close()`` themselves, except where a
test is about calling ``close()`` twice. Acknowledgment alone is never taken as proof: each test looks at the
connections, the files, or the operating system.
"""
import asyncio
import contextlib
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from acc.staging import STAGING_ORIGIN_DEFAULT, StagingApplication

ORIGIN = STAGING_ORIGIN_DEFAULT
SECRET = 'staging-secret-' + 'k' * 32
REPO = Path(__file__).resolve().parent.parent


class Messages:
    """A scripted ASGI lifespan: the server sends ``incoming`` in order; every message the app sends is kept."""

    def __init__(self, *incoming, on_send=None):
        self.incoming = list(incoming)
        self.sent = []
        self.on_send = on_send

    async def receive(self):
        return self.incoming.pop(0)

    async def send(self, message):
        if self.on_send:
            self.on_send(message)
        self.sent.append(message['type'] if 'message' not in message else (message['type'], message['message']))


STARTUP, SHUTDOWN = {'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}


async def lifespan(app, *incoming, on_send=None):
    script = Messages(*(incoming or (STARTUP, SHUTDOWN)), on_send=on_send)
    await app({'type': 'lifespan'}, script.receive, script.send)
    return script.sent


@contextlib.contextmanager
def recorded_connections():
    """Keep every real SQLite connection the two durable stores open, so a test can ask whether it is closed."""
    connections = []
    real_connect = sqlite3.connect

    def recording(*args, **kwargs):
        connections.append(real_connect(*args, **kwargs))
        return connections[-1]
    with mock.patch('acc.auth.sqlite_repository.sqlite3.connect', side_effect=recording), \
            mock.patch('acc.platform.sqlite_repository.sqlite3.connect', side_effect=recording):
        yield connections


def is_closed(connection) -> bool:
    try:
        connection.execute('SELECT 1')
    except sqlite3.ProgrammingError:
        return True
    return False


def database_fds(path) -> int:
    """How many file descriptors of this process point at the database file (Linux only)."""
    base = os.path.realpath(path)
    count = 0
    for entry in os.listdir('/proc/self/fd'):
        try:
            if os.path.realpath(os.readlink(f'/proc/self/fd/{entry}')) == base:
                count += 1
        except OSError:
            pass
    return count


class DurableLifespan(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'staging.sqlite3'

    def durable(self):
        return StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)

    async def test_shutdown_closes_both_connections_before_it_is_acknowledged(self):
        with recorded_connections() as connections:
            app = self.durable()
            self.assertEqual(len(connections), 2, 'durable staging owns exactly two SQLite connections')
            self.assertFalse(any(is_closed(c) for c in connections))
            state_at_acknowledgment = []

            def watch(message):
                if message['type'] == 'lifespan.shutdown.complete':
                    state_at_acknowledgment.append([is_closed(c) for c in connections])
            sent = await lifespan(app, on_send=watch)
        self.assertEqual(sent, ['lifespan.startup.complete', 'lifespan.shutdown.complete'])
        self.assertEqual(state_at_acknowledgment, [[True, True]],
                         'both stores must already be closed when success is reported')
        self.assertTrue(all(is_closed(c) for c in connections))

    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'needs /proc to count descriptors')
    async def test_no_descriptor_for_the_database_survives_a_graceful_shutdown(self):
        app = self.durable()
        self.assertGreater(database_fds(self.path), 0, 'the open stores hold descriptors for the file')
        await lifespan(app)
        self.assertEqual(database_fds(self.path), 0)

    async def test_the_files_can_be_used_again_after_shutdown(self):
        app = self.durable()
        # leave real, committed state behind so the write-ahead log has content
        from test_staging_e2e import http
        status, _, body, _ = await http(app, 'POST', '/staging/session',
                                        headers=[(b'x-acc-staging-secret', SECRET.encode())])
        self.assertEqual(status, 201, body)
        wal = Path(str(self.path) + '-wal')
        self.assertTrue(wal.exists() and wal.stat().st_size > 0, 'a live connection keeps the write-ahead log')
        sent = await lifespan(app)
        self.assertEqual(sent[-1], 'lifespan.shutdown.complete')
        self.assertFalse(wal.exists() and wal.stat().st_size > 0,
                         'the last connection closing checkpoints and removes the write-ahead log')
        # nothing else holds the file: an exclusive lock is available at once, and the data is intact
        db = sqlite3.connect(self.path, timeout=0.2, isolation_level=None)
        try:
            db.execute('BEGIN EXCLUSIVE')
            db.execute('ROLLBACK')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM m08_sessions').fetchone()[0], 1)
        finally:
            db.close()
        # and a fresh application opens it and sees the same session and project
        again = self.durable()
        try:
            status, _, body, _ = await http(again, 'GET', '/health', origin=None)
            self.assertEqual(status, 200)
        finally:
            await lifespan(again)

    async def test_the_file_can_be_replaced_after_shutdown(self):
        app = self.durable()
        await lifespan(app)
        replacement = Path(self.tmp.name) / 'replacement.sqlite3'
        sqlite3.connect(replacement).close()
        os.replace(replacement, self.path)  # would be racing live connections to a deleted inode on baseline
        with recorded_connections() as connections:
            fresh = self.durable()
            self.assertEqual(len(connections), 2)
            await lifespan(fresh)
            self.assertTrue(all(is_closed(c) for c in connections))

    async def test_a_second_shutdown_and_a_manual_close_afterwards_are_harmless(self):
        app = self.durable()
        await lifespan(app)
        app.close()
        app.close()
        self.assertEqual(await lifespan(app, SHUTDOWN), ['lifespan.shutdown.complete'])

    async def test_a_startup_after_a_durable_shutdown_is_refused_not_served_from_closed_stores(self):
        app = self.durable()
        await lifespan(app)
        sent = await lifespan(app, STARTUP)
        self.assertEqual(sent[0][0], 'lifespan.startup.failed')
        self.assertIn('shut down', sent[0][1])
        self.assertNotIn(str(self.path), sent[0][1])

    async def test_the_first_startup_and_unrelated_scopes_behave_as_before(self):
        app = self.durable()
        try:
            self.assertEqual(await lifespan(app, STARTUP, {'type': 'lifespan.unknown'}, SHUTDOWN),
                             ['lifespan.startup.complete', 'lifespan.shutdown.complete'])
        finally:
            app.close()
        with self.assertRaisesRegex(RuntimeError, 'Unsupported ASGI scope'):
            await app({'type': 'unknown'}, None, None)


class CleanupFailure(unittest.IsolatedAsyncioTestCase):
    """One failing closer must not skip the rest, and must not be reported as a successful shutdown."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'staging.sqlite3'

    async def test_a_failing_closer_still_closes_the_other_store_and_reports_failure(self):
        with recorded_connections() as connections:
            app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)
            real_close = app._closers[-1]  # the last registered closer runs first (last in, first out)

            def failing():
                real_close()
                raise sqlite3.OperationalError(f'cannot flush {self.path} secret-token-value')
            app._closers[-1] = failing
            sent = await lifespan(app)
            self.assertTrue(all(is_closed(c) for c in connections), 'every store was still closed')
        self.assertEqual(sent[0], 'lifespan.startup.complete')
        kind, message = sent[1]
        self.assertEqual(kind, 'lifespan.shutdown.failed')
        self.assertNotIn('lifespan.shutdown.complete', sent)
        self.assertIn('OperationalError', message)
        self.assertNotIn(str(self.path), message, 'no path in the message')
        self.assertNotIn('secret-token-value', message, 'no text from the underlying error in the message')

    async def test_every_closer_runs_when_the_first_one_fails(self):
        app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET)
        calls = []

        def closer(name, error=None):
            def run():
                calls.append(name)
                if error:
                    raise error
            return run
        app._closers.extend([closer('auth'), closer('state', RuntimeError('first')), closer('reads', ValueError('second'))])
        sent = await lifespan(app)
        self.assertEqual(calls, ['reads', 'state', 'auth'])
        self.assertEqual(sent[1][0], 'lifespan.shutdown.failed')
        self.assertIn('ValueError', sent[1][1], 'the first failure is the one reported')

    async def test_an_interrupt_in_a_closer_does_not_strand_the_others_and_is_not_swallowed(self):
        app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET)
        calls = []

        def closer(name, error=None):
            def run():
                calls.append(name)
                if error:
                    raise error
            return run
        app._closers.extend([closer('auth'), closer('state', KeyboardInterrupt())])
        sent = Messages(STARTUP, SHUTDOWN)
        with self.assertRaises(KeyboardInterrupt):
            await app({'type': 'lifespan'}, sent.receive, sent.send)
        self.assertEqual(calls, ['state', 'auth'], 'the remaining store was still closed')
        self.assertNotIn('lifespan.shutdown.complete', sent.sent)
        self.assertEqual([m for m in sent.sent if 'shutdown' in str(m)], [], 'no shutdown verdict was claimed')

    async def test_cancelling_the_lifespan_task_propagates_and_acknowledges_nothing(self):
        app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)
        self.addCleanup(app.close)
        gate = asyncio.Event()
        sent = []

        async def receive():
            await gate.wait()

        async def send(message):
            sent.append(message['type'])
        task = asyncio.ensure_future(app({'type': 'lifespan'}, receive, send))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sent, [])

    async def test_failure_during_construction_closes_both_stores_and_leaves_the_database_reusable(self):
        with recorded_connections() as connections:
            with mock.patch('acc.platform.sqlite_repository.SQLiteCommandRepository.put_worker',
                            side_effect=RuntimeError('provisioning failed')):
                with self.assertRaisesRegex(RuntimeError, 'provisioning failed'):
                    StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)
            self.assertEqual(len(connections), 2, 'both stores had been opened before the failure')
            self.assertTrue(all(is_closed(c) for c in connections))
        with recorded_connections() as again:
            app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)
            self.assertEqual(await lifespan(app), ['lifespan.startup.complete', 'lifespan.shutdown.complete'])
            self.assertTrue(all(is_closed(c) for c in again))

    async def test_a_cleanup_failure_during_a_failed_construction_does_not_hide_the_startup_error(self):
        real_close = StagingApplication.close
        with mock.patch('acc.platform.sqlite_repository.SQLiteCommandRepository.put_worker',
                        side_effect=RuntimeError('startup error')), \
                mock.patch.object(StagingApplication, 'close',
                                  side_effect=lambda self: (real_close(self), (_ for _ in ()).throw(OSError('cleanup error')))[1]):
            with self.assertRaisesRegex(RuntimeError, 'startup error'):
                StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)


class InMemoryStaging(unittest.IsolatedAsyncioTestCase):
    async def test_in_memory_staging_is_unchanged_and_can_cycle_lifespan_repeatedly(self):
        app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET)
        self.assertEqual(app._closers, [])
        for _ in range(3):
            self.assertEqual(await lifespan(app), ['lifespan.startup.complete', 'lifespan.shutdown.complete'])
        from test_staging_e2e import http
        status, _, body, _ = await http(app, 'GET', '/health', origin=None)
        self.assertEqual((status, body['status']), (200, 'ok'))
        status, _, body, _ = await http(app, 'POST', '/staging/session',
                                        headers=[(b'x-acc-staging-secret', SECRET.encode())])
        self.assertEqual(status, 201, body)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def uvicorn_available() -> bool:
    try:
        import uvicorn  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(uvicorn_available(), 'needs Uvicorn')
class InProcessUvicorn(unittest.TestCase):
    """A real Uvicorn server in this process: after its graceful shutdown the stores must be closed.

    This is the case that distinguishes the fix. A lifespan host that outlives the server object (a test host, a
    reloader, an embedding application) keeps the connections open when shutdown is only acknowledged."""

    def test_uvicorn_shutdown_closes_both_stores(self):
        import threading
        import uvicorn
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'staging.sqlite3'
            secret = secrets.token_hex(24)  # a throwaway; never printed or stored
            port = free_port()
            with recorded_connections() as connections:
                app = StagingApplication(origin=ORIGIN, bootstrap_secret=secret, database=database)
                server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='on'))
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()
                self.addCleanup(lambda: setattr(server, 'should_exit', True))
                deadline = time.time() + 20
                while not server.started and time.time() < deadline:
                    time.sleep(.05)
                self.assertTrue(server.started, 'the server did not start')
                request = urllib.request.Request(f'http://127.0.0.1:{port}/staging/session', method='POST', data=b'{}', headers={
                    'Origin': ORIGIN, 'X-ACC-Staging-Secret': secret, 'Content-Type': 'application/json'})
                self.assertEqual(urllib.request.urlopen(request, timeout=5).status, 201)
                self.assertEqual(len(connections), 2)
                self.assertFalse(any(is_closed(c) for c in connections), 'still serving: the stores are open')
                server.should_exit = True  # a graceful stop: Uvicorn sends lifespan.shutdown to the application
                thread.join(20)
                self.assertFalse(thread.is_alive(), 'the server stopped')
                self.assertTrue(all(is_closed(c) for c in connections), 'both stores are closed after the server shut down')
            self.assertFalse(Path(str(database) + '-wal').exists() and Path(str(database) + '-wal').stat().st_size > 0)


@unittest.skipUnless(uvicorn_available() and os.name != 'nt', 'needs Uvicorn and POSIX signals')
class RealServerProcess(unittest.TestCase):
    """The deployed shape: ``uvicorn staging_app:app`` as its own process, stopped the way an operator or supervisor does.

    A guard that the fix does not disturb a real graceful stop. It does NOT reproduce the defect: when the whole
    process exits, the interpreter finalizes the connections itself, so the unfixed code also leaves a clean file. The
    defect is about hosts whose process lives on (the in-process test above)."""

    def start(self, database):
        self.port = free_port()
        secret = secrets.token_hex(24)  # a throwaway; never printed or stored
        env = {**os.environ, 'ACC_STAGING_BOOTSTRAP_SECRET': secret, 'ACC_STAGING_DATABASE': str(database),
               'ACC_STAGING_ORIGIN': 'https://console.example.test'}
        env.pop('TYPESAFE_API_KEY', None)
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'staging_app:app', '--host', '127.0.0.1', '--port', str(self.port),
             '--workers', '1', '--log-level', 'info'], cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self.kill)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1).read()
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if self.process.poll() is not None:
                    self.fail('the server exited during startup:\n' + self.output()[-1500:])
                time.sleep(.2)
        else:
            self.fail('the server did not start')
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/staging/session', method='POST', data=b'{}', headers={
            'Origin': 'https://console.example.test', 'X-ACC-Staging-Secret': secret, 'Content-Type': 'application/json'})
        self.assertEqual(urllib.request.urlopen(request, timeout=5).status, 201)

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(5)
        self.log.close()

    def output(self):
        self.log.seek(0)
        return self.log.read().decode('utf-8', 'replace')

    def test_a_graceful_stop_runs_shutdown_cleanup_and_leaves_no_write_ahead_log(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'staging.sqlite3'
            self.start(database)
            wal = Path(str(database) + '-wal')
            self.assertTrue(wal.exists() and wal.stat().st_size > 0, 'live connections hold a write-ahead log')
            self.process.send_signal(signal.SIGINT)  # what an operator's Ctrl-C or a supervisor's stop does
            self.assertEqual(self.process.wait(20), 0)
            output = self.output()
            self.assertIn('Application shutdown complete', output)
            self.assertNotIn('shutdown failed', output.lower())
            self.assertFalse(wal.exists() and wal.stat().st_size > 0,
                             'closing the last connection checkpoints and removes the write-ahead log')
            db = sqlite3.connect(database, timeout=0.2, isolation_level=None)
            try:
                db.execute('BEGIN EXCLUSIVE')
                db.execute('ROLLBACK')
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM m08_sessions').fetchone()[0], 1)
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
