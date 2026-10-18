"""Regression probes for PR25 validation; no paid providers."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from acc import deepastra
from acc.core import Coordinator


class ShutdownRaceTests(unittest.TestCase):
    def test_close_waits_for_launch_before_checking_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'; project.mkdir()
            c = Coordinator(project, root / 'state')
            c.conversation.configure({'enabled': False})
            task = c.create({'title': 'Shutdown race', 'instruction': 'Local fixture',
                             'argv': [sys.executable, '-c', 'import time; time.sleep(30)']})
            entered, release, closing = threading.Event(), threading.Event(), threading.Event()
            errors = []
            real_popen = subprocess.Popen
            def launch(*args, **kwargs):
                entered.set()
                if not release.wait(5): raise RuntimeError('fixture launch timed out')
                return real_popen(*args, **kwargs)
            def start():
                try: c.start(task['id'])
                except Exception as exc: errors.append(exc)
            def close():
                closing.set()
                try: c.close()
                except Exception as exc: errors.append(exc)
            starter = threading.Thread(target=start)
            closer = threading.Thread(target=close)
            try:
                with patch('acc.core.subprocess.Popen', side_effect=launch):
                    starter.start()
                    self.assertTrue(entered.wait(3))
                    closer.start()
                    self.assertTrue(closing.wait(3))
                    self.assertFalse(c.halt.wait(.15), 'shutdown passed an in-flight launch')
                    release.set()
                    starter.join(10); closer.join(15)
                self.assertFalse(starter.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual(errors, [])
                self.assertIsNone(c.process)
            finally:
                release.set()
                if starter.ident: starter.join(10)
                if closer.ident: closer.join(15)
                c.close()


@unittest.skipUnless(hasattr(os, 'pidfd_open'), 'Linux pidfd namespace translation')
class NamespaceTests(unittest.TestCase):
    def test_proc_mount_pids_are_translated_before_signalling(self):
        files = {'/proc/self/status': 'NSpid:\t9000\t5\n',
                 '/proc/self/fdinfo/20': 'Pid:\t9100\nNSpid:\t9100\t6\n',
                 '/proc/9200/status': 'PPid:\t9100\nNSpid:\t9200\t7\n',
                 '/proc/9300/status': 'PPid:\t9100\nNSpid:\t9300\t8\t1\n',
                 '/proc/9400/status': 'PPid:\t9999\nNSpid:\t9400\t9\n'}
        def read(path, **kwargs):
            if str(path) not in files: raise FileNotFoundError(str(path))
            return files[str(path)]
        with patch.object(Path, 'read_text', read), \
                patch.object(Path, 'iterdir', return_value=iter([Path('/proc/9200'), Path('/proc/9300'), Path('/proc/9400')])), \
                patch.object(os, 'pidfd_open', return_value=20) as opened, \
                patch.object(os, 'close') as closed, patch.object(os, 'killpg') as kill:
            deepastra._kill_orphans(6)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [7, 8])
        opened.assert_called_once_with(6)
        closed.assert_called_once_with(20)

    def test_pidfd_is_closed_if_proc_lookup_fails(self):
        def read(path, **kwargs):
            if str(path) == '/proc/self/status': return 'NSpid:\t9000\t5\n'
            raise FileNotFoundError('fixture exited')
        with patch.object(Path, 'read_text', read), patch.object(os, 'pidfd_open', return_value=20), \
                patch.object(os, 'close') as closed, patch.object(os, 'killpg') as kill:
            deepastra._kill_orphans(6)
        closed.assert_called_once_with(20)
        kill.assert_not_called()
