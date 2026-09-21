"""Job-backed implementer steps: a capability job standing in for a spawned CLI's implement stage."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from acc.core import Coordinator, Conflict
from test_acc import eventually


ROOT = Path(__file__).resolve().parents[1]

# Stands in for a text-generating reviewer/coordinator CLI (identical convention to test_workflow.py).
WORKER = r'''
import json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text())
w = p['workflow']; stage = w['stage']
r = {k: w[k] for k in ('task_id', 'run_id', 'revision', 'snapshot_id')}
r.update(summary=stage + ' finished', checks=['fixture check'])
if stage == 'review':
    r.update(verdict='approve', findings=[])
elif stage == 'coordinate':
    review = w['review_result']
    r['action'] = 'request_review' if review is None else 'accept'
pathlib.Path(p['result_file']).write_text(json.dumps(r))
print(json.dumps({'stage': stage}), flush=True)
'''


class JobBackedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.worker = self.root / 'worker.py'
        self.worker.write_text(WORKER)
        agents = [{'id': a, 'name': a, 'argv': [sys.executable, str(self.worker), '{prompt_file}']}
                  for a in ('reviewer', 'coordinator')]
        agents.append({'id': 'asset-builder', 'name': 'Asset Builder', 'kind': 'job',
                       'capability': 'mesh.generate', 'provider': 'agent-3d-studio', 'local': True})
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps({'agents': agents, 'integrations': {'providers': [
            {'id': 'agent-3d-studio', 'enabled': True}]}}))
        self.c = Coordinator(self.project, self.state, self.config)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def start_task(self, **extra):
        task = self.c.create({'title': 'Build a tank', 'instruction': 'Reconstruct the tank asset.'})
        self.c.workflows.configure(task['id'], {'implementer': 'asset-builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator', **extra})
        return task['id']

    def outstanding_job(self, task_id):
        return eventually(lambda: next((j for j in self.c.integrations.snapshot()['jobs']
                                        if j['task_id'] == task_id and j['status'] == 'queued'), None),
                          timeout=5)

    def write_artifact(self, relative_path, content=b'glb-bytes'):
        path = self.project / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def claim_and_finish(self, job_id, status='succeeded', artifacts=None, error=None):
        claim = self.c.integrations.claim({'owner': 'worker-1', 'provider': 'agent-3d-studio'})
        self.assertEqual(claim['job']['id'], job_id)
        payload = {'lease_token': claim['lease_token'], 'fence': claim['job']['fence'], 'status': status}
        if artifacts is not None:
            payload['artifacts'] = artifacts
        if error is not None:
            payload['error'] = error
        if status == 'succeeded':
            payload['result'] = {'summary': 'Reconstructed the tank from the reference image.'}
        return self.c.integrations.finish(job_id, payload)

    def test_successful_job_advances_to_coordinate_with_frozen_snapshot(self):
        task_id = self.start_task()
        job = self.outstanding_job(task_id)
        self.assertIsNotNone(job, 'implement stage should have submitted a queued job')
        self.write_artifact('Assets/tank.glb')
        digest = hashlib.sha256(b'glb-bytes').hexdigest()
        self.claim_and_finish(job['id'], artifacts=[
            {'kind': 'model', 'uri': 'Assets/tank.glb', 'metadata': {'format': 'glb'}, 'sha256': digest}])
        task = eventually(lambda: self.c.store.get(task_id) if
                          self.c.store.get(task_id)['workflow']['stage'] != 'implement' else None)
        self.assertEqual(task['workflow']['stage'], 'coordinate')
        self.assertEqual(task['status'], 'queued')
        impl = task['workflow']['implementation']
        self.assertEqual(impl['checks'], [{'artifact': 'Assets/tank.glb', 'kind': 'model',
                                           'sha256': digest}])
        self.assertIn('Assets/tank.glb', task['workflow']['snapshot']['entries'])
        self.assertIsNone(self.c.running_task)

    def test_full_cycle_reaches_accepted_review(self):
        task_id = self.start_task()
        job = self.outstanding_job(task_id)
        self.write_artifact('Assets/tank.glb')
        digest = hashlib.sha256(b'glb-bytes').hexdigest()
        self.claim_and_finish(job['id'], artifacts=[
            {'kind': 'model', 'uri': 'Assets/tank.glb', 'metadata': {}, 'sha256': digest}])
        task = eventually(lambda: self.c.store.get(task_id) if
                          self.c.store.get(task_id).get('workflow', {}).get('phase') in
                          ('complete', 'held') else None, timeout=12)
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual(task['review']['reference'], task['workflow']['snapshot']['id'])

    def test_failed_job_holds_the_task(self):
        task_id = self.start_task()
        job = self.outstanding_job(task_id)
        self.claim_and_finish(job['id'], status='failed', error='could not reconstruct mesh')
        task = eventually(lambda: self.c.store.get(task_id) if
                          self.c.store.get(task_id)['status'] == 'paused' else None)
        self.assertIn('could not reconstruct mesh', task['activity'])
        self.assertFalse(task['workflow']['enabled'])
        self.assertIsNone(self.c.running_task)

    def test_stop_while_still_queued_cancels_the_job_and_holds(self):
        task_id = self.start_task()
        job = self.outstanding_job(task_id)
        self.c.stop(task_id)
        task = self.c.store.get(task_id)
        self.assertEqual(task['status'], 'paused')
        self.assertIsNone(self.c.running_task)
        cancelled = next(j for j in self.c.integrations.snapshot()['jobs'] if j['id'] == job['id'])
        self.assertEqual(cancelled['status'], 'cancelled')

    def test_timeout_cancels_a_still_queued_job(self):
        task_id = self.start_task()
        self.outstanding_job(task_id)
        task = self.c.store.get(task_id)
        task['timeout_seconds'] = 1
        self.c.store.save(task, 'fixture', {})
        # Re-trigger with the shortened deadline by calling the timeout handler directly,
        # mirroring how threading.Timer would fire it.
        self.c._timeout(task_id, task['run_id'])
        task = eventually(lambda: self.c.store.get(task_id) if
                          self.c.store.get(task_id)['status'] == 'paused' else None)
        self.assertIsNone(self.c.running_task)

    def test_reviewer_cannot_be_job_backed(self):
        task = self.c.create({'title': 'x', 'instruction': 'y'})
        with self.assertRaises(ValueError):
            self.c.workflows.configure(task['id'], {'implementer': 'reviewer',
                                       'reviewer': 'asset-builder', 'coordinator': 'coordinator'})

    def test_restart_recovery_marks_job_backed_step_interrupted(self):
        task_id = self.start_task()
        self.outstanding_job(task_id)
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        task = self.c.store.get(task_id)
        self.assertEqual(task['status'], 'interrupted')
        self.assertIn('job-backed', task['activity'])
        self.assertTrue(self.c.recovery_required)


if __name__ == '__main__':
    unittest.main()
