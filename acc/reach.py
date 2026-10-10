"""Reachability: where ACC listens, which Host headers it answers, and what it refuses to start with.

Sign-in is separate from reachability. Every mode other than ``local`` requires a strong control
token and device pairing (``acc/pairing.py``); no mode trusts the network. ``local`` keeps today's
behavior: loopback only, local token, no remote hosts.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MODES = ('local', 'tailscale-serve', 'private', 'docker', 'custom')
MIN_TOKEN_LENGTH = 32
MAX_REMOTE_HOSTS = 8
_CGNAT = ipaddress.ip_network('100.64.0.0/10')
_HOST_HEADER = re.compile(
    r'^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*'
    r'|\[[0-9a-f:]+\]|\d{1,3}(?:\.\d{1,3}){3})(?::\d{1,5})?$')


class ReachError(ValueError):
    """The requested reachability is unsafe or inconsistent; ACC refuses to start with it."""


@dataclass(frozen=True)
class Finding:
    setting: str
    status: str  # 'ok', 'warn' or 'fail'
    detail: str


def address_class(host: str) -> str:
    """Return loopback, private, wildcard, public or name for a bind address."""
    text = host.strip().lower()
    if text == 'localhost':
        return 'loopback'
    try:
        ip = ipaddress.ip_address(text.strip('[]'))
    except ValueError:
        return 'name'
    if ip.is_unspecified:
        return 'wildcard'
    if ip.is_loopback:
        return 'loopback'
    if ip.is_private or ip.is_link_local or (ip.version == 4 and ip in _CGNAT):
        return 'private'
    return 'public'


def normalize_host(value: str) -> str:
    """Validate one Host header value exactly as a browser would send it (``host`` or ``host:port``)."""
    text = value.strip().lower() if isinstance(value, str) else ''
    if not _HOST_HEADER.match(text):
        raise ReachError(f'Not a valid host value: {value!r}. Use host or host:port exactly as the browser shows it.')
    if ':' in text.rsplit(']', 1)[-1] and not 1 <= int(text.rsplit(':', 1)[1]) <= 65535:
        raise ReachError(f'Port out of range in {value!r}.')
    return text


def in_container() -> bool:
    return Path('/.dockerenv').exists() or os.environ.get('ACC_IN_DOCKER') == '1'


class Reach:
    """The validated reachability of one running server."""

    def __init__(self, mode: str = 'local', bind_host: str = '127.0.0.1',
                 remote_hosts: Iterable[str] = (), secure_cookie: bool = False):
        if mode not in MODES:
            raise ReachError('Unknown reachability mode: ' + str(mode))
        self.mode, self.bind_host, self.secure_cookie = mode, bind_host, secure_cookie
        self._hosts = frozenset(normalize_host(h) for h in remote_hosts)

    @property
    def remote(self) -> bool:
        return self.mode != 'local'

    def remote_hosts(self) -> frozenset:
        return self._hosts

    def set_remote_hosts(self, hosts: Iterable[str]) -> None:
        self._hosts = frozenset(normalize_host(h) for h in hosts)  # replaced whole, so reads never tear

    def classify(self, host_header: str, local_port: int) -> str | None:
        """'local' for this machine's loopback names, 'remote' for an accepted remote host, else None."""
        host = (host_header or '').strip().lower()
        if host in (f'127.0.0.1:{local_port}', f'localhost:{local_port}'):
            return 'local'
        return 'remote' if host in self._hosts else None

    def validate(self, token: str, bind_host: str, pairing_available: bool) -> None:
        """Refuse an unsafe combination at construction time."""
        kind = address_class(bind_host)
        if self.mode == 'local':
            if kind != 'loopback':
                raise ReachError('Local mode binds a loopback address only.')
            return
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
            raise ReachError(f'Remote access needs a control token of at least {MIN_TOKEN_LENGTH} characters.')
        if not pairing_available:
            raise ReachError('Remote access requires device pairing; no mode trusts the network.')
        if self.mode == 'tailscale-serve' and kind != 'loopback':
            raise ReachError('Tailscale Serve mode keeps ACC on loopback; Tailscale carries the traffic.')
        if kind == 'wildcard' and self.mode != 'docker' and not self._hosts:
            raise ReachError('A wildcard bind needs at least one accepted host.')


