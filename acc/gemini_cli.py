"""Gemini CLI adapter: runs a model-driven workflow step through Google's own official
`gemini` CLI (google-gemini/gemini-cli, npm package @google/gemini-cli) in headless mode.
Spawned and supervised the same way the Hermes/DeepSeek-Harness adapters are.

Verified against the CLI's own documented headless contract, not a tutorial: `gemini -p "<task>"
--output-format json` prints one JSON object to stdout -- {"response": <text or null>,
"stats": {...}, "error": <optional>} -- and exits 0 (success), 1 (general/API error), 42 (bad
input), or 53 (turn-limit exceeded). `--yolo` is required for unattended tool execution (skips
the interactive per-action approval prompt), the same shape as Codex CLI's `approval_policy:
never` in the DeepAstra driver -- so the same credential-filtered subprocess environment
(worker_prompt.subprocess_env) applies here too. Real sandboxing exists (macOS Seatbelt,
Docker/Podman, Windows native, gVisor/runsc, LXC/LXD) but is opt-in via GEMINI_SANDBOX/--sandbox
and is left to the operator's own agents.json/settings configuration rather than assumed on;
unlike DeepAstra's launch.py, nothing here has been found (or needs to be worked around) that
detaches a child into its own session, so this driver relies on ACC's ordinary process-group
inheritance rather than DeepAstra's extra SIGTERM/orphan handling.
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
    # core.py invokes this file by direct path, not via `-m acc.gemini_cli`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('gemini-cli-query.txt')).write_text(prompt, encoding='utf-8')
    # The task packet (and thus the prompt) can be sizable; like dsh, gemini-cli takes the task as
    # a positional/-p argument rather than a file or stdin, so a very large packet could in
    # principle hit a platform argv-length limit -- unverified without a real host, the same gap
    # already flagged for dsh and for Windows process-tree termination elsewhere in this codebase.
    argv = [args.executable, '-p', prompt, '--output-format', 'json', '--yolo']
    if args.model:
        argv += ['--model', args.model]
    # Inherit ACC's process group so stop/timeout reaches gemini and its children; filtered env
    # keeps ACC's own unrelated secrets out of reach of the shell/tool access --yolo grants it.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                             env=worker_prompt.subprocess_env())
    try:
        output = proc.stdout.read()
        code = proc.wait()
    except BaseException:
        # A broken pipe or other mid-read failure must not leave gemini running unsupervised
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
        raise ValueError('gemini --output-format json did not print a JSON object.') from exc
    if not isinstance(wrapper, dict):
        raise ValueError('gemini --output-format json did not print a JSON object.')
    if wrapper.get('error'):
        raise ValueError('Gemini CLI error: ' + str(wrapper['error']))
    text = wrapper.get('response')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Gemini CLI produced no response text.')
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(text)
    if not isinstance(result, dict):
        raise ValueError('Gemini CLI final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Gemini CLI adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--executable', default='gemini')
    parser.add_argument('--model')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Gemini CLI connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
