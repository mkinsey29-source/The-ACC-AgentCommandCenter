import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from acc.bridge import dispatch
from acc.core import Coordinator, Conflict, Store, git_snapshot
from acc.server import Server


def eventually(fn, timeout=7):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.04)
    raise AssertionError('Condition did not become true')


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.c = Coordinator(self.project, self.state)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def task(self, script='print("done")', **extras):
        return self.c.create({'title': 'Check', 'instruction': 'Run a concrete check',
                              'argv': [sys.executable, '-u', '-c', script], **extras})

    def complete(self, task):
        self.c.start(task['id'])
        eventually(lambda: self.c.store.get(task['id'])['status'] not in ('running', 'stopping'))
        return self.c.store.get(task['id'])

    def test_actual_output_arrives_before_completion(self):
        task = self.task('import time; print("first output",flush=True); time.sleep(.7); print("last")')
        self.c.start(task['id'])
        eventually(lambda: any(e['kind'] == 'output' for e in self.c.store.events()))
        self.assertEqual(self.c.store.get(task['id'])['status'], 'running')
        eventually(lambda: self.c.store.get(task['id'])['status'] == 'awaiting_review')
        self.assertTrue(any('last' in e['data'].get('message', '') for e in self.c.store.events()))

    def test_failure_is_not_review_ready(self):
        task = self.complete(self.task('raise RuntimeError("fixture error")'))
        self.assertEqual(task['status'], 'failed')
        self.assertNotEqual(task['exit_code'], 0)

    def test_workspace_writer_and_active_assignment_are_blocked(self):
        first, second = self.task('import time; time.sleep(10)'), self.task()
        self.c.start(first['id'])
        with self.assertRaises(Conflict):
            self.c.start(second['id'])
        with self.assertRaises(Conflict):
            self.c.assign(first['id'], 'local-command')
        with self.assertRaises(Conflict):
            self.c.revise(first['id'], 'Different rule')
        self.c.stop(first['id'])
        eventually(lambda: self.c.store.get(first['id'])['status'] == 'paused')
        self.assertTrue((self.state / 'runs' / self.c.store.get(first['id'])['run_id'] / 'handoff.json').exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group test')
    def test_stop_kills_stubborn_child_even_if_parent_exits(self):
        pid_file = self.project / 'child.pid'
        child = 'import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path('+repr(str(pid_file))+').write_text(str(os.getpid())); time.sleep(30)'
        parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(child)+']); time.sleep(30)'
        task = self.task(parent)
        self.c.start(task['id'])
        eventually(pid_file.exists)
        pid = int(pid_file.read_text())
        self.c.stop(task['id'])
        eventually(lambda: self.c.store.get(task['id'])['status'] == 'paused')
        def child_stopped():
            stat = Path(f'/proc/{pid}/stat')
            return not stat.exists() or stat.read_text().split()[2] == 'Z'
        eventually(child_stopped)

    def test_run_timeout_retains_failure(self):
        task = self.task('import time; time.sleep(20)', timeout_seconds=1)
        self.c.start(task['id'])
        eventually(lambda: self.c.store.get(task['id'])['status'] == 'failed')
        self.assertTrue(self.c.store.get(task['id'])['timed_out'])

    def test_revision_and_review_match_run(self):
        task = self.task()
        revised = self.c.revise(task['id'], 'New requirement')
        self.assertEqual(revised['revision'], 2)
        self.assertEqual(len(revised['requirements_history']), 1)
        done = self.complete(revised)
        with self.assertRaises(Conflict):
            self.c.review(task['id'], {'revision': 1, 'run_id': done['run_id'], 'message': 'Reviewed', 'reference': 'snapshot'})
        accepted = self.c.review(task['id'], {'revision': 2, 'run_id': done['run_id'], 'message': 'Reviewed fixture', 'reference': 'snapshot-1'})
        self.assertEqual(accepted['status'], 'accepted')
        self.c.revise(task['id'], 'Another requirement')
        self.assertIsNone(self.c.store.get(task['id'])['review'])

    def test_persistence_and_event_cursor(self):
        task = self.task()
        cursor = self.c.store.tail()
        self.c.report(task['id'], {'message': 'Explicit report'})
        events = [e for e in self.c.store.events(cursor) if e['task_id'] == task['id']]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['kind'], 'report')
        self.c.close()
        self.c = Coordinator(self.project, self.state)
        self.assertEqual(self.c.store.get(task['id'])['evidence'][0]['message'], 'Explicit report')

    def test_restart_does_not_assume_old_worker_stopped(self):
        task = self.task()
        task.update(status='running', pid=os.getpid())
        self.c.store.save(task, 'fixture', {'message': 'old running record'})
        self.c.close()
        self.c = Coordinator(self.project, self.state)
        self.assertTrue(self.c.recovery_required)
        self.assertEqual(self.c.store.get(task['id'])['status'], 'interrupted')
        with self.assertRaises(Conflict):
            self.c.start(task['id'])
        with self.assertRaises(Conflict):
            self.c.recover(task['id'])

    def test_two_coordinators_cannot_own_same_project(self):
        with self.assertRaises(Conflict):
            Coordinator(self.project, self.root / 'other-state')

    def test_local_git_sees_edits_and_commit(self):
        path = self.project / 'file with spaces.txt'
        path.write_text('first')
        snap = git_snapshot(self.project)
        self.assertEqual(snap['files'][0]['path'], path.name)
        subprocess.run(['git', '-C', str(self.project), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture commit'], check=True)
        snap = git_snapshot(self.project)
        self.assertEqual(snap['files'], [])
        self.assertEqual(snap['commits'][0]['subject'], 'Fixture commit')
        path.write_text('second')
        self.assertEqual(git_snapshot(self.project)['files'][0]['status'], ' M')

    def test_unavailable_provider_and_bad_command_rejected(self):
        task = self.task(agent='deepseek')
        with self.assertRaises(ValueError):
            self.c.start(task['id'])
        with self.assertRaises(ValueError):
            self.c.create({'title':'x', 'instruction':'y', 'argv':'shell string'})

    def test_configured_worker_receives_packet(self):
        self.c.close()
        script = self.root / 'worker.py'
        script.write_text('import json,sys; p=json.load(open(sys.argv[1])); print(p["task"]["instruction"])')
        config = self.root / 'agents.json'
        config.write_text(json.dumps({'agents':[{'id':'fixture-worker','name':'Fixture worker','argv':[sys.executable,str(script),'{prompt_file}']}]}))
        self.c = Coordinator(self.project, self.state, config)
        task = self.complete(self.task(agent='fixture-worker'))
        self.assertEqual(task['status'], 'awaiting_review')
        self.assertTrue(any('Run a concrete check' in e['data'].get('message','') for e in self.c.store.events()))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.c = Coordinator(root, root / 'state')
        self.server = Server(('127.0.0.1', 0), self.c, 'test-token')
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.c.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_auth_and_foreign_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url + '/api/state')
        self.assertEqual(ctx.exception.code, 401)
        request = urllib.request.Request(self.url + '/api/state', headers={'Authorization':'Bearer test-token','Origin':'https://untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.assertEqual(ctx.exception.code, 403)

    def test_mcp_tools_control_actual_state(self):
        listed = dispatch({'id':1,'method':'tools/list'}, self.url, 'test-token')
        self.assertIn('acc_create_task', [t['name'] for t in listed['result']['tools']])
        response = dispatch({'id':2,'method':'tools/call','params':{'name':'acc_create_task','arguments':{'title':'From bridge','instruction':'Retain original request'}}}, self.url, 'test-token')
        self.assertFalse(response['result']['isError'])
        self.assertEqual(self.c.store.tasks()[0]['title'], 'From bridge')

    def test_event_stream_replays_after_cursor(self):
        self.c.store.event('fixture', {'message':'first'})
        cursor = self.c.store.tail()
        self.c.store.event('fixture', {'message':'second'})
        request = urllib.request.Request(self.url + '/api/events?after=' + str(cursor), headers={'Authorization':'Bearer test-token'})
        with urllib.request.urlopen(request, timeout=3) as response:
            previous = cursor
            while True:
                line = response.readline().decode()
                if not line.startswith('data: '):
                    continue
                event = json.loads(line[6:])
                self.assertEqual(event['seq'], previous + 1)
                previous = event['seq']
                if event['kind'] == 'fixture':
                    self.assertEqual(event['data']['message'], 'second')
                    break


if __name__ == '__main__':
    unittest.main()
