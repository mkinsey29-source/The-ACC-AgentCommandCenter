"""The in-memory and SQLite command stores must give identical outcomes for the same commands."""
import tempfile
import unittest
from pathlib import Path

from acc.platform.command_memory import InMemoryCommandRepository
from acc.platform.commands import CommandRequest
from acc.platform.sqlite_repository import SQLiteCommandRepository

LIMITS = {'tasks.active': 2}

# (operation_id, kind, expected_revision, payload, actor, limits)
SUITE = (
    ('op-1', 'task.create', 0, {'task_id': 't1'}, 'u1', LIMITS),
    ('op-1', 'task.create', 0, {'task_id': 't1'}, 'u1', LIMITS),      # replay
    ('op-1', 'task.create', 0, {'task_id': 't1'}, 'u2', LIMITS),      # other actor
    ('op-2', 'task.create', 0, {'task_id': 't2'}, 'u1', LIMITS),      # stale revision
    ('op-2', 'task.create', 1, {'task_id': 't1'}, 'u1', LIMITS),      # duplicate task
    ('op-2', 'task.create', 1, {'task_id': 't2'}, 'u1', LIMITS),
    ('op-3', 'task.create', 2, {'task_id': 't3'}, 'u1', LIMITS),      # quota full
    ('op-3', 'task.create', 2, {'task_id': 't3'}, 'u1', {}),          # no ceiling
    ('op-3', 'task.cancel', 2, {'task_id': 'missing'}, 'u1', LIMITS),
    ('op-3', 'task.cancel', 2, {'task_id': 't1'}, 'u1', LIMITS),
    ('op-4', 'task.cancel', 3, {'task_id': 't1'}, 'u1', LIMITS),      # already cancelled
    ('op-4', 'task.create', 3, {'task_id': 't3'}, 'u1', LIMITS),      # released slot
    ('op-5', 'project.mode', 4, {'mode': 'online'}, 'u1', LIMITS),    # already online
    ('op-5', 'project.mode', 4, {'mode': 'offline'}, 'u1', LIMITS),
    ('op-6', 'worker.pause', 5, {'worker_id': 'missing'}, 'u1', LIMITS),
    ('op-6', 'worker.resume', 5, {'worker_id': 'w1'}, 'u1', LIMITS),  # already active
    ('op-6', 'worker.pause', 5, {'worker_id': 'w1'}, 'u1', LIMITS),
    ('op-7', 'worker.resume', 6, {'worker_id': 'w1'}, 'u1', LIMITS),
    ('op-8', 'task.create', 0, {'task_id': 'x'}, 'u1', LIMITS),       # missing project
)


def outcomes(store):
    store.put_project('acct', 'p')
    store.put_worker('acct', 'p', 'w1')
    seen = []
    for operation, kind, revision, payload, actor, limits in SUITE:
        command = CommandRequest(operation, 'acct', 'none' if operation == 'op-8' else 'p',
                                 kind, revision, payload, actor)
        try:
            result = store.execute(command, limits=limits)
            seen.append(('ok', result.revision, dict(result.result), result.replayed))
        except Exception as error:  # the exception type and its data are the outcome
            seen.append(('error', type(error).__name__,
                         getattr(error, 'current_revision', None)))
    events = store.read_events('acct', 'p', after=0, limit=100).events
    return {
        'outcomes': seen,
        'project': store.project('acct', 'p'),
        'quota': store.quota_used('acct', 'tasks.active'),
        'audit': [{k: v for k, v in row.items() if k != 'at'} for row in store.audit()],
        'events': [(e.kind, dict(e.data)) for e in events],
    }


class CommandStoreParityTests(unittest.TestCase):
    def test_in_memory_and_sqlite_agree_on_the_command_suite(self):
        memory = outcomes(InMemoryCommandRepository(clock=lambda: 1000.0))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3',
                                            clock=lambda: 1000.0)
            try:
                durable = outcomes(store)
            finally:
                store.close()
        self.assertEqual(memory['outcomes'], durable['outcomes'])
        self.assertEqual(memory['project'], durable['project'])
        self.assertEqual(memory['quota'], durable['quota'])
        self.assertEqual(memory['audit'], durable['audit'])
        self.assertEqual(memory['events'], durable['events'])
        # Guard against both stores agreeing on a suite that exercised nothing.
        kinds = {o[1] for o in memory['outcomes'] if o[0] == 'error'}
        self.assertEqual(kinds, {'IdempotencyConflict', 'CommandConflict', 'CommandStateConflict',
                                 'QuotaExceeded', 'CommandTargetNotFound'})
        self.assertEqual(memory['quota'], 2)
        self.assertEqual(memory['project']['revision'], 7)

    def test_invalid_clock_value_writes_nothing_in_either_store(self):
        def check(store):
            store.put_project('acct', 'p')
            command = CommandRequest('op-1', 'acct', 'p', 'task.create', 0, {'task_id': 't'}, 'u')
            with self.assertRaises(ValueError):
                store.execute(command, limits=LIMITS)
            self.assertEqual(store.project('acct', 'p'),
                             {'revision': 0, 'mode': 'online', 'tasks': {}, 'workers': {}})
            self.assertEqual(store.quota_used('acct', 'tasks.active'), 0)
            self.assertEqual(store.audit(), ())
            self.assertEqual(store.read_events('acct', 'p', after=0).events, ())

        check(InMemoryCommandRepository(clock=lambda: float('nan')))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3',
                                            clock=lambda: -1.0)
            try:
                check(store)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
