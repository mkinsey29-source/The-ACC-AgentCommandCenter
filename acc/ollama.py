"""Direct Ollama adapter: runs a model-driven workflow step against a local Ollama server, with
no Hermes CLI and no subprocess of its own to supervise -- just an HTTP call. No SDK dependency.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

try:
    from . import worker_prompt
except ImportError:
    # core.py invokes this file by direct path, not via `-m acc.ollama`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def is_reachable(host, timeout=1.5):
    """A live reachability probe, not a CLI-presence check: this driver never spawns a process
    of its own (unlike the Hermes adapter), so whether a local `ollama` binary happens to be
    installed says nothing about whether the configured host actually answers -- it could be a
    remote Ollama instance with no CLI on this machine at all."""
    try:
        with urllib.request.urlopen(host.rstrip('/') + '/api/tags', timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _chat(host, model, prompt, timeout_seconds):
    payload = json.dumps({'model': model, 'stream': False,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(host.rstrip('/') + '/api/chat', data=payload, method='POST',
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise ValueError(f'Could not reach Ollama at {host}: {exc}') from exc
    message = body.get('message') or {}
    content = message.get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Ollama response had no message content.')
    return content


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('ollama-query.txt')).write_text(prompt, encoding='utf-8')
    content = _chat(args.host, args.model, prompt, args.timeout_seconds)
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(content)
    if not isinstance(result, dict):
        raise ValueError('Ollama final response must be a JSON object.')
    output = Path(packet['result_file'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Direct Ollama adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--host', default='http://127.0.0.1:11434')
    parser.add_argument('--model', required=True)
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Ollama connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
