import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acc.tailscale import PORTS, Tailscale, TailscaleError

FAKE = str(Path(__file__).with_name('fake_tailscale.py'))
TARGET = 'http://127.0.0.1:8765'
HOST = 'box.tail1234.ts.net'


class TailscaleCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state_path = Path(self.dir.name) / 'state.json'
        self.write({})
        env = mock.patch.dict(os.environ, {'FAKE_TAILSCALE_STATE': str(self.state_path)})
        env.start()
        self.addCleanup(env.stop)
        self.ts = Tailscale([sys.executable, FAKE])

    def write(self, state):
        self.state_path.write_text(json.dumps(state))

    def state(self):
        return json.loads(self.state_path.read_text())


class ProbeTests(TailscaleCase):
    def test_ready_when_running_with_a_ts_net_name(self):
        status = self.ts.probe()
        self.assertEqual((status['installed'], status['ready'], status['hostname'], status['message']),
                         (True, True, HOST, ''))

    def test_not_running_says_to_sign_in(self):
        self.write({'backend': 'NeedsLogin'})
        status = self.ts.probe()
        self.assertEqual((status['installed'], status['ready']), (True, False))
        self.assertIn('sign in', status['message'])

    def test_no_https_device_name_says_to_enable_magicdns(self):
        self.write({'dns': ''})
        status = self.ts.probe()
        self.assertFalse(status['ready'])
        self.assertIn('MagicDNS', status['message'])

    def test_missing_executable_is_reported_as_not_installed(self):
        status = Tailscale(['/nonexistent/tailscale']).probe()
        self.assertEqual((status['installed'], status['ready']), (False, False))
        self.assertIn('Install Tailscale', status['message'])


class EnableTests(TailscaleCase):
    def test_creates_a_private_route_on_the_first_free_port_and_verifies_it(self):
        route = self.ts.enable(TARGET)
        self.assertEqual((route['port'], route['hostname'], route['target']), (8443, HOST, TARGET))
        self.assertEqual((route['host'], route['origin']), (f'{HOST}:8443', f'https://{HOST}:8443'))
        self.assertEqual(self.state()['web'][f'{HOST}:8443']['Handlers']['/']['Proxy'], TARGET)

    def test_keeps_its_own_port_on_restart(self):
        first = self.ts.enable(TARGET)
        again = self.ts.enable(TARGET, previous=first)
        self.assertEqual(again['port'], first['port'])
        self.assertEqual(len(self.state()['web']), 1)

    def test_never_touches_someone_elses_serve_config(self):
        foreign = {f'{HOST}:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3000'}}}}
        self.write({'web': foreign, 'tcp': {'8443': {'HTTPS': True}}})
        route = self.ts.enable(TARGET)
        self.assertEqual(route['port'], 8444)
        self.assertEqual(self.state()['web'][f'{HOST}:8443'], foreign[f'{HOST}:8443'])

    def test_a_changed_saved_route_is_not_reused(self):
        first = self.ts.enable(TARGET)
        state = self.state()
        state['web'][f"{HOST}:{first['port']}"]['Handlers']['/']['Proxy'] = 'http://127.0.0.1:9999'
        self.write(state)
        again = self.ts.enable(TARGET, previous=first)
        self.assertNotEqual(again['port'], first['port'])
        self.assertEqual(self.state()['web'][f"{HOST}:{first['port']}"]['Handlers']['/']['Proxy'], 'http://127.0.0.1:9999')

    def test_refuses_when_every_port_is_taken(self):
        self.write({'tcp': {str(p): {'HTTPS': True} for p in PORTS}})
        with self.assertRaisesRegex(TailscaleError, 'already in use'):
            self.ts.enable(TARGET)

    def test_refuses_a_publicly_shared_port(self):
        self.write({'funnel': {f'{HOST}:8443': True}})
        with self.assertRaisesRegex(TailscaleError, 'publicly shared'):
            self.ts.enable(TARGET)
        self.assertNotIn('web', self.state())

    def test_refuses_when_tailscale_is_not_ready(self):
        self.write({'backend': 'Stopped'})
        with self.assertRaisesRegex(TailscaleError, 'sign in'):
            self.ts.enable(TARGET)

    def test_serve_failure_explains_what_to_enable(self):
        self.write({'fail_serve': True})
        with self.assertRaisesRegex(TailscaleError, 'Enable HTTPS'):
            self.ts.enable(TARGET)

    def test_an_unverifiable_route_stays_off(self):
        self.write({'serve_noop': True})
        with self.assertRaisesRegex(TailscaleError, 'could not be verified'):
            self.ts.enable(TARGET)


class DisableTests(TailscaleCase):
    def test_removes_only_its_own_route(self):
        route = self.ts.enable(TARGET)
        self.assertTrue(self.ts.disable(route))
        self.assertEqual(self.state().get('web'), {})

    def test_leaves_a_changed_route_alone(self):
        route = self.ts.enable(TARGET)
        state = self.state()
        state['web'][f"{HOST}:{route['port']}"]['Handlers']['/']['Proxy'] = 'http://127.0.0.1:9999'
        self.write(state)
        self.assertFalse(self.ts.disable(route))
        self.assertIn(f"{HOST}:{route['port']}", self.state()['web'])

    def test_nothing_saved_does_nothing(self):
        self.assertFalse(self.ts.disable(None))
        self.assertFalse(self.ts.disable({}))


class OwnsTests(unittest.TestCase):
    saved = {'hostname': HOST, 'port': 8443, 'target': TARGET}

    def config(self, handlers):
        return {'Web': {f'{HOST}:8443': {'Handlers': handlers}}}

    def test_exact_single_handler_is_ours_and_a_trailing_slash_does_not_matter(self):
        self.assertTrue(Tailscale.owns(self.config({'/': {'Proxy': TARGET}}), self.saved))
        self.assertTrue(Tailscale.owns(self.config({'/': {'Proxy': TARGET + '/'}}), self.saved))

    def test_extra_handlers_or_other_targets_are_not_ours(self):
        self.assertFalse(Tailscale.owns(self.config({'/': {'Proxy': TARGET}, '/x': {'Proxy': TARGET}}), self.saved))
        self.assertFalse(Tailscale.owns(self.config({'/': {'Proxy': 'http://127.0.0.1:1'}}), self.saved))
        self.assertFalse(Tailscale.owns({}, self.saved))
        self.assertFalse(Tailscale.owns(self.config({'/': {'Proxy': TARGET}}), None))


class CommandTests(unittest.TestCase):
    def test_a_failing_command_raises_with_its_message(self):
        ts = Tailscale(['tailscale'], run=lambda argv: (1, '', 'boom'))
        with self.assertRaisesRegex(TailscaleError, 'boom'):
            ts.command('status')

    def test_a_command_that_hangs_becomes_a_clear_error(self):
        def hang(argv):
            raise subprocess.TimeoutExpired(argv, 12)
        ts = Tailscale(['tailscale'], run=hang)
        with self.assertRaisesRegex(TailscaleError, 'did not answer'):
            ts.config()
        status = ts.probe()
        self.assertEqual((status['installed'], status['ready']), (True, False))

    def test_unreadable_serve_status_raises(self):
        ts = Tailscale(['tailscale'], run=lambda argv: (0, 'not json', ''))
        with self.assertRaises(TailscaleError):
            ts.config()


if __name__ == '__main__':
    unittest.main()
