"""Hermes CLI adapter and portable MCP configuration generator. No SDK dependency."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

try:
    from . import worker_prompt
except ImportError:
    # core.py invokes this file by direct path, not via `-m acc.hermes`, so there is no package
    # context for a relative import; fall back to a sibling import off this file's own directory,
    # the same reason bridge.py (invoked the same way) stays free of relative imports entirely.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import worker_prompt


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    query = packet_path.with_name('hermes-query.txt')
    query.write_text(worker_prompt.build(packet), encoding='utf-8')
    argv = [args.executable] + (['-p', args.profile] if args.profile else []) + ['chat', '--query-file', str(query), '--oneshot', '--format', 'stream-json']
    for name in ('provider', 'model'):
        value = getattr(args, name)
        if value:
            argv += ['--' + name, value]
    # Inherit ACC's process group so stop/timeout reaches Hermes and its children.
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, encoding='utf-8')
    final = None
    try:
        for line in proc.stdout:
            print(line, end='', flush=True)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('type') == 'result':
                final = event
        code = proc.wait()
    except BaseException:
        # A broken pipe or other mid-stream failure must not leave Hermes running unsupervised
        # until ACC's own outer timeout eventually reaches it via the process group.
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    if code:
        return code
    if not final or final.get('exit_code') != 0:
        raise ValueError('Hermes exited without a successful terminal result event.')
    if packet.get('workflow') or packet.get('conversation'):
        result = worker_prompt.extract_json_object(final['text'])
        if not isinstance(result, dict):
            raise ValueError('Hermes final response must be a JSON object.')
        output = Path(packet['result_file'])
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps(result), encoding='utf-8')
        temporary.replace(output)
    return 0


def config(args):
    # JSON is valid YAML; emit a mergeable fragment without editing Hermes settings.
    bridge = Path(__file__).with_name('bridge.py').resolve()
    result = {'mcp_servers': {'acc': {
        'command': sys.executable,
        'args': [str(bridge), '--url', args.url, '--token-file', str(Path(args.token_file).resolve())]
    }}}
    text = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(text + '\n', encoding='utf-8')
        print('Wrote Hermes MCP configuration fragment: ' + args.output)
    else:
        print(text)


def main():
    parser = argparse.ArgumentParser(description='Hermes connector for ACC')
    commands = parser.add_subparsers(dest='command', required=True)
    adapter = commands.add_parser('run')
    adapter.add_argument('--packet', required=True)
    adapter.add_argument('--executable', default='hermes')
    adapter.add_argument('--profile')
    adapter.add_argument('--provider')
    adapter.add_argument('--model')
    setup = commands.add_parser('config')
    setup.add_argument('--url', default='http://127.0.0.1:8765')
    setup.add_argument('--token-file', required=True)
    setup.add_argument('--output')
    args = parser.parse_args()
    try:
        return run(args) if args.command == 'run' else config(args)
    except (ValueError, OSError, KeyError) as exc:
        print('Hermes connector: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
