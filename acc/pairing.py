"""Pair a phone or browser with this ACC node, then authenticate it on later requests.

The flow (design after ACC-Workspace's mobile pairing, MIT): the owner creates a one-time pairing link
at the computer; the other device claims it and shows a six-digit match code; the owner confirms the
codes match and approves at the computer; the device then receives a long-lived credential once.
Secrets are stored only as SHA-256 digests. Only paired devices reach the API from a remote host; the
control token never does.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

PAIR_TTL = 5 * 60
PENDING_TTL = 5 * 60
DEVICE_TTL = 180 * 24 * 60 * 60
MAX_DEVICES = 8
CLAIMS_PER_MINUTE = 60
TOUCH_INTERVAL = 60
NAME_LIMIT = 60


class PairingError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def _clean_name(value) -> str:
    text = ''.join(ch for ch in str(value or '') if ch.isprintable() and ch not in '<>').strip()[:NAME_LIMIT]
    return text or 'My phone'


class Pairing:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._pairing = None  # {'hash', 'code_hash', 'expires'}
        self._pending: dict[str, dict] = {}
        self._window = [clock(), 0]
        self._devices = self._load()

    # --- persistence -------------------------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise PairingError('The pairing file is unreadable; fix or remove it before enabling remote access.', 500) from exc
        devices = data.get('devices') if isinstance(data, dict) else None
        if not isinstance(devices, list):
            raise PairingError('The pairing file is not valid; fix or remove it before enabling remote access.', 500)
        return [d for d in devices if isinstance(d, dict) and {'id', 'name', 'hash', 'expires'} <= set(d)]

    def _save(self, devices: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix='.pairing-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump({'devices': devices}, handle)
            if os.name != 'nt':
                os.chmod(temp, 0o600)
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def _active(self) -> list[dict]:
        now = self.clock()
        return [d for d in self._devices if d['expires'] > now]

    # --- owner side (local, token-authenticated) ----------------------------------------------
    def begin(self) -> dict:
        """Create a one-time pairing link token and eight-digit code; any earlier one stops working."""
        with self._lock:
            token, code = secrets.token_urlsafe(24), str(secrets.randbelow(90_000_000) + 10_000_000)
            expires = self.clock() + PAIR_TTL
            self._pairing = {'hash': _digest(token), 'code_hash': _digest(code), 'expires': expires}
            return {'token': token, 'code': code, 'expires': expires}

    def pending(self) -> list[dict]:
        with self._lock:
            now = self.clock()
            for key in [k for k, v in self._pending.items() if v['expires'] < now]:
                del self._pending[key]
            return [{'id': v['id'], 'name': v['name'], 'code': v['code'], 'expires': v['expires']}
                    for v in self._pending.values() if v['state'] == 'waiting']

    def approve(self, request_id: str) -> dict:
        with self._lock:
            item = self._pending.get(request_id)
            if item is None or item['expires'] < self.clock() or item['state'] != 'waiting':
                raise PairingError('This connection request has expired or was already answered.', 409)
            if len(self._active()) >= MAX_DEVICES:
                raise PairingError('Disconnect an old device before adding another.', 409)
            credential = secrets.token_urlsafe(32)
            device = {'id': secrets.token_hex(8), 'name': item['name'], 'hash': _digest(credential),
                      'created': self.clock(), 'last_seen': None, 'expires': self.clock() + DEVICE_TTL}
            kept = [*self._active(), device]  # expired devices are dropped on the way
            self._save(kept)  # raises before any in-memory change if the write fails
            self._devices = kept
            item.update(state='approved', credential=credential, device_id=device['id'])
            return self.public(device)

    def deny(self, request_id: str) -> None:
        with self._lock:
            item = self._pending.get(request_id)
            if item is None or item['state'] != 'waiting':
                raise PairingError('This connection request has expired or was already answered.', 409)
            item['state'] = 'denied'

    def devices(self) -> list[dict]:
        with self._lock:
            return [self.public(d) for d in self._active()]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            remaining = [d for d in self._devices if d['id'] != device_id]
            if len(remaining) == len(self._devices):
                return False
            self._save(remaining)
            self._devices = remaining
            return True

    @staticmethod
    def public(device: dict) -> dict:
        return {k: device[k] for k in ('id', 'name', 'created', 'last_seen', 'expires') if k in device}

    # --- device side (remote, unauthenticated until approved) ----------------------------------
    def _rate_limit(self) -> None:
        now = self.clock()
        if now - self._window[0] > 60:
            self._window = [now, 0]
        self._window[1] += 1
        if self._window[1] > CLAIMS_PER_MINUTE:
            raise PairingError('Too many pairing attempts. Wait a minute and try again.', 429)

    def claim(self, secret_value, name) -> dict:
        """Claim the open pairing with its link token or eight-digit code; consumes it either way."""
        with self._lock:
            self._rate_limit()
            pairing = self._pairing
            candidate = _digest(secret_value) if isinstance(secret_value, str) else ''
            if (not pairing or pairing['expires'] < self.clock()
                    or not (_same(pairing['hash'], candidate) or _same(pairing['code_hash'], candidate))):
                raise PairingError('This pairing link has expired or was already used. Create a new one at the computer.', 403)
            if len(self._active()) >= MAX_DEVICES:
                raise PairingError('Disconnect an old device before adding another.', 409)
            self._pairing = None
            request_id, claim = secrets.token_hex(8), secrets.token_urlsafe(24)
            match = str(secrets.randbelow(900_000) + 100_000)
            self._pending[request_id] = {'id': request_id, 'name': _clean_name(name), 'code': match,
                                         'claim_hash': _digest(claim), 'state': 'waiting', 'credential': None,
                                         'expires': self.clock() + PENDING_TTL}
            return {'request_id': request_id, 'claim': claim, 'code': match}

    def poll(self, request_id, claim) -> dict:
        """Report the request's state; hand the credential over exactly once."""
        with self._lock:
            item = self._pending.get(request_id) if isinstance(request_id, str) else None
            if item is None or not isinstance(claim, str) or not _same(item['claim_hash'], _digest(claim)):
                raise PairingError('Unknown connection request.', 404)
            if item['expires'] < self.clock():
                del self._pending[request_id]
                return {'status': 'expired'}
            if item['state'] == 'denied':
                del self._pending[request_id]
                return {'status': 'denied'}
            if item['state'] == 'approved':
                del self._pending[request_id]
                return {'status': 'approved', 'credential': item['credential'], 'device_id': item['device_id'],
                        'max_age': DEVICE_TTL}
            return {'status': 'waiting'}

    # --- every later request -----------------------------------------------------------------
    def authenticate(self, credential) -> dict | None:
        if not isinstance(credential, str) or not credential or len(credential) > 100:
            return None
        digest = _digest(credential)
        with self._lock:
            now = self.clock()
            for device in self._devices:
                if _same(device['hash'], digest) and device['expires'] > now:
                    if device['last_seen'] is None or now - device['last_seen'] > TOUCH_INTERVAL:
                        updated = [{**d, 'last_seen': now} if d is device else d for d in self._devices]
                        try:
                            self._save(updated)
                            self._devices = updated
                            device = next(d for d in updated if d['id'] == device['id'])
                        except OSError:
                            pass  # a failed last-seen write must not lock out a valid device
                    return self.public(device)
        return None

    def is_active(self, device_id: str) -> bool:
        with self._lock:
            return any(d['id'] == device_id for d in self._active())
