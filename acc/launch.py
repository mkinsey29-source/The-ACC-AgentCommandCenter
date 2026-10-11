"""One-time launch codes: hand the owner token to the owner's browser without writing it anywhere.

The owner token exists only in the server's memory and, afterwards, in the owner's browser tab. ACC prints
(and opens) a link carrying a single-use launch code instead. The page exchanges the code for the token over
loopback, once. A code that leaks into a process listing or a terminal log is useless after the first use,
and a stolen first use is visible: the owner's own link then fails.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

LAUNCH_TTL = 600
MAX_OUTSTANDING = 5
ATTEMPTS_PER_MINUTE = 30


class LaunchError(Exception):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LaunchCodes:
    def __init__(self, token: str, *, clock=time.time, ttl: int = LAUNCH_TTL):
        self._token, self.clock, self.ttl = token, clock, ttl
        self._codes: list[dict] = []
        self._window = [clock(), 0]
        self._lock = threading.Lock()

    def mint(self) -> dict:
        """A new single-use code; the oldest outstanding one is dropped past the limit."""
        with self._lock:
            now = self.clock()
            self._codes = [c for c in self._codes if c['expires'] > now]
            code = secrets.token_urlsafe(24)
            self._codes.append({'hash': _digest(code), 'expires': now + self.ttl})
            del self._codes[:-MAX_OUTSTANDING]
            return {'code': code, 'expires': now + self.ttl}

    def redeem(self, code) -> str:
        """Exchange a valid unused code for the owner token. The code is consumed."""
        with self._lock:
            now = self.clock()
            if now - self._window[0] > 60:
                self._window = [now, 0]
            self._window[1] += 1
            if self._window[1] > ATTEMPTS_PER_MINUTE:
                raise LaunchError('Too many attempts. Wait a minute and try again.', 429)
            candidate = _digest(code) if isinstance(code, str) and 0 < len(code) <= 200 else ''
            for entry in self._codes:
                if entry['expires'] > now and hmac.compare_digest(entry['hash'], candidate):
                    self._codes.remove(entry)
                    return self._token
            raise LaunchError('This link has expired or was already used. Use the link ACC printed, or New window in an open ACC tab.')
