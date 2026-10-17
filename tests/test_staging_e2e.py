"""Independent end-to-end tests of the isolated M09 staging runtime through its ASGI boundary.

browser -> staging session -> project state -> event ticket -> WSS -> command -> mutation ->
command.applied -> browser -> disconnect/reconnect/replay
"""
import asyncio
import importlib
import json
import multiprocessing
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acc.auth import UserState
from acc.staging import (STAGING_ACCOUNT_ID, STAGING_ORIGIN_DEFAULT, STAGING_PROJECT_ID,
                         STAGING_USER_ID, StagingApplication, build_staging_app)

ORIGIN = STAGING_ORIGIN_DEFAULT
SECRET = 'staging-secret-' + 'k' * 32
PROJECT = f'/v1/accounts/{STAGING_ACCOUNT_ID}/projects/{STAGING_PROJECT_ID}'


async def http(app, method, path, *, origin=ORIGIN, headers=(), body=None, query=b''):
    sent = []
    raw = json.dumps(body).encode() if body is not None else b''
    incoming = [{'type': 'http.request', 'body': raw, 'more_body': False}]
    async def receive():
        return incoming.pop(0) if incoming else {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
    all_headers = ([(b'origin', origin.encode())] if origin is not None else []) + list(headers)
    await app({'type': 'http', 'method': method, 'path': path, 'query_string': query,
               'headers': all_headers}, receive, send)
    start = next(m for m in sent if m['type'] == 'http.response.start')
    data = b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')
    return start['status'], dict(start['headers']), (json.loads(data) if data else None), data


class WebSocketClient:
    """Drives one ASGI WebSocket connection like a browser tab."""

    def __init__(self, app, ticket, origin=ORIGIN):
        self.inbox = asyncio.Queue()
        self.sent = []
        self.received = asyncio.Queue()
        scope = {'type': 'websocket', 'path': '/v1/events',
                 'query_string': ('ticket=' + ticket).encode(),
                 'headers': [(b'origin', origin.encode())]}
        self.inbox.put_nowait({'type': 'websocket.connect'})
        async def receive():
            return await self.inbox.get()
        async def send(message):
            self.sent.append(message)
            await self.received.put(message)
        self.task = asyncio.ensure_future(app(scope, receive, send))

    async def next(self, timeout=2):
        return await asyncio.wait_for(self.received.get(), timeout)

    async def next_events(self, timeout=2):
        while True:
            message = await self.next(timeout)
            if message['type'] == 'websocket.send':
                return json.loads(message['text'])
            if message['type'] == 'websocket.close':
                raise AssertionError('closed: %r' % message)

    async def close(self):
        await self.inbox.put({'type': 'websocket.disconnect'})
        await asyncio.wait_for(self.task, 2)

    def close_code(self):
        closes = [m for m in self.sent if m['type'] == 'websocket.close']
        return closes[-1]['code'] if closes else None


class StagingEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = self.new_app()

    def new_app(self):
        """A separate staging instance with its own state (in memory here)."""
        return StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET)

    async def bootstrap(self, app=None):
        status, headers, body, _ = await http(
            app or self.app, 'POST', '/staging/session',
            headers=[(b'x-acc-staging-secret', SECRET.encode())])
        self.assertEqual(status, 201, body)
        return 'Bearer ' + body['access_token'], body

    async def get_state(self, auth):
        status, _, body, _ = await http(self.app, 'GET', PROJECT,
                                        headers=[(b'authorization', auth.encode())])
        self.assertEqual(status, 200, body)
        return body

    async def command(self, auth, operation, revision, kind='task.create', payload=None):
        body = {'operation_id': operation, 'kind': kind, 'expected_revision': revision,
                'payload': payload if payload is not None else {'task_id': 'task-1'}}
        status, _, response, _ = await http(
            self.app, 'POST', PROJECT + '/commands',
            headers=[(b'authorization', auth.encode()), (b'content-type', b'application/json')],
            body=body)
        return status, response

    async def ticket(self, auth, after=0):
        status, _, body, _ = await http(self.app, 'POST', PROJECT + '/event-ticket',
                                        headers=[(b'authorization', auth.encode())],
                                        query=f'after={after}'.encode())
        self.assertEqual(status, 201, body)
        return body['ticket']

    # 1-6: bootstrap ------------------------------------------------------------------------
    async def test_01_valid_bootstrap_returns_ordinary_session_with_cors(self):
        status, headers, body, _ = await http(
            self.app, 'POST', '/staging/session',
            headers=[(b'x-acc-staging-secret', SECRET.encode())])
        self.assertEqual(status, 201)
        self.assertEqual(set(body), {'access_token', 'token_type', 'expires_in',
                                     'account_id', 'project_id'})
        self.assertEqual((body['token_type'], body['expires_in']), ('Bearer', 900))
        self.assertEqual((body['account_id'], body['project_id']),
                         (STAGING_ACCOUNT_ID, STAGING_PROJECT_ID))
        self.assertTrue(body['access_token'].startswith('accs_'))
        self.assertEqual(headers[b'access-control-allow-origin'], ORIGIN.encode())
        self.assertNotIn(SECRET.encode(), json.dumps(body).encode())

    async def test_01b_preflight_allows_the_secret_header_only_for_the_console(self):
        status, headers, _, raw = await http(self.app, 'OPTIONS', '/staging/session', headers=[
            (b'access-control-request-method', b'POST'),
            (b'access-control-request-headers', b'x-acc-staging-secret')])
        self.assertEqual((status, raw), (204, b''))
        self.assertEqual(headers[b'access-control-allow-origin'], ORIGIN.encode())
        self.assertIn(b'X-ACC-Staging-Secret', headers[b'access-control-allow-headers'])
        self.assertNotIn(b'access-control-allow-credentials', headers)
        status, headers, _, _ = await http(self.app, 'OPTIONS', '/staging/session',
                                           origin='https://evil.example')
        self.assertEqual(status, 403)
        self.assertNotIn(b'access-control-allow-origin', headers)

    async def test_02_wrong_secret_is_generic_401(self):
        for value in (b'wrong', SECRET[:-1].encode(), (SECRET + 'x').encode(), b' ' + SECRET.encode()):
            status, _, body, raw = await http(self.app, 'POST', '/staging/session',
                                              headers=[(b'x-acc-staging-secret', value)])
            self.assertEqual(status, 401, value)
            self.assertEqual(body, {'error': {'code': 'authentication_required',
                                              'message': 'Authentication required.'}})
            self.assertNotIn(SECRET.encode(), raw)

    async def test_03_missing_secret_and_secret_outside_the_header_are_401(self):
        status, _, _, _ = await http(self.app, 'POST', '/staging/session')
        self.assertEqual(status, 401)
        status, _, _, _ = await http(self.app, 'POST', '/staging/session',
                                     query=b'secret=' + SECRET.encode())
        self.assertEqual(status, 401)
        status, _, _, _ = await http(self.app, 'POST', '/staging/session', body={'secret': SECRET})
        self.assertEqual(status, 401)
        status, _, _, _ = await http(self.app, 'POST', '/staging/session',
                                     headers=[(b'authorization', b'Bearer ' + SECRET.encode())])
        self.assertEqual(status, 401)

    async def test_04_wrong_or_missing_origin_fails_closed(self):
        good = [(b'x-acc-staging-secret', SECRET.encode())]
        for origin in ('https://evil.example', None, 'null', ORIGIN + '/', 'http://' + ORIGIN[8:],
                       ORIGIN.upper(), ORIGIN + '.evil.example'):
            status, headers, body, _ = await http(self.app, 'POST', '/staging/session',
                                                  origin=origin, headers=good)
            self.assertEqual(status, 403, origin)
            self.assertNotIn(b'access-control-allow-origin', headers)
            self.assertNotIn('access_token', body)

    async def test_05_repeated_origin_fails_closed(self):
        for extra in (ORIGIN, 'https://evil.example'):
            status, headers, _, _ = await http(self.app, 'POST', '/staging/session', headers=[
                (b'origin', extra.encode()), (b'x-acc-staging-secret', SECRET.encode())])
            self.assertEqual(status, 403, extra)
            self.assertNotIn(b'access-control-allow-origin', headers)

    async def test_06_repeated_secret_header_fails_closed(self):
        for first, second in ((b'wrong', SECRET.encode()), (SECRET.encode(), b'wrong'),
                              (SECRET.encode(), SECRET.encode())):
            status, _, _, _ = await http(self.app, 'POST', '/staging/session', headers=[
                (b'x-acc-staging-secret', first), (b'x-acc-staging-secret', second)])
            self.assertEqual(status, 401)

    # 7: health ------------------------------------------------------------------------------
    async def test_07_health_is_minimal_public_and_secret_free(self):
        auth, _ = await self.bootstrap()
        await self.command(auth, 'op-1', 0)
        for origin in (None, ORIGIN, 'https://evil.example'):
            status, headers, body, raw = await http(self.app, 'GET', '/health', origin=origin)
            self.assertEqual(status, 200)
            self.assertEqual(body, {'status': 'ok', 'service': 'acc-m09-staging',
                                    'account_id': STAGING_ACCOUNT_ID,
                                    'project_id': STAGING_PROJECT_ID})
            self.assertNotIn(b'access-control-allow-origin', headers)
            for secret in (SECRET, auth[len('Bearer '):], 'task-1', 'op-1'):
                self.assertNotIn(secret.encode(), raw)
        self.assertEqual((await http(self.app, 'POST', '/health'))[0], 404)
        self.assertNotIn(SECRET, repr(self.app) + repr(self.app.auth.identity_verifiers))

    # 8: isolation ---------------------------------------------------------------------------
    async def test_08_staging_bearer_cannot_leave_the_staging_tenant(self):
        auth, _ = await self.bootstrap()
        h = [(b'authorization', auth.encode())]
        status, _, _, _ = await http(self.app, 'GET', '/v1/accounts/other-account/projects', headers=h)
        self.assertEqual(status, 403)
        status, _, _, _ = await http(
            self.app, 'POST', '/v1/accounts/other-account/projects/staging-project/event-ticket',
            headers=h)
        self.assertEqual(status, 403)
        status, _, body, _ = await http(
            self.app, 'GET', f'/v1/accounts/{STAGING_ACCOUNT_ID}/projects/other-project', headers=h)
        self.assertEqual(status, 404)
        cmd = {'operation_id': 'x', 'kind': 'task.create', 'expected_revision': 0,
               'payload': {'task_id': 't'}}
        status, _, _, _ = await http(
            self.app, 'POST', f'/v1/accounts/{STAGING_ACCOUNT_ID}/projects/other-project/commands',
            headers=h, body=cmd)
        self.assertEqual(status, 404)
        status, _, _, _ = await http(
            self.app, 'POST', '/v1/accounts/other-account/projects/staging-project/commands',
            headers=h, body=cmd)
        self.assertEqual(status, 403)
        # Staging fixture and the bearer are not valid in a different app instance.
        other = self.new_app()
        status, _, _, _ = await http(other, 'GET', PROJECT, headers=h)
        self.assertEqual(status, 401)

    # 9-17: the browser path through state, ticket, WSS, command, event, reconnect -------------
    async def test_09_to_17_browser_round_trip_with_reconnect(self):
        auth, _ = await self.bootstrap()
        state = await self.get_state(auth)                                     # 9
        self.assertEqual(state['project']['revision'], 0)
        self.assertEqual(state['tasks'], [])
        self.assertEqual([w['status'] for w in state['workers']], ['active'])

        ws = WebSocketClient(self.app, await self.ticket(auth, after=0))       # 10, 11
        self.assertEqual((await ws.next())['type'], 'websocket.accept')

        status, applied = await self.command(auth, 'op-create', 0)            # 12
        self.assertEqual((status, applied['revision'], applied['replayed']), (201, 1, False))
        message = await ws.next_events()                                       # 13
        (event,) = message['events']
        self.assertEqual(event['kind'], 'command.applied')
        self.assertEqual((event['data']['operation_id'], event['data']['revision'],
                          event['data']['actor_user_id']), ('op-create', 1, 'staging-user'))
        cursor = message['cursor']

        state = await self.get_state(auth)                                     # 14
        self.assertEqual(state['project']['revision'], 1)
        self.assertEqual([(t['id'], t['status']) for t in state['tasks']], [('task-1', 'queued')])
        self.assertNotIn('reserved', state['tasks'][0])

        status, replay = await self.command(auth, 'op-create', 0)              # 15
        self.assertEqual((status, replay['revision'], replay['replayed']), (200, 1, True))
        status, conflict = await self.command(auth, 'op-create', 0, payload={'task_id': 'other'})
        self.assertEqual((status, conflict['error']['code']), (409, 'idempotency_conflict'))

        await ws.close()                                                       # 16
        self.assertIsNone(ws.close_code())
        status, paused = await self.command(auth, 'op-pause', 1, 'worker.pause',
                                            {'worker_id': 'worker-1'})
        self.assertEqual(status, 201)
        ws2 = WebSocketClient(self.app, await self.ticket(auth, after=cursor))
        self.assertEqual((await ws2.next())['type'], 'websocket.accept')
        message = await ws2.next_events()
        self.assertEqual([e['data']['operation_id'] for e in message['events']], ['op-pause'])
        await ws2.close()

        # 17: replay from the start shows each command exactly once; nothing was re-applied.
        status, _, events, _ = await http(self.app, 'GET', PROJECT + '/events',
                                          headers=[(b'authorization', auth.encode())])
        self.assertEqual([e['data']['operation_id'] for e in events['events']],
                         ['op-create', 'op-pause'])
        state = await self.get_state(auth)
        self.assertEqual(state['project']['revision'], 2)
        self.assertEqual(len(state['tasks']), 1)
        self.assertEqual([w['status'] for w in state['workers']], ['paused'])

    async def test_worker_resume_and_task_cancel_use_the_same_state_and_quota(self):
        auth, _ = await self.bootstrap()
        await self.command(auth, 'c1', 0)
        self.assertEqual(self.app.state.quota_used(STAGING_ACCOUNT_ID, 'tasks.active'), 1)
        status, _ = await self.command(auth, 'c2', 1, 'task.cancel', {'task_id': 'task-1'})
        self.assertEqual(status, 201)
        self.assertEqual(self.app.state.quota_used(STAGING_ACCOUNT_ID, 'tasks.active'), 0)
        await self.command(auth, 'c3', 2, 'worker.pause', {'worker_id': 'worker-1'})
        await self.command(auth, 'c4', 3, 'worker.resume', {'worker_id': 'worker-1'})
        state = await self.get_state(auth)
        self.assertEqual([(t['id'], t['status']) for t in state['tasks']], [('task-1', 'cancelled')])
        self.assertEqual([w['status'] for w in state['workers']], ['active'])
        self.assertEqual(state['project']['revision'], 4)

    async def test_staging_quota_ceiling_is_enforced(self):
        auth, _ = await self.bootstrap()
        for i in range(25):
            status, _ = await self.command(auth, f'q{i}', i, payload={'task_id': f't{i}'})
            self.assertEqual(status, 201)
        status, body = await self.command(auth, 'q25', 25, payload={'task_id': 't25'})
        self.assertEqual((status, body['error']['code']), (409, 'quota_exceeded'))

    # 18: revocation / expiry ----------------------------------------------------------------
    async def test_18_revocation_and_expiry_stop_operations_and_streams(self):
        auth, _ = await self.bootstrap()
        ws = WebSocketClient(self.app, await self.ticket(auth))
        self.assertEqual((await ws.next())['type'], 'websocket.accept')
        self.app.auth.revoke(auth[len('Bearer '):])
        self.assertEqual((await self.command(auth, 'r1', 0))[0], 401)
        status, _, _, _ = await http(self.app, 'GET', PROJECT, headers=[(b'authorization', auth.encode())])
        self.assertEqual(status, 401)
        await asyncio.wait_for(ws.task, 3)
        self.assertEqual(ws.close_code(), 4401)

        auth2, _ = await self.bootstrap()
        real_clock = self.app.auth.clock
        with mock.patch.object(self.app.auth, 'clock', lambda: real_clock() + 901):
            self.assertEqual((await self.command(auth2, 'r2', 0))[0], 401)
        self.assertEqual(self.app.state.project_revision(STAGING_ACCOUNT_ID, STAGING_PROJECT_ID), 0)

    # 19: restart resets state ---------------------------------------------------------------
    async def test_19_fresh_instance_has_fresh_state(self):
        auth, _ = await self.bootstrap()
        await self.command(auth, 'op-1', 0)
        fresh = self.new_app()
        self.app = fresh
        auth2, _ = await self.bootstrap(fresh)
        state = await self.get_state(auth2)
        self.assertEqual((state['project']['revision'], state['tasks']), (0, []))
        status, _, body, _ = await http(fresh, 'GET', PROJECT + '/events',
                                        headers=[(b'authorization', auth2.encode())])
        self.assertEqual(body['events'], [])
        # The old instance's bearer is unknown to the fresh one.
        status, _, _, _ = await http(fresh, 'GET', PROJECT, headers=[(b'authorization', auth.encode())])
        self.assertEqual(status, 401)

    # ASGI / deployment ----------------------------------------------------------------------
    async def test_wrong_origin_websocket_and_lifespan(self):
        auth, _ = await self.bootstrap()
        ws = WebSocketClient(self.app, await self.ticket(auth), origin='https://evil.example')
        self.assertEqual((await ws.next())['code'], 4403)
        sent = []
        messages = [{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}]
        async def receive():
            return messages.pop(0)
        async def send(message):
            sent.append(message['type'])
        await self.app({'type': 'lifespan'}, receive, send)
        self.assertEqual(sent, ['lifespan.startup.complete', 'lifespan.shutdown.complete'])

    def test_entrypoint_configuration_fails_closed(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ('ACC_STAGING_BOOTSTRAP_SECRET', 'ACC_STAGING_ORIGIN')}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                build_staging_app()
            for secret, origin, error in (('short', None, ValueError),
                                          (SECRET, 'http://insecure.example', ValueError),
                                          (SECRET, 'https://*.replit.app', ValueError),
                                          (SECRET, '', ValueError)):
                os.environ['ACC_STAGING_BOOTSTRAP_SECRET'] = secret
                if origin is None:
                    os.environ.pop('ACC_STAGING_ORIGIN', None)
                else:
                    os.environ['ACC_STAGING_ORIGIN'] = origin
                with self.assertRaises(error, msg=(secret, origin)) as caught:
                    build_staging_app()
                self.assertNotIn(SECRET, str(caught.exception))
            os.environ['ACC_STAGING_BOOTSTRAP_SECRET'] = SECRET
            os.environ.pop('ACC_STAGING_ORIGIN', None)
            sys.modules.pop('staging_app', None)
            module = importlib.import_module('staging_app')
            self.assertEqual(module.app.transport.origins.allowed_origins, frozenset((ORIGIN,)))
            sys.modules.pop('staging_app', None)



