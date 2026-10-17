"""ACC-SC789R-20261017-01: a gated caller cannot start a stored command by racing a reassignment against the start.

The server decides whether a start needs approval from the task's worker. Before the fix nothing held the coordinator
lock between that decision and the start, so a second request that reassigned the task to the local command in the gap
made the stored command run with no approval. These tests put the reassignment exactly in that gap, which makes the
race deterministic instead of a matter of luck.
"""
import threading
import time
import unittest
from unittest import mock

from acc import server
import test_rereview_fixes as earlier


class StartAssignRace(earlier.ApprovalBinding):
    def command_task_on_another_worker(self):
        """A stored command task that is currently assigned to a configured (non-command) worker."""
        task_id = self.stored_command_task()
        status, body = self.owner('POST', f'/api/tasks/{task_id}/assign', {'agent': 'stub'})
        self.assertEqual(status, 200, body)
        return task_id

    def reassign_in_the_gap(self, task_id):
        """Wrap classify so a concurrent request reassigns the task to the local command right after the gate decision."""
        real_classify = server.classify
        threads = []

        def classify(coordinator, parts, payload):
            gate = real_classify(coordinator, parts, payload)
            if parts[-1:] == ['start'] and not threads:
                def reassign():
                    try:
                        self.c.assign(task_id, 'local-command')
                    except Exception:
                        pass  # once the worker has started, reassigning is refused: that is the correct outcome
                threads.append(threading.Thread(target=reassign, daemon=True))
                threads[0].start()
                threads[0].join(.5)  # long enough to finish if nothing blocks it
            return gate
        return mock.patch.object(server, 'classify', classify), threads

    def test_a_reassignment_between_the_decision_and_the_start_cannot_run_the_command(self):
        for who, caller in self.callers().items():
            with self.subTest(who=who):
                for marker in (self.marker_a, self.marker_b):
                    marker.unlink(missing_ok=True)
                task_id = self.command_task_on_another_worker()
                patched, threads = self.reassign_in_the_gap(task_id)
                with patched:
                    status, body = caller('POST', f'/api/tasks/{task_id}/start', {})
                self.assertTrue(threads, 'the reassignment really was attempted in the gap')
                threads[0].join(5)
                time.sleep(.6)
                self.assertFalse(self.marker_a.exists(), f'the stored command ran with no approval ({status}: {body})')
                self.assertNotEqual((status, body.get('agent')), (200, 'local-command'), body)

    def test_the_worker_the_caller_was_allowed_to_start_still_starts(self):
        task_id = self.command_task_on_another_worker()
        self.marker_b.unlink(missing_ok=True)
        status, body = self.agent('POST', f'/api/tasks/{task_id}/start', {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['agent'], 'stub')
        end = time.time() + 10
        while not self.marker_b.exists() and time.time() < end:
            time.sleep(.1)
        self.assertTrue(self.marker_b.exists(), 'a start that needs no approval is not slowed or blocked')
        self.assertFalse(self.marker_a.exists())

    def test_a_reassignment_before_the_start_is_gated_as_before(self):
        for who, caller in self.callers().items():
            with self.subTest(who=who):
                self.marker_a.unlink(missing_ok=True)
                task_id = self.command_task_on_another_worker()
                self.assertEqual(caller('POST', f'/api/tasks/{task_id}/assign', {'agent': 'local-command'})[0], 200)
                approval = self.request_start(caller, task_id)
                self.assertTrue(approval)
                time.sleep(.3)
                self.assertFalse(self.marker_a.exists(), 'nothing runs until the owner approves')


# Only the fixture (server, stub worker, helpers) is reused; the earlier class's own tests already run in its module.
for _name in dir(earlier.ApprovalBinding):
    if _name.startswith('test_'):
        setattr(StartAssignRace, _name, None)


if __name__ == '__main__':
    unittest.main()
