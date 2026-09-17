"""Actual subprocess handoffs; scripted provider responses, no paid model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from acc.core import Coordinator, Conflict, Store
from acc.bridge import dispatch
from acc.server import Server
from acc.snapshots import inventory
from test_acc import eventually


ROOT = Path(__file__).resolve().parents[1]

# This fixture stands in for model reasoning, while ACC and its Hermes adapter are real.
WORKER = r'''
import json, pathlib, sys, time
p = json.loads(pathlib.Path(sys.argv[1]).read_text())
w = p['workflow']; stage = w['stage']; scenario = p['task']['instruction']
r = {k:w[k] for k in ('task_id','run_id','revision','snapshot_id')}
r.update(summary=stage + ' finished', checks=['fixture check'])
if 'slow' in scenario: time.sleep(.3)
if stage == 'implement':
    pathlib.Path(p['project'], 'answer.txt').write_text('42')
elif stage == 'review':
    assert pathlib.Path('answer.txt').read_text() == '42'
    r.update(verdict='changes_requested' if 'reject' in scenario and w['round']==1 else 'approve', findings=[])
    if 'always-reject' in scenario: r['verdict'] = 'changes_requested'
    if 'tamper' in scenario: pathlib.Path('answer.txt').write_text('99')
elif stage == 'coordinate':
    if 'failure' in scenario and sys.argv[-1] != 'fallback': sys.exit(7)
    review = w['review_result']
    r['action'] = 'request_review' if review is None else ('accept' if review['verdict']=='approve' else 'request_changes')
    if 'premature' in scenario: r['action'] = 'accept'
    if 'project-drift' in scenario and review: pathlib.Path(p['project'], 'answer.txt').write_text('99')
if 'stale' in scenario: r['run_id'] = 'old-run'
if 'missing' not in scenario: pathlib.Path(p['result_file']).write_text(json.dumps(r))
print(json.dumps({'stage':stage, 'result':r}), flush=True)
'''


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.worker = self.root / 'worker.py'; self.worker.write_text(WORKER)
        agents = [{'id': a, 'name': a, 'local': a.startswith('local-'),
                   'argv': [sys.executable, str(self.worker), '{prompt_file}', 'fallback' if a=='local-coordinator' else a]}
                  for a in ('builder', 'reviewer', 'hermes-coordinator', 'local-builder', 'local-reviewer', 'local-coordinator')]
        self.config = self.root / 'agents.json'; self.config.write_text(json.dumps({'agents':agents}))
        self.c = Coordinator(self.project, self.state, self.config)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def start(self, scenario='normal', **extra):
        task = self.c.create({'title':'Exercise handoff', 'instruction':scenario})
        self.c.workflows.configure(task['id'], {'implementer':'builder','reviewer':'reviewer',
                                  'coordinator':'hermes-coordinator', **extra})
        return task['id']

    def done(self, task_id):
        eventually(lambda: self.c.store.get(task_id).get('workflow', {}).get('phase') in ('complete', 'held'), timeout=12)
        return self.c.store.get(task_id)

    def test_full_sequence_uses_snapshot_and_independent_review(self):
        task = self.done(self.start())
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']], ['implement','coordinate','review','coordinate'])
        self.assertEqual(task['review']['reference'], task['workflow']['snapshot']['id'])
        self.assertEqual(task['review']['run_id'], task['runs'][0]['id'])
        self.assertEqual(task['review']['review_run_id'], task['runs'][2]['id'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX fake executable; real Windows host still needs validation')
    def test_complete_workflow_through_hermes_cli_adapter(self):
        self.c.close()
        fake = self.root / 'hermes-fixture'
        fake.write_text('#!' + sys.executable + '\n' + '''import json,pathlib,subprocess,sys
query=pathlib.Path(sys.argv[sys.argv.index('--query-file')+1])
packet=query.with_name('task.json')
p=json.loads(packet.read_text())
run=subprocess.run([sys.executable, WORKER, str(packet)],capture_output=True,text=True)
print(json.dumps({'type':'tool_result','name':'fixture','output':run.stdout}),flush=True)
out=pathlib.Path(p['result_file'])
text=out.read_text() if out.exists() else ''
out.unlink(missing_ok=True)
print(json.dumps({'type':'result','exit_code':run.returncode,'text':text}),flush=True)
sys.exit(run.returncode)
'''.replace('WORKER', repr(str(self.worker))))
        fake.chmod(0o755)
        self.config.write_text(json.dumps({'agents':[{'id':a,'name':a,'driver':'hermes',
                         'executable':str(fake)} for a in ('builder','reviewer','hermes-coordinator')]}))
        self.c=Coordinator(self.project,self.state,self.config)
        task=self.done(self.start('reject'))
        self.assertEqual(task['status'],'accepted')
        self.assertEqual(len(task['runs']),8)
        self.assertTrue(all((Path(r['folder'])/'hermes-query.txt').exists() for r in task['runs']))

    def test_changing_reviewer_invalidates_previous_review(self):
        task=self.done(self.start())
        # Reconstruct a held final decision, as if the coordinator paused after review.
        task.update(status='paused',review=None)
        task['workflow'].update(stage='coordinate',phase='held',enabled=False)
        self.c.store.save(task,'fixture',{})
        self.c.workflows.configure(task['id'], {'reviewer':'local-reviewer'})
        task=self.done(task['id'])
        self.assertEqual(task['status'],'accepted')
        self.assertEqual(task['runs'][-2]['agent'],'local-reviewer')

    def test_retry_stop_after_termination_error(self):
        task=self.c.create({'title':'Stop retry','instruction':'Check ownership',
                           'argv':[sys.executable,'-c','import time; time.sleep(30)']})
        self.c.start(task['id'])
        with patch('acc.core.stop_tree',side_effect=OSError('fixture stop failure')):
            with self.assertRaises(OSError): self.c.stop(task['id'])
        self.assertEqual(self.c.store.get(task['id'])['status'],'stopping')
        self.c.stop(task['id'])
        eventually(lambda: self.c.running_task is None)
        self.assertEqual(self.c.store.get(task['id'])['status'],'paused')

    def test_changes_return_to_implementation_then_fresh_review(self):
        task = self.done(self.start('reject'))
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual(task['workflow']['round'], 2)
        self.assertEqual(len(task['runs']), 8)

    def test_correction_budget_escalates(self):
        task = self.done(self.start('always-reject', max_rounds=1))
        self.assertEqual(task['status'], 'paused')
        self.assertIn('Correction limit', task['activity'])
        self.assertIsNone(task['review'])

    def test_invalid_results_and_mutations_never_approve(self):
        for scenario, reason in [('stale','does not match'), ('missing','structured result'),
                                 ('tamper','snapshot changed'), ('premature','invalid transition'),
                                 ('project-drift','Project changed')]:
            with self.subTest(scenario=scenario):
                task = self.done(self.start(scenario))
                self.assertEqual(task['status'], 'paused')
                self.assertIn(reason, task['activity'])
                self.assertIsNone(task['review'])

    def test_offline_local_roles_and_return_to_preferred(self):
        task = self.done(self.start(mode='offline', fallbacks={'implementer':'local-builder',
                         'reviewer':'local-reviewer','coordinator':'local-coordinator'}))
        self.assertEqual(task['status'], 'accepted')
        self.assertTrue(all(r['agent'].startswith('local-') for r in task['runs']))
        self.c.workflows.configure(task['id'], {'mode':'online', 'restart':True})
        task = self.done(task['id'])
        self.assertEqual(task['runs'][-4]['agent'], 'builder')
        self.assertEqual(task['runs'][-1]['agent'], 'hermes-coordinator')

    def test_coordinator_failure_can_use_explicit_local_fallback(self):
        task = self.done(self.start('failure', fallbacks={'coordinator':'local-coordinator'}))
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['agent'] for r in task['runs']].count('local-coordinator'), 2)

    def test_offline_without_permission_holds(self):
        task = self.done(self.start(mode='offline'))
        self.assertEqual(task['runs'], [])
        self.assertIn('no permitted local adapter', task['activity'])

    def test_pause_survives_restart_and_resume_reuses_result(self):
        task_id = self.start('slow')
        eventually(lambda: self.c.store.get(task_id)['status']=='running')
        self.c.workflows.configure(task_id, {'enabled':False})
        eventually(lambda: self.c.running_task is None)
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        task = self.c.store.get(task_id)
        self.assertFalse(task['workflow']['enabled'])
        self.assertEqual(len(task['runs']), 1)
        self.c.workflows.configure(task_id, {})
        task = self.done(task_id)
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual(len(task['runs']), 4)

    def test_active_switch_blocked_and_manual_approval_cannot_bypass(self):
        task_id = self.start('slow')
        eventually(lambda: self.c.store.get(task_id)['status']=='running')
        with self.assertRaises(Conflict):
            self.c.workflows.configure(task_id, {'reviewer':'local-reviewer'})
        with self.assertRaises(Conflict):
            self.c.review(task_id, {'message':'skip review'})
        self.c.stop(task_id)
        task = self.done(task_id)
        self.assertFalse(task['workflow']['enabled'])

    def test_repeated_enable_after_acceptance_does_not_restart_work(self):
        task=self.done(self.start())
        with self.assertRaises(Conflict):
            self.c.workflows.configure(task['id'], {})
        self.assertEqual(len(self.c.store.get(task['id'])['runs']),4)

    def test_launch_intent_persisted_before_spawn_and_recovery_blocks(self):
        task = self.c.create({'title':'Launch', 'instruction':'Test', 'argv':[sys.executable,'-c','print(1)']})
        real_popen = subprocess.Popen
        def observe(*args, **kw):
            if kw.get('cwd') == self.project:
                saved = self.c.store.get(task['id'])
                self.assertEqual(saved['status'], 'launching')
                self.assertIsNone(saved['pid'])
            return real_popen(*args, **kw)
        with patch('acc.core.subprocess.Popen', side_effect=observe):
            self.c.start(task['id'])
        eventually(lambda: self.c.running_task is None)
        saved = self.c.store.get(task['id']); saved.update(status='launching', pid=None)
        self.c.store.save(saved, 'fixture', {})
        self.c.close(); self.c = Coordinator(self.project, self.state, self.config)
        self.assertTrue(self.c.recovery_required)
        with self.assertRaises(Conflict): self.c.start(task['id'])

    def test_git_ignored_files_excluded_and_symlinks_rejected(self):
        (self.project / '.gitignore').write_text('private.key\n')
        (self.project / 'private.key').write_text('fixture not a real secret')
        self.assertNotIn('private.key', inventory(self.project))
        if os.name == 'posix':
            (self.project / 'link').symlink_to(self.worker)
            with self.assertRaises(ValueError): inventory(self.project)

    def test_mcp_starts_background_workflow_over_http(self):
        server = Server(('127.0.0.1',0),self.c,'fixture')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        task = self.c.create({'title':'MCP', 'instruction':'normal'})
        try:
            response = dispatch({'id':1,'method':'tools/call','params':{'name':'acc_configure_workflow',
                         'arguments':{'task_id':task['id'],'implementer':'builder','reviewer':'reviewer',
                                      'coordinator':'hermes-coordinator'}}},
                         'http://127.0.0.1:'+str(server.server_port),'fixture')
            self.assertFalse(response['result']['isError'])
            self.assertEqual(self.done(task['id'])['status'], 'accepted')
        finally:
            server.shutdown(); server.server_close(); thread.join()


class HermesAdapterTests(unittest.TestCase):
    def test_actual_adapter_parses_cli_terminal_event_and_writes_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Portable fake CLI launcher supplied as executable; actual adapter still invokes documented flags.
            fake = root / 'hermes-fixture'
            fake.write_text('#!' + sys.executable + '\n' + '''import json,sys,pathlib
assert sys.argv[1] == 'chat'
assert '--oneshot' in sys.argv and '--format' in sys.argv
q=pathlib.Path(sys.argv[sys.argv.index('--query-file')+1]).read_text()
assert 'literal $(do-not-run) `example`' in q
print(json.dumps({'type':'tool_use','name':'terminal','input':{'command':'fixture'}}), flush=True)
print(json.dumps({'type':'result','exit_code':0,'text':json.dumps({'task_id':'task','run_id':'run','revision':1,'snapshot_id':None,'summary':'done','checks':[]})}))
''')
            fake.chmod(0o755)
            packet = root / 'task.json'
            output = root / 'result.json'
            packet.write_text(json.dumps({'task':{'instruction':'literal $(do-not-run) `example`'},
                              'workflow':{'stage':'implement'},'result_file':str(output)}))
            if os.name != 'posix': self.skipTest('POSIX executable fixture; native Windows launch needs host validation')
            run = subprocess.run([sys.executable,str(ROOT/'acc/hermes.py'),'run','--packet',str(packet),
                                  '--executable',str(fake)], capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(json.loads(output.read_text())['summary'],'done')
            self.assertIn('tool_use',run.stdout)

    def test_generated_mcp_config_uses_absolute_paths_and_no_token_contents(self):
        run = subprocess.run([sys.executable,str(ROOT/'acc/hermes.py'),'config','--token-file','/private/token'],
                             capture_output=True,text=True,check=True)
        config = json.loads(run.stdout)['mcp_servers']['acc']
        self.assertTrue(Path(config['args'][0]).is_absolute())
        self.assertIn('--token-file',config['args'])


if __name__ == '__main__':
    unittest.main()
