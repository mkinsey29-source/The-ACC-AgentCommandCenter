import asyncio
import json
import unittest

from acc.auth import (AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
                      Membership, UserState, VerifiedIdentity)
from acc.platform import InMemoryCommandRepository, PlatformApi
from acc.platform.transport import HostedTransport, OriginPolicy

ORIGIN = 'https://acc.example'
PATH = '/v1/accounts/acct-1/projects/project-1/commands'


class Verifier:
    id = 'openai'
    def verify(self, assertion):
        return VerifiedIdentity('openai', assertion['subject'])


class Reads:
    def project(self, account_id, project_id):
        return None


async def request(app, *, method='POST', path=PATH, chunks=None, body=None, headers=None,
                  authorization=None, origin=ORIGIN):
    """Send one HTTP request; ``chunks`` is a list of ASGI receive messages."""
    sent = []
    if chunks is None:
        raw = json.dumps(body).encode() if body is not None else b''
        chunks = [{'type': 'http.request', 'body': raw, 'more_body': False}]
    incoming = list(chunks)
    async def receive():
        return incoming.pop(0) if incoming else {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
    if headers is None:
        headers = []
        if origin is not None:
            headers.append((b'origin', origin.encode()))
        if authorization is not None:
            headers.append((b'authorization', authorization.encode()))
    scope = {'type': 'http', 'method': method, 'path': path, 'query_string': b'',
             'headers': headers}
    await app(scope, receive, send)
    start = next(x for x in sent if x['type'] == 'http.response.start')
    data = b''.join(x.get('body', b'') for x in sent if x['type'] == 'http.response.body')
    return start['status'], (json.loads(data) if data else None)


def command(operation='op-1', revision=0, task='task-1'):
    return {'operation_id': operation, 'kind': 'task.create', 'expected_revision': revision,
            'payload': {'task_id': task}}


class CommandTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repo = InMemoryAuthRepository()
        repo.put_user(UserState('user-1'))
        repo.bind_identity(VerifiedIdentity('openai', 'subject-1'), 'user-1')
        repo.put_account(AccountState('acct-1'))
        repo.put_membership(Membership(
            'acct-1', 'user-1', permissions=('task.write', 'event.read', 'project.read')))
        repo.put_entitlements(EntitlementSnapshot(
            'acct-1', features=('acc.web',), limits={'tasks.active': 5}))
        self.auth = AuthService(repo, identity_verifiers=(Verifier(),))
        token = self.auth.exchange_identity('openai', {'subject': 'subject-1'}, 'acct-1')
        self.authorization = 'Bearer ' + token
        self.commands = InMemoryCommandRepository()
        self.commands.put_project('acct-1', 'project-1')
        # One store serves both the command transaction and the event stream.
        self.app = HostedTransport(
            PlatformApi(self.auth, Reads(), self.commands, self.commands),
            origins=OriginPolicy(frozenset((ORIGIN,))), poll_seconds=0.05)

    async def post(self, body=None, **kwargs):
        kwargs.setdefault('authorization', self.authorization)
        return await request(self.app, body=body, **kwargs)

    def untouched(self):
        self.assertEqual(self.commands.project_revision('acct-1', 'project-1'), 0)
        self.assertEqual(self.commands.audit(), ())

    async def test_post_command_route(self):
        status, result = await self.post(command())
        self.assertEqual(status, 201)
        self.assertEqual(result['status'], 'applied')

    async def test_retry_over_http_is_replayed_not_reapplied(self):
        self.assertEqual((await self.post(command()))[0], 201)
        status, result = await self.post(command())
        self.assertEqual(status, 200)
        self.assertTrue(result['replayed'])
        self.assertEqual(len(self.commands.audit()), 1)

    async def test_invalid_json_is_400(self):
        for raw in (b'{bad', b'\xff\xfe{}', b'', b'[]', b'"x"', b'5', b'null',
                    b'{"a":NaN}', b'{"a":Infinity}', b'[' * 5000 + b']' * 5000,
                    b'{"kind":"task.create","kind":"project.mode"}'):
            status, body = await self.post(
                chunks=[{'type': 'http.request', 'body': raw, 'more_body': False}])
            self.assertEqual(status, 400, raw[:40])
            self.assertEqual(body['error']['code'], 'invalid_request')
        self.untouched()

    async def test_chunked_body_is_assembled(self):
        raw = json.dumps(command()).encode()
        parts = [raw[i:i + 7] for i in range(0, len(raw), 7)]
        chunks = [{'type': 'http.request', 'body': p, 'more_body': True} for p in parts[:-1]]
        chunks.append({'type': 'http.request', 'body': parts[-1], 'more_body': False})
        status, _ = await self.post(chunks=chunks)
        self.assertEqual(status, 201)

    async def test_oversized_and_disconnected_bodies_are_rejected(self):
        big = [{'type': 'http.request', 'body': b' ' * 60_000, 'more_body': True}] * 2
        status, _ = await self.post(chunks=big + [{'type': 'http.request', 'body': b'{}'}])
        self.assertEqual(status, 400)
        status, _ = await self.post(chunks=[
            {'type': 'http.request', 'body': b'{"operation_id"', 'more_body': True},
            {'type': 'http.disconnect'}])
        self.assertEqual(status, 400)
        status, _ = await self.post(chunks=[{'type': 'http.request', 'body': 'x', 'more_body': False}])
        self.assertEqual(status, 400)
        self.untouched()

    async def test_origin_and_authorization_are_enforced(self):
        self.assertEqual((await self.post(command(), origin='https://evil.example'))[0], 403)
        self.assertEqual((await self.post(command(), origin=None))[0], 403)
        self.assertEqual((await self.post(command(), authorization='Bearer nope'))[0], 401)
        status, _ = await request(self.app, body=command())
        self.assertEqual(status, 401)
        dup_auth = [(b'origin', ORIGIN.encode()),
                    (b'authorization', self.authorization.encode()),
                    (b'authorization', self.authorization.encode())]
        self.assertEqual((await request(self.app, body=command(), headers=dup_auth))[0], 401)
        self.untouched()

    async def test_wrong_methods_paths_and_ids(self):
        for method in ('GET', 'PUT', 'DELETE', 'PATCH'):
            status, _ = await self.post(command(), method=method)
            self.assertEqual(status, 404, method)
        status, _ = await self.post(command(), path=PATH + '/extra')
        self.assertEqual(status, 404)
        status, _ = await self.post(command(), path='/v1/accounts/' + 'a' * 300 +
                                    '/projects/project-1/commands')
        self.assertEqual(status, 400)
        status, body = await self.post({**command(), 'kind': 'task.delete'})
        self.assertEqual((status, body['error']['code']), (400, 'invalid_request'))
        self.untouched()

    async def test_event_ticket_post_still_ignores_the_body(self):
        # Regression: parsing every POST body as JSON broke PR #31's empty-body ticket request.
        for raw in (b'', b'not json'):
            status, body = await request(
                self.app, method='POST',
                path='/v1/accounts/acct-1/projects/project-1/event-ticket',
                authorization=self.authorization,
                chunks=[{'type': 'http.request', 'body': raw, 'more_body': False}])
            self.assertEqual(status, 201, raw)
            self.assertTrue(body['ticket'].startswith('accw_'))

    async def test_command_event_reaches_http_and_websocket_consumers(self):
        await self.post(command())
        status, body = await request(
            self.app, method='GET', path='/v1/accounts/acct-1/projects/project-1/events',
            authorization=self.authorization)
        self.assertEqual(status, 200)
        (event,) = body['events']
        self.assertEqual((event['kind'], event['data']['operation_id']), ('command.applied', 'op-1'))
        _, ticket = await request(
            self.app, method='POST', path='/v1/accounts/acct-1/projects/project-1/event-ticket',
            authorization=self.authorization)
        sent = []
        queue = asyncio.Queue()
        await queue.put({'type': 'websocket.connect'})
        async def receive():
            return await queue.get()
        async def send(message):
            sent.append(message)
            if message['type'] == 'websocket.send':
                await queue.put({'type': 'websocket.disconnect'})
        scope = {'type': 'websocket', 'path': '/v1/events',
                 'query_string': ('ticket=' + ticket['ticket']).encode(),
                 'headers': [(b'origin', ORIGIN.encode())]}
        await asyncio.wait_for(self.app(scope, receive, send), timeout=2)
        (message,) = [json.loads(m['text']) for m in sent if m['type'] == 'websocket.send']
        self.assertEqual(message['events'][0]['data']['revision'], 1)


if __name__ == '__main__':
    unittest.main()
