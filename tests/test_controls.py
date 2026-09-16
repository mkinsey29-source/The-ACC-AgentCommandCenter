"""Real supervised subprocesses exercise safe role changes and scheduling."""
import unittest
from unittest.mock import patch
from acc.core import Conflict
import test_workflow as fixtures
from test_acc import eventually


class ControlTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    start = fixtures.WorkflowTests.start
    done = fixtures.WorkflowTests.done
    def test_switch_during_implementation_waits_and_reviews_with_replacement(self):
        task_id = self.start('slow')
        eventually(lambda: self.c.store.get(task_id)['status'] == 'running')
        first = self.c.store.get(task_id)['run_id']
        request = {'request_id': 'switch-1', 'role': 'reviewer', 'agent': 'local-reviewer'}
        pending = self.c.controls.switch(task_id, request)
        self.assertEqual(pending['pending_switch']['run_id'], first)
        self.assertEqual(pending['workflow']['reviewer'], 'reviewer')
        self.c.controls.switch(task_id, request)
        done = self.done(task_id)
        self.assertEqual(done['status'], 'accepted')
        self.assertEqual(done['runs'][0]['id'], first)
        self.assertEqual(done['runs'][2]['agent'], 'local-reviewer')
        self.assertEqual(len(done['switch_history']), 1)
        self.assertIsNone(done['pending_switch'])
        self.assertEqual(self.c.controls.switch(task_id, request)['status'], 'accepted')

    def test_reviewer_switch_during_final_decision_requires_fresh_review(self):
        task_id = self.start('slow')
        eventually(lambda: len(self.c.store.get(task_id)['runs']) == 4)
        self.c.controls.switch(task_id, {'request_id': 'switch-review', 'role': 'reviewer', 'agent': 'local-reviewer'})
        eventually(lambda: len(self.c.store.get(task_id)['runs']) >= 7)
        task = self.done(task_id)
        self.assertEqual(task['status'], 'accepted')
        self.assertEqual(task['runs'][-2]['agent'], 'local-reviewer')
        self.assertEqual(task['review']['review_run_id'], task['runs'][-2]['id'])

    def test_dependencies_cycle_and_blocked_run(self):
        a = self.c.create({'title':'A', 'instruction':'A'})
        b = self.c.create({'title':'B', 'instruction':'B'})
        self.c.controls.schedule(a['id'], {'priority':90, 'depends_on':[b['id']]})
        with self.assertRaises(Conflict):
            self.c.controls.schedule(b['id'], {'depends_on':[a['id']]})
        with self.assertRaises(Conflict):
            self.c.start(a['id'])
        b['status']='accepted'; self.c.store.save(b,'fixture',{})
        self.assertEqual(self.c.controls.blocked(self.c.store.get(a['id'])), [])

    def test_publication_reservation_blocks_workers_and_conversation(self):
        task=self.c.create({'title':'Blocked', 'instruction':'Blocked'})
        self.c.github.busy='publication'
        try:
            with self.assertRaises(Conflict): self.c.start(task['id'])
            with self.assertRaises(Conflict): self.c.conversation.claim({'owner':'test'})
        finally:
            self.c.github.busy=None

    def test_baseline_captured_before_first_implementation(self):
        task = self.done(self.start())
        self.assertNotIn('answer.txt', task['baseline']['entries'])
        self.assertIn('answer.txt', task['workflow']['snapshot']['entries'])


if __name__ == '__main__':
    unittest.main()
