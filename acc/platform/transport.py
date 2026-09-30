"""ASGI hosted HTTP/WebSocket transport for M09.

No framework dependency is required: an ASGI server (uvicorn/hypercorn/etc.) can host this app.
The transport never exposes the local loopback ACC server.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from .api import ApiResponse, PlatformApi


_SINGLE_VALUE_HEADERS = frozenset(('origin', 'authorization'))


def _headers(scope) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for key, value in scope.get('headers', ()):
        name = key.decode('latin-1').lower()
        if name in _SINGLE_VALUE_HEADERS and name in result:
            # A repeated Origin or Authorization header is ambiguous; treat it as absent so the
            # request fails closed (origin denied / authentication required).
            result[name] = None
            continue
        result[name] = value.decode('latin-1')
    return result


def _json_text(value) -> str:
    # allow_nan=False: NaN/Infinity are not JSON and would break strict browser parsers.
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _json_bytes(value) -> bytes:
    return _json_text(value).encode('utf-8')


def _query(scope) -> dict[str, list[str]]:
    # keep_blank_values: ``?after=`` must be rejected, not silently treated as a missing value.
    return parse_qs(scope.get('query_string', b'').decode('ascii', 'strict'), keep_blank_values=True)


@dataclass(frozen=True)
class OriginPolicy:
    allowed_origins: frozenset[str]

    def __post_init__(self):
        if isinstance(self.allowed_origins, str):
            raise ValueError('allowed_origins must be a collection of origins.')
        normalized = set()
        for value in self.allowed_origins:
            normalized.add(self._normalize(value))
        if not normalized:
            raise ValueError('At least one hosted origin is required.')
        object.__setattr__(self, 'allowed_origins', frozenset(normalized))

    @staticmethod
    def _normalize(value: object) -> str:
        """Return ``https://host[:port]`` or raise; no wildcard, path, query, fragment or userinfo."""
        if not isinstance(value, str) or not value.isascii() or not value.startswith('https://'):
            raise ValueError('Hosted origins must be explicit HTTPS origins.')
        origin = value.rstrip('/').lower()
        if '*' in origin:
            raise ValueError('Allowed origins cannot use wildcards or paths.')
        try:
            parts = urlsplit(origin)
            port = parts.port
        except ValueError as exc:
            raise ValueError('Hosted origins must be explicit HTTPS origins.') from exc
        if (parts.path or parts.query or parts.fragment or '?' in origin or '#' in origin
                or parts.username is not None or parts.password is not None
                or not parts.hostname):
            raise ValueError('Allowed origins cannot use wildcards or paths.')
        canonical = 'https://' + parts.hostname + ('' if port is None else ':' + str(port))
        if canonical != origin:
            raise ValueError('Hosted origins must be explicit HTTPS origins.')
        return canonical

    def allows(self, origin: str | None) -> bool:
        # Browsers serialize Origin exactly as scheme://host[:port]; compare that form exactly.
        return isinstance(origin, str) and origin in self.allowed_origins


@dataclass(frozen=True)
class EventTicket:
    account_id: str
    project_id: str
    cursor: int
    # Never shown in repr/logging. Held only in process memory; see EventTicketStore.
    authorization: str = field(repr=False)
    expires_at: float = 0.0


class EventTicketStore:
    """Short-lived, single-use WebSocket tickets.

    Browsers cannot attach an Authorization header to the WebSocket constructor. The bearer session
    therefore authorizes an HTTPS ticket request; only a random one-time ticket appears in the WSS
    URL. The store keys records by a SHA-256 digest of that ticket.

    Each record also holds the Authorization value that requested it, so the stream can be
    reauthorized through M08 on every poll. The store is therefore process-local by design: it must
    never be serialized, persisted, or shared across processes. A multi-process deployment must
    route the ticket request and the WebSocket to the same process or replace this store with one
    keyed by a server-side session reference once M08 provides one.
    """

    def __init__(self, *, clock=time.time, ttl_seconds: int = 60):
        if type(ttl_seconds) is not int or not 10 <= ttl_seconds <= 300:
            raise ValueError('WebSocket ticket TTL must be 10-300 seconds.')
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[str, EventTicket] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _digest(ticket: str) -> str:
        return hashlib.sha256(ticket.encode('utf-8')).hexdigest()

    async def issue(
        self, account_id: str, project_id: str, cursor: int, authorization: str,
    ) -> str:
        raw = 'accw_' + secrets.token_urlsafe(32)
        record = EventTicket(
            account_id, project_id, cursor, authorization,
            expires_at=self.clock() + self.ttl_seconds)
        async with self._lock:
            self._expire()
            digest = self._digest(raw)
            if digest in self._tickets:
                raise RuntimeError('WebSocket ticket collision.')
            self._tickets[digest] = record
        return raw

    async def consume(self, raw: str) -> EventTicket | None:
        if not isinstance(raw, str) or not raw.startswith('accw_') or len(raw) > 512:
            return None
        async with self._lock:
            self._expire()
            record = self._tickets.pop(self._digest(raw), None)
        if record is None or record.expires_at <= self.clock():
            return None
        return record

    def _expire(self) -> None:
        now = self.clock()
        for digest, record in list(self._tickets.items()):
            if record.expires_at <= now:
                self._tickets.pop(digest, None)


