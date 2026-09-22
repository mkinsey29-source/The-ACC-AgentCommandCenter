"""Direct Gemini API adapter: runs a model-driven workflow step against Google's Interactions API
over plain HTTP, with no subprocess of its own to supervise -- the same shape as ollama.py. No SDK
dependency (the official google-genai package is deliberately not used, matching every other
driver's stdlib-only convention).

Verified against the plain developer-facing Gemini API (generativelanguage.googleapis.com), not
the two other, materially different surfaces also called "Gemini" as of 2026: the GCP Enterprise
Agent Platform variant (aiplatform.googleapis.com, a GCP project id, OAuth bearer tokens -- for
Vertex AI customers, not a local API-key setup), and "Managed Agents" (agent/environment fields,
a fully Google-hosted remote sandbox -- an entirely different, non-local execution model, not
something ACC could supervise as a subprocess or plain HTTP call the way every other driver here
works). The Interactions API (GA June 2026) is Google's own recommended default over the older
generateContent endpoint for new work; generateContent remains supported but agent-oriented
features are landing on Interactions only.

The real, confirmed contract for a synchronous (non-agentic, non-streaming) call:
POST https://generativelanguage.googleapis.com/v1beta/interactions
Headers: x-goog-api-key: <key>, Content-Type: application/json, Api-Revision: 2026-05-20
Body:    {"model": "<model>", "input": "<prompt text>"}
Response (default, without background=true): synchronous, with an `output_text` field carrying
the model's reply.
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
    # core.py invokes this file by direct path, not via `-m acc.gemini`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt

ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/interactions'
API_REVISION = '2026-05-20'


def _generate(api_key, model, prompt, timeout_seconds, endpoint=ENDPOINT):
    payload = json.dumps({'model': model, 'input': prompt}).encode()
    req = urllib.request.Request(endpoint, data=payload, method='POST', headers={
        'Content-Type': 'application/json', 'x-goog-api-key': api_key, 'Api-Revision': API_REVISION})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            detail = json.loads(exc.read().decode()).get('error', {}).get('message', detail)
        except ValueError:
            pass
        raise ValueError(f'Gemini API error: {detail}') from exc
    except urllib.error.URLError as exc:
        raise ValueError(f'Could not reach the Gemini API: {exc}') from exc
    text = body.get('output_text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Gemini API response had no output_text.')
    return text


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('gemini-query.txt')).write_text(prompt, encoding='utf-8')
    # A local credential-reference file, not an ambient env var -- consistent with the project's
    # own "store credentials through local credential references" convention, and with how
    # deepastra.py's --key-file works.
    api_key = Path(args.api_key_file).read_text(encoding='utf-8').strip()
    if not api_key:
        raise ValueError('Gemini API key file was empty: ' + args.api_key_file)
    content = _generate(api_key, args.model, prompt, args.timeout_seconds, args.endpoint)
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(content)
    if not isinstance(result, dict):
        raise ValueError('Gemini API final response must be a JSON object.')
    output = Path(packet['result_file'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Direct Gemini API adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--api-key-file', required=True)
    parser.add_argument('--model', default='gemini-3.5-flash')
    parser.add_argument('--timeout-seconds', type=int, default=120)
    # Real users never need this; it exists so tests can point the driver at a fake local server
    # without a live Gemini API dependency, the same role ollama.py's --host plays for real.
    parser.add_argument('--endpoint', default=ENDPOINT)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Gemini API connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
