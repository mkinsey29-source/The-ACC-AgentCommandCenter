"""Saved host configuration, process boundaries, and truthful setup diagnostics."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from acc import setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project with spaces & literal $text'
        self.project.mkdir()
        self.host = self.root / 'host settings'

    def initialize(self, **kwargs):
        return setup.initialize(argparse.Namespace(yes=True, project=str(self.project), **kwargs), self.host)

    def test_idempotent_setup_preserves_agents_settings_and_state(self):
        settings = self.initialize(port=8888)
        agents = Path(settings['agents'])
        original = '{"agents": [], "custom": "keep formatting and settings"}\n'
        agents.write_text(original)
        token = Path(settings['state_dir']) / 'token'
        token.write_text('private-token')
        settings['custom'] = {'preserve': True}
        setup.write_json(self.host / 'launcher.json', settings)
        again = self.initialize()
        self.assertEqual(again, settings)
        self.assertEqual(agents.read_text(), original)
        self.assertEqual(token.read_text(), 'private-token')
        self.assertNotIn('private-token', (self.host / 'hermes-mcp.json').read_text())

    def test_first_setup_requires_explicit_noninteractive_project(self):
        with self.assertRaisesRegex(ValueError, '--project'):
            setup.initialize(argparse.Namespace(yes=True), self.host)
        self.assertFalse(self.host.exists())

    def test_mcp_uses_absolute_paths_and_launcher_uses_argv(self):
        settings = self.initialize()
        fragment = setup.read_json(self.host / 'hermes-mcp.json')['mcp_servers']['acc']
        self.assertTrue(Path(fragment['command']).is_absolute())
        self.assertTrue(Path(fragment['args'][0]).is_absolute())
        self.assertEqual(fragment['args'][-1], str(Path(settings['state_dir']) / 'token'))
        with patch.object(setup.subprocess, 'call', return_value=0) as run:
            self.assertEqual(setup.launch(settings), 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--project') + 1], str(self.project))
        self.assertIn('--open-browser', argv)
        self.assertEqual(run.call_args.kwargs, {'cwd': str(setup.REPO)})
        with patch.object(setup.subprocess, 'call', return_value=0) as run:
            setup.launch(settings, False)
        self.assertNotIn('--open-browser', run.call_args.args[0])

    def test_fresh_setup_does_not_enable_inference(self):
        settings = self.initialize()
        self.assertFalse(setup.read_json(settings['agents'])['conversation']['enabled'])

    def test_new_project_uses_distinct_state_and_preserves_old_state(self):
        old = self.initialize()
        marker = Path(old['state_dir']) / 'marker'
        marker.write_text('retain')
        other = self.root / 'other'
        other.mkdir()
        new = setup.initialize(argparse.Namespace(yes=True, project=str(other)), self.host)
        self.assertNotEqual(old['state_dir'], new['state_dir'])
        self.assertEqual(marker.read_text(), 'retain')

    def test_invalid_agents_does_not_replace_saved_configuration(self):
        settings = self.initialize()
        original = (self.host / 'launcher.json').read_bytes()
        with self.assertRaises(ValueError):
            self.initialize(agents=str(self.root / 'missing.json'))
        self.assertEqual(original, (self.host / 'launcher.json').read_bytes())

    def test_local_endpoint_rejects_remote_credentials_and_redirects(self):
        for url in ('https://127.0.0.1', 'http://example.com/v1/models',
                    'http://user:secret@localhost/', 'http://localhost/?token=secret',
                    'http://localhost:bad', 'http://localhost/#secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                setup.local_endpoint(url)
        self.assertEqual(setup.local_endpoint('http://[::1]:8080/v1/models/'), 'http://[::1]:8080/v1/models')
        self.assertIsNone(setup.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com'))

    def test_doctor_distinguishes_installed_tools_and_auth_and_missing_voice(self):
        settings = self.initialize()
        def which(name):
            return '/bin/' + name if name in ('git', 'gh', 'hermes') else None
        def probe(argv):
            return argv[-2:] != ['auth', 'status'] and argv[-1] != 'import faster_whisper'
        with patch.object(setup.shutil, 'which', side_effect=which), \
                patch.object(setup, 'probe', side_effect=probe), \
                patch.object(setup, 'endpoint_available', return_value=False):
            checks = {c['name']: c for c in setup.doctor(settings)}
        self.assertTrue(checks['github_cli']['ok'])
        self.assertFalse(checks['github_auth']['ok'])
        self.assertTrue(checks['hermes']['ok'])
        self.assertIn('unverified', checks['hermes']['detail'])
        self.assertFalse(checks['voice_package']['ok'])
        self.assertFalse(checks['voice_model']['ok'])
        self.assertFalse(checks['local_endpoint']['ok'])

    def test_doctor_reads_actual_configured_voice_model(self):
        model = self.root / 'model files'
        model.mkdir()
        for name in setup.MODEL_FILES:
            (model / name).touch()
        settings = self.initialize(voice_model_dir=str(model), voice_python=sys.executable)
        agents = setup.read_json(settings['agents'])
        self.assertIn('--device', agents['transcription']['argv'])
        self.assertIn('cpu', agents['transcription']['argv'])
        model.rename(self.root / 'moved model')
        with patch.object(setup, 'probe', return_value=True), \
                patch.object(setup, 'endpoint_available', return_value=False):
            checks = {c['name']: c for c in setup.doctor(settings)}
        self.assertFalse(checks['voice_model']['ok'])

    def test_probe_timeout_and_no_output_leak(self):
        with patch.object(setup.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gh', 8)):
            self.assertFalse(setup.probe(['gh', 'auth', 'status']))
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, 'secret', 'secret')) as run:
            self.assertFalse(setup.probe(['gh', 'auth', 'status']))
        self.assertEqual(run.call_args.kwargs['stdout'], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.PIPE)

    @unittest.skipIf(os.name == 'nt', 'Bash launcher runs on Linux/macOS')
    def test_shell_launcher_from_unrelated_cwd(self):
        environment = dict(os.environ, ACC_CONFIG_DIR=str(self.host))
        result = subprocess.run([str(setup.REPO / 'start-acc.sh'), 'init', '--yes', '--project', str(self.project)],
                                cwd=str(self.root), env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(setup.read_json(self.host / 'launcher.json')['project'], str(self.project))


if __name__ == '__main__':
    unittest.main()
