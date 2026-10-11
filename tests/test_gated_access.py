"""SC-9: remote-driven callers cannot run risky operations or approve themselves; the owner decides."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from acc.bridge import dispatch
from acc.core import Coordinator
from acc.gates import MAX_PENDING, TTL_SECONDS
from acc.pairing import Pairing
from acc.reach import Reach
from acc.server import Server

OWNER = 'O' * 43
AGENT = 'A' * 43
REMOTE = 'box.tail1234.ts.net:8443'
ARGV = [__import__('sys').executable, '-c', 'print(1)']


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Coordinator(self.root, self.root / 'state')
        self.reach = Reach('tailscale-serve', '127.0.0.1', [REMOTE], secure_cookie=True)
        self.server = Server(('127.0.0.1', 0), self.c, OWNER, reach=self.reach,
                             pairing=Pairing(self.root / 'state' / 'pairing.json'), agent_token=AGENT)
        self.port = self.server.server_port
        self.local = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.c.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def call(self, method, path, token=None, cookie=None, host=None, body=None):
        headers = {'Host': host or self.local}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if cookie:
            headers['Cookie'] = cookie
        raw = None
        if body is not None:
            raw = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, data

    def owner(self, method, path, body=None):
        return self.call(method, path, token=OWNER, body=body)

    def agent(self, method, path, body=None):
        return self.call(method, path, token=AGENT, body=body)

    def pair_device(self):
        _, begun = self.owner('POST', '/api/pairing/begin', {})
        _, claimed = self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': begun['token'], 'name': 'Pixel'})
        self.owner('POST', '/api/pairing/approve', {'request_id': claimed['request_id']})
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('POST', '/api/pairing/poll', headers={'Host': REMOTE, 'Content-Type': 'application/json'},
                     body=json.dumps({'request_id': claimed['request_id'], 'claim': claimed['claim']}))
        response = conn.getresponse()
        response.read()
        cookie = response.getheader('Set-Cookie').split(';')[0]
        conn.close()
        return cookie

    def device(self, cookie, method, path, body=None):
        return self.call(method, path, cookie=cookie, host=REMOTE, body=body)

    def tasks(self):
        return self.owner('GET', '/api/state')[1]['tasks']

    def events(self):
        return [e['kind'] for e in self.c.store.events(0, 1000)]


class CredentialTests(Fixture):
    def test_the_agent_credential_works_on_local_hosts_and_never_on_remote_ones(self):
        self.assertEqual(self.agent('GET', '/api/state')[0], 200)
        self.assertEqual(self.call('GET', '/api/state', token=AGENT, host=REMOTE)[0], 401)

    def test_a_non_ascii_authorization_header_is_an_ordinary_refusal(self):
        self.assertEqual(self.call('GET', '/api/state', token='é' * 10)[0], 401)

    def test_the_two_secrets_must_differ_and_be_long(self):
        for bad in (OWNER, 'short', '', 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Server(('127.0.0.1', 0), self.c, OWNER, agent_token=bad)

    def test_a_server_without_an_agent_token_still_works_and_refuses_the_value(self):
        server = Server(('127.0.0.1', 0), self.c, 'test-token')
        self.addCleanup(server.server_close)
        self.assertIsNone(server.agent_token)


class OwnerIsNotGatedTests(Fixture):
    def test_the_owner_runs_a_command_task_immediately(self):
        status, task = self.owner('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        self.assertEqual(status, 201)
        self.assertEqual(task['argv'], ARGV)
        self.assertEqual(self.owner('GET', '/api/state')[1]['attention'], [])

    def test_the_owner_exports_without_a_prompt(self):
        out = self.root / 'out'
        out.mkdir()
        self.assertEqual(self.owner('POST', '/api/archive/export', {'directory': str(out)})[0], 200)
        self.assertEqual(len(list(out.iterdir())), 2)


class GateTests(Fixture):
    def callers(self):
        cookie = self.pair_device()
        return {'agent': lambda m, p, b=None: self.agent(m, p, b),
                'device': lambda m, p, b=None: self.device(cookie, m, p, b)}

    def test_a_command_task_waits_for_approval_and_nothing_is_created(self):
        for name, call in self.callers().items():
            with self.subTest(caller=name):
                before = len(self.tasks())
                status, body = call('POST', '/api/tasks', {'title': f'Run {name}', 'instruction': 'run', 'argv': ARGV})
                self.assertEqual((status, body['status']), (202, 'approval_required'))
                self.assertEqual(body['approval']['kind'], 'command.create')
                self.assertEqual(len(self.tasks()), before)

    def test_the_request_appears_in_attention_with_what_it_will_do(self):
        _, body = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        attention = self.owner('GET', '/api/state')[1]['attention']
        self.assertEqual([a['id'] for a in attention], [body['approval_id']])
        self.assertEqual((attention[0]['requested_by'], attention[0]['status']), ('the orchestrator', 'pending'))
        self.assertIn('print(1)', attention[0]['summary'])
        self.assertIn('approval_requested', self.events())

    def test_approving_runs_exactly_what_was_asked_once(self):
        _, body = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        status, done = self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})
        self.assertEqual((status, done['status'], done['result']['status']), (200, 'executed', 201))
        (task,) = [t for t in self.tasks() if t['title'] == 'Run']
        self.assertEqual(task['argv'], ARGV)
        self.assertEqual(self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})[0], 409)
        self.assertEqual(len([t for t in self.tasks() if t['title'] == 'Run']), 1)
        self.assertEqual(self.owner('GET', '/api/state')[1]['attention'], [])
        self.assertEqual(self.agent('GET', f"/api/approvals/{body['approval_id']}")[1]['status'], 'executed')

    def test_denying_runs_nothing(self):
        _, body = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        status, done = self.owner('POST', f"/api/approvals/{body['approval_id']}/deny", {})
        self.assertEqual((status, done['status']), (200, 'denied'))
        self.assertEqual([t for t in self.tasks() if t['title'] == 'Run'], [])
        self.assertEqual(self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})[0], 409)
        self.assertEqual(self.agent('GET', f"/api/approvals/{body['approval_id']}")[1]['status'], 'denied')
        self.assertIn('approval_denied', self.events())

    def test_an_export_writes_nothing_until_approved(self):
        out = self.root / 'out'
        out.mkdir()
        for name, call in self.callers().items():
            with self.subTest(caller=name):
                status, body = call('POST', '/api/archive/export', {'directory': str(out)})
                self.assertEqual((status, body['approval']['kind']), (202, 'files.export'))
                self.assertEqual(list(out.iterdir()), [])
        approvals = self.owner('GET', '/api/state')[1]['attention']
        self.assertEqual(len(approvals), 2)
        self.owner('POST', f"/api/approvals/{approvals[0]['id']}/approve", {})
        self.assertEqual(len(list(out.iterdir())), 2)

    def test_github_settings_publish_recover_and_memory_acceptance_are_gated(self):
        _, proposed = self.owner('POST', '/api/memory/propose', {
            'title': 'Rule', 'kind': 'fact', 'body': 'Always do X', 'tags': [], 'source': 'test', 'request_id': 'r' * 12})
        memory_id = proposed['id']
        cases = [
            ('/api/github/configure', {'remote': 'attacker'}, 'github.configure'),
            ('/api/tasks/nosuchtask/publish', {'preview_id': 'p', 'request_id': 'r' * 12}, 'github.publish'),
            ('/api/tasks/nosuchtask/recover', {'process_tree_inspected': True}, 'task.recover'),
            (f'/api/memory/{memory_id}/review', {'status': 'active'}, 'memory.accept'),
            ('/api/knowledge/transition', {'path': 'Notes/a.md', 'status': 'verified'}, 'knowledge.transition'),
            ('/api/knowledge/rebuttal', {'original_path': 'Notes/a.md', 'title': 'x', 'explanation': 'y'}, 'knowledge.rebuttal'),
            ('/api/knowledge/note', {'title': 't', 'body': 'b', 'status': 'verified'}, 'knowledge.note'),
        ]
        for name, call in self.callers().items():
            for path, body, kind in cases:
                with self.subTest(caller=name, kind=kind):
                    status, asked = call('POST', path, body)
                    self.assertEqual((status, asked['approval']['kind']), (202, kind))
        memory = self.owner('GET', '/api/memory')[1]['memory']
        self.assertTrue(all(m['status'] != 'active' for m in memory), 'nothing was accepted without approval')
        self.assertEqual(self.owner('GET', '/api/state')[1]['github']['settings'].get('remote'), 'origin')

    def test_approving_a_failing_request_reports_the_failure(self):
        _, asked = self.agent('POST', '/api/tasks/nosuchtask/recover', {'process_tree_inspected': True})
        _, done = self.owner('POST', f"/api/approvals/{asked['approval_id']}/approve", {})
        self.assertEqual((done['status'], done['result']['status']), ('failed', 404))
        self.assertIn('approval_failed', self.events())

    def test_rejecting_a_memory_proposal_needs_no_approval(self):
        _, proposed = self.owner('POST', '/api/memory/propose', {
            'title': 'Rule', 'kind': 'fact', 'body': 'Maybe X', 'tags': [], 'source': 'test', 'request_id': 'q' * 12})
        status, _ = self.agent('POST', f"/api/memory/{proposed['id']}/review", {'status': 'rejected', 'reviewer': 'agent'})
        self.assertEqual(status, 200)

    def test_ordinary_work_is_not_gated_for_remote_callers(self):
        for name, call in self.callers().items():
            with self.subTest(caller=name):
                self.assertEqual(call('POST', '/api/tasks', {'title': f'Plain {name}', 'instruction': 'think'})[0], 201)
                self.assertEqual(call('POST', '/api/conversation/send', {'text': 'hello from ' + name, 'id': 'msg-' + name, 'source': name})[0], 200)
                self.assertEqual(call('POST', '/api/project/mode', {'mode': 'offline'})[0], 200)
                self.assertEqual(call('POST', '/api/project/mode', {'mode': 'online'})[0], 200)

    def test_a_retried_request_does_not_flood_attention(self):
        first = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})[1]
        again = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})[1]
        self.assertEqual(first['approval_id'], again['approval_id'])
        self.assertEqual(len(self.owner('GET', '/api/state')[1]['attention']), 1)

    def test_too_many_waiting_requests_are_refused(self):
        for i in range(MAX_PENDING):
            self.assertEqual(self.agent('POST', '/api/tasks', {'title': f't{i}', 'instruction': 'x', 'argv': ARGV + [str(i)]})[0], 202)
        self.assertEqual(self.agent('POST', '/api/tasks', {'title': 'extra', 'instruction': 'x', 'argv': ARGV + ['z']})[0], 429)

    def test_an_expired_request_cannot_be_approved(self):
        _, asked = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        clock = [self.server.approvals.clock()]
        self.server.approvals.clock = lambda: clock[0] + TTL_SECONDS + 1
        self.assertEqual(self.owner('POST', f"/api/approvals/{asked['approval_id']}/approve", {})[0], 409)
        self.assertEqual([t for t in self.tasks() if t['title'] == 'Run'], [])
        self.assertEqual(self.agent('GET', f"/api/approvals/{asked['approval_id']}")[1]['status'], 'expired')


class CannotRaiseOwnPermissionsTests(Fixture):
    def test_a_gated_caller_cannot_approve_deny_or_manage_anything(self):
        cookie = self.pair_device()
        _, asked = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        approval = asked['approval_id']
        paths = [(f'/api/approvals/{approval}/approve', {}), (f'/api/approvals/{approval}/deny', {}),
                 ('/api/pairing/begin', {}), ('/api/pairing/approve', {'request_id': 'x'}),
                 ('/api/pairing/revoke', {'device_id': 'x'})]
        for path, body in paths:
            with self.subTest(path=path):
                self.assertEqual(self.agent('POST', path, body)[0], 403)
                self.assertEqual(self.device(cookie, 'POST', path, body)[0], 403)
        self.assertEqual(self.agent('GET', '/api/remote')[0], 403)
        self.assertEqual(self.device(cookie, 'GET', '/api/remote')[0], 403)
        self.assertEqual(self.agent('GET', f'/api/approvals/{approval}')[1]['status'], 'pending')
        self.assertEqual([t for t in self.tasks() if t['title'] == 'Run'], [])

    def test_a_device_cannot_approve_its_own_request(self):
        cookie = self.pair_device()
        _, asked = self.device(cookie, 'POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        self.assertEqual(self.device(cookie, 'POST', f"/api/approvals/{asked['approval_id']}/approve", {})[0], 403)
        self.assertEqual(self.device(cookie, 'GET', f"/api/approvals/{asked['approval_id']}")[1]['status'], 'pending')

    def test_the_owner_token_over_a_remote_host_still_cannot_approve(self):
        _, asked = self.agent('POST', '/api/tasks', {'title': 'Run', 'instruction': 'run', 'argv': ARGV})
        self.assertEqual(self.call('POST', f"/api/approvals/{asked['approval_id']}/approve", token=OWNER, host=REMOTE, body={})[0], 403)

    def test_unknown_approval_ids(self):
        self.assertEqual(self.owner('GET', '/api/approvals/nope')[0], 404)
        self.assertEqual(self.owner('POST', '/api/approvals/nope/approve', {})[0], 404)
        self.assertEqual(self.call('GET', '/api/approvals/nope')[0], 401)


class BridgeTests(Fixture):
    url = property(lambda self: f'http://127.0.0.1:{self.port}')

    def tool(self, token, name, **arguments):
        reply = dispatch({'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}, self.url, token)
        return reply['result']['isError'], json.loads(reply['result']['content'][0]['text'])

    def test_the_orchestrator_bridge_waits_for_approval_and_can_follow_it(self):
        error, body = self.tool(AGENT, 'acc_create_task', title='Run', instruction='run', argv=ARGV)
        self.assertEqual((error, body['status']), (False, 'approval_required'))
        error, status = self.tool(AGENT, 'acc_approval_status', approval_id=body['approval_id'])
        self.assertEqual((error, status['status']), (False, 'pending'))
        self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})
        self.assertEqual(self.tool(AGENT, 'acc_approval_status', approval_id=body['approval_id'])[1]['status'], 'executed')

    def test_the_bridge_with_the_owner_token_is_not_gated(self):
        error, body = self.tool(OWNER, 'acc_create_task', title='Run', instruction='run', argv=ARGV)
        self.assertEqual((error, body['argv']), (False, ARGV))

    def test_the_bridge_cannot_approve_and_rejects_a_bad_id(self):
        error, body = self.tool(AGENT, 'acc_approval_status', approval_id='nope')
        self.assertTrue(error)
        reply = dispatch({'id': 2, 'method': 'tools/call', 'params': {'name': 'acc_approval_status',
                                                                      'arguments': {'approval_id': '../x'}}}, self.url, AGENT)
        self.assertTrue(reply['result']['isError'])

    def test_the_tools_explain_the_approval_step(self):
        listed = dispatch({'id': 3, 'method': 'tools/list'}, self.url, AGENT)['result']['tools']
        text = {t['name']: t['description'] for t in listed}
        for name in ('acc_create_task', 'acc_export_archive', 'acc_github_configure', 'acc_publish_task', 'acc_memory_review'):
            self.assertIn('acc_approval_status', text[name], name)
        self.assertIn('acc_approval_status', text)


if __name__ == '__main__':
    unittest.main()
