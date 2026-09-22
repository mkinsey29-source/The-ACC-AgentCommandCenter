"""Direct Claude API adapter: runs a model-driven workflow step against Anthropic's Messages API
over plain HTTP, with no subprocess of its own to supervise -- the same shape as ollama.py and
gemini.py. No SDK dependency (the official `anthropic` package is deliberately not used, even
though it's otherwise the normal recommended way to call this API, matching every other driver's
stdlib-only convention here).

Endpoint: POST https://api.anthropic.com/v1/messages
Headers:  x-api-key, anthropic-version: 2023-06-01, Content-Type: application/json
Body:     {"model", "max_tokens", "messages": [{"role": "user", "content": "<prompt>"}]}
Response: `.content` is an array of blocks; a plain (non-tool-use) reply's text lives in the
`type == "text"` blocks. `.stop_reason == "refusal"` means Anthropic's safety classifiers declined
the request (HTTP 200, no usable content) -- checked explicitly here rather than assumed absent,
the same defensive posture already applied to the other drivers' own status/success fields.

Default model is claude-opus-5 (Anthropic's current flagship). `thinking` is intentionally left
unset rather than hard-coded: claude-opus-5 runs adaptive thinking by default when the parameter is
omitted, and a hard-coded thinking config would misbehave if --model is pointed at an older model
that needs `budget_tokens` instead.
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
    # core.py invokes this file by direct path, not via `-m acc.claude_api`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt

ENDPOINT = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION = '2023-06-01'


def _generate(api_key, model, prompt, max_tokens, timeout_seconds, endpoint=ENDPOINT):
    payload = json.dumps({'model': model, 'max_tokens': max_tokens,
                           'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(endpoint, data=payload, method='POST', headers={
        'Content-Type': 'application/json', 'x-api-key': api_key,
        'anthropic-version': ANTHROPIC_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            detail = json.loads(exc.read().decode()).get('error', {}).get('message', detail)
        except ValueError:
            pass
        raise ValueError(f'Claude API error: {detail}') from exc
    except urllib.error.URLError as exc:
        raise ValueError(f'Could not reach the Claude API: {exc}') from exc
    if body.get('stop_reason') == 'refusal':
        raise ValueError('Claude declined the request (stop_reason=refusal).')
    text = ''.join(block.get('text', '') for block in body.get('content', [])
                   if isinstance(block, dict) and block.get('type') == 'text')
    if not text.strip():
        raise ValueError('Claude API response had no text content.')
    return text


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('claude-query.txt')).write_text(prompt, encoding='utf-8')
    # A local credential-reference file, not an ambient env var -- consistent with the project's
    # own "store credentials through local credential references" convention.
    api_key = Path(args.api_key_file).read_text(encoding='utf-8').strip()
    if not api_key:
        raise ValueError('Claude API key file was empty: ' + args.api_key_file)
    content = _generate(api_key, args.model, prompt, args.max_tokens, args.timeout_seconds, args.endpoint)
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(content)
    if not isinstance(result, dict):
        raise ValueError('Claude API final response must be a JSON object.')
    output = Path(packet['result_file'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Direct Claude API adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--api-key-file', required=True)
    parser.add_argument('--model', default='claude-opus-5')
    parser.add_argument('--max-tokens', type=int, default=16000)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    # Real users never need this; it exists so tests can point the driver at a fake local server
    # without a live Claude API dependency, the same role ollama.py's --host plays for real.
    parser.add_argument('--endpoint', default=ENDPOINT)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Claude API connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
