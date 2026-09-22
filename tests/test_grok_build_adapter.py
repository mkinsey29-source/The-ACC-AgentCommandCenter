"""acc/grok_build.py: unit tests for its own subprocess handling and defensive empty-response
check, plus a full workflow cycle through a fake grok executable, mirroring
test_claude_code_adapter.py's and test_antigravity_adapter.py's rigor."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from acc import grok_build
from acc.core import Coordinator
from test_acc import eventually


# Stands in for the real grok CLI's headless --output-format json contract: one JSON object on
# stdout with at least a "text" field carrying the response.
FAKE_GROK = r'''
import json, pathlib, sys
prompt = sys.argv[sys.argv.index('-p') + 1]
packet = json.loads(prompt[prompt.index('{'):])
w = packet['workflow']
stage = w['stage']
r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
     'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake grok build'}
if stage == 'implement':
    r['checks'] = ['fixture check']
    pathlib.Path(packet['project'], 'answer.txt').write_text('42')
elif stage == 'review':
    assert pathlib.Path(packet['project'], 'answer.txt').read_text() == '42'
    r.update(verdict='approve', findings=[], checks=['fixture check'])
elif stage == 'coordinate':
    review = w['review_result']
    r['action'] = 'request_review' if review is None else 'accept'
print(json.dumps({'text': json.dumps(r), 'stopReason': 'stop', 'sessionId': 's1'}))
'''

CRASH_GROK = r'''
import sys
sys.exit(1)
'''


class GrokBuildWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.fake = self.root / 'fake-grok'
        self.fake.write_text('#!' + sys.executable + '\n' + FAKE_GROK)
        self.fake.chmod(0o755)
        self.crash = self.root / 'crash-grok'
        self.crash.write_text('#!' + sys.executable + '\n' + CRASH_GROK)
        self.crash.chmod(0o755)
        agents = [{'id': a, 'name': a, 'driver': 'grok-build', 'executable': str(self.fake)}
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

    def test_full_cycle_through_fake_grok_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise grok-build', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']],
                          ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'grok-build-query.txt').exists() for r in task['runs']))

    def test_crashed_grok_holds_the_task(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'builder', 'name': 'builder', 'driver': 'grok-build', 'executable': str(self.crash)},
            {'id': 'reviewer', 'name': 'reviewer', 'driver': 'grok-build', 'executable': str(self.fake)},
            {'id': 'coordinator', 'name': 'coordinator', 'driver': 'grok-build', 'executable': str(self.fake)},
        ]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        task = self.c.create({'title': 'Exercise grok-build crash', 'instruction': 'x'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'paused')

    def test_unconfigured_executable_is_unavailable(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'ghost', 'name': 'ghost', 'driver': 'grok-build', 'executable': '/no/such/grok-binary'}]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['ghost']['available'])


class GrokBuildUnitTests(unittest.TestCase):
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

    def args(self, executable='grok', model=None, api_key_file=None):
        return argparse.Namespace(packet=str(self.packet_path), executable=executable,
                                   model=model, api_key_file=api_key_file)

    def test_broken_stdout_read_kills_the_subprocess_before_reraising(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout.read.side_effect = OSError('pipe broke')
        with patch('acc.grok_build.subprocess.Popen', return_value=proc):
            with self.assertRaises(OSError):
                grok_build.run(self.args())
        proc.kill.assert_called_once()

    def test_nonzero_exit_is_reported_without_touching_result_file(self):
        packet = self.write_packet()
        proc = MagicMock()
        proc.stdout.read.return_value = ''
        proc.wait.return_value = 1
        with patch('acc.grok_build.subprocess.Popen', return_value=proc):
            code = grok_build.run(self.args())
        self.assertEqual(code, 1)
        self.assertFalse(Path(packet['result_file']).exists())

    def test_success_with_empty_text_is_rejected(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout.read.return_value = json.dumps({'text': '', 'stopReason': 'stop'})
        proc.wait.return_value = 0
        with patch('acc.grok_build.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError) as ctx:
                grok_build.run(self.args())
        self.assertIn('no text content', str(ctx.exception))

    def test_non_json_stdout_is_rejected(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout.read.return_value = 'not json at all'
        proc.wait.return_value = 0
        with patch('acc.grok_build.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError):
                grok_build.run(self.args())

    def test_spawns_with_a_credential_filtered_environment(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout.read.return_value = json.dumps({'text': '{}'})
        proc.wait.return_value = 0
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'shh'}, clear=False), \
                patch('acc.grok_build.subprocess.Popen', return_value=proc) as popen:
            code = grok_build.run(self.args())
        self.assertEqual(code, 0)
        self.assertNotIn('GITHUB_TOKEN', popen.call_args.kwargs['env'])

    def test_api_key_file_is_exported_as_xai_api_key(self):
        self.write_packet()
        key_file = self.root / 'grok.key'
        key_file.write_text('the-real-key')
        proc = MagicMock()
        proc.stdout.read.return_value = json.dumps({'text': '{}'})
        proc.wait.return_value = 0
        with patch('acc.grok_build.subprocess.Popen', return_value=proc) as popen:
            grok_build.run(self.args(api_key_file=str(key_file)))
        self.assertEqual(popen.call_args.kwargs['env']['XAI_API_KEY'], 'the-real-key')

    def test_tilde_api_key_file_is_expanded(self):
        self.write_packet()
        home = self.root / 'home'
        home.mkdir()
        (home / 'grok.key').write_text('expanded-key')
        proc = MagicMock()
        proc.stdout.read.return_value = json.dumps({'text': '{}'})
        proc.wait.return_value = 0
        with patch.dict('os.environ', {'HOME': str(home)}), \
                patch('acc.grok_build.subprocess.Popen', return_value=proc) as popen:
            grok_build.run(self.args(api_key_file='~/grok.key'))
        self.assertEqual(popen.call_args.kwargs['env']['XAI_API_KEY'], 'expanded-key')


if __name__ == '__main__':
    unittest.main()