class DurableStagingEndToEndTests(StagingEndToEndTests):
    """The whole browser end-to-end suite again, with every instance on its own SQLite file."""

    async def asyncSetUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._apps = []
        await super().asyncSetUp()

    async def asyncTearDown(self):
        for app in self._apps:
            app.close()
        self._temp.cleanup()

    def new_app(self):
        path = Path(self._temp.name) / f'staging-{len(self._apps)}.sqlite3'
        app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=path)
        self._apps.append(app)
        return app

    async def test_durable_suite_really_runs_on_sqlite(self):
        from acc.platform import SQLiteCommandRepository
        from acc.auth import SQLiteAuthRepository
        self.assertIsInstance(self.app.state, SQLiteCommandRepository)
        self.assertIsInstance(self.app.auth.repository, SQLiteAuthRepository)
        self.assertTrue(Path(self.app.database).is_file())


def _start_staging(database, barrier, results):
    barrier.wait(timeout=10)
    try:
        StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=database).close()
        results.put('ok')
    except Exception as error:
        results.put(f'{type(error).__name__}: {error}')


class DurableStagingRestartTests(unittest.IsolatedAsyncioTestCase):
    """What durable storage adds: restarts and a second process share one store."""

    async def asyncSetUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name) / 'staging.sqlite3'
        self.apps = []

    async def asyncTearDown(self):
        for app in self.apps:
            app.close()
        self._temp.cleanup()

    def start(self):
        app = StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)
        self.apps.append(app)
        return app

    def restart(self, app):
        app.close()
        return self.start()

    async def bootstrap(self, app, expect=201):
        status, _, body, _ = await http(app, 'POST', '/staging/session',
                                        headers=[(b'x-acc-staging-secret', SECRET.encode())])
        self.assertEqual(status, expect, body)
        return 'Bearer ' + body['access_token'] if status == 201 else None

    async def call(self, app, auth, method='GET', path=PROJECT, body=None, query=b''):
        headers = [(b'authorization', auth.encode())]
        if body is not None:
            headers.append((b'content-type', b'application/json'))
        status, _, response, _ = await http(app, method, path, headers=headers, body=body,
                                            query=query)
        return status, response

    def create(self, operation, revision, task_id):
        return {'operation_id': operation, 'kind': 'task.create', 'expected_revision': revision,
                'payload': {'task_id': task_id}}

    async def test_session_state_and_event_cursor_survive_a_restart(self):
        app = self.start()
        auth = await self.bootstrap(app)
        self.assertEqual((await self.call(app, auth, 'POST', PROJECT + '/commands',
                                          self.create('op-1', 0, 'task-1')))[0], 201)
        _, events = await self.call(app, auth, path=PROJECT + '/events')
        cursor = events['cursor']

        app = self.restart(app)
        status, state = await self.call(app, auth)  # the pre-restart bearer still works
        self.assertEqual(status, 200)
        self.assertEqual((state['project']['revision'], [t['id'] for t in state['tasks']]),
                         (1, ['task-1']))
        _, replay = await self.call(app, auth, path=PROJECT + '/events', query=b'after=0')
        self.assertEqual([e['data']['operation_id'] for e in replay['events']], ['op-1'])
        _, after = await self.call(app, auth, path=PROJECT + '/events',
                                   query=f'after={cursor}'.encode())
        self.assertEqual((after['events'], after.get('reset_required', False)), ([], False))
        # Idempotent replay of a pre-restart operation, then the next revision.
        status, replayed = await self.call(app, auth, 'POST', PROJECT + '/commands',
                                           self.create('op-1', 0, 'task-1'))
        self.assertEqual((status, replayed['replayed']), (200, True))
        self.assertEqual((await self.call(app, auth, 'POST', PROJECT + '/commands',
                                          self.create('op-2', 1, 'task-2')))[0], 201)

    async def test_revocation_survives_a_restart(self):
        app = self.start()
        auth = await self.bootstrap(app)
        self.assertTrue(app.auth.revoke(auth[len('Bearer '):]))
        app = self.restart(app)
        self.assertEqual((await self.call(app, auth))[0], 401)

    async def test_fixture_is_applied_once_and_never_resurrected_by_a_restart(self):
        app = self.start()
        auth = await self.bootstrap(app)
        repository = app.auth.repository
        self.assertTrue(repository.remove_membership(STAGING_ACCOUNT_ID, STAGING_USER_ID))
        app = self.restart(app)
        self.assertEqual((await self.call(app, auth))[0], 403)  # membership stays removed
        await self.bootstrap(app, expect=401)

        app.auth.repository.put_user(UserState(STAGING_USER_ID, status='suspended'))
        app = self.restart(app)
        self.assertEqual(app.auth.repository.user(STAGING_USER_ID).status, 'suspended')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM m08_provisioning').fetchone()[0], 1)

    async def test_two_instances_share_sessions_and_state_but_not_websocket_tickets(self):
        first, second = self.start(), self.start()  # e.g. two worker processes on one file
        auth = await self.bootstrap(first)
        self.assertEqual((await self.call(first, auth, 'POST', PROJECT + '/commands',
                                          self.create('op-1', 0, 'task-1')))[0], 201)
        status, state = await self.call(second, auth)
        self.assertEqual((status, state['project']['revision']), (200, 1))
        status, ticket = await self.call(first, auth, 'POST', PROJECT + '/event-ticket')
        self.assertEqual(status, 201)
        # Tickets are process-local by design: another worker refuses them.
        ws = WebSocketClient(second, ticket['ticket'])
        self.assertEqual((await ws.next())['code'], 4401)

    def test_concurrent_first_starts_apply_the_fixture_once(self):
        context = multiprocessing.get_context('spawn')
        barrier, results = context.Barrier(4), context.Queue()
        workers = [context.Process(target=_start_staging, args=(str(self.path), barrier, results))
                   for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)
        self.assertEqual(sorted(results.get(timeout=5) for _ in workers), ['ok'] * 4)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM m08_provisioning').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM m09_projects').fetchone()[0], 1)

    def test_invalid_configuration_never_creates_or_provisions_a_database(self):
        for origin, secret in (('http://insecure.example', SECRET), (ORIGIN, 'short')):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                StagingApplication(origin=origin, bootstrap_secret=secret, database=self.path)
            self.assertFalse(self.path.exists())

    def test_failure_after_storage_opens_closes_every_connection(self):
        from acc.platform.sqlite_repository import SQLiteCommandRepository
        self.restart(self.start())  # create a valid durable store, then break its M09 schema
        self.apps[-1].close()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE m09_schema_meta SET version=99')
        closed = []
        real_auth_close = __import__('acc.auth', fromlist=['SQLiteAuthRepository']).SQLiteAuthRepository.close
        with mock.patch('acc.staging.SQLiteAuthRepository.close', autospec=True,
                        side_effect=lambda repo: (closed.append('auth'), real_auth_close(repo))):
            with self.assertRaisesRegex(RuntimeError, 'Unsupported M09'):
                StagingApplication(origin=ORIGIN, bootstrap_secret=SECRET, database=self.path)
        self.assertEqual(closed, ['auth'])
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE m09_schema_meta SET version=2')
        SQLiteCommandRepository(self.path).close()

    def test_database_setting_must_be_an_absolute_path(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('ACC_STAGING_')}
        env['ACC_STAGING_BOOTSTRAP_SECRET'] = SECRET
        for value in ('', 'relative/staging.sqlite3', ':memory:'):
            with self.subTest(value=value), mock.patch.dict(os.environ, env, clear=True):
                os.environ['ACC_STAGING_DATABASE'] = value
                with self.assertRaises(ValueError):
                    build_staging_app()
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ['ACC_STAGING_DATABASE'] = str(self.path)
            app = build_staging_app()
            self.apps.append(app)
            self.assertEqual(app.database, str(self.path))
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(build_staging_app().database)  # unset keeps staging in memory


if __name__ == '__main__':
    unittest.main()
