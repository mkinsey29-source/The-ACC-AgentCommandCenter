"""acc/openai_compatible.py: unit tests for its own response-handling, plus a full workflow
cycle driven through a fake OpenAI-compatible server standing in for the real thing (no network
dependency, no real model calls) -- mirroring test_lmstudio_adapter.py's rigor, with the addition
of real Authorization-header checks since this driver, unlike lmstudio.py, supports genuine
credentials."""
import json
from pathlib import Path
import subprocess
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from acc import openai_compatible
from acc.core import Coordinator
from test_acc import eventually


class FakeOpenAICompatibleHandler(BaseHTTPRequestHandler):
    """Answers each workflow stage exactly like test_lmstudio_adapter.py's own fake server does,
    but requires a real bearer token (the point being tested here) rather than accepting any."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer fake-key':
            self.send_response(401)
            self.end_headers()
            return
        payload = b'{"data": []}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        if self.headers.get('Authorization') != 'Bearer fake-key':
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers['Content-Length'])
        body = json.loads(self.rfile.read(length))
        prompt = body['messages'][0]['content']
        packet = json.loads(prompt[prompt.index('{'):])
        w = packet['workflow']
        stage = w['stage']
        r = {'task_id': packet['task']['id'], 'run_id': w['run_id'], 'revision': w['revision'],
             'snapshot_id': w['snapshot_id'], 'summary': stage + ' finished via fake openai-compatible'}
        if stage == 'implement':
            r['checks'] = ['fixture check']
            Path(packet['project'], 'answer.txt').write_text('42')
        elif stage == 'review':
            assert Path(packet['project'], 'answer.txt').read_text() == '42'
            r.update(verdict='approve', findings=[], checks=['fixture check'])
        elif stage == 'coordinate':
            review = w['review_result']
            r['action'] = 'request_review' if review is None else 'accept'
        payload = json.dumps({'choices': [{'message': {'content': json.dumps(r)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class OpenAICompatibleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.state = self.root / 'state'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOpenAICompatibleHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        base_url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.key_file = self.root / 'provider.key'
        self.key_file.write_text('fake-key')
        agents = [{'id': a, 'name': a, 'driver': 'openai-compatible', 'model': 'custom-model',
                   'base_url': base_url, 'api_key_file': str(self.key_file)}
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

    def test_full_cycle_through_a_fake_openai_compatible_server_reaches_accepted(self):
        task = self.c.create({'title': 'Exercise openai-compatible', 'instruction': 'Reconstruct the answer.'})
        self.c.workflows.configure(task['id'], {'implementer': 'builder', 'reviewer': 'reviewer',
                                   'coordinator': 'coordinator'})
        task = self.done(task['id'])
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual([r['stage'] for r in task['runs']], ['implement', 'coordinate', 'review', 'coordinate'])
        self.assertTrue((self.project / 'answer.txt').exists())
        self.assertTrue(all((Path(r['folder']) / 'openai-compatible-query.txt').exists() for r in task['runs']))

    def test_agent_construction_rejects_missing_base_url(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'nobase', 'driver': 'openai-compatible', 'model': 'x'}]}))
        with self.assertRaises(ValueError):
            Coordinator(self.project, self.root / 'state2', self.config)

    def test_agent_construction_rejects_missing_model(self):
        self.config.write_text(json.dumps({'agents': [
            {'id': 'nomodel', 'driver': 'openai-compatible', 'base_url': 'http://x/v1'}]}))
        with self.assertRaises(ValueError):
            Coordinator(self.project, self.root / 'state2', self.config)

    def test_wrong_key_reports_unavailable(self):
        self.config.write_text(json.dumps({'agents': [{
            'id': 'ghost', 'name': 'ghost', 'driver': 'openai-compatible', 'model': 'x',
            'base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
            'api_key_file': str(self.root / 'wrong.key')}]}))
        (self.root / 'wrong.key').write_text('wrong-key')
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['ghost']['available'])

    def test_missing_api_key_file_still_probes_unauthenticated(self):
        # No api_key_file at all is a supported configuration (some custom endpoints need no
        # auth); the reachability probe must not crash, just report accurately.
        self.config.write_text(json.dumps({'agents': [{
            'id': 'noauth', 'name': 'noauth', 'driver': 'openai-compatible', 'model': 'x',
            'base_url': f'http://127.0.0.1:{self.server.server_port}/v1'}]}))
        self.c.close()
        self.c = Coordinator(self.project, self.state, self.config)
        self.assertFalse(self.c.agents['noauth']['available'])


class OpenAICompatibleAdapterUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.packet_path = self.root / 'task.json'
        self.key_file = self.root / 'provider.key'
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

    def args(self, base_url='http://127.0.0.1:1/v1', model='custom-model', api_key_file=None,
             timeout_seconds=5):
        import argparse
        return argparse.Namespace(packet=str(self.packet_path), base_url=base_url, model=model,
                                   api_key_file=api_key_file, timeout_seconds=timeout_seconds)

    def test_result_is_extracted_from_message_content(self):
        packet = self.write_packet()
        content = json.dumps({'task_id': 't1', 'run_id': 'r1', 'revision': 1,
                               'snapshot_id': None, 'summary': 'done', 'checks': []})
        with patch.object(openai_compatible, '_chat', return_value=content):
            code = openai_compatible.run(self.args())
        self.assertEqual(code, 0)
        result = json.loads(Path(packet['result_file']).read_text())
        self.assertEqual(result['summary'], 'done')

    def test_empty_key_file_is_rejected(self):
        self.write_packet()
        self.key_file.write_text('')
        with self.assertRaises(ValueError) as ctx:
            openai_compatible.run(self.args(api_key_file=str(self.key_file)))
        self.assertIn('empty', str(ctx.exception))

    def test_tilde_api_key_file_is_expanded(self):
        self.write_packet()
        home = self.root / 'home'
        home.mkdir()
        (home / 'provider.key').write_text('expanded-key')
        with patch.dict('os.environ', {'HOME': str(home)}), \
                patch.object(openai_compatible, '_chat', return_value='{}') as chat:
            openai_compatible.run(self.args(api_key_file='~/provider.key'))
        self.assertEqual(chat.call_args.args[1], 'expanded-key')

    def test_no_api_key_file_sends_placeholder(self):
        self.write_packet()
        with patch.object(openai_compatible, '_chat', return_value='{}') as chat:
            openai_compatible.run(self.args(api_key_file=None))
        self.assertIsNone(chat.call_args.args[1])

    def test_unreachable_server_reports_a_clear_error(self):
        self.write_packet()
        with self.assertRaises(ValueError) as ctx:
            openai_compatible.run(self.args(base_url='http://127.0.0.1:1/v1'))
        self.assertIn('Could not reach', str(ctx.exception))

    def test_non_dict_response_is_rejected(self):
        self.write_packet()
        with patch.object(openai_compatible, '_chat', return_value='["not", "a", "dict"]'):
            with self.assertRaises(ValueError):
                openai_compatible.run(self.args())

    def test_no_choices_is_rejected(self):
        self.write_packet()
        with patch.object(openai_compatible.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
                {'choices': []}).encode()
            with self.assertRaises(ValueError) as ctx:
                openai_compatible.run(self.args())
        self.assertIn('no choices', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
