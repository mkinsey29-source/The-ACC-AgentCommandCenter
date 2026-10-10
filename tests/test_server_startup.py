"""``python -m acc.server`` startup: refusals have no side effects, and the Tailscale route is owned."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
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

    def run_main(self, *argv, serve=None):
        def fake_serve(srv, *a, **k):
            self.served.append({'hosts': set(srv.reach.remote_hosts()), 'route': srv.route,
                                'remote_origin': srv.remote_origin(), 'bind': srv.server_address[0]})
            if serve:
                serve(srv)
            raise KeyboardInterrupt
        out, err = io.StringIO(), io.StringIO()
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


class LocalAndRemoteStartTests(StartupCase):
    def test_default_start_is_local_only_and_prints_no_remote_line(self):
        code, out, err = self.run_main('--port', '0')
        self.assertEqual(code, 0)
        self.assertEqual(self.served[0]['bind'], '127.0.0.1')
        self.assertEqual(self.served[0]['hosts'], set())
        self.assertNotIn('Remote:', out)
        self.assertTrue((self.state / 'token').exists())
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
        token = (self.state / 'token').read_text().strip()
        self.assertNotIn(token, remote_line)


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
