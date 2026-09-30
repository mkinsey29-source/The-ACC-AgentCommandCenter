import asyncio
import json
import unittest

from acc.auth import (
    AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
    Membership, UserState, VerifiedIdentity,
)
from acc.platform import AccountProjectView, EventBatch, PlatformApi, PlatformEvent
from acc.platform.transport import EventTicketStore, HostedTransport, OriginPolicy


ORIGIN = 'https://acc-web.example'


class Verifier:
    id = 'openai'
    def verify(self, assertion):
        return VerifiedIdentity('openai', assertion['subject'])


class Reads:
    def projects(self, account_id):
        return [AccountProjectView(account_id, 'project-1', 'Alpha')]
    def project(self, account_id, project_id):
        return AccountProjectView(account_id, project_id, 'Alpha')
    def tasks(self, account_id, project_id):
        return [{'id':'task-1','account_id':account_id,'project_id':project_id}]
    def task(self, account_id, project_id, task_id):
        return {'id':task_id,'account_id':account_id,'project_id':project_id}
    def workers(self, account_id, project_id):
        return []
    def attention(self, account_id, project_id):
        return []


class Events:
    def __init__(self):
        self.items = []
        self.reset = None

    def read_events(self, account_id, project_id, *, after, limit=200):
        if self.reset:
            cursor, oldest = self.reset
            return EventBatch((), cursor, False, True, oldest)
        values = [item for item in self.items if item.seq > after][:limit]
        cursor = values[-1].seq if values else after
        return EventBatch(tuple(values), cursor, len([x for x in self.items if x.seq > cursor]) > 0)


def setup_transport():
    repo = InMemoryAuthRepository()
    repo.put_user(UserState('user-1'))
    identity = VerifiedIdentity('openai', 'subject-1')
    repo.bind_identity(identity, 'user-1')
    repo.put_account(AccountState('acct-1'))
    repo.put_membership(Membership(
        'acct-1', 'user-1',
        permissions=('project.read','task.read','event.read')))
    repo.put_entitlements(EntitlementSnapshot('acct-1', features=('acc.web',)))
    auth = AuthService(repo, identity_verifiers=(Verifier(),))
    token = auth.exchange_identity('openai', {'subject':'subject-1'}, 'acct-1')
    events = Events()
    api = PlatformApi(auth, Reads(), events)
    transport = HostedTransport(
        api, origins=OriginPolicy(frozenset((ORIGIN,))), poll_seconds=0.05)
    return transport, auth, token, events


async def call_http(app, method, path, *, origin=ORIGIN, authorization=None, query=''):
    sent = []
    headers = [(b'origin', origin.encode())] if origin is not None else []
    if authorization is not None:
        headers.append((b'authorization', authorization.encode()))
    scope = {
        'type':'http','method':method,'path':path,
        'query_string':query.encode(),'headers':headers,
    }
    incoming = [{'type':'http.request','body':b'','more_body':False}]
    async def receive():
        return incoming.pop(0) if incoming else {'type':'http.disconnect'}
    async def send(message):
        sent.append(message)
    await app(scope, receive, send)
    start = next(item for item in sent if item['type']=='http.response.start')
    body = b''.join(item.get('body',b'') for item in sent if item['type']=='http.response.body')
    return start, json.loads(body) if body else None


class EventResetTests(unittest.TestCase):
    def test_reset_batch_can_move_resume_cursor_to_retained_window(self):
        batch = EventBatch((), 99, False, True, 50)
        self.assertTrue(batch.reset_required)
        self.assertEqual(batch.oldest_available, 50)

    def test_reset_batch_requires_oldest_available(self):
        with self.assertRaises(ValueError):
            EventBatch((), 5, False, True, None)

    def test_reset_batch_cannot_contain_events(self):
        event = PlatformEvent(6,'acct-1','project-1','x',1.0,{})
        with self.assertRaises(ValueError):
            EventBatch((event,), 6, False, True, 6)


class HostedTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport, self.auth, self.token, self.events = setup_transport()
        self.authorization = 'Bearer ' + self.token

    async def test_http_read_route_and_security_headers(self):
        start, body = await call_http(
            self.transport,'GET','/v1/accounts/acct-1/projects',
            authorization=self.authorization)
        self.assertEqual(start['status'],200)
        self.assertEqual(body['projects'][0]['project_id'],'project-1')
        headers = dict(start['headers'])
        self.assertEqual(headers[b'access-control-allow-origin'], ORIGIN.encode())
        self.assertEqual(headers[b'cache-control'], b'no-store')

    async def test_origin_is_mandatory_and_exact(self):
        for origin in (None, 'https://evil.example', ORIGIN + '/path'):
            start, _ = await call_http(
                self.transport,'GET','/v1/accounts/acct-1/projects',
                origin=origin,authorization=self.authorization)
            self.assertEqual(start['status'],403)

    async def test_preflight_never_uses_wildcard_origin(self):
        start, body = await call_http(
            self.transport,'OPTIONS','/v1/accounts/acct-1/projects')
        self.assertEqual(start['status'],204)
        headers = dict(start['headers'])
        self.assertEqual(headers[b'access-control-allow-origin'], ORIGIN.encode())
        self.assertNotEqual(headers[b'access-control-allow-origin'], b'*')
        self.assertIsNone(body)

    async def test_event_ticket_is_authenticated_single_use_and_not_bearer(self):
        start, body = await call_http(
            self.transport,'POST',
            '/v1/accounts/acct-1/projects/project-1/event-ticket',
            authorization=self.authorization,query='after=0')
        self.assertEqual(start['status'],201)
        self.assertTrue(body['ticket'].startswith('accw_'))
        self.assertNotIn(self.token, body['ticket'])
        record = await self.transport.tickets.consume(body['ticket'])
        self.assertEqual(record.account_id,'acct-1')
        self.assertEqual(record.authorization,self.authorization)
        self.assertIsNone(await self.transport.tickets.consume(body['ticket']))

    async def test_ticket_request_reports_reset_instead_of_opening_stream(self):
        self.events.reset = (100, 50)
        start, body = await call_http(
            self.transport,'POST',
            '/v1/accounts/acct-1/projects/project-1/event-ticket',
            authorization=self.authorization,query='after=10')
        self.assertEqual(start['status'],409)
        self.assertEqual(body['error']['code'],'event_reset_required')
        self.assertEqual(body['cursor'],100)
        self.assertEqual(body['oldest_available'],50)

    async def test_revoked_session_cannot_issue_ticket(self):
        self.auth.revoke(self.token)
        start, _ = await call_http(
            self.transport,'POST',
            '/v1/accounts/acct-1/projects/project-1/event-ticket',
            authorization=self.authorization)
        self.assertEqual(start['status'],401)

    async def test_websocket_streams_events_and_reauthorizes_revocation(self):
        self.events.items.append(
            PlatformEvent(1,'acct-1','project-1','task.changed',1.0,{'status':'working'}))
        _, ticket_body = await call_http(
            self.transport,'POST',
            '/v1/accounts/acct-1/projects/project-1/event-ticket',
            authorization=self.authorization)

        sent = []
        queue = asyncio.Queue()
        await queue.put({'type':'websocket.connect'})
        scope = {
            'type':'websocket','path':'/v1/events',
            'query_string':('ticket='+ticket_body['ticket']).encode(),
            'headers':[(b'origin',ORIGIN.encode())],
        }
        async def receive():
            return await queue.get()
        async def send(message):
            sent.append(message)
            if message.get('type')=='websocket.send' and '"type":"events"' in message.get('text',''):
                self.auth.revoke(self.token)

        await asyncio.wait_for(self.transport(scope,receive,send),timeout=1)
        self.assertTrue(any(x['type']=='websocket.accept' for x in sent))
        event_messages = [x for x in sent if x['type']=='websocket.send' and '"type":"events"' in x.get('text','')]
        self.assertEqual(len(event_messages),1)
        close = [x for x in sent if x['type']=='websocket.close'][-1]
        self.assertEqual(close['code'],4401)

    async def test_websocket_rejects_bad_origin_and_reused_ticket(self):
        _, body = await call_http(
            self.transport,'POST',
            '/v1/accounts/acct-1/projects/project-1/event-ticket',
            authorization=self.authorization)
        ticket = body['ticket']

        async def run(origin):
            sent=[]
            incoming=[{'type':'websocket.connect'},{'type':'websocket.disconnect'}]
            async def receive():
                return incoming.pop(0)
            async def send(message):
                sent.append(message)
            scope={'type':'websocket','path':'/v1/events',
                   'query_string':('ticket='+ticket).encode(),
                   'headers':[(b'origin',origin.encode())]}
            await self.transport(scope,receive,send)
            return sent

        denied = await run('https://evil.example')
        self.assertEqual(denied[-1]['code'],4403)

        # Bad origin did not consume the capability; a valid handshake can consume it once.
        accepted = await run(ORIGIN)
        self.assertTrue(any(x['type']=='websocket.accept' for x in accepted))
        reused = await run(ORIGIN)
        self.assertEqual(reused[-1]['code'],4401)


if __name__ == '__main__':
    unittest.main()
