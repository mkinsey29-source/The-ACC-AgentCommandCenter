"""``python -m acc.server`` startup: refusals have no side effects, and the Tailscale route is owned."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from acc import server

FAKE = Path(__file__).with_name('fake_tailscale.py')


class StartupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state'
        self.served = []
        self.servers = []
        self.out = self.err = None

    def run_main(self, *argv, serve=None):
        def fake_serve(srv, *a, **k):
            self.servers.append(srv)
            self.served.append({'hosts': set(srv.reach.remote_hosts()), 'route': srv.route,
                                'remote_origin': srv.remote_origin(), 'bind': srv.server_address[0],
                                'agent_token': srv.agent_token})
            if serve:
                serve(srv)
            raise KeyboardInterrupt
        out, err = self.out, self.err = io.StringIO(), io.StringIO()
        args = ['acc.server', '--project', str(self.project), '--state-dir', str(self.state), *argv]
        code = 0
        with mock.patch.object(sys, 'argv', args), mock.patch.object(server.Server, 'serve_forever', fake_serve), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                server.main()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()


class RefusalTests(StartupCase):
    def test_a_refusal_prints_the_reason_and_changes_nothing(self):
        for argv in (('--reach', 'custom', '--host', '8.8.8.8', '--allow-host', 'a.example.com'),
                     ('--reach', 'private', '--host', '0.0.0.0'),
                     ('--reach', 'docker', '--allow-host', 'localhost:8765'),
                     ('--reach', 'custom', '--host', '192.168.1.20'),
                     ('--reach', 'tailscale-serve', '--host', '192.168.1.20'),
                     ('--host', '192.168.1.20')):
            with self.subTest(argv=argv):
                with mock.patch.dict(os.environ, {'ACC_IN_DOCKER': ''}), mock.patch('acc.reach.in_container', return_value=False):
                    code, out, err = self.run_main(*argv)
                self.assertEqual(code, 2)
                self.assertIn('ACC refused to start:', err)
                self.assertFalse(self.state.exists(), 'a refusal must not create state')
                self.assertEqual(self.served, [])

    def test_reach_check_reports_and_creates_nothing(self):
        code, out, err = self.run_main('--reach-check')
        self.assertEqual((code, self.state.exists()), (0, False))
        self.assertIn('remote access', out)
        code, out, err = self.run_main('--reach', 'private', '--host', '0.0.0.0', '--reach-check')
        self.assertEqual((code, self.state.exists()), (2, False))
        self.assertIn('FAIL', out)

    def test_a_port_that_is_already_taken_is_a_clean_refusal(self):
        import socket
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        self.addCleanup(sock.close)
        code, out, err = self.run_main('--port', str(sock.getsockname()[1]))
        self.assertEqual(code, 2)
        self.assertIn('ACC refused to start:', err)


class AgentTokenTests(StartupCase):
    def test_start_creates_a_private_agent_token_different_from_the_owner_token(self):
        code, out, err = self.run_main('--port', '0')
        self.assertEqual(code, 0, err)
        owner = self.servers[0].token
        agent = (self.state / 'agent-token').read_text().strip()
        self.assertNotEqual(owner, agent)
        self.assertGreaterEqual(len(agent), 32)
        self.assertEqual(self.served[0]['agent_token'], agent)
        if os.name != 'nt':
            self.assertEqual((self.state / 'agent-token').stat().st_mode & 0o777, 0o600)
        self.assertNotIn(agent, out + err)
        self.assertIn('agent-token', out)

    def test_a_restart_keeps_the_same_agent_token(self):
        self.run_main('--port', '0')
        first = (self.state / 'agent-token').read_text()
        self.run_main('--port', '0')
        self.assertEqual((self.state / 'agent-token').read_text(), first)
        self.assertEqual(self.served[1]['agent_token'], first.strip())

    def test_a_weak_agent_token_file_is_a_clean_refusal(self):
        self.state.mkdir()
        (self.state / 'agent-token').write_text('short')
        code, out, err = self.run_main('--port', '0')
        self.assertEqual(code, 2)
        self.assertIn('ACC refused to start:', err)
        self.assertEqual(self.served, [])

    def test_the_reach_check_creates_no_agent_token(self):
        self.run_main('--reach-check')
        self.assertFalse(self.state.exists())


class OwnerTokenTests(StartupCase):
    def files_containing(self, secret):
        return [str(p) for p in self.root.rglob('*') if p.is_file() and secret.encode() in p.read_bytes()]

    def test_the_owner_token_is_never_written_or_printed(self):
        code, out, err = self.run_main('--port', '0', '--reach', 'custom', '--host', '127.0.0.1',
                                       '--allow-host', 'acc.test:9000')
        self.assertEqual(code, 0, err)
        token = self.servers[0].token
        self.assertEqual(self.files_containing(token), [], 'the owner token reached the disk')
        self.assertNotIn(token, out + err)
        self.assertFalse((self.state / 'token').exists())

    def test_the_owner_token_is_new_at_every_start(self):
        self.run_main('--port', '0')
        self.run_main('--port', '0')
        self.assertNotEqual(self.servers[0].token, self.servers[1].token)

    def test_a_legacy_token_file_is_removed_with_a_notice(self):
        self.state.mkdir()
        (self.state / 'token').write_text('old-secret-value')
        code, out, err = self.run_main('--port', '0')
        self.assertEqual(code, 0, err)
        self.assertFalse((self.state / 'token').exists())
        self.assertIn('Removed the old owner token file', out)
        self.assertNotIn('old-secret-value', out + err)

    def test_the_printed_link_carries_a_one_use_code_not_the_token(self):
        seen = {}
        def check(srv):
            line = next(l for l in self.out.getvalue().splitlines() if l.startswith('ACC: '))
            code = line.split('#launch=')[1]
            seen.update(line=line, code=code, first=srv.launch.redeem(code))
            try:
                srv.launch.redeem(code)
            except Exception as exc:
                seen['second'] = type(exc).__name__
        self.run_main('--port', '0', serve=check)
        self.assertNotIn('#token=', seen['line'])
        self.assertEqual(seen['first'], self.servers[0].token)
        self.assertEqual(seen['second'], 'LaunchError')

    def test_the_browser_is_opened_with_its_own_one_use_code(self):
        opened, done = [], threading.Event()
        def fake_open(url):
            opened.append(url)
            done.set()
        def check(srv):
            done.wait(3)
        with mock.patch('webbrowser.open', side_effect=fake_open):
            code, out, err = self.run_main('--port', '0', '--open-browser', serve=check)
        (url,) = opened
        token = self.servers[0].token
        self.assertIn('#launch=', url)
        self.assertNotIn(token, url)
        self.assertNotIn('#token=', url)
        printed = next(l for l in out.splitlines() if l.startswith('ACC: ')).split('#launch=')[1]
        self.assertNotEqual(url.split('#launch=')[1], printed)

    def test_a_custom_agent_token_file_keeps_the_credential_out_of_the_state_folder(self):
        elsewhere = self.root / 'agent-home' / 'orchestrator-token'
        code, out, err = self.run_main('--port', '0', '--agent-token-file', str(elsewhere))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.servers[0].agent_token, elsewhere.read_text())
        self.assertFalse((self.state / 'agent-token').exists())
        if os.name != 'nt':
            self.assertEqual(elsewhere.stat().st_mode & 0o777, 0o600)
        self.assertIn(str(elsewhere), out)
        self.assertEqual(self.files_containing(self.servers[0].token), [])


class LocalAndRemoteStartTests(StartupCase):
    def test_default_start_is_local_only_and_prints_no_remote_line(self):
        code, out, err = self.run_main('--port', '0')
        self.assertEqual(code, 0)
        self.assertEqual(self.served[0]['bind'], '127.0.0.1')
        self.assertEqual(self.served[0]['hosts'], set())
        self.assertNotIn('Remote:', out)
        self.assertFalse((self.state / 'token').exists())
        self.assertFalse((self.state / 'pairing.json').exists())

    def test_custom_mode_binds_and_accepts_the_named_host(self):
        code, out, err = self.run_main('--port', '0', '--reach', 'custom', '--host', '127.0.0.1',
                                       '--allow-host', 'acc.test:9000')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.served[0]['hosts'], {'acc.test:9000'})
        self.assertIn('Remote: http://acc.test:9000', out)
        self.assertNotIn(str(self.state / 'token'), out + err)

    def test_the_control_token_is_never_printed_for_remote_hosts(self):
        code, out, err = self.run_main('--port', '0', '--reach', 'custom', '--host', '127.0.0.1',
                                       '--allow-host', 'acc.test:9000')
        remote_line = next(line for line in out.splitlines() if line.startswith('Remote:'))
        self.assertNotIn(self.servers[0].token, remote_line)


@unittest.skipIf(os.name == 'nt', 'uses a shell wrapper for the fake tailscale command')
class TailscaleServeStartTests(StartupCase):
    def setUp(self):
        super().setUp()
        self.fake_state = self.root / 'fake.json'
        self.fake_state.write_text('{}')
        wrapper = self.root / 'tailscale'
        wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        env = mock.patch.dict(os.environ, {'ACC_TAILSCALE': str(wrapper), 'FAKE_TAILSCALE_STATE': str(self.fake_state)})
        env.start()
        self.addCleanup(env.stop)

    def web(self):
        return json.loads(self.fake_state.read_text()).get('web')

    def test_the_route_exists_while_serving_and_is_removed_on_shutdown(self):
        seen = {}
        code, out, err = self.run_main('--port', '0', '--reach', 'tailscale-serve',
                                       serve=lambda srv: seen.update(web=self.web()))
        self.assertEqual(code, 0, err)
        served = self.served[0]
        self.assertEqual(served['bind'], '127.0.0.1')
        self.assertEqual(served['hosts'], {'box.tail1234.ts.net:8443'})
        self.assertEqual(served['remote_origin'], 'https://box.tail1234.ts.net:8443')
        self.assertIn('Remote: https://box.tail1234.ts.net:8443', out)
        (route,) = seen['web'].values()
        self.assertTrue(route['Handlers']['/']['Proxy'].startswith('http://127.0.0.1:'))
        self.assertEqual(self.web(), {})
        self.assertEqual(json.loads((self.state / 'reach.json').read_text()), {'route': None})

    def test_tailscale_not_ready_is_a_refusal_with_no_state(self):
        self.fake_state.write_text(json.dumps({'backend': 'NeedsLogin'}))
        code, out, err = self.run_main('--port', '0', '--reach', 'tailscale-serve')
        self.assertEqual(code, 2)
        self.assertIn('sign in', err)
        self.assertFalse(self.state.exists())

    def test_a_route_that_cannot_be_verified_stops_the_start(self):
        self.fake_state.write_text(json.dumps({'serve_noop': True}))
        code, out, err = self.run_main('--port', '0', '--reach', 'tailscale-serve')
        self.assertEqual(code, 2)
        self.assertIn('could not be verified', err)
        self.assertEqual(self.served, [])

    def test_a_publicly_shared_port_is_refused(self):
        self.fake_state.write_text(json.dumps({'funnel': {'box.tail1234.ts.net:8443': True}}))
        code, out, err = self.run_main('--port', '0', '--reach', 'tailscale-serve')
        self.assertEqual(code, 2)
        self.assertIn('publicly shared', err)


if __name__ == '__main__':
    unittest.main()
