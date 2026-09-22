"""acc/claude_api.py: unit tests for its own response-handling, plus a full workflow cycle driven
through a fake local Messages API server standing in for the real thing (no network dependency,
no real model calls) -- mirroring test_gemini_adapter.py's rigor."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from acc import claude_api
from acc.core import Coordinator
from test_acc import eventually


class FakeMessagesHandler(BaseHTTPRequestHandler):
    """Answers each workflow stage exactly like test_gemini_adapter.py's own fake server does,
    so the same task/workflow cycle can be exercised end to end against this driver too."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        assert self.path == '/v1/messages'
        assert self.headers['x-api-key'] == 'fake-key'
        assert self.headers['anthropic-version'] == claude_api.ANTHROPIC_VERSION
        length = int(self.headers['Content-Length'])
        body = json.loads(self.rfile.read(length))
        prompt = body['messages'][0]['content']
        packet = json.loads(prompt[prompt.index('{'):])
        w = packet['workflow']
        stage = w['stage']
        r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
             'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake claude'}
        if stage == 'implement':
            r['checks'] = ['fixture check']
            Path(packet['project'], 'answer.txt').write_text('42')
        elif stage == 'review':
            assert Path(packet['project'], 'answer.txt').read_text() == '42'
            r.update(verdict='approve', findings=[], checks=['fixture check'])
        elif stage == 'coordinate':
            review = w['review_result']
            r['action'] = 'request_review' if review is None else 'accept'
        payload = json.dumps({'stop_reason': 'end_turn',
                               'content': [{'type': 'text', 'text': json.dumps(r)}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ClaudeApiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeMessagesHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        endpoint = f'http://127.0.0.1:{self.server.server_port}/v1/messages'
        self.key_file = self.root / 'claude.key'
        self.key_file.write_text('fake-key')
        agents = [{'id': a, 'name': a, 'driver': 'claude', 'api_key_file': str(self.key_file),
                   'endpoint': endpoint} for a in ('builder', 'reviewer', 'coordinator')]
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

    def test_full_cycle_through_a_fake_messages_server_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise claude', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']], ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'claude-query.txt').exists() for r in task['runs']))

    def test_agent_construction_rejects_claude_driver_without_an_api_key_file(self):
        self.config.write_text(json.dumps({'agents': [{'id': 'nokey', 'driver': 'claude'}]}))
        with self.assertRaises(ValueError):
            Coordinator(self.project, self.root / 'state2', self.config)

    def test_missing_api_key_file_is_unavailable(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'ghost', 'name': 'ghost', 'driver': 'claude', 'api_key_file': '/no/such/key'}]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['ghost']['available'])


class ClaudeApiUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.packet_path = self.root / 'task.json'
        self.key_file = self.root / 'claude.key'
        self.key_file.write_text('fake-key')

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

    def args(self, model='claude-opus-5', max_tokens=16000, timeout_seconds=5, endpoint=claude_api.ENDPOINT):
        import argparse
        return argparse.Namespace(packet=str(self.packet_path), api_key_file=str(self.key_file),
                                   model=model, max_tokens=max_tokens, timeout_seconds=timeout_seconds,
                                   endpoint=endpoint)

    def test_result_is_extracted_from_text_blocks(self):
        packet = self.write_packet()
        content = json.dumps({'task_id': 't1', 'run_id': 'r1', 'revision': 1,
                               'snapshot_id': None, 'summary': 'done', 'checks': []})
        with patch.object(claude_api, '_generate', return_value=content):
            code = claude_api.run(self.args())
        self.assertEqual(code, 0)
        result = json.loads(Path(packet['result_file']).read_text())
        self.assertEqual(result['summary'], 'done')

    def test_empty_key_file_is_rejected(self):
        self.write_packet()
        self.key_file.write_text('')
        with self.assertRaises(ValueError) as ctx:
            claude_api.run(self.args())
        self.assertIn('empty', str(ctx.exception))

    def test_non_dict_response_is_rejected(self):
        self.write_packet()
        with patch.object(claude_api, '_generate', return_value='["not", "a", "dict"]'):
            with self.assertRaises(ValueError):
                claude_api.run(self.args())

    def test_refusal_stop_reason_is_rejected(self):
        self.write_packet()
        with patch.object(claude_api.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
                {'stop_reason': 'refusal', 'content': []}).encode()
            with self.assertRaises(ValueError) as ctx:
                claude_api.run(self.args())
        self.assertIn('refusal', str(ctx.exception))

    def test_http_error_reports_the_api_message(self):
        import urllib.error

        self.write_packet()

        def raise_http_error(*a, **k):
            raise urllib.error.HTTPError(
                claude_api.ENDPOINT, 429, 'Too Many Requests', hdrs=None,
                fp=__import__('io').BytesIO(b'{"error": {"message": "rate limited"}}'))
        with patch.object(claude_api.urllib.request, 'urlopen', side_effect=raise_http_error):
            with self.assertRaises(ValueError) as ctx:
                claude_api.run(self.args())
        self.assertIn('rate limited', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
