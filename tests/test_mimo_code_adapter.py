"""acc/mimo_code.py: unit tests for its own NDJSON-event handling and subprocess supervision,
plus a full workflow cycle through a fake mimo executable, mirroring test_hermes_adapter.py's
rigor for the other event-stream-based CLI wrapper."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from acc import mimo_code
from acc.core import Coordinator
from test_acc import eventually


# Stands in for the real mimo CLI's --format json contract as inferred from upstream OpenCode's
# real source (anomalyco/opencode packages/opencode/src/cli/cmd/run.ts): one JSON object per
# line, {"type", "sessionID", ...}, with the answer split across two "text"-type events to
# exercise delta-concatenation (the driver's own working assumption about the streaming shape).
FAKE_MIMO = r'''
import json, pathlib, sys
prompt = sys.argv[-1]
packet = json.loads(prompt[prompt.index('{'):])
w = packet['workflow']
stage = w['stage']
r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
     'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake mimo'}
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
half = len(text) // 2
print(json.dumps({'type': 'step_start', 'sessionID': 's1'}))
print(json.dumps({'type': 'text', 'sessionID': 's1', 'text': text[:half]}))
print(json.dumps({'type': 'text', 'sessionID': 's1', 'text': text[half:]}))
print(json.dumps({'type': 'step_finish', 'sessionID': 's1'}))
'''

CRASH_MIMO = r'''
import json
print(json.dumps({'type': 'error', 'error': 'boom'}))
raise SystemExit(1)
'''


class MimoCodeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.fake = self.root / 'fake-mimo'
        self.fake.write_text('#!' + sys.executable + '\n' + FAKE_MIMO)
        self.fake.chmod(0o755)
        self.crash = self.root / 'crash-mimo'
        self.crash.write_text('#!' + sys.executable + '\n' + CRASH_MIMO)
        self.crash.chmod(0o755)
        agents = [{'id': a, 'name': a, 'driver': 'mimo-code', 'executable': str(self.fake)}
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

    def test_full_cycle_through_fake_mimo_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise mimo-code', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']],
                          ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'mimo-code-query.txt').exists() for r in task['runs']))

    def test_crashed_mimo_surfaces_error_event_and_holds_the_task(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'builder', 'name': 'builder', 'driver': 'mimo-code', 'executable': str(self.crash)},
            {'id': 'reviewer', 'name': 'reviewer', 'driver': 'mimo-code', 'executable': str(self.fake)},
            {'id': 'coordinator', 'name': 'coordinator', 'driver': 'mimo-code', 'executable': str(self.fake)},
        ]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        task = self.c.create({'title': 'Exercise mimo-code crash', 'instruction': 'x'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'paused')

    def test_unconfigured_executable_is_unavailable(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'ghost', 'name': 'ghost', 'driver': 'mimo-code', 'executable': '/no/such/mimo-binary'}]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['ghost']['available'])


def _lines(*items):
    """A generator, not a plain list_iterator: run() closes proc.stdout when done, and only
    generators (not list_iterator objects) have a native .close() method."""
    yield from items


class MimoCodeUnitTests(unittest.TestCase):
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

    def args(self, executable='mimo', model=None):
        return argparse.Namespace(packet=str(self.packet_path), executable=executable, model=model)

    def test_broken_stream_kills_the_subprocess_before_reraising(self):
        self.write_packet()
        proc = MagicMock()

        def broken_lines():
            yield json.dumps({'type': 'step_start'}) + '\n'
            raise OSError('pipe broke')
        proc.stdout = broken_lines()
        with patch('acc.mimo_code.subprocess.Popen', return_value=proc):
            with self.assertRaises(OSError):
                mimo_code.run(self.args())
        proc.kill.assert_called_once()

    def test_nonzero_exit_is_reported_without_touching_result_file(self):
        packet = self.write_packet()
        proc = MagicMock()
        proc.stdout = _lines()
        proc.wait.return_value = 1
        with patch('acc.mimo_code.subprocess.Popen', return_value=proc):
            code = mimo_code.run(self.args())
        self.assertEqual(code, 1)
        self.assertFalse(Path(packet['result_file']).exists())

    def test_error_event_is_rejected_even_with_exit_zero(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout = _lines(json.dumps({'type': 'error', 'error': 'model overloaded'}) + '\n')
        proc.wait.return_value = 0
        with patch('acc.mimo_code.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError) as ctx:
                mimo_code.run(self.args())
        self.assertIn('model overloaded', str(ctx.exception))

    def test_success_with_no_text_events_is_rejected(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout = _lines(json.dumps({'type': 'step_finish'}) + '\n')
        proc.wait.return_value = 0
        with patch('acc.mimo_code.subprocess.Popen', return_value=proc):
            with self.assertRaises(ValueError) as ctx:
                mimo_code.run(self.args())
        self.assertIn('no text content', str(ctx.exception))

    def test_text_deltas_are_concatenated_in_order(self):
        packet = self.write_packet()
        content = json.dumps({'task_id': 't1', 'run_id': 'r1', 'revision': 1,
                               'snapshot_id': None, 'summary': 'done', 'checks': []})
        proc = MagicMock()
        proc.stdout = _lines(
            json.dumps({'type': 'text', 'text': content[:5]}) + '\n',
            json.dumps({'type': 'text', 'text': content[5:]}) + '\n',
        )
        proc.wait.return_value = 0
        with patch('acc.mimo_code.subprocess.Popen', return_value=proc):
            code = mimo_code.run(self.args())
        self.assertEqual(code, 0)
        result = json.loads(Path(packet['result_file']).read_text())
        self.assertEqual(result['summary'], 'done')

    def test_spawns_with_a_credential_filtered_environment(self):
        self.write_packet()
        proc = MagicMock()
        proc.stdout = _lines(json.dumps({'type': 'text', 'text': '{}'}) + '\n')
        proc.wait.return_value = 0
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'shh'}, clear=False), \
                patch('acc.mimo_code.subprocess.Popen', return_value=proc) as popen:
            code = mimo_code.run(self.args())
        self.assertEqual(code, 0)
        self.assertNotIn('GITHUB_TOKEN', popen.call_args.kwargs['env'])


if __name__ == '__main__':
    unittest.main()
