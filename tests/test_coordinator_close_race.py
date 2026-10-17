"""Coordinator.close() must not race a runner launch (the 'Runner termination is unconfirmed' teardown flake).

start() holds the coordinator lock from its shutdown check until the runner process is registered. close() sets the
halt flag under that same lock, so a launch already in progress finishes (and is then stopped by close) and any later
start() refuses. Before this, close() could look at self.process just before a runner was registered and then report
a runner it never stopped (ACC-RR-20261013-TEST-02).
"""
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from acc.core import Conflict, Coordinator


class CloseWaitsForALaunchInProgress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        project = root / 'project'
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        config = root / 'agents.json'
        config.write_text(json.dumps({'agents': []}))
        self.coordinator = Coordinator(project, root / 'state', config)

    def test_halt_is_set_only_after_a_launch_in_progress_has_released_the_lock(self):
        coordinator = self.coordinator
        coordinator.lock.acquire()  # stands in for start() part-way through launching a runner
        closer = threading.Thread(target=coordinator.close)
        closer.start()
        try:
            time.sleep(0.3)
            self.assertFalse(coordinator.halt.is_set(), 'close() must wait for the launch that holds the lock')
        finally:
            coordinator.lock.release()
        closer.join(30)
        self.assertFalse(closer.is_alive())
        self.assertTrue(coordinator.halt.is_set())

    def test_start_refuses_once_closed(self):
        task = self.coordinator.create({'title': 'late', 'instruction': 'noop', 'argv': ['true']})
        self.coordinator.close()
        with self.assertRaisesRegex(Conflict, 'shutting down'):
            self.coordinator.start(task['id'])


if __name__ == '__main__':
    unittest.main()
