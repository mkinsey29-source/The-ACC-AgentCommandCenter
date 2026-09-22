"""acc/ollama.py: unit tests for its own response-handling, plus a full workflow cycle driven
through a fake local Ollama server standing in for the real thing (no network dependency, no
real model calls) -- mirroring the rigor test_workflow.py already applies to the Hermes adapter."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from acc import ollama
from acc.core import Coordinator
from test_acc import eventually


ROOT = Path(__file__).resolve().parents[1]


class FakeOllamaHandler(BaseHTTPRequestHandler):
    """Reads the embedded ACC packet out of the prompt and answers each workflow stage exactly
    like test_workflow.py's own WORKER fixture does for the subprocess-based adapters, so the
    same task/workflow cycle can be exercised end to end against this driver too."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        # Answers is_reachable()'s probe of /api/tags, matching Ollama's real "list models" route.
        payload = b'{"models": []}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers['Content-Length'])
        body = json.loads(self.rfile.read(length))
        prompt = body['messages'][0]['content']
        packet = json.loads(prompt[prompt.index('{'):])
        w = packet['workflow']
        stage = w['stage']
        r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
             'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake ollama'}
        if stage == 'implement':
            r['checks'] = ['fixture check']
            Path(packet['project'], 'answer.txt').write_text('42')
        elif stage == 'review':
            assert Path(packet['project'], 'answer.txt').read_text() == '42'
            r.update(verdict='approve', findings=[], checks=['fixture check'])
        elif stage == 'coordinate':
            review = w['review_result']
            r['action'] = 'request_review' if review is None else 'accept'
        # A leading reasoning trace real DeepSeek-R1 emits despite instructions not to --
        # exercises worker_prompt.extract_json_object's <think> stripping for real, not in
        # isolation, on every stage of an actual run.
        content = '<think>reasoning about ' + stage + '</think>' + json.dumps(r)
        payload = json.dumps({'message': {'content': content}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class OllamaWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOllamaHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        host = f'http://127.0.0.1:{self.server.server_port}'
        agents = [{'id': a, 'name': a, 'driver': 'ollama', 'model': 'deepseek-r1', 'host': host}
                  for a in ('builder', 'reviewer', 'coordinator')]
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps({'agents': agents}))
        self.c = Coordinator(self.project, self.state, self.config)

    def tearDown(self):
        self.c.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join()
        self.tmp.cleanup()

    def done(self, task_id):
        return eventually(lambda: self.c.store.get(task_id) if
                          self.c.store.get(task_id).get('workflow', {}).get('phase') in
                          ('complete', 'held') else None, timeout=12)

    def test_full_cycle_through_a_local_ollama_server_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise ollama', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']], ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'ollama-query.txt').exists() for r in task['runs']))

    def test_agent_construction_rejects_ollama_driver_without_a_model(self):
        self.config.write_text(json.dumps({'agents': [{'id': 'nomodel', 'driver': 'ollama'}]}))
        with self.assertRaises(ValueError):
            Coordinator(self.project, self.root / 'state2', self.config)


class OllamaAdapterUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.packet_path = self.root / 'task.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write_packet(self, **extra):
        packet = {'task': {'id': 't1'}, 'project': str(self.root), 'result_file':
                  str(self.root / 'result.json'), 'workflow': {
                      'stage': 'implement', 'round': 1, 'max_rounds': 3, 'task_id': 't1',
                      'run_id': 'r1', 'revision': 1, 'snapshot_id': None, 'implementation': None,
                      'review_result': None, 'history': [], 'allowed_actions': [],
                      'result_contract': {}}, **extra}
        self.packet_path.write_text(json.dumps(packet))
        return packet

    def args(self, host='http://127.0.0.1:1', model='deepseek-r1', timeout_seconds=5):
        import argparse
        return argparse.Namespace(packet=str(self.packet_path), host=host, model=model,
                                   timeout_seconds=timeout_seconds)

    def test_think_block_and_result_are_stripped_and_parsed(self):
        packet = self.write_packet()
        content = '<think>let me consider this</think>' + json.dumps({
            'task_id': 't1', 'run_id': 'r1', 'revision': 1, 'snapshot_id': None,
            'summary': 'done', 'checks': []})
        with patch.object(ollama, '_chat', return_value=content):
            code = ollama.run(self.args())
        self.assertEqual(code, 0)
        result = json.loads(Path(packet['result_file']).read_text())
        self.assertEqual(result['summary'], 'done')

    def test_unreachable_server_reports_a_clear_error(self):
        self.write_packet()
        with self.assertRaises(ValueError) as ctx:
            ollama.run(self.args(host='http://127.0.0.1:1'))
        self.assertIn('Could not reach Ollama', str(ctx.exception))

    def test_non_dict_response_is_rejected(self):
        self.write_packet()
        with patch.object(ollama, '_chat', return_value='["not", "a", "dict"]'):
            with self.assertRaises(ValueError):
                ollama.run(self.args())


if __name__ == '__main__':
    unittest.main()
