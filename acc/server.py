"""Loopback HTTP controls and authenticated server-sent events."""
import argparse
from http.cookies import CookieError, SimpleCookie
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from .core import Coordinator, Conflict
from .gates import ApprovalError, Approvals, classify
from .knowledge import OwnerApprovalRequired
from .launch import LaunchCodes, LaunchError
from .pairing import Pairing, PairingError
from .reach import MIN_TOKEN_LENGTH, MODES, Reach, address_class, ReachError, check as check_reach, format_findings, resolve as resolve_reach
from .tailscale import Tailscale, TailscaleError

WEB = Path(__file__).parent / 'web'
DEVICE_COOKIE = 'acc_device'
LOOPBACK_CLIENTS = ('127.0.0.1', '::1')
OWNER_ONLY = 'Only the owner at this computer can manage remote access.'


def _same(received, expected):
    """Constant-time comparison that cannot raise on a non-ASCII header."""
    return hmac.compare_digest(received.encode('utf-8', 'replace'), expected.encode('utf-8', 'replace'))


class Server(ThreadingHTTPServer):
    """One listener. ``role`` is 'both' (loopback: this computer and proxied remote hosts), 'public' (a non-loopback
    bind: remote hosts only, never the owner) or 'owner' (loopback only, this computer only). A non-loopback bind is
    always paired with an owner listener that shares its state, so the owner never signs in over the network."""
    daemon_threads = True

    def __init__(self, address, coordinator, token, reach=None, pairing=None, agent_token=None, role='both', shared=None):
        self.reach = reach or Reach()
        self.role = role
        self.pairing, self.route = pairing, None
        if role != 'owner':
            self.reach.validate(token, address[0], pairing is not None)  # refuse an unsafe combination before binding
        if agent_token is not None and (not isinstance(agent_token, str) or len(agent_token) < MIN_TOKEN_LENGTH
                                        or agent_token == token):
            raise ValueError(f'The agent token must be a different secret of at least {MIN_TOKEN_LENGTH} characters.')
        self.coordinator, self.token, self.agent_token = coordinator, token, agent_token
        if shared is not None:  # the owner listener answers for the same approvals, codes and pairings
            self.approvals, self.launch, self.pairing = shared.approvals, shared.launch, shared.pairing
        else:
            self.approvals = Approvals(coordinator, lambda parts, payload: run_mutation(parts, payload, coordinator))
            self.launch = LaunchCodes(token)  # one-use codes that deliver the memory-only owner token to the owner's browser
        super().__init__(address, Handler)

    def remote_origin(self):
        if self.route:
            return self.route['origin']
        hosts = sorted(self.reach.remote_hosts())
        return ('https://' if self.reach.secure_cookie else 'http://') + hosts[0] if hosts else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Never log authorization, prompt text, or URL credentials.

    def reply(self, code, value, content_type='application/json', headers=()):
        raw = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(code)
        for name, header in headers:
            self.send_header(name, header)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def host_class(self):
        """'local' for this computer's loopback names, 'remote' for an accepted remote host, else None.

        The class comes from the Host header, not the client address: Tailscale Serve connects from
        loopback on behalf of a remote device. The local control token is honoured only for 'local'
        requests, and a device credential only for 'remote' ones.
        """
        host = self.headers.get('Host', '').strip().lower()
        kind = self.server.reach.classify(host, self.server.server_port)
        role = self.server.role
        if role == 'public' and host in self.server.reach.remote_hosts():
            kind = 'remote'  # on a network-facing listener an accepted name is a device even if it looks like loopback
        if (role == 'public' and kind == 'local') or (role == 'owner' and kind != 'local'):
            return None  # a network-facing listener never answers as the owner; the owner listener never answers remote hosts
        if kind == 'local' and self.client_address[0] not in LOOPBACK_CLIENTS:
            return None
        origin = self.headers.get('Origin')
        if kind == 'local':
            allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        else:
            allowed = {'http://' + host, 'https://' + host}
        return kind if kind and (not origin or origin in allowed) else None

    def device_credential(self):
        try:
            jar = SimpleCookie(self.headers.get('Cookie', ''))
        except CookieError:
            return None
        morsel = jar.get(DEVICE_COOKIE)
        return morsel.value if morsel else None

    def authenticate(self):
        self.principal = None
        kind = self.host_class()
        if kind == 'local':
            header = self.headers.get('Authorization', '')
            if _same(header, 'Bearer ' + self.server.token):
                self.principal = ('local', None)  # the owner: never gated
            elif self.server.agent_token and _same(header, 'Bearer ' + self.server.agent_token):
                self.principal = ('agent', None)  # the remote-driven orchestrator: gated
        elif kind == 'remote' and self.server.pairing is not None:
            device = self.server.pairing.authenticate(self.device_credential())
            if device:
                self.principal = ('device', device)
        return self.principal is not None

    def owner(self):
        return self.principal is not None and self.principal[0] == 'local'

    def principal_identity(self):
        kind, device = self.principal
        if kind == 'agent':
            return 'agent', 'the orchestrator'
        return 'device:' + device['id'], f"device “{device['name']}”"

    def do_GET(self):
        if self.host_class() is None:
            return self.reply(403, {'error': 'Local origin required.'})
        url = urlsplit(self.path)
        if url.path.startswith('/api/'):
            if not self.authenticate():
                return self.reply(401, {'error': 'Connect with the local session token.' if self.host_class() == 'local'
                                        else 'This device is not paired. Create a pairing link at the computer.'})
            if url.path == '/api/remote':
                if not self.owner():
                    return self.reply(403, {'error': OWNER_ONLY})
                server = self.server
                return self.reply(200, {
                    'mode': server.reach.mode, 'enabled': server.reach.remote, 'origin': server.remote_origin(),
                    'hosts': sorted(server.reach.remote_hosts()),
                    'devices': server.pairing.devices() if server.pairing else [],
                    'pending': server.pairing.pending() if server.pairing else []})
            if url.path == '/api/state':
                return self.reply(200, {**self.server.coordinator.snapshot(), 'attention': self.server.approvals.pending()})
            if url.path.startswith('/api/approvals/') and url.path.count('/') == 3:
                found = self.server.approvals.get(url.path.rsplit('/', 1)[1])
                return self.reply(200, found) if found else self.reply(404, {'error': 'Unknown approval request.'})
            if url.path == '/api/conversation':
                try:
                    query = parse_qs(url.query)
                    after = int(query.get('after', ['0'])[0])
                    session_id = query.get('session_id', [None])[0]
                    return self.reply(200, {'messages': self.server.coordinator.conversation.messages(
                        after, session_id=session_id)})
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
            if url.path == '/api/archive':
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                return self.reply(200, self.server.coordinator.archive.search(query))
            if url.path == '/api/integrations':
                return self.reply(200, self.server.coordinator.integrations.snapshot())
            if url.path == '/api/memory':
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                return self.reply(200, self.server.coordinator.integrations.search_memory(query))
            if url.path == '/api/knowledge':
                return self.reply(200, self.server.coordinator.knowledge.state())
            if url.path == '/api/events':
                try:
                    cursor = int(parse_qs(url.query).get('after', ['0'])[0])
                    if cursor < 0:
                        raise ValueError()
                except ValueError:
                    return self.reply(400, {'error': 'Invalid event cursor.'})
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                c = self.server.coordinator
                device_id = self.principal[1]['id'] if self.principal[0] == 'device' else None
                try:
                    while not c.halt.is_set():
                        if device_id and not self.server.pairing.is_active(device_id):
                            break  # a revoked device's stream ends at once
                        with c.lock:
                            events = c.store.events(cursor)
                        for event in events:
                            cursor = event['seq']
                            self.wfile.write(f'id: {cursor}\ndata: {json.dumps(event)}\n\n'.encode())
                        if not events:
                            self.wfile.write(b': connected\n\n')
                        self.wfile.flush()
                        c.halt.wait(.2 if events else 1)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            return self.reply(404, {'error': 'Unknown endpoint.'})
        names = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if url.path not in names:
            return self.reply(404, {'error': 'Not found.'})
        name, mime = names[url.path]
        body = (WEB / name).read_bytes()
        if name == 'index.html':  # tell the page which listener it came from: a published loopback name must not look like the owner
            body = body.replace(b'<head>', b'<head><meta name="acc-role" content="' + (b'device' if self.server.role == 'public' else b'owner') + b'">', 1)
        self.reply(200, body, mime)

    def do_POST(self):
        parts = urlsplit(self.path).path.strip('/').split('/')
        public = ((parts in (['api', 'pairing', 'claim'], ['api', 'pairing', 'poll'])
                   and self.host_class() == 'remote' and self.server.pairing is not None)
                  or (parts == ['api', 'launch'] and self.host_class() == 'local'))
        self.principal = None
        if parts[:2] == ['api', 'launch'] and self.host_class() != 'local':
            # The owner credential is handed out on this computer only, whatever else the caller is authorized for.
            self.close_connection = True
            return self.reply(403, {'error': 'Owner launch links work only on this computer.'})
        if not public and not self.authenticate():
            self.close_connection = True
            return self.reply(403, {'error': 'Local session authorization required.' if self.host_class() == 'local'
                                    else 'This device is not paired.'})
        answers_approval = len(parts) == 4 and parts[:2] == ['api', 'approvals'] and parts[3] in ('approve', 'deny')
        owner_route = parts[:2] in (['api', 'pairing'], ['api', 'remote']) and not public
        if owner_route or answers_approval or parts == ['api', 'launch', 'new']:
            if not self.owner():
                self.close_connection = True
                return self.reply(403, {'error': OWNER_ONLY})
            if owner_route and self.server.pairing is None:
                return self.reply(404, {'error': 'Remote access is off. Start ACC with --reach to enable it.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            limit = 15 * 1024 * 1024 if self.path == '/api/voice/save' else 100000
            if not 0 < length <= limit:
                raise ValueError('Request size must be between 1 and 100,000 bytes.')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON content type required.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('JSON object required.')
            c = self.server.coordinator
            pairing = self.server.pairing
            if parts == ['api', 'launch']:
                return self.reply(200, {'token': self.server.launch.redeem(payload.get('code'))})
            if parts == ['api', 'launch', 'new']:
                minted = self.server.launch.mint()
                return self.reply(200, {'url': f'http://127.0.0.1:{self.server.server_port}/#launch={minted["code"]}',
                                        'expires': minted['expires']})
            if parts == ['api', 'pairing', 'claim']:
                return self.reply(200, pairing.claim(payload.get('secret'), payload.get('name')))
            if parts == ['api', 'pairing', 'poll']:
                result = pairing.poll(payload.get('request_id'), payload.get('claim'))
                headers = ()
                if result['status'] == 'approved':
                    cookie = (f"{DEVICE_COOKIE}={result.pop('credential')}; HttpOnly; SameSite=Strict; Path=/; "
                              f"Max-Age={result.pop('max_age')}" + ('; Secure' if self.server.reach.secure_cookie else ''))
                    headers = (('Set-Cookie', cookie),)
                    result.pop('device_id')
                return self.reply(200, result, headers=headers)
            if parts == ['api', 'pairing', 'begin']:
                begun = pairing.begin()
                origin = self.server.remote_origin()
                return self.reply(200, {**begun, 'origin': origin,
                                        'url': f"{origin}/#pair={begun['token']}" if origin else None})
            if parts == ['api', 'pairing', 'approve']:
                return self.reply(200, pairing.approve(payload.get('request_id')))
            if parts == ['api', 'pairing', 'deny']:
                pairing.deny(payload.get('request_id'))
                return self.reply(200, {'denied': True})
            if parts == ['api', 'pairing', 'revoke']:
                return self.reply(200, {'revoked': pairing.revoke(payload.get('device_id'))})
            if answers_approval:
                return self.reply(200, self.server.approvals.resolve(parts[2], parts[3] == 'approve'))
            if self.principal is not None and self.principal[0] in ('device', 'agent'):
                gate = classify(c, parts, payload)
                if gate:
                    key, label = self.principal_identity()
                    asked = self.server.approvals.request(key, label, gate, parts, payload)
                    return self.reply(202, {'status': 'approval_required', 'approval_id': asked['id'], 'approval': asked,
                                            'message': 'The owner must approve this at the computer, in Attention. '
                                                       f"Check GET /api/approvals/{asked['id']}."})
            gated = self.principal is not None and self.principal[0] in ('device', 'agent')
            status, body, close = run_mutation(parts, payload, c, 'gated' if gated else 'owner')
            if close:
                self.close_connection = True
            return self.reply(status, body)
        except (PairingError, ApprovalError, LaunchError) as exc:
            self.reply(exc.status, {'error': str(exc)})
        except KeyError:
            self.reply(404, {'error': 'Task not found.'})
        except Conflict as exc:
            self.reply(409, {'error': str(exc)})
        except (ValueError, TypeError, AttributeError) as exc:
            self.close_connection = True
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(500, {'error': 'Operation failed. Inspect local state before retrying.'})


def mutate(c, parts, payload, authority='owner'):
    """Run one mutating API call and return (status, body). Shared by direct calls and approved requests.

    ``authority`` is 'owner' for the owner and for a request the owner approved, 'gated' for an agent credential or a
    paired device. The knowledge writers re-check it themselves, so a path the URL classifier misses still cannot
    establish knowledge for a gated caller."""
    if parts == ['api', 'voice', 'save']:
        return (200, c.voice.save(payload))
    if parts == ['api', 'voice', 'retry']:
        return (200, c.voice.retry(payload))
    if len(parts) == 3 and parts[:2] == ['api', 'conversation']:
        routes = {'send': c.conversation.append, 'configure': c.conversation.configure,
                  'claim': c.conversation.claim, 'renew': c.conversation.renew,
                  'complete': c.conversation.complete, 'release': c.conversation.release,
                  'retry': lambda _: c.conversation.retry()}
        if parts[2] not in routes:
            return (404, {'error': 'Unknown conversation operation.'})
        return (200, routes[parts[2]](payload))
    if parts == ['api', 'orchestrators', 'select']:
        return (200, c.orchestrators.select(payload))
    if len(parts) == 3 and parts[:2] == ['api', 'github']:
        routes = {'configure': c.github.configure, 'refresh': lambda _: c.github.refresh()}
        if parts[2] not in routes:
            return (404, {'error': 'Unknown GitHub operation.'})
        return (200, routes[parts[2]](payload))
    if parts == ['api', 'project', 'mode']:
        return (200, c.controls.set_mode(payload))
    if parts == ['api', 'archive', 'export']:
        return (200, c.archive.export(payload))
    if parts == ['api', 'integrations', 'jobs']:
        return (201, c.integrations.submit(payload))
    if parts == ['api', 'integrations', 'claim']:
        return (200, c.integrations.claim(payload))
    if len(parts) == 5 and parts[:3] == ['api', 'integrations', 'jobs']:
        job_id, action = parts[3:]
        routes = {'renew': lambda: c.integrations.renew(job_id, payload),
                  'finish': lambda: c.integrations.finish(job_id, payload),
                  'cancel': lambda: c.integrations.cancel(job_id),
                  'request-cancel': lambda: c.integrations.request_cancel(job_id),
                  'retry': lambda: c.integrations.retry(job_id)}
        if action not in routes:
            return (404, {'error': 'Unknown integration job operation.'})
        return (200, routes[action]())
    if parts == ['api', 'memory', 'propose']:
        return (201, c.integrations.propose_memory(payload))
    if len(parts) == 4 and parts[:2] == ['api', 'memory'] and parts[3] == 'review':
        if authority == 'gated' and payload.get('status') == 'active':
            raise OwnerApprovalRequired('Accepting shared memory needs the owner\'s approval.')
        return (200, c.integrations.review_memory(parts[2], payload))
    if len(parts) == 3 and parts[:2] == ['api', 'knowledge']:
        routes = {
            'search': c.knowledge.search, 'checkout': c.knowledge.checkout,
            'checkin': lambda p: c.knowledge.checkin(p, authority),
            'note': lambda p: c.knowledge.create_note(p, authority),
            'review': c.knowledge.review,
            'transition': lambda p: c.knowledge.transition(p, authority),
            'rebuttal': lambda p: c.knowledge.rebuttal(p, authority),
        }
        if parts[2] not in routes:
            return (404, {'error': 'Unknown knowledge operation.'})
        return (200, routes[parts[2]](payload))
    if parts == ['api', 'tasks']:
        return (201, c.create(payload))
    if len(parts) != 4 or parts[:2] != ['api', 'tasks']:
        return (404, {'error': 'Unknown endpoint.'})
    task, action = parts[2:]
    routes = {
        'switch': lambda: c.controls.switch(task, payload),
        'schedule': lambda: c.controls.schedule(task, payload),
        'publish-preview': lambda: c.github.preview(task),
        'publish': lambda: c.github.publish(task, payload),
        'start': lambda: c.start(task), 'stop': lambda: c.stop(task),
        'assign': lambda: c.assign(task, payload.get('agent')),
        'instructions': lambda: c.revise(task, payload.get('instruction')),
        'report': lambda: c.report(task, payload), 'review': lambda: c.review(task, payload),
        'workflow': lambda: c.workflows.configure(task, payload),
        'recover': lambda: c.recover(task) if payload.get('process_tree_inspected') is True else
                   (_ for _ in ()).throw(ValueError('Explicit process-tree inspection acknowledgement required.')),
        'recovery-handoff': lambda: c.controls.recovery_handoff(task, payload),
    }
    if action not in routes:
        return (404, {'error': 'Unknown action.'})
    return (200, routes[action]())


def run_mutation(parts, payload, coordinator=None, authority='owner'):
    """mutate() with errors mapped to HTTP statuses: returns (status, body, close_connection)."""
    try:
        status, body = mutate(coordinator, parts, payload, authority)
        return status, body, False
    except KeyError:
        return 404, {'error': 'Task not found.'}, False
    except Conflict as exc:
        return 409, {'error': str(exc)}, False
    except (ValueError, TypeError, AttributeError) as exc:
        return 400, {'error': str(exc)}, True
    except Exception:
        return 500, {'error': 'Operation failed. Inspect local state before retrying.'}, False


def _write_secret(path, value):
    """Create a private file without a moment of default permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, 'w') as out:
        out.write(value)
    if os.name != 'nt':
        os.chmod(path, 0o600)


ENDPOINT_FILE = 'endpoint.json'


def _write_endpoint(path, port):
    """Tell the local orchestrator bridge where the loopback listener is. Holds no credential, only a port."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'url': f'http://127.0.0.1:{port}', 'pid': os.getpid()}))
    os.replace(temporary, path)


def _refuse(message):
    print(f'ACC refused to start: {message}', file=sys.stderr, flush=True)
    sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description='ACC local command center')
    parser.add_argument('--project', required=True)
    parser.add_argument('--state-dir')
    parser.add_argument('--agents', help='Local JSON adapter configuration')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--reach', choices=MODES, default='local',
                        help='Where ACC is reachable from: local (default, this computer only), tailscale-serve, '
                             'private, docker or custom. Every remote mode requires device pairing.')
    parser.add_argument('--host', help='Address to bind in private and custom modes')
    parser.add_argument('--allow-host', action='append', default=[],
                        help='A Host header value ACC accepts, exactly as the browser sends it (repeatable)')
    parser.add_argument('--public-ack', action='store_true',
                        help='Custom mode only: acknowledge the address may be reachable from the internet')
    parser.add_argument('--behind-https', action='store_true',
                        help='A TLS front door serves the page, so the device cookie is marked Secure')
    parser.add_argument('--agent-token-file', help='Where the orchestrator credential lives (default: agent-token in the state '
                        'folder). Put it outside the state folder, readable only by the orchestrator\'s account.')
    parser.add_argument('--owner-port', type=int, default=0,
                        help='Loopback port for the owner and the orchestrator bridge when ACC listens on a network address '
                             '(default: any free port, announced in the endpoint file). Ignored when ACC listens on loopback.')
    parser.add_argument('--reach-check', action='store_true',
                        help='Validate the reachability settings and read-only readiness (for Tailscale: sign-in, a free port, '
                             'no public sharing), then exit with status 2 if they are unusable. It changes nothing, so it cannot '
                             'prove startup will succeed: permission to change Tailscale Serve, HTTPS certificates, binding the '
                             'port and changes made by other tools are only found when ACC starts.')
    args = parser.parse_args()
    project = Path(args.project).resolve()
    suffix = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    state = Path(args.state_dir).resolve() if args.state_dir else Path.home() / '.acc' / suffix
    token = secrets.token_urlsafe(32)  # the owner token: memory only, new at every start, never written anywhere
    # The orchestrator bridge's credential: its risky actions wait for approval.
    agent_path = Path(args.agent_token_file).expanduser().resolve() if args.agent_token_file else state / 'agent-token'
    new_agent = not agent_path.exists()
    agent_token = secrets.token_urlsafe(32) if new_agent else agent_path.read_text().strip()
    tailscale = Tailscale()
    options = dict(port=args.port, host=args.host, allow_hosts=args.allow_host, public_ack=args.public_ack,
                   behind_https=args.behind_https, route=tailscale if args.reach == 'tailscale-serve' else None)
    if args.reach_check:
        try:
            saved_route = json.loads((state / 'reach.json').read_text()).get('route')
        except (OSError, ValueError, AttributeError):
            saved_route = None
        findings, reach = check_reach(args.reach, token=token, previous_route=saved_route, **options)
        print(format_findings(findings))
        sys.exit(0 if reach else 2)
    try:  # refuse before any side effect: no state directory, token file or database is created
        reach = resolve_reach(args.reach, token=token, **options)
    except ReachError as exc:
        _refuse(exc)
    coordinator = Coordinator(project, state, args.agents)
    legacy = state / 'token'
    if legacy.exists():  # an older ACC kept the owner token here; it no longer works, so do not leave the secret around
        try:
            legacy.unlink()
            print('Removed the old owner token file. The owner token is now memory-only; point any orchestrator '
                  'bridge or tool at agent-token instead.', flush=True)
        except OSError:
            print(f'Warning: could not remove the old owner token file {legacy}; it is no longer valid, delete it.',
                  file=sys.stderr, flush=True)
    server = owner_server = route = None
    try:
        if new_agent:
            _write_secret(agent_path, agent_token)
        pairing = Pairing(state / 'pairing.json') if reach.remote else None
        server = Server((reach.bind_host, args.port), coordinator, token, reach=reach, pairing=pairing,
                        agent_token=agent_token, role='public' if address_class(reach.bind_host) != 'loopback' else 'both')
        owner_server = server
        if server.role == 'public':  # the owner's own door: this computer only, same state, never reachable from the network
            owner_server = Server(('127.0.0.1', args.owner_port), coordinator, token, reach=reach, pairing=pairing,
                                  agent_token=agent_token, role='owner', shared=server)
        if reach.mode == 'tailscale-serve':
            record = state / 'reach.json'
            try:
                previous = json.loads(record.read_text()).get('route')
            except (OSError, ValueError, AttributeError):
                previous = None
            route = tailscale.enable(f'http://127.0.0.1:{server.server_port}', previous=previous)
            record.write_text(json.dumps({'route': {k: route[k] for k in ('port', 'hostname', 'target')}}))
            reach.set_remote_hosts([route['host']])
            server.route = route
    except (ReachError, PairingError, TailscaleError, OSError, ValueError) as exc:
        if owner_server is not None and owner_server is not server:
            owner_server.server_close()
        if server is not None:
            server.server_close()
        coordinator.close()
        _refuse(exc)
    owner_port = owner_server.server_port
    endpoint = state / ENDPOINT_FILE
    _write_endpoint(endpoint, owner_port)
    print(f"ACC: http://127.0.0.1:{owner_port}/#launch={server.launch.mint()['code']}", flush=True)
    print('That link works once, for 10 minutes; use New window in ACC for another. The owner token is never saved.', flush=True)
    print(f'State: {state}\nPoint the orchestrator bridge at {agent_path}: its risky actions wait for your approval. '
          'Do not share it.', flush=True)
    if reach.remote:
        print(f"Remote: {server.remote_origin() or 'accepted hosts: ' + ', '.join(sorted(reach.remote_hosts()))}\n"
              'Pair a device: at this computer open ACC, choose Remote access, then Create pairing link.', flush=True)
    if args.open_browser:
        import webbrowser
        # A separate one-use code: the URL is a process argument, so it must carry nothing that stays valid.
        url = f"http://127.0.0.1:{owner_port}/#launch={server.launch.mint()['code']}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    if owner_server is not server:
        threading.Thread(target=owner_server.serve_forever, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if owner_server is not server:
            owner_server.shutdown()
            owner_server.server_close()
        try:
            endpoint.unlink()
        except OSError:
            pass
        if route:
            try:
                tailscale.disable(route)
                (state / 'reach.json').write_text(json.dumps({'route': None}))
            except (TailscaleError, OSError, subprocess.SubprocessError):
                pass  # a stale route is harmless; it is verified before it is reused
        coordinator.close()
        server.server_close()


if __name__ == '__main__':
    main()
