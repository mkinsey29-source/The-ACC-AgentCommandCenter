"""Regression tests for the independent review of SC-1..SC-9 and the owner-token fix (2026-10-12).

Each class names the finding it reproduces. They fail on the reviewed commits and pass now.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from acc.core import Coordinator
from acc.pairing import Pairing
from acc.reach import Reach, check
from acc.server import Server
from acc.tailscale import Tailscale, TailscaleError
from test_gated_access import AGENT, OWNER, REMOTE, Fixture as GatedFixture

FAKE = str(Path(__file__).with_name('fake_tailscale.py'))
TARGET = 'http://127.0.0.1:8765'


class Launch(GatedFixture):
    """OTF-01: the owner credential is handed out on this computer only, whatever the caller is authorized for."""

    def test_a_paired_device_cannot_redeem_an_owner_launch_code(self):
        cookie = self.pair_device()
        _, minted = self.owner('POST', '/api/launch/new', {})
        code = minted['url'].split('launch=')[1]
        status, body = self.device(cookie, 'POST', '/api/launch', {'code': code})
        self.assertEqual(status, 403)
        self.assertNotIn('token', body)
        # the refusal did not use up the code: the owner's own browser can still redeem it
        status, body = self.call('POST', '/api/launch', body={'code': code})
        self.assertEqual((status, body['token']), (200, OWNER))

    def test_the_agent_credential_on_a_remote_host_gets_no_owner_token(self):
        _, minted = self.owner('POST', '/api/launch/new', {})
        code = minted['url'].split('launch=')[1]
        status, body = self.call('POST', '/api/launch', host=REMOTE, token=AGENT, body={'code': code})
        self.assertEqual(status, 403)
        self.assertNotIn('token', body)

    def test_the_page_never_stores_the_owner_token(self):
        app = (Path(__file__).parent.parent / 'acc' / 'web' / 'app.js').read_text()
        import re
        self.assertNotIn('sessionStorage', app)
        for call in re.findall(r'localStorage\.setItem\([^)]*\)', app):  # drafts may use it; the token never may
            self.assertNotRegex(call, r'(?i)token')


class GatedStart(GatedFixture):
    """SC9-02: starting an existing local-command task is gated; SC9-03: the owner sees the whole command."""

    def make_task(self, marker):
        argv = [sys.executable, '-c', f'open({str(marker)!r}, "w").write("ran")']
        status, task = self.owner('POST', '/api/tasks', {'title': 'Write marker', 'instruction': 'run', 'argv': argv})
        self.assertEqual(status, 201, task)
        return task

    def test_an_agent_cannot_start_an_existing_command_task_without_approval(self):
        marker = self.root / 'marker-agent'
        task = self.make_task(marker)
        status, body = self.agent('POST', f"/api/tasks/{task['id']}/start", {})
        self.assertEqual((status, body['status']), (202, 'approval_required'))
        self.assertFalse(marker.exists())
        self.assertEqual(body['approval']['kind'], 'command.start')

    def test_a_paired_device_cannot_either(self):
        marker = self.root / 'marker-device'
        task = self.make_task(marker)
        cookie = self.pair_device()
        status, body = self.device(cookie, 'POST', f"/api/tasks/{task['id']}/start", {})
        self.assertEqual((status, body['status']), (202, 'approval_required'))
        self.assertFalse(marker.exists())

    def test_the_owner_approving_runs_it(self):
        marker = self.root / 'marker-owner'
        task = self.make_task(marker)
        _, body = self.agent('POST', f"/api/tasks/{task['id']}/start", {})
        status, answered = self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})
        self.assertEqual(status, 200, answered)
        for _ in range(100):
            if marker.exists():
                break
            threading.Event().wait(.1)
        self.assertTrue(marker.exists())

    def test_the_owner_starting_it_directly_is_not_gated(self):
        marker = self.root / 'marker-direct'
        task = self.make_task(marker)
        status, body = self.owner('POST', f"/api/tasks/{task['id']}/start", {})
        self.assertEqual(status, 200, body)

    def test_the_approval_shows_the_whole_command_not_only_the_clipped_start(self):
        argv = [sys.executable, '-c', ' ' * 400 + 'print("late code")']
        _, one = self.agent('POST', '/api/tasks', {'title': 't', 'instruction': 'i', 'argv': argv})
        _, two = self.agent('POST', '/api/tasks', {'title': 't', 'instruction': 'i', 'argv': argv[:2] + [' ' * 400 + 'print("other")']})
        shown = [a for a in self.owner('GET', '/api/state')[1]['attention']]
        commands = sorted(a['details']['full_command'] for a in shown)
        self.assertEqual(len(commands), 2)
        self.assertIn('late code', commands[0] + commands[1])
        self.assertIn('other', commands[0] + commands[1])
        self.assertNotEqual(commands[0], commands[1])


class GatedCheckin(GatedFixture):
    """SC9-01: a check-in cannot carry verified knowledge past the owner."""

    VERIFIED = {'title': 'Claim', 'summary': 's', 'worker': 'orchestrator', 'scopes': ['Knowledge'],
                'completed_knowledge': [{'title': 'Fact', 'body': 'b', 'scope': 'Knowledge', 'status': 'verified',
                                         'evidence': ['self assertion']}]}

    def test_verified_completed_knowledge_waits_for_the_owner_for_both_callers(self):
        cookie = self.pair_device()
        for who, call in (('agent', lambda: self.agent('POST', '/api/knowledge/checkin', self.VERIFIED)),
                          ('device', lambda: self.device(cookie, 'POST', '/api/knowledge/checkin', self.VERIFIED))):
            with self.subTest(who=who):
                status, body = call()
                self.assertEqual((status, body.get('status')), (202, 'approval_required'), body)

    def test_an_explicit_verified_or_active_check_in_status_waits(self):
        for status_value in ('verified', 'active'):
            with self.subTest(status=status_value):
                payload = {'title': f'T {status_value}', 'summary': 's', 'worker': 'o', 'scopes': ['Knowledge'], 'status': status_value}
                status, body = self.agent('POST', '/api/knowledge/checkin', payload)
                self.assertEqual((status, body.get('status')), (202, 'approval_required'))

    def test_supported_knowledge_is_not_gated(self):
        from acc.gates import classify
        payload = {**self.VERIFIED, 'status': 'supported',
                   'completed_knowledge': [{**self.VERIFIED['completed_knowledge'][0], 'status': 'supported'}]}
        self.assertIsNone(classify(self.c, ['api', 'knowledge', 'checkin'], payload))

    def test_malformed_completed_knowledge_does_not_crash_the_gate(self):
        from acc.gates import classify
        for bad in ('x', [1, None, 'y'], {'a': 1}):
            with self.subTest(bad=bad):
                self.assertIsNone(classify(self.c, ['api', 'knowledge', 'checkin'], {'status': 'supported', 'completed_knowledge': bad}))


class OwnerListener(unittest.TestCase):
    """SC8-01 / SC8-02: a network-facing listener never answers as the owner; the owner has a loopback door."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Coordinator(self.root, self.root / 'state')
        self.reach = Reach('custom', '127.0.0.1', ['localhost:8765'], secure_cookie=False)
        self.pairing = Pairing(self.root / 'state' / 'pairing.json')
        # A test cannot bind a private interface here, so a loopback socket plays the network-facing listener.
        self.public = Server(('127.0.0.1', 0), self.c, OWNER, reach=self.reach, pairing=self.pairing, agent_token=AGENT, role='public')
        self.owner_server = Server(('127.0.0.1', 0), self.c, OWNER, reach=self.reach, pairing=self.pairing, agent_token=AGENT,
                                   role='owner', shared=self.public)
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.public, self.owner_server)]
        for t in self.threads:
            t.start()
        # the host the network sees for the public listener, which here is a loopback name
        self.reach.set_remote_hosts([f'localhost:{self.public.server_port}'])

    def tearDown(self):
        self.c.close()
        for s in (self.public, self.owner_server):
            s.shutdown()
            s.server_close()
        self.tmp.cleanup()

    def request(self, server, host, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        head = {'Host': host, **(headers or {})}
        raw = None
        if body is not None:
            raw, head['Content-Type'] = json.dumps(body), 'application/json'
        conn.request(method, path, body=raw, headers=head)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, data

    def test_the_owner_can_sign_in_on_the_loopback_listener(self):
        code = self.public.launch.mint()['code']
        status, body = self.request(self.owner_server, f'127.0.0.1:{self.owner_server.server_port}', 'POST', '/api/launch', {'code': code})
        self.assertEqual((status, body['token']), (200, OWNER))

    def test_the_network_facing_listener_never_answers_as_the_owner(self):
        code = self.public.launch.mint()['code']
        local = f'127.0.0.1:{self.public.server_port}'
        self.assertEqual(self.request(self.public, local, 'POST', '/api/launch', {'code': code})[0], 403)
        self.assertEqual(self.request(self.public, local, 'GET', '/api/state', headers={'Authorization': 'Bearer ' + OWNER})[0], 403)

    def test_the_owner_listener_refuses_remote_hosts(self):
        status, _ = self.request(self.owner_server, f'localhost:{self.public.server_port}', 'GET', '/')
        self.assertEqual(status, 403)

    def test_a_published_loopback_name_pairs_as_a_device_and_is_told_it_is_one(self):
        host = f'localhost:{self.public.server_port}'
        status, page = self.request(self.public, host, 'GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'name="acc-role" content="device"', page)
        _, owner_page = self.request(self.owner_server, f'127.0.0.1:{self.owner_server.server_port}', 'GET', '/')
        self.assertIn(b'name="acc-role" content="owner"', owner_page)
        begun = self.pairing.begin()
        status, claimed = self.request(self.public, host, 'POST', '/api/pairing/claim', {'secret': begun['token'], 'name': 'Pixel'})
        self.assertEqual(status, 200, claimed)

    def test_both_listeners_share_approvals_and_pairings(self):
        self.assertIs(self.public.approvals, self.owner_server.approvals)
        self.assertIs(self.public.launch, self.owner_server.launch)
        self.assertIs(self.public.pairing, self.owner_server.pairing)


class ReachCheckPreflight(unittest.TestCase):
    """SC7-01: reach-check refuses what startup would refuse; SC8-03: the Serve change is narrowed and verified."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'state.json'
        env = mock.patch.dict(os.environ, {'FAKE_TAILSCALE_STATE': str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        self.ts = Tailscale([sys.executable, FAKE])
        self.write({})

    def write(self, state):
        self.path.write_text(json.dumps(state))

    def test_the_dry_run_fails_when_every_serve_port_is_taken(self):
        self.write({'tcp': {str(p): {'HTTPS': True} for p in (8443, 8444, 8445, 8446)}})
        findings, reach = check('tailscale-serve', port=8765, token='T' * 43, route=self.ts)
        self.assertIsNone(reach)
        self.assertIn('already in use', findings[-1].detail)

    def test_the_dry_run_fails_on_a_publicly_shared_port(self):
        self.write({'funnel': {'box.tail1234.ts.net:8443': True}})
        findings, reach = check('tailscale-serve', port=8765, token='T' * 43, route=self.ts)
        self.assertIsNone(reach)
        self.assertIn('publicly shared', findings[-1].detail)

    def test_the_dry_run_passes_and_changes_nothing_when_a_port_is_free(self):
        self.write({})
        findings, reach = check('tailscale-serve', port=8765, token='T' * 43, route=self.ts)
        self.assertIsNotNone(reach)
        self.assertIn('tailscale route', {f.setting for f in findings})
        self.assertEqual(json.loads(self.path.read_text()), {})

    def test_a_change_between_planning_and_writing_is_refused(self):
        calls = {'n': 0}
        real = self.ts.plan

        def moving(previous=None):
            calls['n'] += 1
            if calls['n'] == 2:  # someone claimed 8443 after ACC's first read
                self.write({'tcp': {'8443': {'HTTPS': True}}, 'web': {'box.tail1234.ts.net:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3000'}}}}})
            return real(previous)
        self.ts.plan = moving
        with self.assertRaisesRegex(TailscaleError, 'changed while ACC was preparing'):
            self.ts.enable(TARGET)
        self.assertEqual(json.loads(self.path.read_text())['web']['box.tail1234.ts.net:8443']['Handlers']['/']['Proxy'], 'http://127.0.0.1:3000')

    def test_another_setting_changing_during_the_write_is_reported(self):
        original = self.ts.command

        def command(*args):
            out = original(*args)
            if args[:2] == ('serve', '--bg'):  # another tool adds a route right after ours
                state = json.loads(self.path.read_text())
                state['web']['box.tail1234.ts.net:8444'] = {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3000'}}}
                state['tcp']['8444'] = {'HTTPS': True}
                self.write(state)
            return out
        self.ts.command = command
        with self.assertRaisesRegex(TailscaleError, 'Another Tailscale Serve setting changed'):
            self.ts.enable(TARGET)


if __name__ == '__main__':
    unittest.main()
