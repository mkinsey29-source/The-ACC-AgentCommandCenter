import unittest

from acc.gates import ApprovalError, Approvals, Gate, MAX_PENDING, TTL_SECONDS, classify


class FakeStore:
    def __init__(self):
        self.events = []

    def get(self, task_id):
        if task_id == 'known':
            return {'title': 'Fix the parser'}
        raise KeyError(task_id)

    def event(self, kind, data, task_id=None):
        self.events.append((kind, data))


class FakeCoordinator:
    def __init__(self):
        self.store = FakeStore()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeCoordinator()

    def kind(self, parts, payload=None):
        gate = classify(self.c, parts, payload or {})
        return gate.kind if gate else None

    def test_each_gated_operation(self):
        cases = [
            (['api', 'tasks'], {'title': 't', 'argv': ['rm', '-rf', 'x']}, 'command.create'),
            (['api', 'archive', 'export'], {'directory': '/tmp/out'}, 'files.export'),
            (['api', 'github', 'configure'], {'remote': 'origin', 'base': 'main'}, 'github.configure'),
            (['api', 'tasks', 'known', 'publish'], {'preview_id': 'p'}, 'github.publish'),
            (['api', 'tasks', 'known', 'recover'], {'process_tree_inspected': True}, 'task.recover'),
            (['api', 'memory', 'abc123', 'review'], {'status': 'active'}, 'memory.accept'),
            (['api', 'knowledge', 'transition'], {'path': 'Notes/a.md', 'status': 'verified'}, 'knowledge.transition'),
            (['api', 'knowledge', 'rebuttal'], {'original_path': 'Notes/a.md', 'title': 'x', 'explanation': 'y'}, 'knowledge.rebuttal'),
            (['api', 'knowledge', 'note'], {'title': 't', 'body': 'b', 'status': 'verified'}, 'knowledge.note'),
            (['api', 'knowledge', 'note'], {'title': 't', 'body': 'b', 'status': 'active'}, 'knowledge.note'),
        ]
        for parts, payload, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.kind(parts, payload), expected)

    def test_everything_else_is_not_gated(self):
        cases = [
            (['api', 'tasks'], {'title': 't', 'instruction': 'i'}),
            (['api', 'tasks'], {'title': 't', 'agent': 'local-command', 'argv': []}),
            (['api', 'tasks'], {'title': 't', 'argv': 'a shell string'}),
            (['api', 'tasks', 'known', 'start'], {}),
            (['api', 'tasks', 'known', 'stop'], {}),
            (['api', 'tasks', 'known', 'assign'], {'agent': 'claude'}),
            (['api', 'tasks', 'known', 'workflow'], {'enabled': True}),
            (['api', 'tasks', 'known', 'report'], {'message': 'm'}),
            (['api', 'tasks', 'known', 'publish-preview'], {}),
            (['api', 'memory', 'abc123', 'review'], {'status': 'rejected'}),
            (['api', 'memory', 'propose'], {'title': 't'}),
            (['api', 'conversation', 'send'], {'text': 'hi'}),
            (['api', 'github', 'refresh'], {}),
            (['api', 'project', 'mode'], {'mode': 'offline'}),
            (['api', 'orchestrators', 'select'], {'selected': 'auto'}),
            (['api', 'knowledge', 'note'], {}),
            (['api', 'knowledge', 'note'], {'title': 't', 'body': 'b', 'status': 'hypothesis'}),
            (['api', 'knowledge', 'checkin'], {'title': 't', 'summary': 's', 'worker': 'w'}),
            (['api', 'knowledge', 'checkout'], {}),
            (['api', 'knowledge', 'review'], {}),
            (['api', 'knowledge', 'search'], {'query': 'q'}),
            (['api', 'integrations', 'jobs'], {}),
        ]
        for parts, payload in cases:
            with self.subTest(parts=parts, payload=payload):
                self.assertIsNone(self.kind(parts, payload))

    def test_summaries_say_what_will_happen(self):
        gate = classify(self.c, ['api', 'tasks'], {'title': 'Cleanup', 'argv': ['rm', '-rf', 'build']})
        self.assertIn('rm -rf build', gate.summary)
        self.assertEqual(gate.details['argv'], ['rm', '-rf', 'build'])
        self.assertIn('/tmp/out', classify(self.c, ['api', 'archive', 'export'], {'directory': '/tmp/out'}).summary)
        self.assertIn('Fix the parser', classify(self.c, ['api', 'tasks', 'known', 'publish'], {}).summary)
        self.assertIn('remote=origin', classify(self.c, ['api', 'github', 'configure'], {'remote': 'origin'}).summary)

    def test_an_unknown_task_still_gates_and_never_raises(self):
        gate = classify(self.c, ['api', 'tasks', 'missing', 'publish'], {})
        self.assertEqual(gate.kind, 'github.publish')
        self.assertIn('missing', gate.summary)

    def test_long_values_are_clipped(self):
        gate = classify(self.c, ['api', 'tasks'], {'title': 't', 'argv': ['x' * 5000]})
        self.assertLess(len(gate.summary), 400)
        self.assertLess(len(gate.details['argv'][0]), 250)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeCoordinator()
        self.clock = Clock()
        self.ran = []
        self.outcome = (201, {'id': 'task-1'}, False)
        self.approvals = Approvals(self.c, self.execute, clock=self.clock)
        self.gate = Gate('command.create', 'Run echo', {'argv': ['echo']})

    def execute(self, parts, payload):
        self.ran.append((list(parts), payload))
        return self.outcome

    def ask(self, key='agent', label='the orchestrator', payload=None, gate=None):
        return self.approvals.request(key, label, gate or self.gate, ['api', 'tasks'], payload or {'argv': ['echo']})

    def test_a_request_is_pending_and_runs_nothing(self):
        view = self.ask()
        self.assertEqual((view['status'], view['kind'], view['requested_by']), ('pending', 'command.create', 'the orchestrator'))
        self.assertEqual(self.ran, [])
        self.assertEqual([v['id'] for v in self.approvals.pending()], [view['id']])
        self.assertNotIn('payload', view)
        self.assertNotIn('parts', view)

    def test_approving_runs_the_stored_request_exactly_once(self):
        payload = {'argv': ['echo', 'hi']}
        view = self.ask(payload=payload)
        payload['argv'].append('TAMPERED')  # the caller changing its copy afterwards changes nothing
        done = self.approvals.resolve(view['id'], True)
        self.assertEqual(self.ran, [(['api', 'tasks'], {'argv': ['echo', 'hi']})])
        self.assertEqual((done['status'], done['result']), ('executed', {'status': 201, 'body': {'id': 'task-1'}}))
        with self.assertRaises(ApprovalError) as ctx:
            self.approvals.resolve(view['id'], True)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(self.approvals.pending(), [])

    def test_denying_runs_nothing_and_cannot_be_undone(self):
        view = self.ask()
        self.assertEqual(self.approvals.resolve(view['id'], False)['status'], 'denied')
        self.assertEqual(self.ran, [])
        with self.assertRaises(ApprovalError):
            self.approvals.resolve(view['id'], True)
        self.assertEqual(self.ran, [])

    def test_a_failed_run_is_reported_as_failed(self):
        self.outcome = (404, {'error': 'Task not found.'}, False)
        done = self.approvals.resolve(self.ask()['id'], True)
        self.assertEqual((done['status'], done['result']['status']), ('failed', 404))

    def test_a_request_expires_and_cannot_run_later(self):
        view = self.ask()
        self.clock.now += TTL_SECONDS + 1
        self.assertEqual(self.approvals.pending(), [])
        self.assertEqual(self.approvals.get(view['id'])['status'], 'expired')
        with self.assertRaises(ApprovalError) as ctx:
            self.approvals.resolve(view['id'], True)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.ran, [])

    def test_a_retry_returns_the_same_request_but_another_caller_gets_its_own(self):
        first, again = self.ask(), self.ask()
        self.assertEqual(first['id'], again['id'])
        other = self.ask(key='device:1', label='device “Pixel”')
        self.assertNotEqual(first['id'], other['id'])
        different = self.ask(payload={'argv': ['echo', 'other']})
        self.assertNotEqual(first['id'], different['id'])
        self.assertEqual(len(self.approvals.pending()), 3)

    def test_the_pending_queue_is_capped(self):
        for i in range(MAX_PENDING):
            self.ask(payload={'argv': ['echo', str(i)]})
        with self.assertRaises(ApprovalError) as ctx:
            self.ask(payload={'argv': ['echo', 'one too many']})
        self.assertEqual(ctx.exception.status, 429)
        self.approvals.resolve(self.approvals.pending()[0]['id'], False)
        self.ask(payload={'argv': ['echo', 'now there is room']})

    def test_unknown_ids_are_refused(self):
        for bad in ('nope', '', None, 5):
            with self.subTest(bad=bad):
                self.assertIsNone(self.approvals.get(bad))
                with self.assertRaises(ApprovalError) as ctx:
                    self.approvals.resolve(bad, True)
                self.assertEqual(ctx.exception.status, 404)

    def test_every_step_is_recorded_as_an_event(self):
        view = self.ask()
        self.approvals.resolve(view['id'], True)
        other = self.ask(payload={'argv': ['echo', '2']})
        self.approvals.resolve(other['id'], False)
        late = self.ask(payload={'argv': ['echo', '3']})
        self.clock.now += TTL_SECONDS + 1
        self.approvals.pending()
        kinds = [k for k, _ in self.c.store.events]
        self.assertEqual(kinds, ['approval_requested', 'approval_approved', 'approval_executed',
                                 'approval_requested', 'approval_denied', 'approval_requested', 'approval_expired'])
        self.assertIn('Run echo', self.c.store.events[0][1]['message'])
        self.assertEqual(self.c.store.events[0][1]['requested_by'], 'the orchestrator')

    def test_an_event_failure_does_not_decide_an_approval(self):
        self.c.store.event = lambda *a, **k: (_ for _ in ()).throw(OSError('disk full'))
        done = self.approvals.resolve(self.ask()['id'], True)
        self.assertEqual(done['status'], 'executed')

    def test_answered_requests_are_forgotten_after_an_hour(self):
        view = self.ask()
        self.approvals.resolve(view['id'], False)
        self.assertIsNotNone(self.approvals.get(view['id']))
        self.clock.now += 3601
        self.assertIsNone(self.approvals.get(view['id']))


if __name__ == '__main__':
    unittest.main()
