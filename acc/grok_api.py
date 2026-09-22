"""Direct Grok (xAI) API adapter: runs a model-driven workflow step against xAI's API over plain
HTTP, with no subprocess of its own to supervise -- the same shape as ollama.py, gemini.py, and
claude_api.py. No SDK dependency, matching every other driver's stdlib-only convention.

Deliberately uses the older Chat Completions endpoint (`POST api.x.ai/v1/chat/completions`,
OpenAI-compatible `messages` request, `choices[0].message.content` / `choices[0].finish_reason`
response) rather than xAI's newer "Responses API" (`/v1/responses`), even though xAI's own docs
now recommend the latter. The Responses API's request shape (`{"model", "input"}`) was confirmed
from multiple sources, but its raw JSON *response* shape could not be independently verified from
here: docs.x.ai is blocked by this environment's egress proxy, and the one third-party source found
describing it (`bigsk1/xai-api`) turned out, on checking its own README, to be an unofficial
FastAPI proxy/wrapper around xAI's API, not xAI's own documentation -- its schema describes that
project's own reimplementation, not a verified xAI wire format. Chat Completions' response shape is
the long-established, unambiguous OpenAI-compatible convention xAI explicitly advertises
compatibility with, so it's the safer choice absent a verified primary source for the newer
endpoint. Revisit if/when the Responses API's actual response schema can be confirmed directly.

Auth: `Authorization: Bearer <key>` (OpenAI-compatible). Default model `grok-4.6`, xAI's current
recommended model for chat/coding/agentic workloads as of this writing; older model names
(`grok-4-1-fast`, `grok-4-fast`, `grok-4-0709`, `grok-3`) now redirect server-side to a newer model
and use its pricing, per xAI's own docs.
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
    # core.py invokes this file by direct path, not via `-m acc.grok_api`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt

ENDPOINT = 'https://api.x.ai/v1/chat/completions'


def _generate(api_key, model, prompt, max_tokens, timeout_seconds, endpoint=ENDPOINT):
    payload = json.dumps({'model': model, 'max_tokens': max_tokens,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(endpoint, data=payload, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            detail = json.loads(exc.read().decode()).get('error', {}).get('message', detail)
        except ValueError:
            pass
        raise ValueError(f'Grok API error: {detail}') from exc
    except urllib.error.URLError as exc:
        raise ValueError(f'Could not reach the Grok API: {exc}') from exc
    choices = body.get('choices') or []
    if not choices:
        raise ValueError('Grok API response had no choices.')
    choice = choices[0]
    if choice.get('finish_reason') == 'length':
        raise ValueError('Grok API response was truncated (finish_reason=length); '
                          'the required JSON object may be incomplete.')
    text = (choice.get('message') or {}).get('content')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Grok API response had no message content.')
    return text


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('grok-query.txt')).write_text(prompt, encoding='utf-8')
    # A local credential-reference file, not an ambient env var -- consistent with the project's
    # own "store credentials through local credential references" convention.
    api_key = Path(args.api_key_file).read_text(encoding='utf-8').strip()
    if not api_key:
        raise ValueError('Grok API key file was empty: ' + args.api_key_file)
    content = _generate(api_key, args.model, prompt, args.max_tokens, args.timeout_seconds, args.endpoint)
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(content)
    if not isinstance(result, dict):
        raise ValueError('Grok API final response must be a JSON object.')
    output = Path(packet['result_file'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Direct Grok (xAI) API adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--api-key-file', required=True)
    parser.add_argument('--model', default='grok-4.6')
    parser.add_argument('--max-tokens', type=int, default=16000)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    # Real users never need this; it exists so tests can point the driver at a fake local server
    # without a live Grok API dependency, the same role ollama.py's --host plays for real.
    parser.add_argument('--endpoint', default=ENDPOINT)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Grok API connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
