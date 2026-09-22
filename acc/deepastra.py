"""DeepAstra adapter: runs a model-driven workflow step through DeepAstra's `launch.py exec`
mode, which drives OpenAI's Codex CLI reconfigured to call DeepSeek's (or OpenRouter's) API
instead of OpenAI's. DeepAstra (github.com/ItsssssJack/DeepAstra) is an unofficial,
single-maintainer project -- not a DeepSeek or OpenAI product -- so this driver works around two
real gaps found by reading its actual shipped `launch.py` source, rather than assuming it behaves
like the Hermes or DeepSeek Harness adapters:

1. `launch.py` spawns the real `codex exec` subprocess with `start_new_session=True`, moving it
   to its own detached session/process group, separate from `launch.py`'s own. `launch.py`'s only
   cleanup path is a `try/except (subprocess.TimeoutExpired, KeyboardInterrupt)` around its own
   `proc.wait()`; there is no signal handler, `atexit`, or `finally`. An external SIGTERM --
   exactly what ACC's `stop_tree()` sends to the whole process group on stop/cancel/timeout --
   kills `launch.py` immediately via Python's default signal disposition, before that except block
   ever runs, orphaning the Codex process, which keeps calling DeepSeek's (or OpenRouter's) paid
   API unsupervised. Fixed here with our own SIGTERM handler that walks /proc for the codex child
   of `launch.py`'s pid and kills its process group directly before this process exits.
2. `launch.py`'s own `run-status.json` never carries the model's final answer text, only
   usage/tool-event counts (confirmed by reading its `summarize()` function). The real answer has
   to be recovered from the raw Codex JSONL log `launch.py` already writes under `--log-dir`: the
   last `item.completed` event whose `item.type == 'agent_message'` carries it in `item.text` --
   confirmed against Codex's own event types in `codex-rs/exec/src/exec_events.rs`
   (github.com/openai/codex), not a blog post.

POSIX only: /proc-based child discovery has no Windows equivalent, matching the Windows
process-tree gap already flagged in acc/core.py's own stop_tree().
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

try:
    from . import worker_prompt
except ImportError:
    # core.py invokes this file by direct path, not via `-m acc.deepastra`; see hermes.py's own
    # fallback for why a bare relative import doesn't survive that.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def _children(pid):
    """Immediate child pids of `pid`, or [] if unavailable (e.g. Windows, or already reaped)."""
    try:
        text = Path('/proc/%d/task/%d/children' % (pid, pid)).read_text(encoding='ascii')
    except OSError:
        return []
    return [int(p) for p in text.split()]


def _kill_orphans(launcher_pid):
    """launch.py detaches its codex child into its own session (see module docstring); reach it
    directly since a killpg on our own process group never will."""
    for child in _children(launcher_pid):
        try:
            os.killpg(child, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _last_agent_message(log_dir):
    logs = sorted(Path(log_dir).glob('*.jsonl'))
    if not logs:
        raise ValueError('DeepAstra produced no run log to recover a result from.')
    text = None
    for line in logs[-1].read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('type') == 'item.completed' and \
                row.get('item', {}).get('type') == 'agent_message':
            text = row['item'].get('text')
    if text is None:
        raise ValueError('DeepAstra run log had no agent_message to recover a result from.')
    return text


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    prompt = worker_prompt.build(packet)
    query = packet_path.with_name('deepastra-query.txt')
    query.write_text(prompt, encoding='utf-8')
    log_dir = packet_path.with_name('deepastra-logs')
    log_dir.mkdir(exist_ok=True)
    status_file = packet_path.with_name('deepastra-status.json')
    argv = [sys.executable, str(Path(args.launcher).expanduser()), 'exec', '--cwd', packet['project'],
            '--prompt-file', str(query), '--status-file', str(status_file),
            '--log-dir', str(log_dir), '--timeout', str(args.timeout_seconds),
            '--provider', args.provider]
    if args.key_file:
        argv += ['--key-file', args.key_file]
    # Inherit ACC's process group like the other subprocess-based drivers -- launch.py itself is
    # reachable that way. Its own codex child is not; see the module docstring and _kill_orphans.
    # The filtered env (worker_prompt.subprocess_env) keeps ACC's own unrelated secrets out of
    # reach of codex's shell/tool access; a real DeepSeek/OpenRouter credential still reaches
    # launch.py explicitly via --key-file rather than an ambient *_API_KEY env var.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=worker_prompt.subprocess_env())

    def _on_terminate(signum, frame):
        # Read /proc and kill while launch.py's pid is still live -- once reaped it could be
        # recycled to an unrelated process, so this must never run after proc.wait() returns.
        _kill_orphans(proc.pid)
        os._exit(143)

    previous = signal.signal(signal.SIGTERM, _on_terminate) if os.name != 'nt' else None
    try:
        code = proc.wait()
    except BaseException:
        _kill_orphans(proc.pid)
        proc.kill()
        proc.wait()
        raise
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if code:
        return code
    if not (packet.get('workflow') or packet.get('conversation')):
        return 0
    text = _last_agent_message(log_dir)
    result = worker_prompt.extract_json_object(text)
    if not isinstance(result, dict):
        raise ValueError('DeepAstra final response must be a JSON object.')
    output_path = Path(packet['result_file'])
    temporary = output_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='DeepAstra adapter for ACC')
    parser.add_argument('--packet', required=True)
    parser.add_argument('--launcher', default='launch.py')
    parser.add_argument('--provider', default='deepseek', choices=('deepseek', 'openrouter'))
    parser.add_argument('--key-file')
    parser.add_argument('--timeout-seconds', type=int, default=900)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print('DeepAstra connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
