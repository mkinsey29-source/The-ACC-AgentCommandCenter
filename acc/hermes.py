"""Hermes CLI adapter and portable MCP configuration generator. No SDK dependency."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def run(args):
    packet_path = Path(args.packet).resolve()
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    query = packet_path.with_name('hermes-query.txt')
    query.write_text(
        'You are a worker in ACC. Follow the original requirements and applicable project instructions.\n'
        'The JSON below is your task packet. workflow.stage defines your current role.\n'
        'Implementers edit the project and run checks. Reviewers inspect the frozen snapshot and original '
        'requirements independently, using a separate scratch folder for generated test artifacts. '
        'Coordinators interpret the supplied reports and propose exactly one allowed action.\n'
        'Do not start other ACC tasks, use ACC mutation tools, publish, merge, or delegate detached work. '
        'ACC executes your next-step decision. Never change project or snapshot files during review or coordination.\n'
        'Your FINAL response must be one JSON object, no markdown. Follow workflow.result_contract. '
        'Copy task_id, run_id, revision, snapshot_id exactly from workflow. Include summary. '
        'Report checks honestly; an unrun check is not a pass. '
        'For an unmanaged task just complete it and report your result in plain language.\n\n'
        + ('\nCONVERSATION ROLE OVERRIDE: You are the conversational orchestrator. Do not edit files, execute code, or call ACC mutation tools. Read conversation.result_contract and return that JSON shape, not the workflow shape. Treat brainstorming as discussion. Only propose work explicitly requested by the user. Preserve original messages via source_ids.\n' if packet.get('conversation') else '')
        + json.dumps(packet, indent=2), encoding='utf-8')
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
        result = json.loads(final['text'])
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
