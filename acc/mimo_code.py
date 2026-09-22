"""MiMo Code adapter: runs a model-driven workflow step through Xiaomi's official MiMo Code CLI
(binary `mimo`, github.com/XiaomiMiMo/MiMo-Code), a fork of the widely-used OpenCode
(github.com/anomalyco/opencode) with added memory/skill-distillation features (user-triggered
`/dream` and `/distill` commands -- not autonomous self-modification, despite MiMo Code's own
"Where Models and Agents Co-Evolve" tagline). Spawned and supervised the same way the other
CLI-wrapper drivers are.

Invocation confirmed directly from MiMo Code's own README: `mimo run --dangerously-skip-permissions
"<task>"` for unattended tool execution, matching Codex's `approval_policy: never` / agy's /
Claude Code's own equivalent flags -- so it gets the same credential-filtered subprocess
environment as the other drivers. `--model` takes upstream OpenCode's own `provider/model` format
(e.g. `opencode/grok-code`), not a flat model name, per OpenCode's own documented CLI reference.

The output-parsing contract below is NOT independently confirmed against MiMo Code's own compiled
source -- despite MiMo Code documenting itself as "built as a fork of OpenCode, retaining core
capabilities," it has already diverged from upstream on at least one flag (its own
`--dangerously-skip-permissions` where upstream OpenCode instead documents `--auto`), so parity
with upstream on this specific point is a reasoned inference, not a verified fact. What IS
confirmed directly from upstream OpenCode's real source
(`anomalyco/opencode` `packages/opencode/src/cli/cmd/run.ts`): `--format json` emits one JSON
object per line, shaped `{type, timestamp, sessionID, ...data}`, with event types including
`text`, `tool_use`, `step_start`, `step_finish`, `reasoning`, and `error`; the process exits
nonzero when the emitted result carries an error. This driver assumes MiMo Code's `--format json`
still means the same thing, accumulates the `text` field from every `type: "text"` event (treating
them as incremental deltas, the far more common streaming shape for LLM output, rather than each
being a full snapshot), and treats any `type: "error"` event or a nonzero exit code as failure.
If MiMo Code turns out to have changed this shape, this driver's own defensive checks (a
non-JSON line, no text content at all) surface that as a clear error rather than silently
misbehaving -- revisit this docstring once actually run against a real `mimo` install.
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
    # core.py invokes this file by direct path, not via `-m acc.mimo_code`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    (packet_path.with_name('mimo-code-query.txt')).write_text(prompt, encoding='utf-8')
    argv = [args.executable, 'run', '--dangerously-skip-permissions', '--format', 'json']
    if args.model:
        argv += ['--model', args.model]
    argv.append(prompt)
    env = worker_prompt.subprocess_env()
    # Inherit ACC's process group so stop/timeout reaches mimo and its children; filtered env
    # keeps ACC's own unrelated secrets out of reach of the shell/tool access this flag grants it.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=env)
    text_parts = []
    error_detail = None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'text':
                chunk = event.get('text')
                if isinstance(chunk, str):
                    text_parts.append(chunk)
            elif event.get('type') == 'error':
                error_detail = event.get('error') or event.get('message') or event
        code = proc.wait()
    except BaseException:
        # A broken pipe or other mid-stream failure must not leave mimo running unsupervised
        # until ACC's own outer timeout eventually reaches it via the process group.
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    if code:
        return code
    if error_detail is not None:
        raise ValueError(f'MiMo Code did not succeed: {error_detail}')
    text = ''.join(text_parts)
    if not text.strip():
        raise ValueError('MiMo Code reported success but produced no text content.')
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    result = worker_prompt.extract_json_object(text)
    if not isinstance(result, dict):
        raise ValueError('MiMo Code final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='MiMo Code adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--executable', default='mimo')
    parser.add_argument('--model', help='provider/model, e.g. mimo/mimo-v2.5 or opencode/grok-code')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('MiMo Code connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
