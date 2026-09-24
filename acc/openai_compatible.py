"""Custom OpenAI-compatible adapter: runs a model-driven workflow step against any server that
speaks the OpenAI Chat Completions wire format, with no subprocess of its own to supervise -- the
same shape as ollama.py and lmstudio.py, generalized with a required --base-url instead of a fixed
default host, and real Authorization support instead of only a local placeholder token.

Unlike ollama.py (Ollama's own dialect) and lmstudio.py (LM Studio's well-known default port),
this driver has no sensible default endpoint of its own -- it exists for whatever
OpenAI-Chat-Completions-compatible target isn't one of ACC's other named drivers: a paid gateway
(OpenRouter, Together, Fireworks, Groq's inference API, Azure OpenAI), a self-hosted server with
authentication enabled, or literally OpenAI's own API, if the user wants to route to it directly.

Endpoints: GET {base_url}/models (reachability probe) and POST {base_url}/chat/completions
(`{"model", "messages"}` request, `choices[0].message.content` response) -- the same long-stable
convention already documented in lmstudio.py, not something specific to this driver. A missing
api_key_file is not an error: some custom endpoints (an internal proxy, for instance) need no
auth at all, in which case a placeholder Bearer token is sent instead, since some OpenAI-compatible
servers still reject a request with no Authorization header at all.
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
    # core.py invokes this file by direct path, not via `-m acc.openai_compatible`; see
    # hermes.py's own fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def is_reachable(base_url, api_key, timeout=1.5):
    """A live reachability probe, not a CLI-presence check: this driver never spawns a process
    of its own, and unlike ollama.py/lmstudio.py there is no fixed default host to assume, so the
    only way to know a configured endpoint answers is to actually ask it -- with whatever
    credential is configured, since a server that requires auth would otherwise look unreachable
    even when it's actually up and simply rejecting an unauthenticated probe."""
    req = urllib.request.Request(base_url.rstrip('/') + '/models',
                                  headers={'Authorization': 'Bearer ' + (api_key or 'not-needed')})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _chat(base_url, api_key, model, prompt, timeout_seconds):
    payload = json.dumps({'model': model, 'stream': False,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(base_url.rstrip('/') + '/chat/completions', data=payload, method='POST',
                                  headers={'Content-Type': 'application/json',
                                           'Authorization': 'Bearer ' + (api_key or 'not-needed')})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            detail = json.loads(exc.read().decode()).get('error', {}).get('message', detail)
        except ValueError:
            pass
        raise ValueError(f'OpenAI-compatible server error: {detail}') from exc
    except urllib.error.URLError as exc:
        raise ValueError(f'Could not reach {base_url}: {exc}') from exc
    choices = body.get('choices') or []
    if not choices:
        raise ValueError('OpenAI-compatible server response had no choices.')
    content = (choices[0].get('message') or {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('OpenAI-compatible server response had no message content.')
    return content


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('openai-compatible-query.txt')).write_text(prompt, encoding='utf-8')
    api_key = None
    if args.api_key_file:
        api_key = Path(args.api_key_file).expanduser().read_text(encoding='utf-8').strip()
        if not api_key:
            raise ValueError('API key file was empty: ' + args.api_key_file)
    content = _chat(args.base_url, api_key, args.model, prompt, args.timeout_seconds)
    if not (packet.get('workflow') or packet.get('conversation') or packet.get('knowledge')):
        return 0
    result = worker_prompt.extract_json_object(content)
    if not isinstance(result, dict):
        raise ValueError('OpenAI-compatible server final response must be a JSON object.')
    output = Path(packet['result_file'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Custom OpenAI-compatible adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--api-key-file')
    parser.add_argument('--timeout-seconds', type=int, default=180)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('OpenAI-compatible connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
