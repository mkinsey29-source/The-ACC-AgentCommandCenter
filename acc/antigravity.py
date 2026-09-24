"""Antigravity CLI adapter: runs a model-driven workflow step through Google's official
Antigravity CLI (`agy`, google-antigravity/antigravity-cli), the real, confirmed successor to
Gemini CLI -- Google began retiring the plain `gemini` binary for individual/free-tier accounts
on 2026-06-18, already past as of this writing (the `gemini-cli` driver this replaces has been
removed rather than left to quietly rot). Spawned and supervised the same way the other
CLI-wrapper drivers are.

Verified against agy's own real, currently open issue tracker, not tutorials alone -- important
because print/headless mode has multiple confirmed, currently-open reliability bugs directly
relevant to ACC's own usage shape (a large, JSON-heavy prompt on every single call):

- #840: such a prompt can make print mode silently return an empty response with
  `status: SUCCESS` and exit 0 -- an internal pubsub stall, not a real completion.
- #794: an auto-denied tool call can also leave exit 0 / status SUCCESS with no structured_output.
- #1044: a launched background command can still be executing after the turn reports SUCCESS and
  gets killed -- an internal orphan/premature-completion bug. Unlike DeepAstra's detached `codex`
  child, there is no separate, externally reachable process here to /proc-walk and kill; this is
  agy's own internal turn-completion signaling being wrong, not fixable from outside the process.

Because of this, an exit code of 0 and `status: SUCCESS` are both necessary but NOT sufficient for
treating a run as real: this driver additionally requires a non-blank `response` field before
trusting the result, and raises a distinct, clearly-labeled error when that's missing instead of
quietly treating an empty success as "nothing to do."

Real, confirmed contract: `agy -p "<task>" --output-format json --dangerously-skip-permissions
[--model <model>]` prints one JSON object to stdout -- {conversation_id, status, response,
num_turns, duration_seconds, usage, structured_output?}. A model/agent API failure additionally
prints a structured `AGY_ERROR: {"status", "code", "retryable", "error_id"}` line to stderr and
exits nonzero (reported as exit code 3 for this specific failure class in some agy versions;
treated generically here as "any nonzero exit is a failure," matching every other driver's
convention, rather than hard-coding one exact number that may not hold across agy's own
fast-moving versions -- its changelog shows frequent point releases).

Authentication for headless use was not pinned down to one certain mechanism: sources describe
both a directly-read `ANTIGRAVITY_API_KEY` environment variable and a `GEMINI_API_KEY` variable
that additionally requires a `modelProvider` setting in agy's own config file, and the two may not
be interchangeable across agy versions. This driver reads a local credential-reference file
(matching the project's convention, and deepastra.py's --key-file) and exports it under an
operator-chosen env var name (--api-key-env, default ANTIGRAVITY_API_KEY) rather than assuming one
fixed mechanism; confirm the right variable name against your installed agy version's own --help.
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
    # core.py invokes this file by direct path, not via `-m acc.antigravity`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def _agy_error(stderr_text):
    for line in stderr_text.splitlines():
        if line.startswith('AGY_ERROR:'):
            return line
    return None


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('antigravity-query.txt')).write_text(prompt, encoding='utf-8')
    # Like dsh and gemini-cli, the task is a positional/-p argument rather than a file or stdin --
    # a very large packet could in principle hit a platform argv-length limit, the same
    # unverified-without-a-real-host gap already flagged for those drivers.
    argv = [args.executable, '-p', prompt, '--output-format', 'json', '--dangerously-skip-permissions']
    if args.model:
        argv += ['--model', args.model]
    env = worker_prompt.subprocess_env()
    if args.api_key_file:
        key = Path(args.api_key_file).expanduser().read_text(encoding='utf-8').strip()
        if not key:
            raise ValueError('Antigravity API key file was empty: ' + args.api_key_file)
        env[args.api_key_env] = key
    # Inherit ACC's process group so stop/timeout reaches agy and its children. stderr is captured
    # separately from stdout (not merged) so a structured AGY_ERROR line there can never corrupt
    # the stdout JSON parse; communicate() (not sequential .read() calls) avoids the classic
    # dual-pipe deadlock that risks if one fills while only the other is being drained.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
    try:
        output, error_output = proc.communicate()
        code = proc.returncode
    except BaseException:
        # A broken pipe or other mid-read failure must not leave agy running unsupervised until
        # ACC's own outer timeout eventually reaches it via the process group.
        proc.kill()
        proc.wait()
        raise
    if code:
        detail = _agy_error(error_output) or error_output.strip() or 'no error detail on stderr.'
        raise ValueError('Antigravity CLI failed (exit ' + str(code) + '): ' + detail)
    try:
        wrapper = json.loads(output)
    except ValueError as exc:
        raise ValueError('agy --output-format json did not print a JSON object.') from exc
    if not isinstance(wrapper, dict):
        raise ValueError('agy --output-format json did not print a JSON object.')
    if wrapper.get('status') != 'SUCCESS':
        raise ValueError('Antigravity CLI turn did not succeed: status=' + str(wrapper.get('status')))
    text = wrapper.get('response')
    # A known, currently-open class of agy bug (upstream issues #840, #794) reports SUCCESS with
    # an empty response for exactly this driver's own prompt shape (large, JSON-heavy). Exit 0 and
    # status SUCCESS are necessary but not sufficient; treat blank content as a real failure, not
    # nothing-to-do.
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Antigravity CLI reported success but returned no response text -- a '
                          'known class of upstream bug (agy issues #840, #794), not a real result.')
    if not (packet.get('workflow') or packet.get('conversation') or packet.get('knowledge')):
        return 0
    result = worker_prompt.extract_json_object(text)
    if not isinstance(result, dict):
        raise ValueError('Antigravity CLI final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Antigravity CLI adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--executable', default='agy')
    parser.add_argument('--model')
    parser.add_argument('--api-key-file')
    parser.add_argument('--api-key-env', default='ANTIGRAVITY_API_KEY')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Antigravity CLI connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
