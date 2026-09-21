"""agent-3d-studio worker loop; a fake coding-agent CLI stands in for real model calls."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest

from acc.core import Coordinator
from acc.server import Server
from test_acc import eventually


ROOT = Path(__file__).resolve().parents[1]

# Stands in for a real coding-agent CLI: reads the prompt from stdin, finds the result-file
# path it was told to write to, and reports either a produced artifact or a failure. Also
# records its own argv so tests can assert on how --extra-args was forwarded.
FAKE_AGENT = r'''
import json, pathlib, re, sys

pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))
prompt = sys.stdin.read()
result_file = pathlib.Path(re.search(r'to (\S+\.json)', prompt).group(1))
if 'FAIL' in prompt:
    result_file.write_text(json.dumps({'status': 'failed', 'error': 'could not reconstruct mesh'}))
else:
    out = pathlib.Path('assets'); out.mkdir(exist_ok=True)
    (out / 'tank.glb').write_bytes(b'glb-bytes')
    result_file.write_text(json.dumps({
        'status': 'succeeded', 'summary': 'built tank from reference',
        'artifacts': [{'kind': 'model', 'uri': 'assets/tank.glb', 'metadata': {'format': 'glb'}}]}))
print(json.dumps({'type': 'result', 'is_error': False}))
'''

# Stands in for a coding-agent CLI that crashes before writing anything.
CRASH_AGENT = r'''
import sys
sys.stdin.read()
sys.exit(3)
'''

# Stands in for a coding-agent CLI that's still working when a cancellation lands.
SLEEPY_AGENT = r'''
import sys, time
sys.stdin.read()
time.sleep(120)
'''


class Agent3DTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps(
            {'integrations': {'providers': [{'id': 'agent-3d-studio', 'enabled': True}]}}))
        self.c = Coordinator(self.project, self.state, self.config)
        self.token_file = self.root / 'token'
        self.token_file.write_text('test-token')
        self.server = Server(('127.0.0.1', 0), self.c, 'test-token')
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.fake_agent = self.root / 'fake-claude'
        self.fake_agent.write_text('#!' + sys.executable + '\n' + FAKE_AGENT)
        self.fake_agent.chmod(0o755)
        self.crash_agent = self.root / 'crash-claude'
        self.crash_agent.write_text('#!' + sys.executable + '\n' + CRASH_AGENT)
        self.crash_agent.chmod(0o755)
        self.sleepy_agent = self.root / 'sleepy-claude'
        self.sleepy_agent.write_text('#!' + sys.executable + '\n' + SLEEPY_AGENT)
        self.sleepy_agent.chmod(0o755)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join()
        self.c.close()
        self.tmp.cleanup()

    def submit(self, **input_overrides):
        return self.c.integrations.submit({
            'capability': 'mesh.generate', 'provider': 'agent-3d-studio',
            'input': {'output_dir': 'assets', 'reference_image': 'concept.png',
                      'subject': 'tank', **input_overrides}})

    def run_worker(self):
        return subprocess.run(
            [sys.executable, str(ROOT / 'acc' / 'agent3d.py'), '--url', self.url,
             '--token-file', str(self.token_file), '--workspace', str(self.project),
             '--executable', str(self.fake_agent), '--once'],
            capture_output=True, text=True)

    def artifacts_for(self, job_id):
        with self.c.store.connect() as db:
            rows = db.execute('SELECT data FROM integration_artifacts WHERE job_id=?',
                               (job_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def test_succeeded_job_reports_hashed_artifact_and_cleans_up_manifest(self):
        job = self.submit()
        run = self.run_worker()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        finished = self.c.integrations.snapshot()['jobs'][0]
        self.assertEqual(finished['id'], job['id'])
        self.assertEqual(finished['status'], 'succeeded')
        self.assertEqual(finished['result']['summary'], 'built tank from reference')
        artifact = self.artifacts_for(job['id'])[0]
        self.assertEqual(artifact['uri'], 'assets/tank.glb')
        self.assertEqual(artifact['sha256'], hashlib.sha256(b'glb-bytes').hexdigest())
        leftovers = list(self.project.glob('.acc-agent3d-*.json'))
        self.assertEqual(leftovers, [])

    def test_agent_reported_failure_is_recorded_without_artifacts(self):
        job = self.submit(subject='FAIL')
        run = self.run_worker()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        finished = self.c.integrations.snapshot()['jobs'][0]
        self.assertEqual(finished['status'], 'failed')
        self.assertEqual(finished['last_error'], 'could not reconstruct mesh')
        self.assertEqual(self.artifacts_for(job['id']), [])

    def test_crashed_agent_process_fails_the_job(self):
        job = self.submit()
        run = subprocess.run(
            [sys.executable, str(ROOT / 'acc' / 'agent3d.py'), '--url', self.url,
             '--token-file', str(self.token_file), '--workspace', str(self.project),
             '--executable', str(self.crash_agent), '--once'],
            capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        finished = self.c.integrations.snapshot()['jobs'][0]
        self.assertEqual(finished['id'], job['id'])
        self.assertEqual(finished['status'], 'failed')
        self.assertIn('exited 3', finished['last_error'])

    def test_cancellation_request_kills_the_agents_own_process(self):
        job = self.submit()
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'acc' / 'agent3d.py'), '--url', self.url,
             '--token-file', str(self.token_file), '--workspace', str(self.project),
             '--executable', str(self.sleepy_agent), '--lease-seconds', '15', '--once'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            eventually(lambda: next((j for j in self.c.integrations.snapshot()['jobs']
                                     if j['id'] == job['id'] and j['status'] == 'running'), None),
                       timeout=10)
            self.c.integrations.request_cancel(job['id'])
            out, _ = proc.communicate(timeout=30)
        except Exception:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, 0, out)
        finished = self.c.integrations.snapshot()['jobs'][0]
        self.assertEqual(finished['id'], job['id'])
        self.assertEqual(finished['status'], 'failed')
        self.assertIn('Cancelled by operator', finished['last_error'])

    def test_no_queued_job_is_a_clean_noop(self):
        run = self.run_worker()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.c.integrations.snapshot()['jobs'], [])

    def test_extra_args_with_embedded_flags_reach_the_agent_cli(self):
        self.submit()
        run = subprocess.run(
            [sys.executable, str(ROOT / 'acc' / 'agent3d.py'), '--url', self.url,
             '--token-file', str(self.token_file), '--workspace', str(self.project),
             '--executable', str(self.fake_agent), '--once', '--extra-args',
             "--allowedTools 'Bash(python3 forge/*.py *)' Write Edit"],
            capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        argv = json.loads((self.project / 'argv.json').read_text())
        self.assertEqual(argv, ['-p', '--output-format', 'json', '--permission-mode',
                                 'acceptEdits', '--allowedTools',
                                 'Bash(python3 forge/*.py *)', 'Write', 'Edit'])


if __name__ == '__main__':
    unittest.main()
