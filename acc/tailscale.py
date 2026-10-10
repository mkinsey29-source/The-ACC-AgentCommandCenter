"""A private Tailscale Serve route to ACC's loopback server, with safeguards.

ACC stays bound to loopback; ``tailscale serve`` carries tailnet traffic to it over HTTPS. This module
never resets or replaces anyone else's Serve configuration, refuses a port that is shared publicly
(Funnel), and verifies the route before reporting it. The command line mirrors what ACC-Workspace
(MIT) does; it has been exercised against a fake ``tailscale`` command, not yet against a real one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

PORTS = (8443, 8444, 8445, 8446)
TIMEOUT_SECONDS = 12


class TailscaleError(RuntimeError):
    """The route could not be made safely; the message says what to change."""


def _default_executable() -> list[str]:
    override = os.environ.get('ACC_TAILSCALE')
    if override:
        return [override]
    found = shutil.which('tailscale')
    if found:
        return [found]
    if os.name == 'nt':
        installed = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale' / 'tailscale.exe'
        if installed.exists():
            return [str(installed)]
    return ['tailscale']


def _normalize_target(value: str) -> str:
    return str(value).rstrip('/')


class Tailscale:
    def __init__(self, executable: Sequence[str] | None = None,
                 run: Callable[[list[str]], tuple[int, str, str]] | None = None):
        self.executable = list(executable) if executable else _default_executable()
        self._run = run or self._subprocess

    def _subprocess(self, argv: list[str]) -> tuple[int, str, str]:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, shell=False)
        return done.returncode, done.stdout, done.stderr

    def command(self, *args: str) -> str:
        try:
            code, out, err = self._run([*self.executable, *args])
        except subprocess.TimeoutExpired as exc:
            raise TailscaleError('Tailscale did not answer in time. Check that it is running, then try again.') from exc
        if code != 0:
            raise TailscaleError((err or out or 'tailscale failed').strip()[:300])
        return out

    def probe(self) -> dict:
        try:
            status = json.loads(self.command('status', '--json'))
        except FileNotFoundError:
            return {'installed': False, 'ready': False, 'hostname': '',
                    'message': 'Install Tailscale on this computer and your phone, then sign in to the same account.'}
        except (TailscaleError, ValueError, OSError):
            return {'installed': True, 'ready': False, 'hostname': '',
                    'message': 'Open Tailscale and sign in, then try again.'}
        hostname = str((status.get('Self') or {}).get('DNSName') or '').rstrip('.').lower()
        running = status.get('BackendState') == 'Running'
        named = bool(hostname) and hostname.endswith('.ts.net')
        message = '' if running and named else (
            'Open Tailscale and sign in, then try again.' if not running else
            'Tailscale needs an HTTPS device name. Enable MagicDNS and HTTPS in your Tailscale account.')
        return {'installed': True, 'ready': running and named, 'hostname': hostname, 'message': message}

    def config(self) -> dict:
        try:
            return json.loads(self.command('serve', 'status', '--json') or '{}')
        except ValueError as exc:
            raise TailscaleError('Tailscale returned a Serve status ACC cannot read.') from exc

    @staticmethod
    def _funnel(config: dict, hostname: str, port: int) -> bool:
        return bool((config.get('AllowFunnel') or {}).get(f'{hostname}:{port}'))

    @staticmethod
    def owns(config: dict, saved: dict | None) -> bool:
        """True only when the saved route is still exactly ours: one handler proxying to our target."""
        if not saved or not saved.get('hostname') or not saved.get('port') or not saved.get('target'):
            return False
        handlers = ((config.get('Web') or {}).get(f"{saved['hostname']}:{saved['port']}") or {}).get('Handlers') or {}
        proxy = (handlers.get('/') or {}).get('Proxy')
        return len(handlers) == 1 and proxy is not None and _normalize_target(proxy) == _normalize_target(saved['target'])

    def enable(self, target: str, previous: dict | None = None) -> dict:
        """Create (or keep) a private HTTPS route to ``target`` and return it; raise rather than risk exposure."""
        status = self.probe()
        if not status['ready']:
            raise TailscaleError(status['message'] or 'Tailscale is not ready.')
        hostname = status['hostname']
        config = self.config()
        port = None
        if previous and previous.get('hostname') == hostname and self.owns(config, previous):
            port = int(previous['port'])
        if port is None:
            used = {int(key.rsplit(':', 1)[1]) for key in (config.get('Web') or {}) if key.rsplit(':', 1)[-1].isdigit()}
            used |= {int(key) for key in (config.get('TCP') or {}) if str(key).isdigit()}
            port = next((p for p in PORTS if p not in used), None)
        if port is None:
            raise TailscaleError(f'Tailscale HTTPS ports {PORTS[0]}-{PORTS[-1]} are already in use. Free one and try again.')
        if self._funnel(config, hostname, port):
            raise TailscaleError('This Tailscale port is publicly shared. Turn off Funnel for it before connecting ACC.')
        try:
            self.command('serve', '--bg', '--yes', f'--https={port}', target)
        except TailscaleError as exc:
            raise TailscaleError('Tailscale Serve could not start. Enable HTTPS for this device in Tailscale and check '
                                 'its Serve permissions. ' + str(exc)) from exc
        saved = {'port': port, 'hostname': hostname, 'target': target}
        verified = self.config()
        if not self.owns(verified, saved) or self._funnel(verified, hostname, port):
            raise TailscaleError('The private Tailscale route could not be verified, so remote access stays off.')
        return {**saved, 'host': f'{hostname}:{port}', 'origin': f'https://{hostname}:{port}'}

    def disable(self, saved: dict | None) -> bool:
        """Remove the route only if it is still ours; leave anything else untouched."""
        if not saved or not self.owns(self.config(), saved):
            return False
        self.command('serve', '--yes', f"--https={saved['port']}", 'off')
        return True
