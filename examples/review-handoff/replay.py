"""Replay the transport/validation handoff using preserved real-agent outputs.

This does not create fresh model sessions or require provider credentials.
It starts the actual ACC service and calls its actual stdio MCP bridge.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
from acc.core import Coordinator
from acc.server import Server


def replay():
    manifest = json.loads((HERE / 'snapshot/manifest.json').read_text())
    assert manifest['snapshot_id'] == 'sha256:' + hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((HERE / 'snapshot' / name).read_bytes()).hexdigest() == digest, name
    with tempfile.TemporaryDirectory(prefix='acc-review-replay-') as temp:
        root = Path(temp)
        project = root / 'project'
        shutil.copytree(HERE / 'snapshot', project)
        coordinator = Coordinator(project, root / 'state')
        # Test-only local credential; not a provider key. No credential is exported.
        token = 'isolated-replay-session'
        key = root / 'token'
        key.write_text(token)
        server = Server(('127.0.0.1', 0), coordinator, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        calls = Counter()

        def call(name, args=None, expected_error=False):
            calls[name] += 1
            messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                         'params': {'protocolVersion': '2025-06-18'}},
                        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                         'params': {'name': name, 'arguments': args or {}}}]
            run = subprocess.run([sys.executable, '-m', 'acc.bridge', '--url', url, '--token-file', str(key)],
                                 cwd=REPO, input=''.join(json.dumps(m)+'\n' for m in messages),
                                 capture_output=True, text=True, timeout=30, check=True)
            result = json.loads(run.stdout.splitlines()[-1])['result']
            assert bool(result.get('isError')) == expected_error, result
            return json.loads(result['content'][0]['text'])

        def wait(task):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = call('acc_state')
                found = next(t for t in state['tasks'] if t['id'] == task['id'])
                if found['status'] not in ('running', 'stopping'):
                    return found
                time.sleep(.1)
            raise TimeoutError(task['title'])

        def run_task(title, argv):
            task = call('acc_create_task', {'title': title, 'instruction': (project/'REQUIREMENTS.md').read_text(), 'argv': argv})
            call('acc_start_task', {'task_id': task['id']})
            return wait(task)

        try:
            implemented = run_task('Implementer validation', [sys.executable, '-m', 'unittest', 'discover', '-s', str(project), '-v'])
            assert implemented['status'] == 'awaiting_review'
            reviewed = run_task('Independent reviewer validation', [sys.executable, str(HERE/'test_independent.py'), str(project)])
            assert reviewed['status'] == 'awaiting_review'
            old_run = implemented['run_id']
            call('acc_start_task', {'task_id': implemented['id']})
            implemented = wait(implemented)
            stale = call('acc_record_review', {'task_id': implemented['id'], 'revision': implemented['revision'],
                         'run_id': old_run, 'reference': manifest['snapshot_id'], 'message': 'Stale approval probe'}, expected_error=True)
            wrong_revision = call('acc_record_review', {'task_id': implemented['id'], 'revision': 0,
                         'run_id': implemented['run_id'], 'reference': manifest['snapshot_id'], 'message': 'Wrong revision probe'}, expected_error=True)
            call('acc_report', {'task_id': implemented['id'], 'reference': manifest['snapshot_id'],
                               'message': 'Replay of the preserved independent-agent review: '+(HERE/'REVIEW.md').read_text()})
            accepted = call('acc_record_review', {'task_id': implemented['id'], 'revision': implemented['revision'],
                         'run_id': implemented['run_id'], 'reference': manifest['snapshot_id'],
                         'message': 'Preserved reviewer approval, fresh independent validation, and manifest hash verification.'})
            assert accepted['status'] == 'accepted'
            negative = root/'negative'
            negative.mkdir()
            source = (project/'job_progress.py').read_text()
            needle = 'all(available_staff.get(level, 0) >= count for level, count in required_staff.items())'
            assert needle in source
            wrong = 'all(sum(n for candidate, n in available_staff.items() if candidate >= level) >= count for level, count in required_staff.items())'
            (negative/'job_progress.py').write_text(source.replace(needle, wrong))
            rejected = run_task('Intentional defect: higher-level substitution', [sys.executable, str(HERE/'test_independent.py'), str(negative)])
            assert rejected['status'] == 'failed' and rejected['exit_code'] != 0
            failed_approval = call('acc_record_review', {'task_id': rejected['id'], 'revision': rejected['revision'],
                         'run_id': rejected['run_id'], 'reference': 'intentionally-broken-snapshot',
                         'message': 'Attempt to accept a failed run'}, expected_error=True)
            for name, digest in manifest['files'].items():
                assert hashlib.sha256((project/name).read_bytes()).hexdigest() == digest
            return {'snapshot_id': manifest['snapshot_id'], 'implementer_checks': implemented['exit_code'],
                    'reviewer_checks': reviewed['exit_code'], 'accepted_status': accepted['status'],
                    'stale_run_rejected': stale, 'wrong_revision_rejected': wrong_revision,
                    'negative_control_status': rejected['status'], 'negative_control_exit': rejected['exit_code'],
                    'failed_run_approval_rejected': failed_approval,
                    'snapshot_unchanged': True, 'stdio_tool_calls': dict(calls),
                    'limitation': 'Replays real-agent artifacts. Agent delegation, snapshot creation, and routing are external to ACC.'}
        finally:
            coordinator.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = replay()
    if args.output:
        args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
