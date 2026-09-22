"""acc/antigravity.py: unit tests for its own subprocess handling and defensive empty-response
checks (the real, currently-open agy upstream bugs this driver guards against), plus a full
workflow cycle through a fake agy executable, mirroring test_deepseek_harness_adapter.py's rigor."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from acc import antigravity
from acc.core import Coordinator
from test_acc import eventually


# Stands in for the real agy CLI's headless --output-format json contract: one JSON object on
# stdout, {"status": "SUCCESS", "response": <text>, ...} or {"status": "ERROR", ...}.
FAKE_AGY = r'''
import json, pathlib, sys
prompt = sys.argv[sys.argv.index('-p') + 1]
packet = json.loads(prompt[prompt.index('{'):])
w = packet['workflow']
stage = w['stage']
r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
     'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake agy'}
if stage == 'implement':
    r['checks'] = ['fixture check']
    pathlib.Path(packet['project'], 'answer.txt').write_text('42')
elif stage == 'review':
    assert pathlib.Path(packet['project'], 'answer.txt').read_text() == '42'
    r.update(verdict='approve', findings=[], checks=['fixture check'])
elif stage == 'coordinate':
    review = w['review_result']
    r['action'] = 'request_review' if review is None else 'accept'
# A leading reasoning trace real models sometimes emit despite instructions not to.
text = '<think>reasoning about ' + stage + '</think>' + json.dumps(r)
print(json.dumps({'status': 'SUCCESS', 'response': text, 'conversation_id': 'c1',
                   'num_turns': 1, 'duration_seconds': 0.1, 'usage': {}}))
'''

CRASH_AGY = r'''
import sys
print('AGY_ERROR: {"status": "RESOURCE_EXHAUSTED", "code": 429, "retryable": true}', file=sys.stderr)
sys.exit(3)
'''


class AntigravityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.fake = self.root / 'fake-agy'
        self.fake.write_text('#!' + sys.executable + '\n' + FAKE_AGY)
        self.fake.chmod(0o755)
        self.crash = self.root / 'crash-agy'
        self.crash.write_text('#!' + sys.executable + '\n' + CRASH_AGY)
        self.crash.chmod(0o755)
        agents = [{'id': a, 'name': a, 'driver': 'antigravity', 'executable': str(self.fake)}
                  for a in ('builder', 'reviewer', 'coordinator')]
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps({'agents': agents}))
        self.c = Coordinator(self.project, self.state, self.config)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def done(self, task_id):
        return eventually(lambda: self.c.store.get(task_id) if
                          self.c.store.get(task_id).get('workflow', {}).get('phase') in
                          ('complete', 'held') else None, timeout=12)

    def test_full_cycle_through_fake_agy_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise antigravity', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']],
                          ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'antigravity-query.txt').exists() for r in task['runs']))

    def test_crashed_agy_surfaces_agy_error_and_holds_the_task(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'builder', 'name': 'builder', 'driver': 'antigravity', 'executable': str(self.crash)},
            {'id': 'reviewer', 'name': 'reviewer', 'driver': 'antigravity', 'executable': str(self.fake)},
            {'id': 'coordinator', 'name': 'coordinator', 'driver': 'antigravity', 'executable': str(self.fake)},
        ]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        task = self.c.create({'title': 'Exercise antigravity crash', 'instruction': 'x'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'paused')

    def test_unconfigured_executable_is_unavailable(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'ghost', 'name': 'ghost', 'driver': 'antigravity', 'executable': '/no/such/agy-binary'}]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['ghost']['available'])


class AntigravityUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.packet_path = self.root / 'task.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write_packet(self):
        packet = {'task': {'id': 't1'}, 'project': str(self.root),
                  'result_file': str(self.root / 'result.json'), 'workflow': {
                      'stage': 'implement', 'round': 1, 'max_rounds': 3, 'task_id': 't1',
                      'run_id': 'r1', 'revision': 1, 'snapshot_id': None, 'implementation': None,
                      'review_result': None, 'history': [], 'allowed_actions': [],
                      'result_contract': {}}}
        self.packet_path.write_text(json.dumps(packet))
        return packet

    def args(self, executable='agy', model=None, api_key_file=None, api_key_env='ANTIGRAVITY_API_KEY'):
        return argparse.Namespace(packet=str(self.packet_path), executable=executable, model=model,
                                   api_key_file=api_key_file, api_key_env=api_key_env)

    def fake_proc(self, stdout='', stderr='', code=0):
        proc = MagicMock()
        proc.communicate.return_value = (stdout, stderr)
        proc.returncode = code
        return proc

    def test_broken_communicate_kills_the_subprocess_before_reraising(self):
        self.write_packet()
        proc = MagicMock()
        proc.communicate.side_effect = OSError('pipe broke')
        with patch('acc.antigravity.subprocess.Popen', return_value=proc):
            with self.assertRaises(OSError):
                antigravity.run(self.args())
        proc.kill.assert_called_once()

    def test_nonzero_exit_surfaces_the_agy_error_line(self):
        self.write_packet()
        proc = self.fake_proc(stderr='some noise\nAGY_ERROR: {"status": "RESOURCE_EXHAUSTED"}\n', code=3)
        with patch('acc.antigravity.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError) as ctx:
                antigravity.run(self.args())
        self.assertIn('AGY_ERROR', str(ctx.exception))
        self.assertIn('RESOURCE_EXHAUSTED', str(ctx.exception))

    def test_status_not_success_is_rejected(self):
        self.write_packet()
        proc = self.fake_proc(stdout=json.dumps({'status': 'ERROR', 'response': None}))
        with patch('acc.antigravity.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError) as ctx:
                antigravity.run(self.args())
        self.assertIn('status', str(ctx.exception))

    def test_success_with_empty_response_is_rejected(self):
        # The real, currently-open upstream bug class (agy issues #840, #794) this driver guards
        # against: exit 0, status SUCCESS, but no actual content.
        self.write_packet()
        proc = self.fake_proc(stdout=json.dumps({'status': 'SUCCESS', 'response': ''}))
        with patch('acc.antigravity.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError) as ctx:
                antigravity.run(self.args())
        self.assertIn('no response text', str(ctx.exception))

    def test_non_json_stdout_is_rejected(self):
        self.write_packet()
        proc = self.fake_proc(stdout='not json at all')
        with patch('acc.antigravity.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError):
                antigravity.run(self.args())

    def test_spawns_with_a_credential_filtered_environment(self):
        self.write_packet()
        proc = self.fake_proc(stdout=json.dumps({'status': 'SUCCESS', 'response': '{}'}))
        with patch.dict('os.environ', {'GOOGLE_API_KEY': 'shh'}, clear=False), \
                patch('acc.antigravity.subprocess.Popen', return_value=proc) as popen:
            code = antigravity.run(self.args())
        self.assertEqual(code, 0)
        self.assertNotIn('GOOGLE_API_KEY', popen.call_args.kwargs['env'])

    def test_api_key_file_is_exported_under_the_configured_env_name(self):
        self.write_packet()
        key_file = self.root / 'agy.key'
        key_file.write_text('the-real-key')
        proc = self.fake_proc(stdout=json.dumps({'status': 'SUCCESS', 'response': '{}'}))
        with patch('acc.antigravity.subprocess.Popen', return_value=proc) as popen:
            antigravity.run(self.args(api_key_file=str(key_file), api_key_env='MY_CUSTOM_KEY'))
        self.assertEqual(popen.call_args.kwargs['env']['MY_CUSTOM_KEY'], 'the-real-key')

    def test_empty_api_key_file_is_rejected(self):
        self.write_packet()
        key_file = self.root / 'agy.key'
        key_file.write_text('')
        with self.assertRaises(ValueError) as ctx:
            antigravity.run(self.args(api_key_file=str(key_file)))
        self.assertIn('empty', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
