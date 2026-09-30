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



class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now
    def __call__(self):
        return self.now


def setup_clocked():
    clock = Clock()
    repo = InMemoryAuthRepository()
    repo.put_user(UserState('user-1'))
    repo.bind_identity(VerifiedIdentity('openai', 'subject-1'), 'user-1')
    for account in ('acct-1', 'acct-2'):
        repo.put_account(AccountState(account))
        repo.put_entitlements(EntitlementSnapshot(account, features=('acc.web',)))
    repo.put_membership(Membership(
        'acct-1', 'user-1', permissions=('project.read', 'task.read', 'event.read')))
    auth = AuthService(repo, identity_verifiers=(Verifier(),), clock=clock,
                       default_ttl_seconds=600)
    token = auth.exchange_identity('openai', {'subject': 'subject-1'}, 'acct-1')
    events = Events()
    api = PlatformApi(auth, Reads(), events)
    transport = HostedTransport(
        api, origins=OriginPolicy(frozenset((ORIGIN,))),
        tickets=EventTicketStore(clock=clock), poll_seconds=0.05)
    return transport, auth, repo, token, events, clock


async def run_ws(app, ticket, *, origin=ORIGIN, path='/v1/events', query=None,
                 incoming=None, on_send=None, timeout=2):
    """Drive one WebSocket connection; ``incoming`` messages follow websocket.connect."""
    sent = []
    queue = asyncio.Queue()
    await queue.put({'type': 'websocket.connect'})
    for item in incoming or ():
        await queue.put(item)
    headers = [(b'origin', origin.encode())] if origin is not None else []
    if query is None:
        query = 'ticket=' + ticket
    scope = {'type': 'websocket', 'path': path, 'query_string': query.encode(), 'headers': headers}
    async def receive():
        return await queue.get()
    async def send(message):
        sent.append(message)
        if on_send is not None:
            await on_send(message, queue)
    await asyncio.wait_for(app(scope, receive, send), timeout=timeout)
    return sent


def ws_texts(sent):
    return [json.loads(x['text']) for x in sent if x['type'] == 'websocket.send']


def ws_close(sent):
    closes = [x for x in sent if x['type'] == 'websocket.close']
    return closes[-1]['code'] if closes else None


class TransportAdversarialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (self.transport, self.auth, self.repo, self.token,
         self.events, self.clock) = setup_clocked()
        self.authorization = 'Bearer ' + self.token

    async def ticket(self, after=0, project='project-1', account='acct-1', authorization=None):
        start, body = await call_http(
            self.transport, 'POST',
            f'/v1/accounts/{account}/projects/{project}/event-ticket',
            authorization=authorization or self.authorization, query=f'after={after}')
        self.assertEqual(start['status'], 201, body)
        return body['ticket']

    def add_event(self, seq, project='project-1', data=None):
        self.events.items.append(
            PlatformEvent(seq, 'acct-1', project, 'task.changed', 1.0, data or {'n': seq}))

    # --- ticket secrecy and lifecycle -------------------------------------------------------
    async def test_ticket_record_repr_never_contains_bearer(self):
        ticket = await self.ticket()
        record = await self.transport.tickets.consume(ticket)
        self.assertNotIn(self.token, repr(record))
        self.assertNotIn(self.token, str(record))
        self.assertNotIn(ticket, repr(self.transport.tickets._tickets))

    async def test_tickets_are_random_and_expire(self):
        first, second = await self.ticket(), await self.ticket()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 40)
        self.clock.now += self.transport.tickets.ttl_seconds
        sent = await run_ws(self.transport, first)
        self.assertEqual(ws_close(sent), 4401)
        self.assertFalse(any(x['type'] == 'websocket.accept' for x in sent))
        # Expired records are purged, not retained.
        await self.transport.tickets.consume('accw_nothing')
        self.assertEqual(self.transport.tickets._tickets, {})

    async def test_malformed_missing_and_duplicate_tickets_are_rejected(self):
        ticket = await self.ticket()
        for query in ('', 'ticket=', 'ticket=x', 'ticket=accw_' + 'a' * 600,
                      f'ticket={ticket}&ticket={ticket}', 'ticket=%FF', 'tick=' + ticket):
            sent = await run_ws(self.transport, ticket, query=query)
            self.assertEqual(ws_close(sent), 4401, query)
            self.assertFalse(any(x['type'] == 'websocket.accept' for x in sent), query)
        sent = await run_ws(self.transport, ticket, query='ticket=' + ticket + '\xff'.encode('latin-1').decode('latin-1'))
        self.assertEqual(ws_close(sent), 4401)

    async def test_rejected_origin_or_path_does_not_burn_ticket(self):
        ticket = await self.ticket()
        for kwargs in ({'origin': 'https://evil.example'}, {'origin': None},
                       {'origin': 'null'}, {'path': '/v1/other'}):
            sent = await run_ws(self.transport, ticket, **kwargs)
            self.assertEqual(ws_close(sent), 4403, kwargs)
            self.assertFalse(any(x['type'] == 'websocket.accept' for x in sent))
        sent = await run_ws(self.transport, ticket,
                            incoming=[{'type': 'websocket.disconnect'}])
        self.assertTrue(any(x['type'] == 'websocket.accept' for x in sent))

    async def test_handshake_waits_for_connect_before_validating(self):
        ticket = await self.ticket()
        sent = []
        messages = [{'type': 'websocket.disconnect'}]
        async def receive():
            return messages.pop(0)
        async def send(message):
            sent.append(message)
        scope = {'type': 'websocket', 'path': '/v1/events',
                 'query_string': ('ticket=' + ticket).encode(),
                 'headers': [(b'origin', ORIGIN.encode())]}
        await self.transport(scope, receive, send)
        self.assertEqual(sent, [])
        # The ticket was not consumed by a connection that never completed its handshake.
        self.assertIsNotNone(await self.transport.tickets.consume(ticket))

    # --- tenant isolation --------------------------------------------------------------------
    async def test_cross_account_ticket_is_denied(self):
        start, _ = await call_http(
            self.transport, 'POST', '/v1/accounts/acct-2/projects/project-1/event-ticket',
            authorization=self.authorization)
        self.assertEqual(start['status'], 403)

    async def test_ticket_is_bound_to_its_project(self):
        self.add_event(1, project='project-1')
        self.add_event(2, project='project-2')
        calls = []
        items = self.events.items
        def read_events(account_id, project_id, *, after, limit=200):
            calls.append((account_id, project_id))
            values = [e for e in items if e.project_id == project_id and e.seq > after][:limit]
            return EventBatch(tuple(values), values[-1].seq if values else after, False)
        self.events.read_events = read_events
        ticket = await self.ticket(project='project-2')
        async def on_send(message, queue):
            if message['type'] == 'websocket.send':
                await queue.put({'type': 'websocket.disconnect'})
        sent = await run_ws(self.transport, ticket, on_send=on_send)
        delivered = [e for m in ws_texts(sent) for e in m.get('events', [])]
        self.assertEqual([(e['project_id'], e['seq']) for e in delivered], [('project-2', 2)])
        self.assertTrue(calls)
        self.assertTrue(all(call == ('acct-1', 'project-2') for call in calls))

    async def test_foreign_event_from_source_closes_without_leaking(self):
        ticket = await self.ticket()
        self.events.items.append(
            PlatformEvent(1, 'acct-2', 'project-1', 'task.changed', 1.0, {'secret': 'B'}))
        sent = await run_ws(self.transport, ticket)
        self.assertEqual(ws_close(sent), 1011)
        self.assertNotIn('secret', json.dumps([x.get('text', '') for x in sent]))

    # --- live authorization loss -------------------------------------------------------------
    async def _assert_loss_closes(self, mutate, code):
        self.add_event(1)
        ticket = await self.ticket()
        async def on_send(message, queue):
            if message['type'] == 'websocket.send':
                mutate()
                self.add_event(len(self.events.items) + 1)
        sent = await run_ws(self.transport, ticket, on_send=on_send)
        self.assertEqual(ws_close(sent), code)
        batches = [m for m in ws_texts(sent) if m['type'] == 'events']
        self.assertEqual(len(batches), 1)

    async def test_session_revocation_closes_stream(self):
        await self._assert_loss_closes(lambda: self.auth.revoke(self.token), 4401)

    async def test_session_expiry_closes_stream(self):
        def expire():
            self.clock.now += 601
        await self._assert_loss_closes(expire, 4401)

    async def test_membership_removal_closes_stream(self):
        await self._assert_loss_closes(
            lambda: self.repo.remove_membership('acct-1', 'user-1'), 4403)

    async def test_event_read_permission_loss_closes_stream(self):
        await self._assert_loss_closes(lambda: self.repo.put_membership(Membership(
            'acct-1', 'user-1', permissions=('project.read', 'task.read'))), 4403)

    async def test_acc_web_entitlement_loss_closes_stream(self):
        await self._assert_loss_closes(lambda: self.repo.put_entitlements(
            EntitlementSnapshot('acct-1', features=())), 4403)

    async def test_account_suspension_closes_stream(self):
        await self._assert_loss_closes(lambda: self.repo.put_account(
            AccountState('acct-1', status='suspended')), 4403)

    # --- replay / reset ----------------------------------------------------------------------
    async def test_replay_starts_after_ticket_cursor_and_drains_backlog_in_order(self):
        for seq in range(1, 451):
            self.add_event(seq)
        ticket = await self.ticket(after=5)
        seen = []
        async def on_send(message, queue):
            if message['type'] == 'websocket.send':
                seen.extend(e['seq'] for e in json.loads(message['text']).get('events', []))
                if seen and seen[-1] == 450:
                    await queue.put({'type': 'websocket.disconnect'})
        await run_ws(self.transport, ticket, on_send=on_send)
        self.assertEqual(seen, list(range(6, 451)))

    async def test_reset_mid_stream_sends_resume_cursor_then_closes_4009(self):
        self.add_event(1)
        ticket = await self.ticket()
        async def on_send(message, queue):
            if message['type'] == 'websocket.send' and self.events.reset is None:
                self.events.reset = (40, 30)
        sent = await run_ws(self.transport, ticket, on_send=on_send)
        self.assertEqual(ws_texts(sent)[-1],
                         {'type': 'reset_required', 'cursor': 40, 'oldest_available': 30})
        self.assertEqual(ws_close(sent), 4009)

    # --- client channel ----------------------------------------------------------------------
    async def test_only_ping_is_accepted_from_client(self):
        for bad in ({'type': 'websocket.receive', 'text': 'PING'},
                    {'type': 'websocket.receive', 'text': '{"op":"task.cancel"}'},
                    {'type': 'websocket.receive', 'bytes': b'ping'}):
            ticket = await self.ticket()
            sent = await run_ws(self.transport, ticket,
                                incoming=[{'type': 'websocket.receive', 'text': 'ping'}, bad])
            self.assertIn({'type': 'pong'}, ws_texts(sent))
            self.assertEqual(ws_close(sent), 4400, bad)

    async def test_disconnect_and_cancellation_leave_no_pending_receive(self):
        ticket = await self.ticket()
        sent = await run_ws(self.transport, ticket, incoming=[{'type': 'websocket.disconnect'}])
        self.assertIsNone(ws_close(sent))
        before = asyncio.all_tasks()
        ticket = await self.ticket()
        task = asyncio.ensure_future(run_ws(self.transport, ticket, timeout=5))
        await asyncio.sleep(0.15)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        leftover = [t for t in asyncio.all_tasks() - before if not t.done()]
        self.assertEqual(leftover, [])

    async def test_non_json_event_data_fails_closed(self):
        self.add_event(1, data={'bad': float('nan')})
        ticket = await self.ticket()
        sent = await run_ws(self.transport, ticket)
        self.assertEqual(ws_close(sent), 1011)
        start, body = await call_http(
            self.transport, 'GET', '/v1/accounts/acct-1/projects/project-1/events',
            authorization=self.authorization)
        self.assertEqual(start['status'], 500)
        self.assertEqual(body, {'error': {'code': 'internal_error', 'message': 'Internal error.'}})

    # --- HTTP parsing and CORS ---------------------------------------------------------------
    async def test_malformed_and_duplicate_query_values_are_400(self):
        path = '/v1/accounts/acct-1/projects/project-1/events'
        for query in ('after=', 'after=1&after=2', 'after=-1', 'after=1.0', 'after=%EF%BC%91',
                      'limit=0', 'limit=501', 'limit=', 'after=' + '9' * 5000):
            start, body = await call_http(self.transport, 'GET', path,
                                          authorization=self.authorization, query=query)
            self.assertEqual(start['status'], 400, query)
        start, body = await call_http(self.transport, 'POST',
                                      path.replace('/events', '/event-ticket'),
                                      authorization=self.authorization, query='after=')
        self.assertEqual(start['status'], 400)

    async def test_non_ascii_query_string_is_400_not_crash(self):
        sent = []
        scope = {'type': 'http', 'method': 'GET',
                 'path': '/v1/accounts/acct-1/projects/project-1/events',
                 'query_string': b'after=\xff',
                 'headers': [(b'origin', ORIGIN.encode()),
                             (b'authorization', self.authorization.encode())]}
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            sent.append(message)
        await self.transport(scope, receive, send)
        self.assertEqual(sent[0]['status'], 400)

    async def test_duplicate_origin_or_authorization_headers_fail_closed(self):
        async def call(headers):
            sent = []
            scope = {'type': 'http', 'method': 'GET', 'path': '/v1/accounts/acct-1/projects',
                     'query_string': b'', 'headers': headers}
            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            async def send(message):
                sent.append(message)
            await self.transport(scope, receive, send)
            return sent[0]
        auth = (b'authorization', self.authorization.encode())
        start = await call([(b'origin', b'https://evil.example'), (b'origin', ORIGIN.encode()), auth])
        self.assertEqual(start['status'], 403)
        self.assertNotIn(b'access-control-allow-origin', dict(start['headers']))
        start = await call([(b'origin', ORIGIN.encode()), auth, (b'authorization', b'Bearer x')])
        self.assertEqual(start['status'], 401)

    async def test_disallowed_preflight_gets_no_cors_headers(self):
        for origin in ('https://evil.example', 'null', 'https://acc-web.example.evil.example',
                       'http://acc-web.example', ORIGIN + '/'):
            start, _ = await call_http(self.transport, 'OPTIONS', '/v1/accounts/acct-1/projects',
                                       origin=origin)
            self.assertEqual(start['status'], 403, origin)
            self.assertNotIn(b'access-control-allow-origin', dict(start['headers']))
            self.assertNotIn(b'access-control-allow-credentials', dict(start['headers']))

    def test_origin_policy_rejects_non_origin_values(self):
        for value in ('*', 'https://*', 'https://*.example.com', 'http://a.example', 'https://',
                      'https://a.example/path', 'https://a.example?x=1', 'https://a.example#f',
                      'https://user@a.example', 'https://a.example:99999', 'https://a.example:x',
                      'https://exämple.com', 'null'):
            with self.assertRaises(ValueError, msg=value):
                OriginPolicy(frozenset((value,)))
        with self.assertRaises(ValueError):
            OriginPolicy('https://a.example')
        with self.assertRaises(ValueError):
            OriginPolicy(frozenset())
        policy = OriginPolicy(frozenset(('https://A.example:8443/',)))
        self.assertEqual(policy.allowed_origins, frozenset(('https://a.example:8443',)))
        self.assertTrue(policy.allows('https://a.example:8443'))
        self.assertFalse(policy.allows('https://a.example'))

    async def test_lifespan_scope_is_supported(self):
        sent = []
        messages = [{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}]
        async def receive():
            return messages.pop(0)
        async def send(message):
            sent.append(message)
        await self.transport({'type': 'lifespan'}, receive, send)
        self.assertEqual([m['type'] for m in sent],
                         ['lifespan.startup.complete', 'lifespan.shutdown.complete'])


if __name__ == '__main__':
    unittest.main()
