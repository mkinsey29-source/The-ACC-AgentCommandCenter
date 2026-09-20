import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from acc.bridge import dispatch
from acc.core import Conflict, Coordinator
from acc.server import Server


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps({'integrations': {'providers': [
            {'id': 'gemini', 'enabled': True, 'verified': True},
            {'id': 'hearth-pipeline', 'enabled': True, 'verified': True},
            {'id': 'typesafe-jev', 'enabled': True},
        ]}}))
        self.c = Coordinator(self.project, self.state, self.config)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_capability_routing_and_offline_queue_reconciliation(self):
        self.c.controls.set_mode({'mode': 'offline'})
        remote = self.c.integrations.submit({
            'capability': 'image.generate', 'provider': 'gemini', 'input': {'prompt': 'tank'},
            'idempotency_key': 'asset-concept-1'})
        local = self.c.integrations.submit({
            'capability': 'blender.process', 'provider': 'hearth-pipeline', 'input': {'asset': 'tank'}})
        self.assertEqual(remote['status'], 'blocked_offline')
        self.assertEqual(local['status'], 'queued')
        self.assertEqual(self.c.integrations.submit({
            'capability': 'image.generate', 'provider': 'gemini', 'input': {'prompt': 'tank'},
            'idempotency_key': 'asset-concept-1'})['id'], remote['id'])
        with self.assertRaises(Conflict):
            self.c.integrations.submit({
                'capability': 'image.generate', 'provider': 'gemini', 'input': {'prompt': 'different'},
                'idempotency_key': 'asset-concept-1'})
        changed = self.c.controls.set_mode({'mode': 'online'})
        self.assertEqual(changed['mode'], 'online')
        jobs = {job['id']: job for job in self.c.integrations.snapshot()['jobs']}
        self.assertEqual(jobs[remote['id']]['status'], 'queued')
        self.assertEqual(jobs[local['id']]['status'], 'queued')

    def test_fenced_lease_completion_artifacts_and_expiry(self):
        job = self.c.integrations.submit({
            'capability': 'blender.process', 'provider': 'hearth-pipeline',
            'input': {'recipe': 'vehicle'}, 'priority': 80})
        claim = self.c.integrations.claim({'owner': 'blender-worker-1', 'provider': 'hearth-pipeline'})
        self.assertEqual(claim['job']['id'], job['id'])
        self.assertNotIn('lease_token_hash', claim['job'])
        lease = {'lease_token': claim['lease_token'], 'fence': claim['job']['fence']}
        with self.assertRaises(Conflict):
            self.c.integrations.finish(job['id'], {**lease, 'fence': lease['fence'] + 1,
                                                  'status': 'succeeded'})
        renewed = self.c.integrations.renew(job['id'], {**lease, 'lease_seconds': 60})
        self.assertEqual(renewed['status'], 'running')
        result = self.c.integrations.finish(job['id'], {
            **lease, 'status': 'succeeded', 'result': {'triangles': 12000},
            'cost': {'seconds': 4.2}, 'artifacts': [
                {'kind': 'model', 'uri': 'Assets/Vehicles/tank.glb',
                 'sha256': 'a' * 64, 'metadata': {'format': 'glb'}}]})
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['artifacts'][0]['kind'], 'model')
        with self.assertRaises(Conflict):
            self.c.integrations.finish(job['id'], {**lease, 'status': 'failed'})

        expiring = self.c.integrations.submit({
            'capability': 'blender.process', 'provider': 'hearth-pipeline', 'input': {}})
        old = self.c.integrations.claim({'owner': 'lost-worker', 'provider': 'hearth-pipeline'})
        with self.c.store.connect() as db:
            record = json.loads(db.execute('SELECT data FROM integration_jobs WHERE id=?',
                                           (expiring['id'],)).fetchone()[0])
            record['lease_until'] = 0
            db.execute('UPDATE integration_jobs SET data=? WHERE id=?', (json.dumps(record), expiring['id']))
        snapshot = self.c.integrations.snapshot()
        queued = next(item for item in snapshot['jobs'] if item['id'] == expiring['id'])
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['attempts'], 1)
        new = self.c.integrations.claim({'owner': 'replacement', 'provider': 'hearth-pipeline'})
        self.assertEqual(new['job']['fence'], old['job']['fence'] + 1)

    def test_versioned_reviewed_shared_memory_persists(self):
        first = self.c.integrations.propose_memory({
            'title': 'Asset authority', 'body': 'ACC records evidence; the pipeline owns editor jobs.',
            'kind': 'architecture', 'key': 'asset-authority', 'source': 'task-1',
            'tags': ['acc', 'pipeline'], 'request_id': 'memory-request-1'})
        duplicate = self.c.integrations.propose_memory({
            'title': 'Asset authority', 'body': 'ACC records evidence; the pipeline owns editor jobs.',
            'kind': 'architecture', 'key': 'asset-authority', 'source': 'task-1',
            'tags': ['acc', 'pipeline'], 'request_id': 'memory-request-1'})
        self.assertEqual(first['id'], duplicate['id'])
        self.c.integrations.review_memory(first['id'], {'status': 'active', 'reviewer': 'coordinator'})
        second = self.c.integrations.propose_memory({
            'title': 'Asset authority', 'body': 'ACC schedules; one pipeline coordinator owns each editor session.',
            'kind': 'architecture', 'key': 'asset-authority', 'source': 'task-2'})
        self.assertEqual(second['version'], 2)
        self.c.integrations.review_memory(second['id'], {'status': 'active', 'reviewer': 'coordinator'})
        active = self.c.integrations.search_memory({'q': 'editor session'})['memory']
        self.assertEqual([item['id'] for item in active], [second['id']])
        all_versions = self.c.integrations.search_memory({'status': 'all', 'kind': 'architecture'})['memory']
        self.assertEqual({item['status'] for item in all_versions}, {'active', 'superseded'})
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertEqual(self.c.integrations.search_memory({'q': 'editor session'})['count'], 1)

    def test_http_and_mcp_expose_jobs_and_memory(self):
        server = Server(('127.0.0.1', 0), self.c, 'test-token')
        url = 'http://127.0.0.1:' + str(server.server_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            listed = dispatch({'id': 1, 'method': 'tools/list'}, url, 'test-token')
            names = {tool['name'] for tool in listed['result']['tools']}
            self.assertIn('acc_submit_integration_job', names)
            self.assertIn('acc_memory_propose', names)
            created = dispatch({'id': 2, 'method': 'tools/call', 'params': {
                'name': 'acc_submit_integration_job', 'arguments': {
                    'capability': 'decision.score', 'provider': 'typesafe-jev',
                    'input': {'state': 'fixture'}, 'idempotency_key': 'mcp-job-1'}}}, url, 'test-token')
            self.assertFalse(created['result']['isError'])
            proposal = dispatch({'id': 3, 'method': 'tools/call', 'params': {
                'name': 'acc_memory_propose', 'arguments': {
                    'title': 'MCP fact', 'body': 'Recorded through the bridge.',
                    'kind': 'fact', 'request_id': 'mcp-memory-1'}}}, url, 'test-token')
            self.assertFalse(proposal['result']['isError'])
            state = dispatch({'id': 4, 'method': 'tools/call',
                              'params': {'name': 'acc_state', 'arguments': {}}}, url, 'test-token')
            payload = json.loads(state['result']['content'][0]['text'])
            self.assertEqual(payload['integrations']['job_counts']['queued'], 1)
            self.assertEqual(payload['integrations']['memory'][0]['status'], 'proposed')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
