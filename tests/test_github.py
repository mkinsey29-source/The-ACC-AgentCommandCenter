"""Real local Git repositories; simulated GitHub/remote transport never pushes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from acc.core import Conflict, Store
from acc.github import GitHub, repository_from_url
from acc import snapshots


class Metadata:
    def __init__(self, store):
        self.store = store
        self.lease = None

    def meta(self, key, default=None):
        with self.store.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, db, key, value):
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def _expire(self):
        return self.lease


class SimulatedGitHub(GitHub):
    def __init__(self, coordinator, head):
        super().__init__(coordinator)
        self.remote = {'main': head, 'temporary': head}
        self.pushes = []
        self.created = 0
        self.existing = []
        self.fail_create = False
        self.before_push = None

    def git(self, *args, **kwargs):
        if args[0] == 'ls-remote':
            return '\n'.join(self.remote[ref.removeprefix('refs/heads/')] + '\t' + ref
                             for ref in args[2:] if ref.removeprefix('refs/heads/') in self.remote)
        if args[0] == 'push':
            if self.before_push:
                self.before_push()
            oid, ref = args[-1].split(':', 1)
            self.remote[ref.removeprefix('refs/heads/')] = oid
            self.pushes.append(list(args))
            return ''
        return super().git(*args, **kwargs)

    def gh(self, *args, **kwargs):
        if args[:2] == ('pr', 'list'):
            return json.dumps(self.existing)
        if args[:2] == ('pr', 'create'):
            if self.fail_create:
                raise ValueError('Simulated create failure')
            self.created += 1
            url = 'https://github.com/owner/project/pull/1'
            self.existing = [{'number': 1, 'url': url, 'headRefOid': self.remote['temporary']}]
            return url
        if args[:2] == ('pr', 'view'):
            return json.dumps({'url': self.existing[0]['url'], 'headRefOid': self.existing[0]['headRefOid'],
                               'headRefName': 'temporary', 'baseRefName': 'main', 'state': 'OPEN'})
        raise AssertionError('Unexpected GH command: ' + repr(args))


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Local test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        (self.project / 'answer.txt').write_text('old\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Initial')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-qb', 'temporary')
        self.git('remote', 'add', 'origin', 'https://github.com/owner/project.git')
        store = Store(self.state / 'acc.sqlite3')
        self.c = SimpleNamespace(project=self.project, state=self.state, store=store,
                                 lock=threading.RLock(), halt=threading.Event(), running_task=None,
                                 recovery_required=False, conversation=Metadata(store))
        self.github = SimulatedGitHub(self.c, self.head)
        self.addCleanup(self.close)
        self.baseline = {'revision': 1, 'head': self.head, 'entries': snapshots.inventory(self.project), 'dirty_paths': []}
        (self.project / 'answer.txt').write_text('reviewed\n')
        self.accept()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.project, check=True, capture_output=True, text=True).stdout.strip()

    def accept(self):
        destination = self.state / ('snap-' + str(len(list(self.state.glob('snap-*')))))
        snap = snapshots.freeze(self.project, destination)
        self.task = {'id': 'task1', 'status': 'accepted', 'revision': 1, 'title': 'Reviewed change',
                     'instruction': 'Implement the requested work.', 'baseline': self.baseline,
                     'workflow': {'snapshot': snap, 'phase': 'complete'},
                     'review': {'reference': snap['id'], 'revision': 1, 'message': 'Independent checks passed.'}}
        self.c.store.save(self.task, 'fixture', {})

    def close(self):
        self.c.halt.set()
        if self.github.publisher:
            self.github.publisher.join(10)
        if not self.github.unsafe_process:
            self.github.close()

    def publish(self, preview=None, request='request1'):
        preview = preview or self.github.preview('task1')
        result = self.github.publish('task1', {'preview_id': preview['id'], 'request_id': request})
        if self.github.publisher:
            self.github.publisher.join(10)
            self.assertFalse(self.github.publisher.is_alive())
        return self.c.store.get('task1')

    def test_url_parsing_accepts_only_direct_github_remotes(self):
        for url in ('https://github.com/owner/project.git', 'git@github.com:owner/project.git', 'ssh://git@github.com/owner/project'):
            self.assertEqual(repository_from_url(url), 'owner/project')
        for url in ('https://secret@github.com/owner/project', 'https://github.com.evil/owner/project', '/local/repo'):
            self.assertIsNone(repository_from_url(url))

    def test_exact_reviewed_commit_and_confirmed_pr_with_idempotent_retry(self):
        preview = self.github.preview('task1')
        self.assertEqual(preview['paths'], ['answer.txt'])
        result = self.publish(preview)
        self.assertEqual(result['publication']['status'], 'confirmed')
        self.assertEqual(len(self.github.pushes), 1)
        self.assertEqual(self.github.created, 1)
        self.assertEqual(self.git('show', 'HEAD:answer.txt'), 'reviewed')
        again = self.publish(preview)
        self.assertEqual(again['publication'], result['publication'])
        self.assertEqual(len(self.github.pushes), 1)
        self.assertIsNone(self.github.busy)

    def test_new_reviewed_snapshot_same_revision_can_publish_after_confirmation(self):
        first = self.publish()
        prior = first['publication']
        (self.project / 'answer.txt').write_text('reviewed second cycle\n')
        self.accept()
        self.task['publication'] = prior
        self.c.store.save(self.task, 'fixture_second_cycle', {})
        self.assertEqual(self.task['revision'], prior['preview']['revision'])
        self.assertNotEqual(self.task['workflow']['snapshot']['id'], prior['preview']['snapshot_id'])
        # Simulate GitHub updating the existing PR when its branch receives the new commit.
        self.github.before_push = lambda: self.github.existing[0].update(headRefOid=self.git('rev-parse', 'HEAD'))
        result = self.publish(request='request2')
        self.assertEqual(result['publication']['status'], 'confirmed')
        self.assertEqual(result['publication']['request_id'], 'request2')
        self.assertEqual(result['publication']['preview']['snapshot_id'], self.task['workflow']['snapshot']['id'])
        self.assertEqual(len(self.github.pushes), 2)
        self.assertEqual(self.github.created, 1)
        self.assertEqual(self.git('show', 'HEAD:answer.txt'), 'reviewed second cycle')

    def test_duplicate_request_with_different_preview_rejected(self):
        preview = self.github.preview('task1')
        self.publish(preview)
        with self.assertRaises(Conflict):
            self.github.publish('task1', {'preview_id': 'different', 'request_id': 'request1'})

    def test_outgoing_and_entire_pr_history_are_shown(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Worker made a commit')
        preview = self.github.preview('task1')
        self.assertEqual(preview['paths'], [])
        self.assertEqual(preview['outgoing_commits'][0]['subject'], 'Worker made a commit')
        self.assertEqual(preview['pr_commits'], preview['outgoing_commits'])
        result = self.publish(preview)
        self.assertEqual(result['publication']['status'], 'confirmed')
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')

    def test_index_unrelated_changes_and_baseline_overlap_are_rejected(self):
        self.git('add', 'answer.txt')
        with self.assertRaisesRegex(Conflict, 'index'):
            self.github.preview('task1')
        self.git('reset', '-q')
        self.task['baseline']['dirty_paths'] = ['answer.txt']
        self.c.store.save(self.task, 'fixture', {})
        with self.assertRaisesRegex(Conflict, 'overlaps'):
            self.github.preview('task1')

    def test_review_identity_and_snapshot_drift_block_publication(self):
        self.task['review']['revision'] = 2
        self.c.store.save(self.task, 'fixture', {})
        with self.assertRaisesRegex(Conflict, 'revision'):
            self.github.preview('task1')
        self.accept()
        (self.project / 'answer.txt').write_text('unreviewed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.github.preview('task1')
        self.assertEqual(self.github.pushes, [])

    def test_remote_change_invalidates_approved_preview(self):
        preview = self.github.preview('task1')
        del self.github.remote['temporary']
        with self.assertRaisesRegex(Conflict, 'target changed'):
            self.github.publish('task1', {'preview_id': preview['id'], 'request_id': 'request1'})
        self.assertEqual(self.github.pushes, [])

    def test_existing_pr_must_have_exact_head(self):
        self.github.existing = [{'url': 'https://github.com/owner/project/pull/8', 'number': 8, 'headRefOid': self.head}]
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'needs_attention')
        self.assertIn('head does not match', result['publication']['error'])
        self.assertEqual(self.github.created, 0)

    def test_retry_after_create_failure_reuses_commit_without_duplicate(self):
        self.github.fail_create = True
        first = self.publish()
        self.assertEqual(first['publication']['status'], 'needs_attention')
        committed = self.git('rev-parse', 'HEAD')
        self.github.fail_create = False
        result = self.publish(request='request2')
        self.assertEqual(result['publication']['status'], 'confirmed')
        self.assertEqual(self.git('rev-parse', 'HEAD'), committed)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')

    def test_committed_tree_must_match_snapshot_even_if_worktree_matches(self):
        snap = self.task['workflow']['snapshot']
        with self.assertRaisesRegex(Conflict, 'Committed files differ'):
            self.github.verify_commit(snap, self.head)
        self.git('add', '.')
        self.git('commit', '-qm', 'Correct content')
        self.github.verify_commit(snap, self.git('rev-parse', 'HEAD'))

    @unittest.skipIf(os.name == 'nt', 'POSIX executable hook fixture')
    def test_hook_cannot_publish_unreviewed_commit_hidden_by_restored_worktree(self):
        hooks = self.project / '.git' / 'hooks'
        before = hooks / 'pre-commit'
        before.write_text("#!/bin/sh\nprintf 'unreviewed\\n' > answer.txt\ngit add -- answer.txt\n")
        before.chmod(0o755)
        after = hooks / 'post-commit'
        after.write_text("#!/bin/sh\nprintf 'reviewed\\n' > answer.txt\n")
        after.chmod(0o755)
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'needs_attention')
        self.assertIn('Committed files differ', result['publication']['error'])
        self.assertEqual(self.github.pushes, [])

    def test_autocrlf_normalizes_frozen_crlf_to_reviewed_git_blob(self):
        self.git('config', 'core.autocrlf', 'true')
        (self.project / 'answer.txt').write_bytes(b'reviewed\r\n')
        self.accept()
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'confirmed')
        blob = subprocess.check_output(['git', 'show', 'HEAD:answer.txt'], cwd=self.project)
        self.assertEqual(blob, b'reviewed\n')

    def test_gitattributes_text_normalization_is_supported(self):
        self.git('config', 'core.autocrlf', 'false')
        (self.project / '.gitattributes').write_text('*.txt text eol=lf\n')
        (self.project / 'answer.txt').write_bytes(b'reviewed\r\n')
        self.accept()
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'confirmed')
        blob = subprocess.check_output(['git', 'show', 'HEAD:answer.txt'], cwd=self.project)
        self.assertEqual(blob, b'reviewed\n')

    def test_filemode_false_preserves_tracked_executable_on_non_executable_filesystem(self):
        self.git('update-index', '--chmod=+x', 'answer.txt')
        self.git('commit', '-qm', 'Existing executable')
        self.git('config', 'core.filemode', 'false')
        (self.project / 'answer.txt').chmod(0o644)
        self.accept()
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'confirmed')
        self.assertTrue(self.git('ls-tree', 'HEAD', 'answer.txt').startswith('100755'))

    def test_filemode_false_new_file_uses_normal_git_mode(self):
        self.git('config', 'core.filemode', 'false')
        script = self.project / 'new-script'
        script.write_text('reviewed script\n')
        script.chmod(0o755)
        self.accept()
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'confirmed')
        self.assertTrue(self.git('ls-tree', 'HEAD', 'new-script').startswith('100644'))

    @unittest.skipIf(os.name == 'nt', 'POSIX executable hook fixture')
    def test_hook_cannot_change_filemode_under_core_filemode_false(self):
        self.git('config', 'core.filemode', 'false')
        hook = self.project / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\ngit update-index --chmod=+x -- answer.txt\n')
        hook.chmod(0o755)
        result = self.publish()
        self.assertEqual(result['publication']['status'], 'needs_attention')
        self.assertIn('Committed files differ', result['publication']['error'])
        self.assertEqual(self.github.pushes, [])

    def test_assigned_clean_filter_is_rejected_before_hashing_or_staging(self):
        (self.project / '.gitattributes').write_text('answer.txt filter=arbitrary\n')
        self.accept()
        with patch.object(self.github, 'git', wraps=self.github.git) as git:
            with self.assertRaisesRegex(Conflict, 'does not support clean filters'):
                self.github.preview('task1')
        calls = [call.args[0] for call in git.call_args_list]
        self.assertNotIn('hash-object', calls)
        self.assertNotIn('add', calls)
        self.assertNotIn('diff', calls)
        self.assertEqual(self.github.pushes, [])

    def test_active_runner_and_external_lease_block_publication(self):
        preview = self.github.preview('task1')
        self.c.running_task = 'other'
        with self.assertRaises(Conflict):
            self.github.publish('task1', {'preview_id': preview['id'], 'request_id': 'request1'})
        self.c.running_task = None
        self.c.conversation.lease = {'kind': 'external'}
        with self.assertRaises(Conflict):
            self.github.publish('task1', {'preview_id': preview['id'], 'request_id': 'request1'})

    def test_unconfirmed_process_cleanup_retains_ownership(self):
        preview = self.github.preview('task1')
        # Start after synchronous preview validation; the publisher's first real Git command fails cleanup.
        original = self.github._publish
        def publish_with_failed_cleanup(task_id):
            with patch('acc.github.stop_tree', side_effect=Conflict('unconfirmed')):
                original(task_id)
        with patch.object(self.github, '_publish', side_effect=publish_with_failed_cleanup):
            result = self.publish(preview)
        self.assertEqual(result['status'], 'interrupted')
        self.assertTrue(self.c.recovery_required)
        self.assertEqual(self.github.busy, 'task1')
        with self.assertRaises(Conflict):
            self.github.close()
        self.assertEqual(self.github.pushes, [])

    def test_snapshot_is_copy_not_mutable_internal_settings(self):
        self.github.snapshot()['settings']['source'] = 'main'
        self.assertEqual(self.github.settings['source'], 'temporary')


if __name__ == '__main__':
    unittest.main()
