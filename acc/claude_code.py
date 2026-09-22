"""Claude Code adapter: runs a model-driven workflow step through Claude Code's own headless mode
(`claude -p "<task>" --output-format json`). Spawned and supervised the same way the other
CLI-wrapper drivers are.

Real, confirmed contract: prints one JSON object to stdout with (at least) `type`, `subtype`
("success" or an "error_*" value such as "error_max_turns"/"error_during_execution"), `result`
(the final free-form text), and `session_id`. An `error_*` subtype sets `is_error: true` and the
process exits nonzero. `--dangerously-skip-permissions` is required for unattended tool execution
-- the same shape as Codex's `approval_policy: never` and agy's `--dangerously-skip-permissions`
-- so it gets the same credential-filtered subprocess environment as the other drivers.

A real, currently-open bug (anthropics/claude-code#79500) means `subtype: "success"` and exit 0
are not fully reliable either: an API-level failure (e.g. a rate limit) can be reported as a
"success" envelope with the actual error text landing in `result` as plain prose instead of a
structured error. This isn't specially handled beyond what the existing contract already
provides: `worker_prompt.extract_json_object` requires `result` to contain a recoverable JSON
object per ACC's own contract, so a prose error message (which isn't JSON) is naturally rejected
with a clear error rather than silently accepted as a real result -- the same protection this
driver would otherwise need to add on purpose, already present as a side effect of the contract
every driver already enforces.
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
    # core.py invokes this file by direct path, not via `-m acc.claude_code`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('claude-code-query.txt')).write_text(prompt, encoding='utf-8')
    # Like dsh, agy, and gemini-cli before it, the task is a positional/-p argument rather than a
    # file or stdin -- a very large packet could in principle hit a platform argv-length limit,
    # the same unverified-without-a-real-host gap already flagged for those drivers.
    argv = [args.executable, '-p', prompt, '--output-format', 'json', '--dangerously-skip-permissions']
    if args.model:
        argv += ['--model', args.model]
    env = worker_prompt.subprocess_env()
    if args.api_key_file:
        key = Path(args.api_key_file).expanduser().read_text(encoding='utf-8').strip()
        if not key:
            raise ValueError('Claude API key file was empty: ' + args.api_key_file)
        env['ANTHROPIC_API_KEY'] = key
    # Inherit ACC's process group so stop/timeout reaches claude and its children; filtered env
    # keeps ACC's own unrelated secrets out of reach of the shell/tool access this flag grants it.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=env)
    try:
        output = proc.stdout.read()
        code = proc.wait()
    except BaseException:
        # A broken pipe or other mid-read failure must not leave claude running unsupervised
        # until ACC's own outer timeout eventually reaches it via the process group.
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
        raise ValueError('claude --output-format json did not print a JSON object.') from exc
    if not isinstance(wrapper, dict):
        raise ValueError('claude --output-format json did not print a JSON object.')
    subtype = wrapper.get('subtype')
    if wrapper.get('is_error') or (isinstance(subtype, str) and subtype.startswith('error')):
        raise ValueError('Claude Code did not succeed: subtype=' + str(subtype))
    text = wrapper.get('result')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Claude Code produced no result text.')
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(text)
    if not isinstance(result, dict):
        raise ValueError('Claude Code final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Claude Code adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--executable', default='claude')
    parser.add_argument('--model')
    parser.add_argument('--api-key-file')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Claude Code connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
