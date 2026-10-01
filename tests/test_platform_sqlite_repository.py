"""SQLite-backed M09 command storage compatibility and recovery tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acc.platform.commands import CommandRequest
from acc.platform.sqlite_repository import SQLiteCommandRepository


class SQLiteCommandRepositoryTests(unittest.TestCase):
    def test_applied_command_and_retry_survive_repository_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            repository = SQLiteCommandRepository(database, clock=lambda: 123.5)
            repository.put_project('acct-1', 'project-1')
            command = CommandRequest(
                operation_id='op-1', account_id='acct-1', project_id='project-1',
                kind='task.create', expected_revision=0,
                payload={'task_id': 'task-1'}, actor_user_id='user-1')

            applied = repository.execute(command, limits={'tasks.active': 3})
            self.assertEqual((applied.revision, applied.result, applied.replayed),
                             (1, {'task_id': 'task-1', 'status': 'queued'}, False))
            repository.close()

            recovered = SQLiteCommandRepository(database, clock=lambda: 999.0)
            replay = recovered.execute(command, limits={'tasks.active': 3})
            self.assertEqual((replay.revision, replay.result, replay.replayed),
                             (1, {'task_id': 'task-1', 'status': 'queued'}, True))
            self.assertEqual(recovered.project_revision('acct-1', 'project-1'), 1)
            self.assertEqual(recovered.quota_used('acct-1', 'tasks.active'), 1)
            self.assertEqual(len(recovered.audit()), 1)
            events = recovered.read_events('acct-1', 'project-1', after=0)
            self.assertEqual([event.seq for event in events.events], [1])
            self.assertEqual(events.events[0].data['operation_id'], 'op-1')
            recovered.close()


    def test_reusing_operation_id_with_changed_command_is_rejected_without_mutation(self):
        from acc.platform.commands import IdempotencyConflict
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3')
            repository.put_project('acct-1', 'project-1')
            original = CommandRequest('op-1', 'acct-1', 'project-1', 'task.create', 0,
                                      {'task_id': 'task-1'}, 'user-1')
            altered = CommandRequest('op-1', 'acct-1', 'project-1', 'task.create', 0,
                                     {'task_id': 'task-2'}, 'user-1')
            repository.execute(original, limits={'tasks.active': 2})
            with self.assertRaises(IdempotencyConflict):
                repository.execute(altered, limits={'tasks.active': 2})
            self.assertEqual(repository.project_revision('acct-1', 'project-1'), 1)
            self.assertEqual(set(repository.project('acct-1', 'project-1')['tasks']), {'task-1'})
            self.assertEqual(repository.quota_used('acct-1', 'tasks.active'), 1)
            self.assertEqual(len(repository.audit()), 1)
            self.assertEqual(len(repository.read_events('acct-1', 'project-1', after=0).events), 1)
            repository.close()

    def test_quota_failure_rolls_back_all_rows_and_can_retry(self):
        from acc.platform.commands import QuotaExceeded
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3')
            repository.put_project('acct-1', 'project-1')
            command = CommandRequest('op-1', 'acct-1', 'project-1', 'task.create', 0,
                                     {'task_id': 'task-1'}, 'user-1')
            with self.assertRaises(QuotaExceeded):
                repository.execute(command, limits={'tasks.active': None})
            self.assertEqual(repository.project_revision('acct-1', 'project-1'), 0)
            self.assertEqual(repository.project('acct-1', 'project-1')['tasks'], {})
            self.assertEqual(repository.quota_used('acct-1', 'tasks.active'), 0)
            self.assertEqual(repository.audit(), ())
            self.assertEqual(repository.read_events('acct-1', 'project-1', after=0).events, ())
            applied = repository.execute(command, limits={'tasks.active': 1})
            self.assertEqual(applied.revision, 1)
            repository.close()

    def test_two_repository_instances_serialize_replay_for_same_operation(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            first = SQLiteCommandRepository(database)
            first.put_project('acct-1', 'project-1')
            second = SQLiteCommandRepository(database)
            command = CommandRequest('op-1', 'acct-1', 'project-1', 'task.create', 0,
                                     {'task_id': 'task-1'}, 'user-1')
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda repo: repo.execute(
                    command, limits={'tasks.active': 1}), (first, second)))
            self.assertEqual(sorted(result.replayed for result in results), [False, True])
            self.assertEqual(first.project_revision('acct-1', 'project-1'), 1)
            self.assertEqual(first.quota_used('acct-1', 'tasks.active'), 1)
            self.assertEqual(len(first.audit()), 1)
            self.assertEqual(len(first.read_events('acct-1', 'project-1', after=0).events), 1)
            first.close()
            second.close()

    def test_task_cancel_releases_reserved_quota_and_events_remain_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3')
            repository.put_project('acct-1', 'project-1')
            create = CommandRequest('op-create', 'acct-1', 'project-1', 'task.create', 0,
                                    {'task_id': 'task-1'}, 'user-1')
            repository.execute(create, limits={'tasks.active': 1})
            cancel = CommandRequest('op-cancel', 'acct-1', 'project-1', 'task.cancel', 1,
                                    {'task_id': 'task-1'}, 'user-1')
            repository.execute(cancel, limits={})
            self.assertEqual(repository.project_revision('acct-1', 'project-1'), 2)
            self.assertEqual(repository.project('acct-1', 'project-1')['tasks']['task-1'],
                             {'id': 'task-1', 'status': 'cancelled', 'reserved': {}})
            self.assertEqual(repository.quota_used('acct-1', 'tasks.active'), 0)
            batch = repository.read_events('acct-1', 'project-1', after=0)
            self.assertEqual([event.seq for event in batch.events], [1, 2])
            self.assertEqual([event.data['operation_id'] for event in batch.events],
                             ['op-create', 'op-cancel'])
            repository.close()

    def test_project_and_worker_transitions_use_persisted_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3')
            repository.put_project('acct-1', 'project-1')
            repository.put_worker('acct-1', 'project-1', 'worker-1')
            offline = CommandRequest('op-offline', 'acct-1', 'project-1', 'project.mode', 0,
                                     {'mode': 'offline'}, 'user-1')
            repository.execute(offline, limits={})
            pause = CommandRequest('op-pause', 'acct-1', 'project-1', 'worker.pause', 1,
                                   {'worker_id': 'worker-1'}, 'user-1')
            repository.execute(pause, limits={})
            resume = CommandRequest('op-resume', 'acct-1', 'project-1', 'worker.resume', 2,
                                    {'worker_id': 'worker-1'}, 'user-1')
            repository.execute(resume, limits={})
            project = repository.project('acct-1', 'project-1')
            self.assertEqual(project['mode'], 'offline')
            self.assertEqual(project['workers']['worker-1']['status'], 'active')
            self.assertEqual(repository.project_revision('acct-1', 'project-1'), 3)
            repository.close()


if __name__ == '__main__':
    unittest.main()
