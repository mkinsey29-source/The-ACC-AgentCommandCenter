"""Loopback HTTP controls and authenticated server-sent events."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
from urllib.parse import parse_qs, urlsplit
from .core import Coordinator, Conflict

WEB = Path(__file__).parent / 'web'


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, coordinator, token):
        self.coordinator, self.token = coordinator, token
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Never log authorization, prompt text, or URL credentials.

    def reply(self, code, value, content_type='application/json'):
        raw = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def allowed_host(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        return host in allowed and (not origin or origin in {'http://' + x for x in allowed})

    def authenticate(self):
        return self.allowed_host() and secrets.compare_digest(
            self.headers.get('Authorization', ''), 'Bearer ' + self.server.token)

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(403, {'error': 'Local origin required.'})
        url = urlsplit(self.path)
        if url.path.startswith('/api/'):
            if not self.authenticate():
                return self.reply(401, {'error': 'Connect with the local session token.'})
            if url.path == '/api/state':
                return self.reply(200, self.server.coordinator.snapshot())
            if url.path == '/api/conversation':
                try:
                    after = int(parse_qs(url.query).get('after', ['0'])[0])
                    return self.reply(200, {'messages': self.server.coordinator.conversation.messages(after)})
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
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
                try:
                    while not c.halt.is_set():
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
        self.reply(200, (WEB / name).read_bytes(), mime)

    def do_POST(self):
        if not self.authenticate():
            self.close_connection = True
            return self.reply(403, {'error': 'Local session authorization required.'})
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
            parts = urlsplit(self.path).path.strip('/').split('/')
            c = self.server.coordinator
            if parts == ['api', 'voice', 'save']:
                return self.reply(200, c.voice.save(payload))
            if parts == ['api', 'voice', 'retry']:
                return self.reply(200, c.voice.retry(payload))
            if len(parts) == 3 and parts[:2] == ['api', 'conversation']:
                routes = {'send': c.conversation.append, 'configure': c.conversation.configure,
                          'claim': c.conversation.claim, 'renew': c.conversation.renew,
                          'complete': c.conversation.complete, 'release': c.conversation.release,
                          'retry': lambda _: c.conversation.retry()}
                if parts[2] not in routes:
                    return self.reply(404, {'error': 'Unknown conversation operation.'})
                return self.reply(200, routes[parts[2]](payload))
            if len(parts) == 3 and parts[:2] == ['api', 'github']:
                routes = {'configure': c.github.configure, 'refresh': lambda _: c.github.refresh()}
                if parts[2] not in routes:
                    return self.reply(404, {'error': 'Unknown GitHub operation.'})
                return self.reply(200, routes[parts[2]](payload))
            if parts == ['api', 'tasks']:
                return self.reply(201, c.create(payload))
            if len(parts) != 4 or parts[:2] != ['api', 'tasks']:
                return self.reply(404, {'error': 'Unknown endpoint.'})
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
            }
            if action not in routes:
                return self.reply(404, {'error': 'Unknown action.'})
            return self.reply(200, routes[action]())
        except KeyError:
            self.reply(404, {'error': 'Task not found.'})
        except Conflict as exc:
            self.reply(409, {'error': str(exc)})
        except (ValueError, TypeError, AttributeError) as exc:
            self.close_connection = True
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(500, {'error': 'Operation failed. Inspect local state before retrying.'})


def main():
    parser = argparse.ArgumentParser(description='ACC local command center')
    parser.add_argument('--project', required=True)
    parser.add_argument('--state-dir')
    parser.add_argument('--agents', help='Local JSON adapter configuration')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args()
    project = Path(args.project).resolve()
    suffix = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    state = Path(args.state_dir).resolve() if args.state_dir else Path.home() / '.acc' / suffix
    coordinator = Coordinator(project, state, args.agents)
    token_path = state / 'token'
    if token_path.exists():
        token = token_path.read_text().strip()
    else:
        token = secrets.token_urlsafe(32)
        token_path.write_text(token)
        if os.name != 'nt':
            token_path.chmod(0o600)
    server = Server(('127.0.0.1', args.port), coordinator, token)
    print(f'ACC: http://127.0.0.1:{server.server_port}/#token={token}', flush=True)
    print(f'State: {state}\nUse the token file for the orchestrator bridge. Do not share it.', flush=True)
    if args.open_browser:
        import webbrowser
        threading.Thread(target=webbrowser.open, args=(f'http://127.0.0.1:{server.server_port}/#token={token}',), daemon=True).start()
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        coordinator.close()
        server.server_close()


if __name__ == '__main__':
    main()
