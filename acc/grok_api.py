"""Direct Grok (xAI) API adapter: runs a model-driven workflow step against xAI's Responses API
over plain HTTP, with no subprocess of its own to supervise -- the same shape as ollama.py,
gemini.py, and claude_api.py. No SDK dependency, matching every other driver's stdlib-only
convention.

Endpoint: POST https://api.x.ai/v1/responses
Headers:  Authorization: Bearer <key>, Content-Type: application/json
Body:     {"model", "input", "max_output_tokens"}

Response schema confirmed directly against xAI's own API reference (pasted in full by the user
after `docs.x.ai` turned out to be unreachable from this environment): there is no top-level
`output_text` string in the raw JSON, only an `output` array of items. A text reply's item has
`{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]}`;
`.output_text` is purely an SDK-side convenience property some client libraries add, the same
pattern OpenAI's own Responses API uses -- confirming the earlier guess (made without access to
this schema) not to assume that convenience field exists on the wire was the right call. The
top-level `status` field (`"completed"` / `"in_progress"` / `"incomplete"`) is checked explicitly:
`"incomplete"` covers what the old Chat-Completions-based version of this driver checked via
`finish_reason == "length"`, and more generally (any reason generation didn't finish cleanly, not
just a token-limit truncation). A populated top-level `error` object is also checked explicitly.

Default model `grok-4.7`, matching xAI's own current documented example request.
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

ENDPOINT = 'https://api.x.ai/v1/responses'


def _extract_text(body):
    parts = []
    for item in body.get('output') or []:
        if not isinstance(item, dict) or item.get('type') != 'message':
            continue
        for block in item.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'output_text':
                text = block.get('text')
                if isinstance(text, str):
                    parts.append(text)
    return ''.join(parts)


def _generate(api_key, model, prompt, max_output_tokens, timeout_seconds, endpoint=ENDPOINT):
    payload = json.dumps({'model': model, 'input': prompt,
                           'max_output_tokens': max_output_tokens}).encode()
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
    if body.get('error'):
        raise ValueError(f'Grok API error: {body["error"]}')
    status = body.get('status')
    if status != 'completed':
        raise ValueError(f'Grok API response did not complete (status={status}): '
                          f'{body.get("incomplete_details")}')
    text = _extract_text(body)
    if not text.strip():
        raise ValueError('Grok API response had no output_text content.')
    return text


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('grok-query.txt')).write_text(prompt, encoding='utf-8')
    # A local credential-reference file, not an ambient env var -- consistent with the project's
    # own "store credentials through local credential references" convention.
    api_key = Path(args.api_key_file).expanduser().read_text(encoding='utf-8').strip()
    if not api_key:
        raise ValueError('Grok API key file was empty: ' + args.api_key_file)
    content = _generate(api_key, args.model, prompt, args.max_tokens, args.timeout_seconds, args.endpoint)
    if not (packet.get('workflow') or packet.get('conversation') or packet.get('knowledge')):
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
    parser.add_argument('--model', default='grok-4.7')
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
