"""DeepSeek Harness (dsh) adapter: runs a model-driven workflow step through dsh's one-shot
headless profile. No SDK dependency; a real spawned subprocess, supervised the same way the
Hermes adapter is.

Verified against the actual shipped @deepseek-ai/dsh (0.1.5-rc.2) and @deepseek-ai/dsh-headless
(0.0.1-rc.1) packages' own published TypeScript type declarations and CLI argument parser, not
third-party tutorials -- those describe a `--json` NDJSON event stream (session/status/text/
thinking/tool_call/tool_result/final) that does not exist anywhere in the actual shipped code.
The real, confirmed contract is much simpler: `dsh --profile headless "<task>"` prints the final
assistant text to stdout and exits 0 for a completed turn, 1 for anything else (aborted, errored,
or no turn in the owned interval).
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
    # core.py invokes this file by direct path, not via `-m acc.deepseek_harness`; see hermes.py's
    # own fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('dsh-query.txt')).write_text(prompt, encoding='utf-8')
    # The task is a positional argument per dsh-headless's own Config (`task: string`), not a
    # file or stdin -- confirmed from its published types, not assumed. A very large packet could
    # in principle hit a platform argv-length limit (Windows especially); unverified without a
    # real host, same class of gap already flagged for Windows process-tree termination elsewhere.
    argv = [args.executable, '--profile', 'headless', prompt]
    # Inherit ACC's process group so stop/timeout reaches dsh and its children -- same convention
    # as the Hermes adapter. This script does not enforce its own timeout; ACC's outer supervision
    # (the coordinator's deadline timer + stop_tree/killpg) owns interrupting a hung run.
    # The filtered env keeps ACC's own unrelated secrets out of reach of whatever shell/tool
    # access dsh grants the model; see worker_prompt.subprocess_env.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                             env=worker_prompt.subprocess_env())
    try:
        output = proc.stdout.read()
        code = proc.wait()
    except BaseException:
        # A broken pipe or other mid-read failure must not leave dsh running unsupervised until
        # ACC's own outer timeout eventually reaches it via the process group.
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    if code:
        return code
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(output)
    if not isinstance(result, dict):
        raise ValueError('dsh final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='DeepSeek Harness (dsh) adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--executable', default='dsh')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('DeepSeek Harness connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
