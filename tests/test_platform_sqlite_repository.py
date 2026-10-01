"""SQLite-backed M09 command storage compatibility and recovery tests."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from acc.platform.commands import CommandRequest
from acc.platform.sqlite_repository import SQLiteCommandRepository, _Transaction


def _run_command_in_process(database, command, barrier, results):
    repository = SQLiteCommandRepository(database, timeout=10)
    barrier.wait(timeout=10)
    try:
        result = repository.execute(command, limits={'tasks.active': 10})
        results.put(('applied' if not result.replayed else 'replayed', result.revision))
    except Exception as error:
        results.put((type(error).__name__, getattr(error, 'current_revision', None)))
    finally:
        repository.close()


class SQLiteCommandRepositoryTests(unittest.TestCase):
    def test_failed_rollback_closes_connection_and_preserves_original_error(self):
        class BrokenRollbackConnection:
            closed = False

            def execute(self, statement):
                if statement in ('COMMIT', 'ROLLBACK'):
                    raise sqlite3.OperationalError(statement.lower() + ' failure')

            def close(self):
                self.closed = True

        connection = BrokenRollbackConnection()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'commit failure'):
            with _Transaction(connection):
                pass
        self.assertTrue(connection.closed)

        connection = BrokenRollbackConnection()
        with self.assertRaisesRegex(ValueError, 'body failure'):
            with _Transaction(connection):
                raise ValueError('body failure')
        self.assertTrue(connection.closed)

    def test_commit_failure_rolls_back_and_leaves_repository_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            now = [123.0]
            repository = SQLiteCommandRepository(database, clock=lambda: now[0])
            repository.put_project('acct-1', 'project-1')
            repo_connection = repository._db
            repo_connection.set_authorizer(
                lambda action, arg1, *_: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT'
                else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                repository.set_quota_usage('acct-1', 'tasks.active', 2)
            repo_connection.set_authorizer(None)

            self.assertEqual(repository.quota_used('acct-1', 'tasks.active'), 0)
            repository.set_quota_usage('acct-1', 'tasks.active', 1)
            self.assertEqual(repository.quota_used('acct-1', 'tasks.active'), 1)

            repo_connection.set_authorizer(
                lambda action, arg1, *_: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT'
                else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                repository.project('acct-1', 'project-1')
            repo_connection.set_authorizer(None)
            self.assertEqual(repository.project('acct-1', 'project-1')['revision'], 0)
            repository.close()

    def test_memory_and_empty_paths_are_rejected(self):
        for path in ('', '   ', ':memory:', 'file::memory:', 'file:test?mode=memory&cache=shared'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                SQLiteCommandRepository(path)

    def test_worker_bootstrap_does_not_overwrite_existing_durable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            now = [123.0]
            repository = SQLiteCommandRepository(database, clock=lambda: now[0])
            repository.put_project('acct-1', 'project-1')
            repository.put_worker('acct-1', 'project-1', 'worker-1')
            pause = CommandRequest('op-pause', 'acct-1', 'project-1', 'worker.pause', 0,
                                   {'worker_id': 'worker-1'}, 'user-1')
            repository.execute(pause, limits={})
            repository.close()

            recovered = SQLiteCommandRepository(database)
            recovered.put_worker('acct-1', 'project-1', 'worker-1', status='active')
            state = recovered.project('acct-1', 'project-1')
            self.assertEqual(state['workers']['worker-1']['status'], 'paused')
            self.assertEqual(state['revision'], 1)
            self.assertEqual(len(recovered.read_events('acct-1', 'project-1', after=0).events), 1)
            recovered.close()

    def test_cursor_ahead_of_restored_event_head_requires_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            backup = Path(directory) / 'backup.sqlite3'
            now = [123.0]
            repository = SQLiteCommandRepository(database, clock=lambda: now[0])
            repository.put_project('acct-1', 'project-1')
            first = CommandRequest('op-1', 'acct-1', 'project-1', 'project.mode', 0,
                                  {'mode': 'offline'}, 'user-1')
            repository.execute(first, limits={})
            with sqlite3.connect(backup) as backup_db:
                repository._db.backup(backup_db)
            now[0] = 124.0
            second = CommandRequest('op-2', 'acct-1', 'project-1', 'project.mode', 1,
                                    {'mode': 'online'}, 'user-1')
            repository.execute(second, limits={})
            now[0] = 125.0
            third = CommandRequest('op-3', 'acct-1', 'project-1', 'project.mode', 2,
                                   {'mode': 'offline'}, 'user-1')
            repository.execute(third, limits={})
            repository.close()

            with sqlite3.connect(database) as restored_db, sqlite3.connect(backup) as backup_db:
                backup_db.backup(restored_db)
            restored = SQLiteCommandRepository(database, clock=lambda: now[0])
            batch = restored.read_events('acct-1', 'project-1', after=126_000_000)
            self.assertTrue(batch.reset_required)
            self.assertEqual(batch.cursor, 123_000_000)
            self.assertEqual(batch.oldest_available, 123_000_001)
            restored.close()

    def test_restored_store_allocates_events_after_pre_restore_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            backup = Path(directory) / 'backup.sqlite3'
            now = [123.0]
            repository = SQLiteCommandRepository(database, clock=lambda: now[0])
            repository.put_project('acct-1', 'project-1')
            first = CommandRequest('op-1', 'acct-1', 'project-1', 'project.mode', 0,
                                   {'mode': 'offline'}, 'user-1')
            repository.execute(first, limits={})
            with sqlite3.connect(backup) as backup_db:
                repository._db.backup(backup_db)
            now[0] = 124.0
            second = CommandRequest('op-2', 'acct-1', 'project-1', 'project.mode', 1,
                                    {'mode': 'online'}, 'user-1')
            repository.execute(second, limits={})
            now[0] = 125.0
            third = CommandRequest('op-3', 'acct-1', 'project-1', 'project.mode', 2,
                                   {'mode': 'offline'}, 'user-1')
            repository.execute(third, limits={})
            old_cursor = repository.read_events('acct-1', 'project-1', after=0).cursor
            self.assertEqual(old_cursor, 125_000_000)
            repository.close()

            with sqlite3.connect(database) as restored_db, sqlite3.connect(backup) as backup_db:
                backup_db.backup(restored_db)
            now[0] = 126.0
            restored = SQLiteCommandRepository(database, clock=lambda: now[0])
            after_restore = CommandRequest('op-4', 'acct-1', 'project-1', 'project.mode', 1,
                                          {'mode': 'online'}, 'user-1')
            restored.execute(after_restore, limits={})
            replay = restored.execute(after_restore, limits={})
            self.assertTrue(replay.replayed)
            # New events never reuse a pre-restore sequence...
            events = restored.read_events('acct-1', 'project-1', after=0).events
            self.assertGreater(events[-1].seq, old_cursor)
            # ...and the client that applied op-2/op-3 (lost by the restore) must refetch even
            # though the head has already moved past its cursor.
            batch = restored.read_events('acct-1', 'project-1', after=old_cursor)
            self.assertTrue(batch.reset_required)
            self.assertEqual(batch.cursor, events[-1].seq)
            resumed = restored.read_events('acct-1', 'project-1', after=batch.cursor)
            self.assertEqual((resumed.reset_required, resumed.events), (False, ()))
            self.assertEqual(len(restored.audit()), 2)
            restored.close()

    def test_restore_then_new_command_still_resets_client_holding_lost_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            backup = Path(directory) / 'backup.sqlite3'
            now = [1000.0]
            repository = SQLiteCommandRepository(database, clock=lambda: now[0])
            repository.put_project('acct-1', 'project-1')

            def create(repo, operation, revision, task_id):
                return repo.execute(CommandRequest(
                    operation, 'acct-1', 'project-1', 'task.create', revision,
                    {'task_id': task_id}, 'user-1'), limits={'tasks.active': 10})

            create(repository, 'op-1', 0, 'task-1')
            with sqlite3.connect(backup) as backup_db:
                repository._db.backup(backup_db)
            now[0] = 1001.0
            create(repository, 'op-2', 1, 'task-2')           # lost by the restore
            client_cursor = repository.read_events('acct-1', 'project-1', after=0).cursor
            repository.close()
            with sqlite3.connect(database) as live, sqlite3.connect(backup) as backup_db:
                backup_db.backup(live)

            now[0] = 1002.0
            restored = SQLiteCommandRepository(database, clock=lambda: now[0])
            create(restored, 'op-3', 1, 'task-3')              # head moves past the client's cursor
            batch = restored.read_events('acct-1', 'project-1', after=client_cursor)
            self.assertTrue(batch.reset_required)
            self.assertEqual(batch.events, ())
            self.assertEqual(sorted(restored.project('acct-1', 'project-1')['tasks']),
                             ['task-1', 'task-3'])
            # A client that had only seen history still present continues normally.
            first_seq = restored.read_events('acct-1', 'project-1', after=0).events[0].seq
            normal = restored.read_events('acct-1', 'project-1', after=first_seq)
            self.assertFalse(normal.reset_required)
            self.assertEqual([e.data['operation_id'] for e in normal.events], ['op-3'])
            restored.close()

    def test_unknown_cursor_requires_reset_and_zero_never_does(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCommandRepository(Path(directory) / 'platform.sqlite3',
                                                 clock=lambda: 50.0)
            repository.put_project('acct-1', 'project-1')
            self.assertFalse(repository.read_events('acct-1', 'project-1', after=0).reset_required)
            repository.execute(CommandRequest('op-1', 'acct-1', 'project-1', 'project.mode', 0,
                                              {'mode': 'offline'}, 'user-1'), limits={})
            head = repository.read_events('acct-1', 'project-1', after=0).cursor
            self.assertFalse(repository.read_events('acct-1', 'project-1', after=head).reset_required)
            stale = repository.read_events('acct-1', 'project-1', after=head - 1)
            self.assertTrue(stale.reset_required)
            self.assertEqual(stale.cursor, head)
            self.assertEqual(repository.read_events('acct-1', 'missing', after=7).events, ())
            repository.close()

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
            self.assertEqual(len(events.events), 1)
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

    def test_separate_processes_serialize_same_operation_and_revision_race(self):
        import multiprocessing

        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'platform.sqlite3'
            repository = SQLiteCommandRepository(database)
            repository.put_project('acct-1', 'same-operation')
            repository.put_project('acct-1', 'revision-race')
            repository.close()

            for project_id, commands, expected in (
                    ('same-operation', [CommandRequest(
                        'shared-op', 'acct-1', 'same-operation', 'task.create', 0,
                        {'task_id': 'task-1'}, 'user-1')] * 4,
                     ['applied', 'replayed', 'replayed', 'replayed']),
                    ('revision-race', [CommandRequest(
                        f'op-{index}', 'acct-1', 'revision-race', 'task.create', 0,
                        {'task_id': f'task-{index}'}, 'user-1') for index in range(4)],
                     ['applied', 'CommandConflict', 'CommandConflict', 'CommandConflict'])):
                barrier = context.Barrier(len(commands))
                results = context.Queue()
                processes = [context.Process(
                    target=_run_command_in_process,
                    args=(str(database), command, barrier, results)) for command in commands]
                for process in processes:
                    process.start()
                observed = [results.get(timeout=20)[0] for _ in processes]
                for process in processes:
                    process.join(timeout=20)
                    self.assertEqual(process.exitcode, 0)
                self.assertCountEqual(observed, expected)

            final = SQLiteCommandRepository(database)
            self.assertEqual(final.project_revision('acct-1', 'same-operation'), 1)
            self.assertEqual(final.project_revision('acct-1', 'revision-race'), 1)
            self.assertEqual(len(final.audit()), 2)
            final.close()

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
            self.assertEqual(len(batch.events), 2)
            self.assertLess(batch.events[0].seq, batch.events[1].seq)
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
