"""acc/deepastra.py: unit tests for the orphaned-process fix and JSONL result recovery, plus a
full workflow cycle through a fake launch.py, mirroring test_deepseek_harness_adapter.py's rigor
for the dsh adapter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from acc import deepastra
from acc.core import Coordinator
from test_acc import eventually


ROOT = Path(__file__).resolve().parents[1]

# Stands in for the real launch.py: reads the prompt file, writes a Codex-shaped JSONL log
# (an item.completed/agent_message event carrying the answer, per the real schema confirmed
# against openai/codex's codex-rs/exec/src/exec_events.rs) and a status file, mirroring what the
# real tool actually produces rather than an assumed simpler contract.
FAKE_LAUNCH = r'''
import json, pathlib, sys

def opt(name):
    return sys.argv[sys.argv.index(name) + 1]

assert sys.argv[1] == 'exec'
prompt = pathlib.Path(opt('--prompt-file')).read_text()
packet = json.loads(prompt[prompt.index('{'):])
w = packet['workflow']
stage = w['stage']
r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
     'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake deepastra'}
if stage == 'implement':
    r['checks'] = ['fixture check']
    pathlib.Path(packet['project'], 'answer.txt').write_text('42')
elif stage == 'review':
    assert pathlib.Path(packet['project'], 'answer.txt').read_text() == '42'
    r.update(verdict='approve', findings=[], checks=['fixture check'])
elif stage == 'coordinate':
    review = w['review_result']
    r['action'] = 'request_review' if review is None else 'accept'
# A leading reasoning trace real DeepSeek emits despite instructions not to.
text = '<think>reasoning about ' + stage + '</think>' + json.dumps(r)
log_dir = pathlib.Path(opt('--log-dir'))
lines = [json.dumps({'type': 'turn.completed', 'usage': {}}),
         json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}})]
(log_dir / 'run.jsonl').write_text('\n'.join(lines) + '\n')
pathlib.Path(opt('--status-file')).write_text(json.dumps({'state': 'completed', 'exit_code': 0}))
'''

CRASH_LAUNCH = r'''
import sys
sys.exit(1)
'''

# A real detached grandchild, spawned with start_new_session=True exactly like launch.py spawns
# codex, so the orphan-cleanup fix is proven against genuine OS process-group semantics rather
# than a mocked stand-in.
SLEEPER = r'''
import pathlib, sys, time
marker = pathlib.Path(sys.argv[1])
while True:
    marker.write_text(str(time.time()))
    time.sleep(0.05)
'''

LAUNCHER_WITH_DETACHED_CHILD = r'''
import pathlib, subprocess, sys, time
marker, sleeper = sys.argv[1], sys.argv[2]
proc = subprocess.Popen([sys.executable, sleeper, marker], start_new_session=True)
pathlib.Path(marker + '.child-pid').write_text(str(proc.pid))
time.sleep(30)
'''


class DeepAstraWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.fake = self.root / 'fake-launch.py'
        self.fake.write_text(FAKE_LAUNCH)
        self.crash = self.root / 'crash-launch.py'
        self.crash.write_text(CRASH_LAUNCH)
        agents = [{'id': a, 'name': a, 'driver': 'deepastra', 'launcher': str(self.fake)}
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

    def test_full_cycle_through_fake_launch_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise DeepAstra', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']],
                          ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'deepastra-query.txt').exists() for r in task['runs']))

    def test_crashed_launcher_holds_the_task(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'builder', 'name': 'builder', 'driver': 'deepastra', 'launcher': str(self.crash)},
            {'id': 'reviewer', 'name': 'reviewer', 'driver': 'deepastra', 'launcher': str(self.fake)},
            {'id': 'coordinator', 'name': 'coordinator', 'driver': 'deepastra', 'launcher': str(self.fake)},
        ]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        task = self.c.create({'title': 'Exercise DeepAstra crash', 'instruction': 'x'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'paused')

    def test_unconfigured_launcher_is_unavailable(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'ghost', 'name': 'ghost', 'driver': 'deepastra', 'launcher': '/no/such/launch.py'}]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['ghost']['available'])


class DeepAstraOrphanCleanupTests(unittest.TestCase):
    """Proves the fix for launch.py's real gap: it spawns its codex child with
    start_new_session=True, detaching it into its own process group, so a killpg on our own
    process group (what ACC's stop_tree() sends) never reaches it. _kill_orphans must reach it
    another way -- verified here against a real detached OS process, not a mock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_kill_orphans_reaches_a_detached_grandchild(self):
        marker = self.root / 'heartbeat'
        sleeper = self.root / 'sleeper.py'
        sleeper.write_text(SLEEPER)
        launcher = self.root / 'launcher.py'
        launcher.write_text(LAUNCHER_WITH_DETACHED_CHILD)
        proc = subprocess.Popen([sys.executable, str(launcher), str(marker), str(sleeper)])
        try:
            child_pid_file = Path(str(marker) + '.child-pid')
            eventually(lambda: True if child_pid_file.exists() else None)
            eventually(lambda: True if marker.exists() else None)
            before = marker.read_text()
            time.sleep(0.2)
            self.assertNotEqual(before, marker.read_text(), 'sleeper should still be ticking')

            deepastra._kill_orphans(proc.pid)

            time.sleep(0.3)
            stale = marker.read_text()
            time.sleep(0.3)
            # The killed sleeper is a zombie until its still-running parent (the fake launcher,
            # blocked in its own sleep(30)) reaps it, so os.kill(child_pid, 0) would still say it
            # "exists" -- the heartbeat going stale is the real, reliable signal that it died.
            self.assertEqual(stale, marker.read_text(), 'sleeper should have stopped ticking')
        finally:
            proc.kill()
            proc.wait()

    def test_children_of_a_nonexistent_pid_is_empty(self):
        self.assertEqual(deepastra._children(999999), [])


class DeepAstraUnitTests(unittest.TestCase):
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

    def args(self, launcher='launch.py'):
        import argparse
        return argparse.Namespace(packet=str(self.packet_path), launcher=launcher,
                                   provider='deepseek', key_file=None, timeout_seconds=900)

    def test_tilde_launcher_path_is_expanded(self):
        self.write_packet()
        home = self.root / 'home'
        home.mkdir()
        (home / 'launch.py').write_text('# stand-in launcher, never actually executed')
        proc = MagicMock()
        proc.pid = 424244
        proc.wait.return_value = 1
        with patch.dict('os.environ', {'HOME': str(home)}), \
                patch('acc.deepastra.subprocess.Popen', return_value=proc) as popen:
            deepastra.run(self.args(launcher='~/launch.py'))
        self.assertEqual(popen.call_args.args[0][1], str(home / 'launch.py'))

    def test_broken_wait_kills_the_subprocess_before_reraising(self):
        self.write_packet()
        proc = MagicMock()
        proc.pid = 424242
        proc.wait.side_effect = OSError('wait failed')
        with patch('acc.deepastra.subprocess.Popen', return_value=proc), \
                patch('acc.deepastra._kill_orphans') as kill_orphans:
            with self.assertRaises(OSError):
                deepastra.run(self.args())
        kill_orphans.assert_called_once_with(424242)
        proc.kill.assert_called_once()

    def test_nonzero_exit_is_reported_without_touching_result_file(self):
        packet = self.write_packet()
        proc = MagicMock()
        proc.pid = 424243
        proc.wait.return_value = 1
        with patch('acc.deepastra.subprocess.Popen', return_value=proc):
            code = deepastra.run(self.args())
        self.assertEqual(code, 1)
        self.assertFalse(Path(packet['result_file']).exists())

    def test_spawns_with_a_credential_filtered_environment(self):
        self.write_packet()
        proc = MagicMock()
        proc.pid = 424244
        proc.wait.return_value = 1
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'shh'}, clear=False), \
                patch('acc.deepastra.subprocess.Popen', return_value=proc) as popen:
            deepastra.run(self.args())
        self.assertNotIn('DEEPSEEK_API_KEY', popen.call_args.kwargs['env'])

    def test_last_agent_message_requires_a_log(self):
        with self.assertRaises(ValueError):
            deepastra._last_agent_message(self.root / 'no-such-dir')

    def test_last_agent_message_requires_an_agent_message_event(self):
        log_dir = self.root / 'logs'
        log_dir.mkdir()
        (log_dir / 'run.jsonl').write_text(json.dumps({'type': 'turn.completed', 'usage': {}}) + '\n')
        with self.assertRaises(ValueError):
            deepastra._last_agent_message(log_dir)

    def test_last_agent_message_takes_the_last_matching_event(self):
        log_dir = self.root / 'logs'
        log_dir.mkdir()
        lines = [json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'first'}}),
                 json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'last'}})]
        (log_dir / 'run.jsonl').write_text('\n'.join(lines) + '\n')
        self.assertEqual(deepastra._last_agent_message(log_dir), 'last')


if __name__ == '__main__':
    unittest.main()