class HostedTransport:
    def __init__(
        self,
        api: PlatformApi,
        *,
        origins: OriginPolicy,
        tickets: EventTicketStore | None = None,
        poll_seconds: float = 0.5,
    ):
        if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or not 0.05 <= poll_seconds <= 30:
            raise ValueError('poll_seconds must be between 0.05 and 30.')
        self.api = api
        self.origins = origins
        self.tickets = tickets or EventTicketStore()
        self.poll_seconds = float(poll_seconds)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            await self._http(scope, receive, send)
            return
        if scope['type'] == 'websocket':
            await self._websocket(scope, receive, send)
            return
        if scope['type'] == 'lifespan':
            await self._lifespan(receive, send)
            return
        raise RuntimeError('Unsupported ASGI scope.')

    @staticmethod
    async def _lifespan(receive, send):
        while True:
            message = await receive()
            if message.get('type') == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message.get('type') == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return

    async def _http(self, scope, receive, send):
        headers = _headers(scope)
        origin = headers.get('origin')
        if not self.origins.allows(origin):
            await self._send_json(send, 403, {'error': {'code': 'origin_denied', 'message': 'Origin denied.'}})
            return

        method = scope.get('method', 'GET').upper()
        path = scope.get('path', '')
        if method == 'OPTIONS':
            await self._send_json(send, 204, {}, origin=origin)
            return

        parts = [part for part in path.split('/') if part]
        authorization = headers.get('authorization')

        try:
            query = _query(scope)
            response = self._route_http(method, parts, authorization, query)
            if asyncio.iscoroutine(response):
                response = await response
        except (UnicodeError, ValueError):
            response = ApiResponse(400, {'error': {'code': 'invalid_request', 'message': 'Invalid request.'}})
        await self._send_json(send, response.status, response.body, origin=origin)

    def _route_http(self, method, parts, authorization, query):
        if len(parts) >= 3 and parts[:2] == ['v1', 'accounts']:
            account_id = parts[2]
            if method == 'GET' and parts[3:] == ['projects']:
                return self.api.handle(self.api.list_projects, authorization, account_id)

            if len(parts) >= 5 and parts[3] == 'projects':
                project_id = parts[4]
                tail = parts[5:]
                if method == 'GET' and not tail:
                    return self.api.handle(
                        self.api.project_state, authorization, account_id, project_id)
                if method == 'GET' and len(tail) == 2 and tail[0] == 'tasks':
                    return self.api.handle(
                        self.api.task, authorization, account_id, project_id, tail[1])
                if method == 'GET' and tail == ['events']:
                    after = self._query_int(query, 'after', 0)
                    limit = self._query_int(query, 'limit', 200)
                    return self.api.handle(
                        self.api.events_after, authorization, account_id, project_id,
                        after, limit=limit)
                if method == 'POST' and tail == ['event-ticket']:
                    after = self._query_int(query, 'after', 0)
                    return self._issue_ticket(authorization, account_id, project_id, after)
                if method == 'POST' and tail == ['commands']:
                    return self._execute_command(
                        authorization, account_id, project_id, self._request_body)

        return ApiResponse(404, {'error': {'code': 'not_found', 'message': 'Not found.'}})

    async def _issue_ticket(self, authorization, account_id, project_id, after):
        # Authorize through the same event path before issuing a WSS capability.
        response = self.api.handle(
            self.api.events_after, authorization, account_id, project_id, after, limit=1)
        if response.status != 200:
            return response
        if response.body.get('reset_required'):
            return ApiResponse(409, {
                'error': {
                    'code': 'event_reset_required',
                    'message': 'Refresh project state before reconnecting.',
                },
                'cursor': response.body.get('cursor'),
                'oldest_available': response.body.get('oldest_available'),
            })
        ticket = await self.tickets.issue(account_id, project_id, after, authorization)
        return ApiResponse(201, {
            'ticket': ticket,
            'expires_in': self.tickets.ttl_seconds,
            'websocket_path': '/v1/events',
        })

    @staticmethod
    def _query_int(query, name, default):
        values = query.get(name)
        if not values:
            return default
        if len(values) != 1:
            raise ValueError('duplicate query value')
        value = values[0]
        if not value.isascii() or not value.isdigit():
            raise ValueError('invalid integer query')
        return int(value)

    async def _send_json(self, send, status, body, *, origin=None):
        headers = [
            (b'content-type', b'application/json'),
            (b'cache-control', b'no-store'),
            (b'x-content-type-options', b'nosniff'),
            (b'referrer-policy', b'no-referrer'),
        ]
        if origin and self.origins.allows(origin):
            encoded = origin.rstrip('/').encode('ascii')
            headers.extend([
                (b'access-control-allow-origin', encoded),
                (b'vary', b'Origin'),
                (b'access-control-allow-headers', b'Authorization, Content-Type'),
                (b'access-control-allow-methods', b'GET, POST, OPTIONS'),
                (b'access-control-max-age', b'600'),
            ])
        if status == 204:
            raw = b''
        else:
            try:
                raw = _json_bytes(body)
            except (TypeError, ValueError, RecursionError):
                # A non-JSON value from a read model/event source: fail closed and opaque.
                status = 500
                raw = _json_bytes({'error': {'code': 'internal_error', 'message': 'Internal error.'}})
        headers.append((b'content-length', str(len(raw)).encode('ascii')))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': raw})

    async def _websocket(self, scope, receive, send):
        # ASGI: the first event on a WebSocket scope is websocket.connect. Validate only after it,
        # and close before accept so the server rejects the handshake (HTTP 403).
        first = await receive()
        if first.get('type') != 'websocket.connect':
            return
        headers = _headers(scope)
        if not self.origins.allows(headers.get('origin')) or scope.get('path') != '/v1/events':
            await send({'type': 'websocket.close', 'code': 4403, 'reason': 'Origin denied.'})
            return
        try:
            values = _query(scope).get('ticket', [])
            if len(values) != 1:
                raise ValueError()
            ticket = await self.tickets.consume(values[0])
        except (UnicodeError, ValueError):
            ticket = None
        if ticket is None:
            await send({'type': 'websocket.close', 'code': 4401, 'reason': 'Authentication required.'})
            return

        await send({'type': 'websocket.accept'})
        # One outstanding receive() at a time; a poll timeout never cancels it mid-message.
        pending = [asyncio.ensure_future(receive())]
        try:
            await self._stream(ticket, receive, send, pending)
        finally:
            if not pending[0].done():
                pending[0].cancel()
                try:
                    await pending[0]
                except (asyncio.CancelledError, Exception):
                    pass

    async def _stream(self, ticket: EventTicket, receive, send, pending):
        cursor = ticket.cursor
        while True:
            # Re-authorize every poll using the session that created the one-time ticket. The
            # bearer is held only in process memory for this short-lived connection and is never
            # sent in the WebSocket URL or event payload.
            response = self.api.handle(
                self.api.events_after,
                ticket.authorization,
                ticket.account_id,
                ticket.project_id,
                cursor,
            )
            if response.status == 401:
                await send({'type': 'websocket.close', 'code': 4401, 'reason': 'Session ended.'})
                return
            if response.status == 403:
                await send({'type': 'websocket.close', 'code': 4403, 'reason': 'Access ended.'})
                return
            if response.status != 200:
                await send({'type': 'websocket.close', 'code': 1011, 'reason': 'Event stream failed.'})
                return

            body = response.body
            if body.get('reset_required'):
                message = {
                    'type': 'reset_required',
                    'cursor': body['cursor'],
                    'oldest_available': body.get('oldest_available'),
                }
                await send({'type': 'websocket.send', 'text': _json_text(message)})
                await send({'type': 'websocket.close', 'code': 4009, 'reason': 'State refresh required.'})
                return

            events = body.get('events', [])
            has_more = bool(events) and bool(body.get('has_more'))
            if events:
                try:
                    text = _json_text({
                        'type': 'events',
                        'events': events,
                        'cursor': body['cursor'],
                        'has_more': has_more,
                    })
                except (TypeError, ValueError, RecursionError):
                    await send({'type': 'websocket.close', 'code': 1011, 'reason': 'Event stream failed.'})
                    return
                await send({'type': 'websocket.send', 'text': text})
                cursor = body['cursor']

            incoming = pending[0]
            if not has_more:
                await asyncio.wait((incoming,), timeout=self.poll_seconds)
            if not incoming.done():
                continue
            message = incoming.result()
            kind = message.get('type')
            if kind == 'websocket.disconnect':
                return
            if kind == 'websocket.receive' and message.get('text') == 'ping':
                await send({'type': 'websocket.send', 'text': '{"type":"pong"}'})
            elif kind == 'websocket.receive':
                # Client data is not a command channel. A tiny ping is the only accepted input.
                await send({'type': 'websocket.close', 'code': 4400, 'reason': 'Read-only stream.'})
                return
            pending[0] = asyncio.ensure_future(receive())
