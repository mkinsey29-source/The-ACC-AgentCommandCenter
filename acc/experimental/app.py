"""Isolated ASGI prototype; production/staging entrypoints stay unchanged.

Construct with the existing PlatformApi/AuthService/read repository. No bootstrap
identity, API key or secret endpoint is added. Serve only behind private HTTPS.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path

from ..platform.api import ApiError
from ..platform.transport import OriginPolicy, _headers, _unique_object, _reject_constant
from .live_sessions import LiveSessions
from .sentry import SentryReads


class PrototypeApplication:
    def __init__(self, api, *, origin, decisions=None, live_http=None, voice_enabled=False, connector=None):
        self.origins = OriginPolicy(frozenset((origin,)))
        self.reads = SentryReads(api, source_origin=origin, decisions=decisions)
        self.live = LiveSessions(self.reads, live_http, enabled=voice_enabled, connector=connector)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    await self.live.shutdown()
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            return
        headers = _headers(scope)
        status, body = 404, {'error': 'not_found'}
        content_type = b'application/json'
        try:
            path = scope.get('path', '')
            if scope.get('method') == 'GET' and path in ('/experimental/sentry', '/experimental/sentry.js'):
                name = 'sentry.html' if path.endswith('sentry') else 'sentry.js'
                body = (Path(__file__).parent / 'web' / name).read_bytes()
                content_type = b'text/html; charset=utf-8' if name.endswith('html') else b'text/javascript'
                status = 200
            else:
                if not self.origins.allows(headers.get('origin')):
                    raise ApiError(403, 'origin_denied', 'Origin denied.')
                if scope.get('method') != 'POST' or headers.get('content-type') != 'application/json':
                    raise ApiError(404, 'not_found', 'Not found.')
                parts = path.strip('/').split('/')
                if len(parts) != 5 or parts[:2] != ['experimental', 'sentry']:
                    raise ApiError(404, 'not_found', 'Not found.')
                account, project, action = parts[2:]
                # Authorize BEFORE body processing or any paid call.
                self.reads.api.project_state(headers.get('authorization'), account, project)
                raw = bytearray()
                while True:
                    event = await asyncio.wait_for(receive(), 5)
                    if event['type'] == 'http.disconnect':
                        return
                    raw.extend(event.get('body', b''))
                    if len(raw) > 64_000:
                        raise ApiError(413, 'too_large', 'Request too large.')
                    if not event.get('more_body'):
                        break
                data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                if not isinstance(data, dict):
                    raise ValueError('object required')
                auth = headers.get('authorization')
                if action == 'lookup' and set(data) <= {'intent', 'text'}:
                    body = await self.reads.lookup(auth, account, project, **data)
                elif action == 'session' and set(data) == {'sdp'}:
                    body = await self.live.open(auth, account, project, data['sdp'])
                elif action == 'status' and set(data) == {'session_key'}:
                    body = self.live.status(auth, account, project, data['session_key'])
                elif action == 'close' and set(data) == {'session_key'}:
                    body = await self.live.close(auth, account, project, data['session_key'])
                else:
                    raise ApiError(404, 'not_found', 'Not found.')
                status = 200
        except ApiError as exc:
            status, body = exc.status, {'error': exc.code, 'message': exc.message}
        except (ValueError, TypeError, UnicodeError):
            status, body = 400, {'error': 'invalid_request'}
        except Exception:
            status, body = 503, {'error': 'unavailable', 'message': 'Prototype unavailable. Use text status.'}
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', content_type), (b'cache-control', b'no-store'),
                                (b'x-content-type-options', b'nosniff'),
                                (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' https://api.openai.com; media-src 'self' blob:; frame-ancestors 'none'")]})
        await send({'type': 'http.response.body', 'body': body})
