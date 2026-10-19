#!/usr/bin/env python3
"""ACC's single-user chat bridge. Provider credentials stay in the official CLIs.

No arbitrary commands, file access API, tool approvals, or public unauthenticated
listener. This bridge is separate from ACC's existing coordinator/server.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import ssl
import stat
import subprocess
import tempfile
import threading
import time

PROVIDERS = ("claude", "codex")
MAX_BODY = 100_000
MAX_OUTPUT = 2_000_000


class BridgeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def child_environment():
    # Unrelated service credentials never enter provider processes. The official
    # CLI still reads its own saved credentials/keychain; ACC never opens them.
    allowed = {"HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "TEMP", "TMP",
               "LANG", "LC_ALL", "TERM", "SHELL", "SYSTEMROOT", "WINDIR",
               "APPDATA", "LOCALAPPDATA", "USERPROFILE", "XDG_CONFIG_HOME",
               "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
               "CODEX_HOME", "CLAUDE_CONFIG_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    return {k: v for k, v in os.environ.items() if k in allowed}


def stop_process(proc):
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()
    except ProcessLookupError:
        pass


def run_process(argv, prompt=None, *, timeout=120, cancel=None, merge_stderr=False):
    """Bound stdin/stdout, use no shell, kill the process group on cancel/timeout."""
    with tempfile.TemporaryDirectory(prefix="acc-chat-") as cwd:
        proc = subprocess.Popen(argv, cwd=cwd, env=child_environment(),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                start_new_session=os.name == "posix")
        output = bytearray()
        oversized = threading.Event()

        output_lock = threading.Lock()
        def read(stream, keep):
            consumed = 0
            while chunk := stream.read(8192):
                consumed += len(chunk)
                if consumed > MAX_OUTPUT:
                    oversized.set()
                    stop_process(proc)
                    break
                if keep:
                    with output_lock:
                        output.extend(chunk)

        reader = threading.Thread(target=read, args=(proc.stdout, True), daemon=True)
        errors = threading.Thread(target=read, args=(proc.stderr, merge_stderr), daemon=True)
        reader.start()
        errors.start()
        def write():
            try:
                proc.stdin.write((prompt or "").encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if cancel and cancel.is_set():
                    raise BridgeError("Request cancelled.", 409)
                if time.monotonic() >= deadline:
                    raise BridgeError("Provider timed out. Check its terminal before retrying.", 504)
                time.sleep(0.05)
            reader.join(timeout=2)
            errors.join(timeout=2)
            if reader.is_alive() or errors.is_alive():
                raise BridgeError("Provider did not close its output stream.", 502)
            if oversized.is_set():
                raise BridgeError("Provider output exceeded the safety limit.", 502)
            if cancel and cancel.is_set():
                raise BridgeError("Request cancelled.", 409)
            return proc.returncode, output.decode("utf-8", errors="replace")
        finally:
            stop_process(proc)
            proc.wait(timeout=5)
            writer.join(timeout=2)
            reader.join(timeout=2)
            errors.join(timeout=2)
            proc.stdout.close()
            proc.stderr.close()
            if not proc.stdin.closed:
                proc.stdin.close()


def provider_status(provider, executable):
    if not shutil.which(executable):
        return {"id": provider, "state": "not_installed", "billing": "unknown"}
    try:
        command = [executable, "auth", "status", "--json"] if provider == "claude" else [executable, "login", "status"]
        code, text = run_process(command, timeout=12, merge_stderr=provider == "codex")
        if provider == "claude":
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("Invalid status")
            method = data.get("authMethod")
            billing = "subscription" if method == "claude.ai" else "api" if method in {"api_key", "api_key_helper", "third_party"} else "unknown"
            logged = data.get("loggedIn") is True and code == 0
        else:
            billing = "subscription" if "logged in using chatgpt" in text.lower() else "api" if "api key" in text.lower() else "unknown"
            logged = code == 0
        return {"id": provider, "state": "signed_in" if logged else "sign_in_needed", "billing": billing}
    except (OSError, ValueError, BridgeError, subprocess.SubprocessError):
        return {"id": provider, "state": "check_failed", "billing": "unknown"}


def command_for(provider, executable, model):
    if provider == "claude":
        argv = [executable, "-p", "--output-format", "json", "--no-session-persistence",
                "--safe-mode", "--restricted", "--tools", "", "--disallowedTools", "mcp__*",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--setting-sources", "", "--permission-mode", "dontAsk", "--permission-prompts", "none"]
    else:
        argv = [executable, "--ask-for-approval", "never", "exec", "--json", "--ephemeral",
                "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--skip-git-repo-check",
                "-c", 'web_search="disabled"', "-c", 'features.shell_tool=false',
                "-c", 'features.unified_exec=false', "-c", 'features.multi_agent=false',
                "-c", 'features.image_generation=false', "-c", 'features.view_image=false',
                "-c", 'features.plugins=false', "-c", 'features.remote_plugin=false',
                "-c", 'features.skill_search=false', "-c", 'features.skill_mcp_dependency_install=false',
                "-c", 'features.tool_suggest=false', "-c", 'features.skip_host_skill_discovery=true',
                "-c", 'features.shell_snapshot=false', "-c", 'project_doc_max_bytes=0']
    if model:
        argv += ["--model", model]
    if provider == "codex":
        argv.append("-")
    return argv


def parse_reply(provider, code, text):
    # Never forward raw subprocess errors: they can include account data/tokens.
    if code:
        raise BridgeError("Provider failed. Check sign-in, model access or quota in its own terminal.", 502)
    try:
        if provider == "claude":
            data = json.loads(text)
            if data.get("is_error") or data.get("subtype") != "success":
                raise ValueError("unsuccessful result")
            reply = data.get("result")
        else:
            events = [json.loads(line) for line in text.splitlines() if line.strip()]
            if not any(e.get("type") == "turn.completed" for e in events) or any(e.get("type") in {"turn.failed", "error"} for e in events):
                raise ValueError("incomplete turn")
            replies = [e["item"].get("text", "") for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
            reply = "\n\n".join(replies)
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 100_000:
            raise ValueError("empty or oversized reply")
        return reply
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BridgeError("Provider did not return a complete response. Check its terminal.", 502) from None


def validate_request(data):
    if not isinstance(data, dict) or set(data) - {"id", "provider", "model", "messages", "allow_api"}:
        raise BridgeError("Unsupported request.")
    if not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", data["id"]):
        raise BridgeError("A unique request ID is required.")
    if data.get("provider") not in PROVIDERS:
        raise BridgeError("Unsupported provider.")
    model = data.get("model", "")
    if not isinstance(model, str) or len(model) > 120 or model.startswith("-") or any(ord(c) < 33 or ord(c) > 126 for c in model):
        raise BridgeError("Invalid model name.")
    if not isinstance(data.get("allow_api", False), bool):
        raise BridgeError("Invalid billing consent.")
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 40:
        raise BridgeError("Send between 1 and 40 messages.")
    total = 0
    for i, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "text"} or message["role"] != ("user" if i % 2 == 0 else "assistant"):
            raise BridgeError("Invalid conversation order. Start a new conversation.")
        if not isinstance(message["text"], str) or not message["text"].strip():
            raise BridgeError("Empty message.")
        total += len(message["text"])
    if messages[-1]["role"] != "user" or total > 50_000:
        raise BridgeError("Conversation is too long. Start a new conversation.")
    return data


class Runtime:
    def __init__(self, token, executables=None):
        self.token = token
        self.executables = executables or {p: p for p in PROVIDERS}
        self.lock = threading.RLock()
        self.jobs = {}
        self.closed = False
        self.boot = secrets.token_hex(16)
        self.nonces = {}

    def status(self):
        return {"protocol": 2, "runtime": "acc-mobile", "providers": [provider_status(p, self.executables[p]) for p in PROVIDERS]}

    def mac(self, text):
        return hmac.new(self.token.encode(), text.encode(), hashlib.sha256).hexdigest()

    def authenticate(self, headers, method, path, body):
        nonce, stamp, boot = (headers.get(k, '') for k in ('X-ACC-Nonce', 'X-ACC-Time', 'X-ACC-Boot'))
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', nonce) or not re.fullmatch(r'[0-9]{10}', stamp) or boot != self.boot:
            raise BridgeError('Pair this device with the runtime.', 401)
        if abs(time.time() - int(stamp)) > 30:
            raise BridgeError('Check the clocks on both devices.', 401)
        signed = '\n'.join(('acc-request-v2', boot, nonce, stamp, method, path, hashlib.sha256(body).hexdigest()))
        if not hmac.compare_digest(headers.get('X-ACC-Signature', '').encode(), self.mac(signed).encode()):
            raise BridgeError('Pair this device with the runtime.', 401)
        with self.lock:
            now = time.time()
            self.nonces = {n: t for n, t in self.nonces.items() if now - t <= 60}
            if nonce in self.nonces or len(self.nonces) >= 1000:
                raise BridgeError('Replay or request limit reached.', 409)
            self.nonces[nonce] = now

    def submit(self, data):
        data = validate_request(data)
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.closed:
                raise BridgeError("Runtime is shutting down.", 503)
            if data["id"] in self.jobs:
                job = self.jobs[data["id"]]
                if job["fingerprint"] != fingerprint:
                    raise BridgeError("Request ID already used for different content.", 409)
                return self.snapshot(data["id"])
            if any(j["state"] == "running" for j in self.jobs.values()):
                raise BridgeError("Another request is running. Wait or cancel it.", 409)
            # Memory-only results expire; never replay a request after host restart.
            now = time.monotonic()
            self.jobs = {k: j for k, j in self.jobs.items() if now - j["created"] < 1800}
            if len(self.jobs) >= 100:
                del self.jobs[next(iter(self.jobs))]
            job = {"id": data["id"], "provider": data["provider"], "state": "running", "fingerprint": fingerprint,
                   "created": now, "cancel": threading.Event(), "reply": "", "error": ""}
            self.jobs[data["id"]] = job
            thread = threading.Thread(target=self._run, args=(job, data), daemon=True)
            job["thread"] = thread
            thread.start()
            return self.snapshot(data["id"])

    def _run(self, job, data):
        try:
            if job["cancel"].is_set():
                raise BridgeError("Request cancelled.", 409)
            p = data["provider"]
            status = provider_status(p, self.executables[p])
            if status["state"] != "signed_in":
                raise BridgeError("Sign in through the provider's own terminal first.", 401)
            if status["billing"] != "subscription" and not data.get("allow_api", False):
                raise BridgeError("Billing is API-based or unconfirmed. Explicitly enable API/unknown billing to use it.", 403)
            prompt = "Continue this conversation. Reply to the last user message. This is a text conversation; do not execute tools.\n" + json.dumps(data["messages"], ensure_ascii=False)
            if job["cancel"].is_set():
                raise BridgeError("Request cancelled.", 409)
            code, text = run_process(command_for(p, self.executables[p], data.get("model", "")), prompt, cancel=job["cancel"])
            reply = parse_reply(p, code, text)
            with self.lock:
                if not job["cancel"].is_set():
                    job.update(state="completed", reply=reply)
                else:
                    job.update(state="cancelled")
        except BridgeError as e:
            with self.lock:
                job.update(state="cancelled" if job["cancel"].is_set() else "failed", error=str(e))
        except (OSError, subprocess.SubprocessError):
            with self.lock:
                job.update(state="failed", error="Provider process could not run. Check the runtime installation.")

    def snapshot(self, jid):
        with self.lock:
            if jid not in self.jobs:
                raise BridgeError("Request not found. Runtime may have restarted; do not automatically resend it.", 404)
            job = self.jobs[jid]
            return {key: job[key] for key in ("id", "provider", "state", "reply", "error")}

    def cancel(self, jid):
        with self.lock:
            if jid not in self.jobs:
                raise BridgeError("Request not found.", 404)
            self.jobs[jid]["cancel"].set()
            return self.snapshot(jid)

    def close(self):
        with self.lock:
            self.closed = True
            jobs = list(self.jobs.values())
            for job in jobs:
                job["cancel"].set()
        for job in jobs:
            job["thread"].join(timeout=20)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, runtime):
        self.runtime = runtime
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log Authorization, URLs, prompts, account details or replies.

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def respond(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        nonce = self.headers.get('X-ACC-Nonce', '')
        if re.fullmatch(r'[A-Za-z0-9_-]{16,80}', nonce):
            signed = '\n'.join(('acc-response-v2', nonce, str(status), hashlib.sha256(raw).hexdigest()))
            self.send_header('X-ACC-Proof', self.server.runtime.mac(signed))
        self.end_headers()
        self.wfile.write(raw)

    def dispatch(self, method):
        try:
            if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                raise BridgeError("Browser-origin requests are refused.", 403)
            if self.headers.get("Transfer-Encoding"):
                raise BridgeError("Chunked requests are not accepted.")
            path = self.path
            if "?" in path or "#" in path:
                raise BridgeError("Query strings are not accepted.")
            body = b''
            if method == 'POST':
                if self.headers.get('Content-Type') != 'application/json':
                    raise BridgeError('JSON content is required.', 415)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise BridgeError('Invalid body size.') from None
                if not 0 < length <= MAX_BODY:
                    raise BridgeError('Request exceeds the size limit.', 413)
                body = self.rfile.read(length)
            if method == 'POST' and path == '/v1/handshake':
                data = json.loads(body)
                nonce = data.get('nonce') if isinstance(data, dict) else None
                if not isinstance(nonce, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', nonce):
                    raise BridgeError('Invalid handshake.')
                boot = self.server.runtime.boot
                return self.respond(200, {'protocol': 2, 'nonce': nonce, 'boot': boot,
                    'proof': self.server.runtime.mac('acc-server-v2\n' + nonce + '\n' + boot)})
            self.server.runtime.authenticate(self.headers, method, path, body)
            match = re.fullmatch(r"/v1/jobs/([a-zA-Z0-9_-]{16,80})(/cancel)?", path)
            if method == "GET" and path == "/v1/status":
                return self.respond(200, self.server.runtime.status())
            if method == "GET" and match and not match[2]:
                return self.respond(200, self.server.runtime.snapshot(match[1]))
            if method == "POST" and match and match[2]:
                return self.respond(200, self.server.runtime.cancel(match[1]))
            if method == "POST" and path == "/v1/chat":
                data = json.loads(body)
                return self.respond(202, self.server.runtime.submit(data))
            raise BridgeError("Route not found.", 404)
        except BridgeError as e:
            self.respond(e.status, {"error": str(e)})
        except (ValueError, UnicodeError):
            self.respond(400, {"error": "Invalid request."})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


def token_at(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or (os.name == "posix" and (parent.st_uid != os.getuid() or parent.st_mode & 0o077)):
        raise ValueError("Pairing directory must be a private, owner-only directory.")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                raise ValueError("Pairing file must be a private, owner-only regular file.")
            token = source.read(130).strip()
    else:
        token = secrets.token_urlsafe(32)
        with os.fdopen(fd, "w") as output:
            output.write(token + "\n")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
        raise ValueError("Invalid pairing file.")
    return token


def main():
    parser = argparse.ArgumentParser(description="ACC mobile chat runtime (personal/local testing). Provider sign-in happens in official CLIs.")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".config/acc-mobile/pairing-token")
    parser.add_argument("--show-pairing", action="store_true", help="Show the ACC pairing secret in your own terminal; never share it in chat.")
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    try:
        bind = ipaddress.ip_address(args.bind)
        if bind.version != 4 or bind.is_unspecified:
            parser.error("Choose a concrete IPv4 address; default is loopback.")
        if not bind.is_loopback and not (args.cert and args.key):
            parser.error("Non-loopback listeners require a TLS certificate and key.")
        token = token_at(args.token_file)
        runtime = Runtime(token)
        server = Server((str(bind), args.port), runtime)
        if args.cert and args.key:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(args.cert), str(args.key))
            server.socket = context.wrap_socket(server.socket, server_side=True)
        print(f"ACC runtime listening on {'https' if args.cert else 'http'}://{bind}:{server.server_port}", flush=True)
        print("Sign in separately with: claude auth login   and   codex login", flush=True)
        if args.show_pairing:
            print("ACC pairing secret (enter only in the app Connections screen): " + token, flush=True)
        previous_term = signal.getsignal(signal.SIGTERM)
        def shutdown(_signum, _frame):
            # shutdown() waits for serve_forever; never call it on that thread.
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            runtime.close()
            signal.signal(signal.SIGTERM, previous_term)
    except (ValueError, OSError):
        parser.exit(1, "Runtime could not start. Check private token-file permissions, address, port and TLS files.\n")


if __name__ == "__main__":
    main()
