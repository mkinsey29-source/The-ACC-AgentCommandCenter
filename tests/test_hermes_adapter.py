"""Unit tests for acc/hermes.py's own subprocess-handling robustness, independent of a real
Hermes install: the fixtures in test_workflow.py exercise the happy/failure CLI paths through a
real subprocess; this file targets the mid-stream-crash cleanup path directly instead, since that
is hard to trigger reliably by racing a real pipe."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from acc import hermes


class HermesAdapterCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.packet_path = self.root / 'task.json'
        self.packet_path.write_text(json.dumps({'task': {'id': 't1'}, 'project': str(self.root)}))

    def tearDown(self):
        self.tmp.cleanup()

    def args(self):
        return argparse.Namespace(packet=str(self.packet_path), executable='hermes',
                                   profile=None, provider=None, model=None)

    def test_broken_stdout_stream_kills_the_subprocess_before_reraising(self):
        proc = MagicMock()

        def broken_lines():
            yield '{"type": "tool_use"}\n'
            raise OSError('pipe broke')
        proc.stdout = broken_lines()
        with patch('acc.hermes.subprocess.Popen', return_value=proc):
            with self.assertRaises(OSError):
                hermes.run(self.args())
        # The point of the fix: a mid-stream failure must not leave the process running
        # unsupervised until ACC's own outer timeout eventually reaches it.
        proc.kill.assert_called_once()

    def test_clean_nonzero_exit_still_reports_the_code_without_killing(self):
        proc = MagicMock()

        def lines():
            yield '{"type": "result", "exit_code": 1}\n'
        proc.stdout = lines()
        proc.wait.return_value = 7
        with patch('acc.hermes.subprocess.Popen', return_value=proc):
            code = hermes.run(self.args())
        self.assertEqual(code, 7)
        proc.kill.assert_not_called()

    def test_spawns_with_a_credential_filtered_environment(self):
        proc = MagicMock()

        def lines():
            yield '{"type": "result", "exit_code": 0}\n'
        proc.stdout = lines()
        proc.wait.return_value = 0
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'shh'}, clear=False), \
                patch('acc.hermes.subprocess.Popen', return_value=proc) as popen:
            hermes.run(self.args())
        self.assertNotIn('GITHUB_TOKEN', popen.call_args.kwargs['env'])


if __name__ == '__main__':
    unittest.main()
