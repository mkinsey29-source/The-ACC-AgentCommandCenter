"""Independent regression checks; fixture adapters do not perform model inference."""
import json
import threading
import unittest

from acc.bridge import dispatch
from acc.server import Server
import test_workflow as fixtures


class ReadinessReviewTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    start = fixtures.WorkflowTests.start
    done = fixtures.WorkflowTests.done

    def test_recovery_clears_pending_switch_and_allows_explicit_resume(self):
        task = self.done(self.start())
        # Persist the state left by a crash after a switch request was saved.
        task.update(status='interrupted', pid=None, review=None)
        task['workflow'].update(enabled=False, phase='held', stage='coordinate')
        task['pending_switch'] = {'id': 'before-crash', 'role': 'coordinator',
                                  'agent': 'local-coordinator', 'at': 0, 'run_id': task['run_id']}
        self.c.store.save(task, 'interrupted', {})
        self.c.recovery_required = True
        self.c.recover(task['id'])
        recovered = self.c.store.get(task['id'])
        self.assertIsNone(recovered['pending_switch'])
        self.assertEqual(recovered['workflow']['coordinator'], 'hermes-coordinator')
        self.c.controls.switch(task['id'], {'request_id': 'after-inspection',
                                          'role': 'coordinator', 'agent': 'local-coordinator'})
        self.c.workflows.configure(task['id'], {})
        self.assertEqual(self.done(task['id'])['status'], 'accepted')

    def test_new_mcp_control_routes_reach_authenticated_server(self):
        with self.c.lock:
            task_id = self.start()
            self.c.workflows.configure(task_id, {'enabled': False})
        server = Server(('127.0.0.1', 0), self.c, 'review-test-token')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call(name, arguments):
            return dispatch({'id': 1, 'method': 'tools/call',
                             'params': {'name': name, 'arguments': arguments}},
                            'http://127.0.0.1:' + str(server.server_port), 'review-test-token')['result']

        try:
            result = call('acc_switch_agent', {'task_id': task_id, 'request_id': 'mcp-switch',
                                              'role': 'reviewer', 'agent': 'local-reviewer'})
            self.assertFalse(result['isError'], result)
            self.assertEqual(self.c.store.get(task_id)['workflow']['reviewer'], 'local-reviewer')
            result = call('acc_schedule_task', {'task_id': task_id, 'priority': 91, 'depends_on': []})
            self.assertFalse(result['isError'], result)
            self.assertEqual(self.c.store.get(task_id)['priority'], 91)
            result = call('acc_github_configure', {'enabled': False})
            self.assertFalse(result['isError'], result)
            self.assertFalse(self.c.github.snapshot()['settings']['enabled'])
            result = call('acc_publish_preview', {'task_id': task_id})
            self.assertTrue(result['isError'])
            self.assertIn('review', json.dumps(result).lower())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
