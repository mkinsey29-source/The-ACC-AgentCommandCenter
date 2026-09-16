"""Run ACC with a human/orchestrator-mediated live-agent relay, never canned responses."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from acc.core import Coordinator
from acc.server import Server
from acc.bridge import dispatch

REQUEST = '''Please build a small Python module staffing.py with allocate_staff(requirements, available, training).
Each input maps positive integer engineer levels to nonnegative integer counts; reject booleans, noninteger keys/counts, zero/negative levels, negative counts, and non-dict inputs with ValueError.
Available includes all engineers at the current level, including those in training. Training removes them from working capacity immediately; reject training counts above available at any level.
Only exact levels can fill a requirement; higher levels cannot substitute. Return {"can_run": bool, "assigned": mapping, "shortages": mapping}. If all requirements are met, assigned equals the nonzero requirements and shortages is empty. If any required level is short, the job stops completely: assigned is empty and shortages lists each missing count at the required level. Empty/zero requirements can run with no assignments. Do not mutate any input.
Include readable unit tests for success, exact-level shortages, training interruptions, invalid input, and nonmutation. Use Python standard library only. Have a different agent independently review the result. Do not publish this exercise as game code.'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    root = Path(args.run_dir).resolve()
    project = root / 'project'; project.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    queue = root / 'queue'; queue.mkdir()
    config = root / 'agents.json'
    adapters = [{'id': role, 'name': role, 'local': True,
                 'argv': [sys.executable, str(Path(__file__).with_name('relay.py')), '--packet', '{prompt_file}', '--queue', str(queue), '--role', role]}
                for role in ('live-planner', 'live-builder', 'live-reviewer')]
    config.write_text(json.dumps({'agents': adapters}))
    c = Coordinator(project, root / 'state', config)
    server = Server(('127.0.0.1', 0), c, 'isolated-live-exercise')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    def call(name, arguments):
        response = dispatch({'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}, url, 'isolated-live-exercise')['result']
        if response['isError']:
            raise RuntimeError(response['content'][0]['text'])
        return json.loads(response['content'][0]['text'])
    try:
        c.conversation.configure({'enabled': True, 'local_agent': 'live-planner', 'mode': 'offline',
                                  'workflow': {'implementer': 'live-builder', 'reviewer': 'live-reviewer', 'coordinator': 'live-planner'}})
        call('acc_conversation_send', {'id': 'live-request-1', 'source': 'live exercise', 'text': REQUEST})
        (root / 'request.txt').write_text(REQUEST)
        print(json.dumps({'run_dir': str(root), 'queue': str(queue), 'project': str(project)}), flush=True)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            state = c.snapshot()
            (root / 'progress.tmp').write_text(json.dumps(state, indent=2))
            (root / 'progress.tmp').replace(root / 'progress.json')
            if state['conversation']['held']:
                raise RuntimeError(state['conversation']['held'])
            tasks = state['tasks']
            if not tasks and state['conversation']['pending'] == 0 and c.running_task is None:
                raise RuntimeError('The live planner handled the request without creating work; inspect its response.')
            if tasks and tasks[0].get('workflow', {}).get('phase') in ('complete', 'held') and c.running_task is None:
                # Exercise a returning external orchestrator through the actual MCP/HTTP path.
                handoff = call('acc_conversation_claim', {'owner': 'Returning desktop orchestrator (live exercise)'})
                (root / 'remote-handoff.json').write_text(json.dumps(handoff, indent=2))
                call('acc_conversation_release', {'token': handoff['token']})
                (root / 'result.json').write_text(json.dumps({'state': c.snapshot(), 'events': c.store.events(0, 1000),
                      'remote_handoff': handoff, 'transport': 'MCP dispatch over authenticated loopback HTTP; live responses through filesystem relay'}, indent=2))
                print(json.dumps({'status': tasks[0]['status'], 'task_id': tasks[0]['id'], 'run_dir': str(root)}), flush=True)
                return 0 if tasks[0]['status'] == 'accepted' else 1
            time.sleep(.2)
        raise TimeoutError('Live exercise did not finish within 30 minutes.')
    finally:
        server.shutdown(); server.server_close(); thread.join()
        c.close()


if __name__ == '__main__':
    raise SystemExit(main())
