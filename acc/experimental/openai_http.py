"""Fixed-origin HTTP boundary; nothing invokes it from production composition.

Real testing requires owner-confirmed account, secret injection and budget permit.
Reservations are conservative experiment limits, NOT measured vendor cost.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
import json
import os
import re
import threading
import urllib.error
import urllib.request


class SafeFailure(Exception):
    def __init__(self, safe_code):
        self.safe_code = safe_code
        super().__init__(safe_code)


@dataclass
class ApiTestPermit:
    organization_id: str
    project_id: str
    limit_usd: Decimal
    reserve_per_request_usd: Decimal
    max_requests: int
    _spent: Decimal = field(default=Decimal('0'), init=False, repr=False)
    _requests: int = field(default=0, init=False, repr=False)
    _lock: object = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        if not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', x)
                   for x in (self.organization_id, self.project_id)):
            raise ValueError('confirmed API organization/project required')
        for x in (self.limit_usd, self.reserve_per_request_usd):
            if not isinstance(x, Decimal) or not x.is_finite() or x <= 0:
                raise ValueError('positive explicit spending limits required')
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 100:
            raise ValueError('bounded request count required')

    def reserve(self):
        with self._lock:
            if self._requests >= self.max_requests or self._spent + self.reserve_per_request_usd > self.limit_usd:
                raise SafeFailure('budget_exhausted')
            self._requests += 1
            self._spent += self.reserve_per_request_usd


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAIHTTP:
    def __init__(self, permit: ApiTestPermit):
        if not isinstance(permit, ApiTestPermit):
            raise ValueError('API testing permit required')
        self.permit = permit

    async def post(self, path, payload, timeout):
        if path not in ('/v1/decisions', '/v1/live/sessions'):
            raise ValueError('unsupported OpenAI route')
        raw = json.dumps(payload, allow_nan=False).encode('utf-8')
        if len(raw) > 100_000:
            raise ValueError('request too large')
        key = os.environ.get('OPENAI_API_KEY', '').strip()
        if not key:
            raise SafeFailure('credential_unavailable')
        self.permit.reserve()
        def request():
            req = urllib.request.Request('https://api.openai.com' + path, data=raw, method='POST',
                headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                         'OpenAI-Organization': self.permit.organization_id,
                         'OpenAI-Project': self.permit.project_id})
            try:
                with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
                    body = response.read(1_000_001)
                    if len(body) > 1_000_000:
                        raise SafeFailure('http_error')
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                raise SafeFailure('rate_limited' if exc.code == 429 else 'http_error') from None
            except (urllib.error.URLError, OSError, ValueError):
                raise SafeFailure('http_error') from None
        return await asyncio.to_thread(request)

    async def create(self, payload, timeout):
        return await self.post('/v1/decisions', payload, timeout)
