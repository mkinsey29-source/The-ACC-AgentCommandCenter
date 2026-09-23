"""Direct LM Studio adapter: runs a model-driven workflow step against a local LM Studio server's
OpenAI-compatible API, with no subprocess of its own to supervise -- the same shape as ollama.py.
No SDK dependency.

LM Studio's local server (Developer tab -> Start Server) speaks the same OpenAI Chat Completions
wire format used by most other local-inference runtimes (llama.cpp's own `server`, vLLM's
OpenAI-compatible server, text-generation-webui, koboldcpp, ...). This driver targets that shared,
long-stable convention via a configurable host rather than anything LM-Studio-specific, so the
same code works against any of them with the right --host -- unlike this year's fast-moving
agentic CLIs, the OpenAI Chat Completions shape has been a stable convention for years.

Default host `http://localhost:1234`, LM Studio's own long-standing default port. Endpoints:
GET /v1/models (reachability probe, mirroring ollama.py's own live-probe-not-CLI-check reasoning)
and POST /v1/chat/completions (`{"model", "messages"}` request, `choices[0].message.content`
response). No API key is required by a local server, but a placeholder Bearer token is still sent
since some OpenAI-compatible servers reject a request with no Authorization header at all.
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
    # core.py invokes this file by direct path, not via `-m acc.lmstudio`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def is_reachable(host, timeout=1.5):
    """A live reachability probe, not a CLI-presence check: this driver never spawns a process
    of its own (unlike the Hermes/dsh/agy/grok-build adapters), so whether a local LM Studio
    install happens to exist says nothing about whether the configured host actually answers --
    it could be a remote inference box, or a different OpenAI-compatible server entirely."""
    try:
        with urllib.request.urlopen(host.rstrip('/') + '/v1/models', timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _chat(host, model, prompt, timeout_seconds):
    payload = json.dumps({'model': model, 'stream': False,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(host.rstrip('/') + '/v1/chat/completions', data=payload, method='POST',
                                  headers={'Content-Type': 'application/json',
                                           'Authorization': 'Bearer local'})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            detail = json.loads(exc.read().decode()).get('error', {}).get('message', detail)
        except ValueError:
            pass
        raise ValueError(f'LM Studio server error: {detail}') from exc
    except urllib.error.URLError as exc:
        raise ValueError(f'Could not reach the LM Studio server at {host}: {exc}') from exc
    choices = body.get('choices') or []
    if not choices:
        raise ValueError('LM Studio response had no choices.')
    content = (choices[0].get('message') or {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('LM Studio response had no message content.')
    return content


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('lmstudio-query.txt')).write_text(prompt, encoding='utf-8')
    content = _chat(args.host, args.model, prompt, args.timeout_seconds)
    if not (packet.get('workflow') or packet.get('conversation') or packet.get('knowledge')):
        return 0
    result = worker_prompt.extract_json_object(content)
    if not isinstance(result, dict):
        raise ValueError('LM Studio final response must be a JSON object.')
    output = Path(packet['result_file'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Direct LM Studio (OpenAI-compatible) adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--host', default='http://localhost:1234')
    parser.add_argument('--model', required=True)
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('LM Studio connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