def resolve(mode: str, *, port: int, token: str, host: str | None = None,
            allow_hosts: Iterable[str] = (), public_ack: bool = False, behind_https: bool = False,
            container: bool | None = None, route=None) -> Reach:
    """Turn command-line choices into a validated ``Reach`` or raise ``ReachError`` with the reason."""
    allow = [normalize_host(h) for h in allow_hosts]
    if len(set(allow)) > MAX_REMOTE_HOSTS:
        raise ReachError(f'At most {MAX_REMOTE_HOSTS} accepted hosts.')
    if mode not in MODES:
        raise ReachError('Unknown reachability mode: ' + str(mode) + '. Choose one of: ' + ', '.join(MODES) + '.')
    if public_ack and mode != 'custom':
        raise ReachError('--public-ack applies only to custom mode.')
    if mode == 'local':
        if host or allow or behind_https:
            raise ReachError('--host, --allow-host and --behind-https need a remote mode.')
        return Reach('local', '127.0.0.1')
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
        raise ReachError(f'Remote access needs a control token of at least {MIN_TOKEN_LENGTH} characters.')
    if mode == 'tailscale-serve':
        if host or allow:
            raise ReachError('Tailscale Serve mode derives its host from Tailscale; remove --host and --allow-host.')
        status = route.probe() if route is not None else {'ready': False, 'message': 'Tailscale is not available.'}
        if not status.get('ready'):
            raise ReachError(status.get('message') or 'Tailscale is not ready.')
        return Reach('tailscale-serve', '127.0.0.1', (), secure_cookie=True)
    if mode == 'private':
        if not host or address_class(host) != 'private':
            raise ReachError('Private mode binds one explicit private address (for example 192.168.1.20 or a 100.x Tailscale address); wildcards and public addresses are refused.')
        if not allow and not port:
            raise ReachError('Private mode needs a fixed --port or an explicit --allow-host.')
        return Reach('private', host.strip(), allow or [f'{host.strip()}:{port}'], secure_cookie=behind_https)
    if mode == 'docker':
        inside = in_container() if container is None else container
        if not inside:
            raise ReachError('Docker mode runs only inside a container (set ACC_IN_DOCKER=1 if detection fails).')
        bind = (host or '0.0.0.0').strip()
        if address_class(bind) not in ('wildcard', 'private'):
            raise ReachError('Docker mode binds 0.0.0.0 or a private address inside the container.')
        if not allow:
            raise ReachError('Docker mode needs --allow-host for the address the host publishes (for example localhost:8765).')
        return Reach('docker', bind, allow, secure_cookie=behind_https)
    # custom
    if not host:
        raise ReachError('Custom mode needs --host.')
    kind = address_class(host)
    if not allow:
        raise ReachError('Custom mode needs at least one --allow-host.')
    if kind in ('public', 'name') and not public_ack:
        raise ReachError('This address may be reachable from the internet. Add --public-ack only behind your own TLS front door.')
    return Reach('custom', host.strip(), allow, secure_cookie=behind_https)


def check(mode: str, *, port: int, token: str | None, host: str | None = None,
            allow_hosts: Iterable[str] = (), public_ack: bool = False, behind_https: bool = False,
            container: bool | None = None, route=None) -> tuple[list[Finding], Reach | None]:
    """The mode-aware check: report every setting, and whether ACC would start."""
    findings: list[Finding] = [Finding('mode', 'ok', mode)]
    try:
        reach = resolve(mode, port=port, token=token or '', host=host, allow_hosts=allow_hosts,
                        public_ack=public_ack, behind_https=behind_https, container=container, route=route)
    except ReachError as exc:
        findings.append(Finding('start', 'fail', str(exc)))
        return findings, None
    kind = address_class(reach.bind_host)
    findings.append(Finding('bind address', 'ok' if kind in ('loopback', 'private') else 'warn',
                            f'{reach.bind_host} ({kind})'))
    if reach.remote:
        findings.append(Finding('control token', 'ok', 'present and long enough; never accepted from a remote host'))
        findings.append(Finding('device pairing', 'ok', 'required for every remote request'))
        hosts = ', '.join(sorted(reach.remote_hosts())) or 'derived from Tailscale when the route starts'
        findings.append(Finding('accepted hosts', 'ok', hosts))
        findings.append(Finding('cookie', 'ok' if reach.secure_cookie else 'warn',
                                'Secure (served over HTTPS)' if reach.secure_cookie else
                                'not marked Secure; use --behind-https only if a TLS front door serves the page'))
    else:
        findings.append(Finding('remote access', 'ok', 'off; loopback and local token only'))
    return findings, reach


def format_findings(findings: list[Finding]) -> str:
    width = max(len(f.setting) for f in findings)
    marks = {'ok': 'ok  ', 'warn': 'warn', 'fail': 'FAIL'}
    return '\n'.join(f'{marks[f.status]}  {f.setting.ljust(width)}  {f.detail}' for f in findings)
