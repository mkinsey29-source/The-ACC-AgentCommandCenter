"""Grok Build adapter: runs a model-driven workflow step through xAI's official agentic CLI,
Grok Build (binary name `grok`, repo `xai-org/grok-build`), in headless mode. Spawned and
supervised the same way the other CLI-wrapper drivers are.

Real, confirmed contract (from the project's own docs, `crates/codegen/xai-grok-pager/docs/
user-guide/14-headless-mode.md`): `grok -p "<task>" --output-format json --yolo` prints one JSON
object to stdout with response text, a stop reason, a session id, token usage, and cost data.
Exit codes: 0 success, 1 error (auth/network/runtime), 130 interrupted (Ctrl+C), 143 terminated
(SIGTERM). `--yolo` is required for unattended tool execution -- the same shape as Codex's
`approval_policy: never`, agy's `--dangerously-skip-permissions`, and Claude Code's
`--dangerously-skip-permissions` -- so it gets the same credential-filtered subprocess environment
as the other drivers. Authentication for headless/CI use is the `XAI_API_KEY` environment
variable, confirmed directly in the same doc.

The exact field name carrying the response text (`text`) is corroborated by two independent
secondary sources, not the primary doc itself (which describes the envelope's contents in prose
without spelling out every key) -- a materially lower confidence level than the invocation flags,
exit codes, and env var name above, which came directly from the official doc. No confirmed,
currently-open "reports success but returns nothing" bug was found for Grok Build specifically
(unlike Antigravity's agy or Claude Code) -- xai-org/grok-build has its own GitHub Issues disabled,
with reports instead filed against xai-org/plugin-marketplace, and none found there matched that
pattern. The same non-empty-response defensive check is still applied here as a general baseline
consistent with the other CLI drivers, and because the project's own docs define a real success as
requiring "a recognized successful terminal result, process exit 0, AND non-empty canonical
output" -- three conditions, not one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

try:
    from . import worker_prompt
except ImportError:
    # core.py invokes this file by direct path, not via `-m acc.grok_build`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('grok-build-query.txt')).write_text(prompt, encoding='utf-8')
    # Like dsh, agy, gemini-cli, and Claude Code before it, the task is a positional/-p argument
    # rather than a file or stdin -- a very large packet could in principle hit a platform
    # argv-length limit, the same unverified-without-a-real-host gap already flagged for those.
    argv = [args.executable, '-p', prompt, '--output-format', 'json', '--yolo']
    if args.model:
        argv += ['--model', args.model]
    env = worker_prompt.subprocess_env()
    if args.api_key_file:
        key = Path(args.api_key_file).read_text(encoding='utf-8').strip()
        if not key:
            raise ValueError('Grok API key file was empty: ' + args.api_key_file)
        env['XAI_API_KEY'] = key
    # Inherit ACC's process group so stop/timeout reaches grok and its children; filtered env
    # keeps ACC's own unrelated secrets out of reach of the shell/tool access --yolo grants it.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=env)
    try:
        output = proc.stdout.read()
        code = proc.wait()
    except BaseException:
        # A broken pipe or other mid-read failure must not leave grok running unsupervised until
        # ACC's own outer timeout eventually reaches it via the process group.
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    if code:
        return code
    try:
        wrapper = json.loads(output)
    except ValueError as exc:
        raise ValueError('grok --output-format json did not print a JSON object.') from exc
    if not isinstance(wrapper, dict):
        raise ValueError('grok --output-format json did not print a JSON object.')
    text = wrapper.get('text')
    # Exit 0 alone is not sufficient per the tool's own documented definition of success (exit 0
    # AND non-empty canonical output); check the content explicitly rather than trust the exit
    # code in isolation.
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Grok Build reported success but returned no text content.')
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(text)
    if not isinstance(result, dict):
        raise ValueError('Grok Build final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Grok Build adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--executable', default='grok')
    parser.add_argument('--model')
    parser.add_argument('--api-key-file')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Grok Build connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
