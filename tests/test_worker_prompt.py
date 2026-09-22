"""acc/worker_prompt.py: unit tests for subprocess_env's credential filtering, shared by every
subprocess-spawning driver (hermes.py, deepseek_harness.py, deepastra.py)."""
import unittest
from unittest.mock import patch

from acc import worker_prompt


class SubprocessEnvTests(unittest.TestCase):
    def test_strips_key_token_and_secret_named_variables(self):
        fake_env = {'PATH': '/usr/bin', 'HOME': '/home/x', 'AWS_SECRET_ACCESS_KEY': 'shh',
                    'GITHUB_TOKEN': 'shh', 'STRIPE_API_KEY': 'shh', 'my_secret_thing': 'shh'}
        with patch.dict('os.environ', fake_env, clear=True):
            filtered = worker_prompt.subprocess_env()
        self.assertEqual(filtered, {'PATH': '/usr/bin', 'HOME': '/home/x'})

    def test_leaves_ordinary_variables_untouched(self):
        fake_env = {'LANG': 'en_US.UTF-8', 'NODE_ENV': 'production'}
        with patch.dict('os.environ', fake_env, clear=True):
            filtered = worker_prompt.subprocess_env()
        self.assertEqual(filtered, fake_env)


if __name__ == '__main__':
    unittest.main()
