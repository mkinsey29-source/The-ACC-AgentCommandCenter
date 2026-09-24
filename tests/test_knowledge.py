import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from acc.bridge import dispatch
from acc.core import Conflict, Coordinator
from acc.server import Server


class FakeEmbedder:
    model = 'fixture-embedding'

    def embed(self, texts):
        result = []
        for text in texts:
            words = text.lower().split()
            result.append([float(words.count('cache')), float(words.count('compiler')), 1.0])
        return result


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.vault = self.root / 'vault'
        self.project.mkdir(); self.vault.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), 'checkout', '-q', '-b', 'feature/knowledge-test'],
                       check=True)
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps({'knowledge': {
            'enabled': True, 'vault': {'id': 'fixture', 'path': str(self.vault)},
            'embeddings': {'provider': 'none'},
        }}))
        self.c = Coordinator(self.project, self.root / 'state', self.config)
        self.c.knowledge.embedder = FakeEmbedder()
        from acc.knowledge import EmbeddingCache
        self.c.knowledge.cache = EmbeddingCache(self.root / 'state' / 'fixture-embeddings.sqlite3')

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def test_search_checkout_checkin_and_rebuttal_lifecycle(self):
        original = self.c.knowledge.create_note({
            'title': 'Caching theory', 'body': 'The compiler cache always survives a clean build.',
            'type': 'hypothesis', 'status': 'hypothesis', 'worker': 'grok'})
        search = self.c.knowledge.search({'query': 'compiler cache', 'include_inactive': True})
        self.assertEqual(search['mode'], 'hybrid')
        self.assertEqual(search['results'][0]['path'], original['path'])

        checkout = self.c.knowledge.checkout({
            'title': 'Repair compiler caching', 'instruction': 'Investigate the compiler cache failure.',
            'worker': 'claude', 'task_id': 'task-fixture', 'run_id': 'run-fixture', 'stage': 'implement'})
        checkout_text = (self.vault / checkout['path']).read_text()
        self.assertIn('Agent synthesis before work', checkout_text)
        self.assertIn(original['path'][:-3], checkout_text)
        self.assertEqual(Path(checkout['absolute_path']), self.vault / checkout['path'])

        checkin = self.c.knowledge.checkin({
            'title': 'Repair compiler caching', 'summary': 'Used a clean cache directory.',
            'worker': 'claude', 'task_id': 'task-fixture', 'run_id': 'run-fixture',
            'checkout_path': checkout['path'], 'issues': ['Old cache metadata looped.'],
            'solutions': ['Created a clean cache directory.']})
        self.assertTrue((self.vault / checkin['path']).is_file())

        correction = self.c.knowledge.rebuttal({
            'original_path': original['path'], 'title': 'Caching theory correction',
            'explanation': 'A clean build invalidated the old cache metadata.',
            'worker': 'reviewer', 'evidence': ['test-cache-clean passed']})
        changed = (self.vault / original['path']).read_text()
        self.assertIn('status: "disproven"', changed)
        self.assertIn(correction['path'][:-3], changed)
        active = self.c.knowledge.search({'query': 'compiler cache'})
        self.assertNotIn(original['path'], [item['path'] for item in active['results']])
        self.c.knowledge.transition({'path': original['path'], 'status': 'disproven',
                                     'corrected_by': correction['path']})
        changed_again = (self.vault / original['path']).read_text()
        self.assertEqual(changed_again.count('ACC:STATUS-BANNER:START'), 1)

    def test_result_capture_requires_structured_learning_when_enabled(self):
        task = {'id': 'task-id', 'task_number': 7, 'title': 'Knowledge task',
                'knowledge': {'last_checkout': self.c.knowledge.checkout({
                    'title': 'Knowledge task', 'instruction': 'Use earlier evidence.',
                    'worker': 'builder', 'task_id': 'task-id', 'run_id': 'run-id',
                    'stage': 'implement'})}}
        with self.assertRaises(ValueError):
            self.c.knowledge.capture_result(task, {'summary': 'Done', 'run_id': 'run-id'},
                                            'implement', 'builder')
        checkout_path = self.vault / task['knowledge']['last_checkout']['path']
        checkout_text = checkout_path.read_text()
        checkout_path.write_text(checkout_text.replace(
            '- What I learned:\n- How I will apply it:\n- Conflicts or uncertainty:\n- Known mistakes I will avoid:',
            '- What I learned: prior evidence matters.\n- How I will apply it: reuse the verified procedure.\n'
            '- Conflicts or uncertainty: none.\n- Known mistakes I will avoid: stale assumptions.'))
        note = self.c.knowledge.capture_result(task, {
            'summary': 'Done', 'run_id': 'run-id', 'knowledge': {
                'learnings': ['A reusable lesson.'], 'issues': [], 'solutions': [], 'loops': [],
                'decisions': [], 'corrections': [], 'evidence': ['test passed']}},
            'implement', 'builder')
        self.assertTrue((self.vault / note['path']).is_file())

    def test_checkin_separates_review_material_from_factual_record(self):
        checkin = self.c.knowledge.checkin({
            'title': 'Render recovery', 'summary': 'The final render opened successfully.',
            'worker': 'builder', 'task_id': 'render-task', 'run_id': 'render-run',
            'learnings': ['The verified preview settings produced the expected frame.'],
            'decisions': ['Kept the validated CPU fallback.'],
            'evidence': ['final.png opened and dimensions matched'],
            'issues': ['GPU rendering failed.'],
            'solutions': ['A CPU preview completed.'],
            'loops': ['Two unchanged GPU retries were abandoned.'],
            'corrections': ['The GPU default may be unsafe.'],
            'unvalidated': ['The driver version may be the root cause.'],
        })
        factual = (self.vault / checkin['path']).read_text()
        reviews = checkin['reviews']
        review_texts = [(self.vault / item['path']).read_text() for item in reviews]
        self.assertEqual(5, len(reviews))
        self.assertTrue(all(item['path'].startswith('Reviews/') for item in reviews))
        self.assertTrue(all(item['status'] == 'pending-review' for item in reviews))
        self.assertIn('The verified preview settings', factual)
        self.assertNotIn('GPU rendering failed', factual)
        self.assertTrue(any('GPU rendering failed' in text for text in review_texts))
        self.assertTrue(any('The driver version may be the root cause' in text
                            for text in review_texts))
        self.assertTrue(all(checkin['path'][:-3] in text for text in review_texts))
        self.assertTrue(all(item['path'][:-3] in factual for item in reviews))
        normal = self.c.knowledge.search({'query': 'driver root cause'})
        self.assertFalse(set(item['path'] for item in reviews) &
                         set(item['path'] for item in normal['results']))
        queued = self.c.knowledge.search({'query': 'driver root cause', 'include_inactive': True,
                                          'include_reviews': True})
        self.assertTrue(set(item['path'] for item in reviews) &
                        set(item['path'] for item in queued['results']))

    def test_review_can_target_another_review_without_promoting_truth(self):
        finding = self.c.knowledge.create_note({
            'title': 'Uncertain cache finding', 'body': 'The cache may fail after cleanup.',
            'type': 'finding', 'status': 'hypothesis', 'worker': 'builder'})
        first = self.c.knowledge.review({
            'target_path': finding['path'], 'title': 'First cache review',
            'summary': 'The existing evidence is incomplete.', 'worker': 'reviewer-one',
            'verdict': 'needs-evidence', 'findings': ['No clean-build trace was attached.'],
            'evidence': []})
        second = self.c.knowledge.review({
            'target_path': first['path'], 'title': 'Review of first cache review',
            'summary': 'The first reviewer correctly identified the missing trace.',
            'worker': 'reviewer-two', 'verdict': 'supports',
            'findings': ['The cited artifact list contains no clean-build trace.'],
            'evidence': ['artifact-index.json inspected']})
        first_text = (self.vault / first['path']).read_text()
        second_text = (self.vault / second['path']).read_text()
        finding_text = (self.vault / finding['path']).read_text()
        self.assertTrue(first['path'].startswith('Reviews/'))
        self.assertTrue(second['path'].startswith('Reviews/'))
        self.assertIn(second['path'][:-3], first_text)
        self.assertIn(first['path'][:-3], second_text)
        self.assertIn(first['path'][:-3], finding_text)
        self.assertEqual(first['status'], 'pending-review')
        self.assertEqual(second['status'], 'pending-review')
        self.assertIn('status: "hypothesis"', finding_text)

    def test_scoped_checkout_uses_latest_review_and_records_agreement(self):
        blender = self.c.knowledge.create_note({
            'title': 'Blender render baseline', 'body': 'Inspect the rendered artifact.',
            'type': 'procedure', 'status': 'verified', 'worker': 'operator',
            'folder': 'Blender'})
        email = self.c.knowledge.create_note({
            'title': 'Email reply baseline', 'body': 'Confirm the recipient before sending.',
            'type': 'procedure', 'status': 'verified', 'worker': 'operator',
            'folder': 'Email'})
        seed = self.c.knowledge.checkin({
            'title': 'Blender retry', 'summary': 'A preview rendered.', 'worker': 'builder',
            'scopes': ['Blender'], 'issues': ['The first preview failed.']})
        first_review = seed['review']
        latest = self.c.knowledge.review({
            'target_path': first_review['path'], 'title': 'Review of Blender retry',
            'summary': 'The failure needs its log.', 'worker': 'reviewer',
            'verdict': 'needs-evidence', 'findings': ['No log was linked.'], 'evidence': []})
        checkout = self.c.knowledge.checkout({
            'title': 'Blender artifact task', 'instruction': 'Use the Blender render baseline.',
            'worker': 'next-agent', 'task_id': 'scoped-task', 'run_id': 'scoped-run',
            'stage': 'task', 'scopes': ['Blender']})
        paths = [item['path'] for item in checkout['sources']]
        self.assertIn(blender['path'], paths)
        self.assertNotIn(email['path'], paths)
        self.assertEqual([latest['path']], [item['path'] for item in checkout['reviews']])
        task = {'id': 'scoped-task', 'task_number': 4, 'title': 'Blender artifact task',
                'knowledge_scopes': ['Blender'], 'knowledge': {'last_checkout': checkout}}
        note = self.c.knowledge.capture_result(task, {
            'summary': 'Completed scoped task.', 'run_id': 'scoped-run', 'knowledge': {
                'checkout_synthesis': {
                    'learned': 'Inspect the actual artifact.',
                    'application': 'I will inspect the output.',
                    'conflicts': 'None.', 'mistakes_to_avoid': 'Trusting console output.'},
                'review_acknowledgements': [{
                    'path': latest['path'], 'disposition': 'agree',
                    'note': 'I agree that the missing log prevents a stronger conclusion.'}],
                'learnings': [], 'issues': [], 'solutions': [], 'loops': [],
                'decisions': [], 'corrections': [], 'unvalidated': [],
                'evidence': ['artifact inspected']}
        }, 'task', 'next-agent')
        checkout_text = (self.vault / checkout['path']).read_text()
        self.assertIn('— agree: I agree that the missing log', checkout_text)
        self.assertNotIn('- [ ]', checkout_text)
        latest_text = (self.vault / latest['path']).read_text()
        self.assertIn('## Agreements', latest_text)
        self.assertIn(checkout['path'][:-3], latest_text)
        self.assertIsNone(note['review'])

    def test_checkin_writes_scoped_knowledge_and_distinct_review_threads(self):
        result = self.c.knowledge.checkin({
            'title': 'Blender recovery', 'summary': 'Recovered and verified the render.',
            'worker': 'builder', 'task_id': 'task-detailed', 'run_id': 'run-detailed',
            'scopes': ['Blender'], 'evidence': ['final artifact opened'],
            'completed_knowledge': [{
                'title': 'CPU fallback procedure',
                'body': 'Use a small CPU preview before restoring final settings.',
                'scope': 'Blender', 'type': 'procedure', 'status': 'verified',
                'evidence': ['preview.png and final.png inspected'],
            }],
            'review_items': [{
                'title': 'GPU initialization crash', 'kind': 'solved-issue',
                'situation': 'GPU initialization stopped the first render.',
                'handling': 'Captured the error, changed only the device to CPU, and ran a preview.',
                'outcome': 'The preview and final render completed on CPU.',
                'uncertainty': 'The GPU root cause remains unknown.',
                'evidence': ['gpu-error.log', 'preview.png'],
            }, {
                'title': 'Missing driver diagnosis', 'kind': 'unfinished-work',
                'situation': 'The task did not include driver-level diagnosis.',
                'handling': 'Recorded the driver version for a later diagnostic task.',
                'outcome': 'Rendering completed, but driver diagnosis remains open.',
                'uncertainty': 'Whether the driver caused the failure is unproven.',
                'evidence': ['driver-version.txt'],
            }],
        })
        self.assertEqual(1, len(result['knowledge_notes']))
        self.assertEqual(2, len(result['reviews']))
        knowledge = result['knowledge_notes'][0]
        self.assertTrue(knowledge['path'].startswith('Blender/'))
        self.assertIn('CPU fallback procedure', (self.vault / knowledge['path']).read_text())
        kinds = {(self.vault / item['path']).read_text() for item in result['reviews']}
        self.assertTrue(any('review_kind: "solved-issue"' in text for text in kinds))
        self.assertTrue(any('review_kind: "unfinished-work"' in text for text in kinds))
        checkin_text = (self.vault / result['path']).read_text()
        self.assertIn('## Durable knowledge created', checkin_text)
        for item in result['reviews']:
            self.assertIn(item['path'][:-3], checkin_text)

    def test_checkout_conflict_creates_next_review_thread_leaf(self):
        seed = self.c.knowledge.checkin({
            'title': 'Cache task', 'summary': 'Task ended.', 'worker': 'first',
            'scopes': ['Compiler'], 'review_items': [{
                'title': 'Cache workaround', 'kind': 'workaround',
                'situation': 'The cache was stale.', 'handling': 'The cache was deleted.',
                'outcome': 'The build passed once.', 'uncertainty': 'Recurrence is unknown.',
                'evidence': ['build-1.log'],
            }]})
        original = seed['reviews'][0]
        checkout = self.c.knowledge.checkout({
            'title': 'Compiler build', 'instruction': 'Run the compiler build.',
            'worker': 'second', 'task_id': 'compiler-task', 'run_id': 'compiler-run',
            'stage': 'task', 'scopes': ['Compiler']})
        task = {'id': 'compiler-task', 'task_number': 8, 'title': 'Compiler build',
                'knowledge_scopes': ['Compiler'], 'knowledge': {'last_checkout': checkout}}
        self.c.knowledge.capture_result(task, {
            'summary': 'Build completed.', 'run_id': 'compiler-run', 'knowledge': {
                'checkout_synthesis': {'learned': 'The prior cache workaround passed once.',
                    'application': 'Test without deleting first.', 'conflicts': 'Deletion may hide evidence.',
                    'mistakes_to_avoid': 'Deleting evidence before inspection.'},
                'review_acknowledgements': [{
                    'path': original['path'], 'disposition': 'conflict',
                    'note': 'Deleting the cache before capturing its metadata removes diagnostic evidence.'}],
                'learnings': [], 'issues': [], 'solutions': [], 'loops': [], 'decisions': [],
                'corrections': [], 'unvalidated': [], 'evidence': ['build-2.log']}
        }, 'task', 'second')
        next_checkout = self.c.knowledge.checkout({
            'title': 'Compiler follow-up', 'instruction': 'Review the cache evidence.',
            'worker': 'third', 'scopes': ['Compiler']})
        self.assertEqual(1, len(next_checkout['reviews']))
        newest = next_checkout['reviews'][0]
        self.assertNotEqual(original['path'], newest['path'])
        self.assertEqual(original['path'], newest['review_target'])
        self.assertEqual('challenges', newest['review_verdict'])

    def test_invalid_result_is_rejected_before_checkout_or_vault_mutation(self):
        checkout = self.c.knowledge.checkout({
            'title': 'Atomic validation', 'instruction': 'Use scoped knowledge.',
            'worker': 'builder', 'task_id': 'atomic-task', 'run_id': 'atomic-run',
            'stage': 'task', 'scopes': ['Compiler']})
        before = (self.vault / checkout['path']).read_text()
        existing = {path.relative_to(self.vault).as_posix() for path in self.vault.rglob('*.md')}
        task = {'id': 'atomic-task', 'task_number': 10, 'title': 'Atomic validation',
                'knowledge_scopes': ['Compiler'], 'knowledge': {'last_checkout': checkout}}
        with self.assertRaises(ValueError):
            self.c.knowledge.capture_result(task, {
                'summary': 'Invalid result.', 'run_id': 'atomic-run', 'knowledge': {
                    'checkout_synthesis': {'learned': 'A', 'application': 'B',
                                           'conflicts': 'C', 'mistakes_to_avoid': 'D'},
                    'learnings': [], 'issues': [], 'solutions': [], 'loops': [],
                    'decisions': [], 'corrections': [], 'unvalidated': [], 'evidence': [],
                    'completed_knowledge': [{
                        'title': 'Wrong scope', 'body': 'Must not be written.',
                        'scope': 'Email', 'evidence': ['none']}],
                }}, 'task', 'builder')
        self.assertEqual(before, (self.vault / checkout['path']).read_text())
        after = {path.relative_to(self.vault).as_posix() for path in self.vault.rglob('*.md')}
        self.assertEqual(existing, after)

    def test_checkin_commit_failure_rolls_back_all_vault_writes(self):
        checkout = self.c.knowledge.checkout({
            'title': 'Atomic commit', 'instruction': 'Write one scoped procedure.',
            'worker': 'builder', 'task_id': 'commit-task', 'run_id': 'commit-run',
            'stage': 'task', 'scopes': ['Compiler']})
        task = {'id': 'commit-task', 'task_number': 12, 'title': 'Atomic commit',
                'knowledge_scopes': ['Compiler'], 'knowledge': {'last_checkout': checkout}}
        checkout_path = self.vault / checkout['path']
        before_text = checkout_path.read_text()
        before_paths = {path.relative_to(self.vault).as_posix()
                        for path in self.vault.rglob('*.md')}
        original_write = self.c.knowledge.adapter._write_now
        calls = {'count': 0}

        def fail_second_write(path, content):
            calls['count'] += 1
            if calls['count'] == 2:
                raise OSError('injected commit failure')
            return original_write(path, content)

        self.c.knowledge.adapter._write_now = fail_second_write
        try:
            with self.assertRaisesRegex(OSError, 'injected commit failure'):
                self.c.knowledge.capture_result(task, {
                    'summary': 'Prepared the procedure.', 'run_id': 'commit-run', 'knowledge': {
                        'checkout_synthesis': {
                            'learned': 'Use an atomic write.', 'application': 'Create one procedure.',
                            'conflicts': 'None.', 'mistakes_to_avoid': 'Partial publication.'},
                        'review_acknowledgements': [],
                        'learnings': [], 'issues': [], 'solutions': [], 'loops': [],
                        'decisions': [], 'corrections': [], 'unvalidated': [],
                        'evidence': ['procedure inspected'],
                        'completed_knowledge': [{
                            'title': 'Atomic compiler procedure', 'body': 'Publish the batch together.',
                            'scope': 'Compiler', 'type': 'procedure', 'status': 'supported',
                            'evidence': ['procedure inspected']}],
                        'review_items': []}}, 'task', 'builder')
        finally:
            self.c.knowledge.adapter._write_now = original_write
        after_paths = {path.relative_to(self.vault).as_posix()
                       for path in self.vault.rglob('*.md')}
        self.assertEqual(before_paths, after_paths)
        self.assertEqual(before_text, checkout_path.read_text())
        self.assertNotIn('last_checkin', task['knowledge'])

    def test_public_checkin_commit_failure_rolls_back_all_vault_writes(self):
        before_paths = {path.relative_to(self.vault).as_posix()
                        for path in self.vault.rglob('*.md')}
        original_write = self.c.knowledge.adapter._write_now
        calls = {'count': 0}

        def fail_second_write(path, content):
            calls['count'] += 1
            if calls['count'] == 2:
                raise OSError('injected public checkin failure')
            return original_write(path, content)

        self.c.knowledge.adapter._write_now = fail_second_write
        try:
            with self.assertRaisesRegex(OSError, 'injected public checkin failure'):
                self.c.knowledge.checkin({
                    'title': 'Direct atomicity probe', 'summary': 'Prepared one procedure.',
                    'worker': 'external-agent', 'task_id': 'direct-task', 'run_id': 'direct-run',
                    'scopes': ['Compiler'], 'evidence': ['artifact inspected'],
                    'completed_knowledge': [{
                        'title': 'Direct compiler procedure', 'body': 'Commit the batch together.',
                        'scope': 'Compiler', 'type': 'procedure', 'status': 'supported',
                        'evidence': ['artifact inspected']}],
                    'review_items': [{
                        'title': 'Direct write issue', 'kind': 'solved-issue',
                        'situation': 'A staged write was needed.',
                        'handling': 'The agent used the public check-in.',
                        'outcome': 'The batch was prepared.', 'uncertainty': 'None.',
                        'evidence': ['artifact inspected']}],
                })
        finally:
            self.c.knowledge.adapter._write_now = original_write
        after_paths = {path.relative_to(self.vault).as_posix()
                       for path in self.vault.rglob('*.md')}
        self.assertEqual(before_paths, after_paths)

    def test_rejected_result_creates_one_scoped_review_for_the_failed_loop(self):
        checkout = self.c.knowledge.checkout({
            'title': 'Blender result validation', 'instruction': 'Prepare a render plan.',
            'worker': 'builder', 'task_id': 'rejected-task', 'run_id': 'rejected-run',
            'stage': 'task', 'scopes': ['Blender']})
        task = {'id': 'rejected-task', 'task_number': 11, 'title': 'Blender result validation',
                'run_id': 'rejected-run', 'knowledge_scopes': ['Blender'],
                'knowledge': {'last_checkout': checkout}}
        first = self.c.knowledge.record_result_failure(
            task, 'builder', 'task', 'review_items must use the required field names.',
            ['Rejected result file: runs/rejected-run/result.json'])
        repeated = self.c.knowledge.record_result_failure(
            task, 'builder', 'task', 'review_items must use the required field names.',
            ['Rejected result file: runs/rejected-run/result.json'])
        self.assertEqual(first['path'], repeated['path'])
        second = self.c.knowledge.record_result_failure(
            task, 'builder', 'task', 'checkout_synthesis is missing.',
            ['ACC validation error: missing synthesis'])
        replayed_first = self.c.knowledge.record_result_failure(
            task, 'builder', 'task', 'review_items must use the required field names.',
            ['Rejected result file: runs/rejected-run/result.json'])
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(first['path'], replayed_first['path'])
        self.assertTrue(first['path'].startswith('Reviews/'))
        text = (self.vault / first['path']).read_text()
        self.assertIn('review_kind: "failed-loop"', text)
        self.assertIn('ACC rejected the worker result', text)
        self.assertIn('preserved task files and checkout', text)
        self.assertIn('require review before reuse', text)
        self.assertIn('Rejected result file', text)
        next_checkout = self.c.knowledge.checkout({
            'title': 'Blender follow-up', 'instruction': 'Continue only after reviewing failures.',
            'worker': 'next-builder', 'scopes': ['Blender']})
        self.assertEqual({first['path'], second['path']},
                         {item['path'] for item in next_checkout['reviews']})

    def test_failure_deduplication_is_scoped_to_the_current_checkout(self):
        task = {'id': 'retry-task', 'task_number': 13, 'title': 'Retry task',
                'run_id': 'stale-run', 'knowledge_scopes': ['Compiler'], 'knowledge': {}}
        first_checkout = self.c.knowledge.checkout({
            'title': 'Retry task', 'instruction': 'First attempt.', 'worker': 'builder',
            'task_id': task['id'], 'run_id': 'first-run', 'stage': 'task',
            'scopes': ['Compiler']})
        task['knowledge']['last_checkout'] = first_checkout
        first = self.c.knowledge.record_result_failure(
            task, 'builder', 'task', 'Provider dispatch failed.')
        second_checkout = self.c.knowledge.checkout({
            'title': 'Retry task', 'instruction': 'Second attempt.', 'worker': 'builder',
            'task_id': task['id'], 'run_id': 'second-run', 'stage': 'task',
            'scopes': ['Compiler']})
        task['knowledge']['last_checkout'] = second_checkout
        second = self.c.knowledge.record_result_failure(
            task, 'builder', 'task', 'Provider dispatch failed.')
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(first_checkout['path'], first['target_path'])
        self.assertEqual(second_checkout['path'], second['target_path'])

    def test_coordinator_routes_rejected_model_checkin_to_reviews(self):
        worker = (
            'import json,sys,pathlib; '
            'p=json.load(open(sys.argv[1])); rc=p["knowledge"]["result_contract"]; '
            'r={"task_id":rc["task_id"],"run_id":rc["run_id"],'
            '"revision":rc["revision"],"snapshot_id":rc["snapshot_id"],'
            '"summary":"Task files written, but check-in is malformed.",'
            '"knowledge":{}}; '
            'pathlib.Path(p["result_file"]).write_text(json.dumps(r))')
        self.c.agents['malformed-model'] = {
            'id': 'malformed-model', 'name': 'Malformed model', 'kind': 'model',
            'available': True, 'local': True,
            'argv': [sys.executable, '-c', worker, '{prompt_file}'],
        }
        task = self.c.create({
            'title': 'Malformed Blender check-in', 'instruction': 'Return a valid check-in.',
            'agent': 'malformed-model', 'knowledge_scopes': ['Blender']})
        self.c.start(task['id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = self.c.store.get(task['id'])
            if current['status'] == 'failed':
                break
            time.sleep(.04)
        else:
            self.fail('Malformed knowledge result did not reach failed state.')
        current = self.c.store.get(task['id'])
        review = current['knowledge']['last_result_failure_review']
        self.assertTrue(review['path'].startswith('Reviews/'))
        review_text = (self.vault / review['path']).read_text()
        self.assertIn('review_kind: "failed-loop"', review_text)
        self.assertIn('Worker did not complete the required checkout synthesis', review_text)
        self.assertIn('Worker result retained at:', review_text)
        follow_up = self.c.knowledge.checkout({
            'title': 'Follow up malformed result', 'instruction': 'Review the failed loop first.',
            'worker': 'next-worker', 'scopes': ['Blender']})
        self.assertEqual([review['path']], [item['path'] for item in follow_up['reviews']])

    def test_nonzero_model_exit_becomes_scoped_failed_loop_review(self):
        self.c.agents['failing-model'] = {
            'id': 'failing-model', 'name': 'Failing model', 'kind': 'model',
            'available': True, 'local': True,
            'argv': [sys.executable, '-c', 'import sys; sys.exit(3)'],
        }
        task = self.c.create({
            'title': 'Failed Blender worker', 'instruction': 'Attempt the scoped task.',
            'agent': 'failing-model', 'knowledge_scopes': ['Blender']})
        self.c.start(task['id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = self.c.store.get(task['id'])
            if current['status'] == 'failed':
                break
            time.sleep(.04)
        else:
            self.fail('Nonzero model run did not reach failed state.')
        current = self.c.store.get(task['id'])
        review = current['knowledge']['last_result_failure_review']
        text = (self.vault / review['path']).read_text()
        self.assertIn('review_kind: "failed-loop"', text)
        self.assertIn('Worker process exited with code 3', text)
        self.assertIn('No trustworthy worker handling was returned', text)
        self.assertIn('Observed subprocess exit code: 3', text)
        follow_up = self.c.knowledge.checkout({
            'title': 'Retry failed Blender worker', 'instruction': 'Review the failure first.',
            'worker': 'next-worker', 'scopes': ['Blender']})
        self.assertEqual([review['path']], [item['path'] for item in follow_up['reviews']])

    def test_model_timeout_is_failed_loop_not_unfinished_work(self):
        self.c.agents['slow-model'] = {
            'id': 'slow-model', 'name': 'Slow model', 'kind': 'model',
            'available': True, 'local': True,
            'argv': [sys.executable, '-c', 'import time; time.sleep(10)'],
        }
        task = self.c.create({
            'title': 'Timed Blender worker', 'instruction': 'Attempt the scoped task.',
            'agent': 'slow-model', 'knowledge_scopes': ['Blender'], 'timeout_seconds': 1})
        self.c.start(task['id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = self.c.store.get(task['id'])
            if current['status'] == 'failed':
                break
            time.sleep(.04)
        else:
            self.fail('Timed model run did not reach failed state.')
        review = current['knowledge']['last_result_failure_review']
        text = (self.vault / review['path']).read_text()
        self.assertIn('review_kind: "failed-loop"', text)
        self.assertIn('timed out', text)

    def test_knowledge_worker_is_blocked_on_main(self):
        subprocess.run(['git', '-C', str(self.project), 'checkout', '-q', '-b', 'main'], check=True)
        self.c.agents['fixture-model'] = {
            'id': 'fixture-model', 'name': 'Fixture model', 'kind': 'model',
            'available': True, 'argv': [sys.executable, '-c', 'pass']}
        task = self.c.create({'title': 'Protected branch', 'instruction': 'Do work.',
                              'agent': 'fixture-model', 'knowledge_scopes': ['Compiler']})
        with self.assertRaises(Conflict):
            self.c.start(task['id'])

    def test_scoped_checkout_includes_more_than_one_hundred_review_leaves(self):
        target = self.c.knowledge.create_note({
            'title': 'Large review target', 'body': 'Review the render incidents.',
            'type': 'finding', 'status': 'supported', 'worker': 'builder',
            'folder': 'Blender', 'scopes': ['Blender']})
        for number in range(101):
            self.c.knowledge._create_review({
                'title': f'Render incident {number}', 'summary': f'Incident {number}',
                'body': '## Evidence\n\n- fixture', 'target_path': target['path'],
                'verdict': 'unreviewed', 'worker': 'fixture', 'scopes': ['Blender'],
                'review_kind': 'unresolved-issue'})
        checkout = self.c.knowledge.checkout({
            'title': 'Review every Blender incident',
            'instruction': 'Inspect the complete queue.', 'worker': 'reviewer',
            'scopes': ['Blender']})
        self.assertEqual(101, len(checkout['reviews']))

    def test_folder_scope_excludes_same_named_root_note(self):
        self.c.knowledge.adapter.write('Blender.md', '---\ntitle: "Root Blender"\n'
                                       'type: "reference"\nstatus: "verified"\n---\n'
                                       '# Root Blender\n\nroot-only token\n')
        self.c.knowledge.create_note({
            'title': 'Scoped Blender', 'body': 'folder token', 'type': 'reference',
            'status': 'verified', 'worker': 'operator', 'folder': 'Blender'})
        root = self.c.knowledge.search({'query': 'root-only token', 'scopes': ['Blender']})
        self.assertNotIn('Blender.md', [item['path'] for item in root['results']])
        scoped = self.c.knowledge.search({'query': 'folder token', 'scopes': ['Blender']})
        self.assertEqual(1, scoped['count'])

    def test_unmanaged_model_run_checks_out_and_checks_in_automatically(self):
        runner = self.root / 'knowledge_runner.py'
        runner.write_text('''
import json, pathlib, sys
packet = json.loads(pathlib.Path(sys.argv[1]).read_text())
checkout = pathlib.Path(packet["knowledge"]["absolute_path"])
text = checkout.read_text()
text = text.replace("- What I learned:\\n- How I will apply it:\\n- Conflicts or uncertainty:\\n- Known mistakes I will avoid:",
                    "- What I learned: use the vault.\\n- How I will apply it: follow the note.\\n- Conflicts or uncertainty: none.\\n- Known mistakes I will avoid: skipping checkout.")
checkout.write_text(text)
contract = packet["knowledge"]["result_contract"]
result = {**{k: contract[k] for k in ("task_id", "run_id", "revision", "snapshot_id")},
          "summary": "Completed with vault context.",
          "knowledge": {"learnings": ["Checkout worked."], "issues": [], "solutions": [],
                        "loops": [], "decisions": [], "corrections": [], "evidence": ["runner"]}}
pathlib.Path(packet["result_file"]).write_text(json.dumps(result))
''')
        self.c.agents['fixture-model'] = {
            'id': 'fixture-model', 'name': 'Fixture model', 'kind': 'model', 'available': True,
            'argv': [sys.executable, str(runner), '{prompt_file}']}
        task = self.c.create({'title': 'Automatic knowledge', 'instruction': 'Use project knowledge.',
                              'agent': 'fixture-model'})
        self.c.start(task['id'])
        self.c.worker_thread.join(timeout=10)
        finished = self.c.store.get(task['id'])
        self.assertEqual(finished['status'], 'awaiting_review')
        self.assertTrue((self.vault / finished['knowledge']['last_checkin']['path']).is_file())

    def test_remote_worker_can_return_checkout_synthesis(self):
        checkout = self.c.knowledge.checkout({
            'title': 'Remote task', 'instruction': 'Use the retrieved knowledge.',
            'worker': 'remote-agent', 'task_id': 'remote-task', 'run_id': 'remote-run',
            'stage': 'implement'})
        task = {'id': 'remote-task', 'task_number': 9, 'title': 'Remote task',
                'knowledge': {'last_checkout': checkout}}
        note = self.c.knowledge.capture_result(task, {
            'summary': 'Remote work finished.', 'run_id': 'remote-run', 'knowledge': {
                'checkout_synthesis': {
                    'learned': 'The existing workflow is reusable.',
                    'application': 'I followed its sequence.',
                    'conflicts': 'None observed.',
                    'mistakes_to_avoid': 'Skipping evidence.'},
                'learnings': [], 'issues': [], 'solutions': [], 'loops': [], 'decisions': [],
                'corrections': [], 'evidence': ['remote result']}}, 'implement', 'remote-agent')
        self.assertTrue((self.vault / note['path']).is_file())
        self.assertIn('The existing workflow is reusable.',
                      (self.vault / checkout['path']).read_text())

    def test_mcp_exposes_high_level_knowledge_tools(self):
        server = Server(('127.0.0.1', 0), self.c, 'knowledge-token')
        url = 'http://127.0.0.1:' + str(server.server_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            listed = dispatch({'id': 1, 'method': 'tools/list'}, url, 'knowledge-token')
            names = {tool['name'] for tool in listed['result']['tools']}
            self.assertIn('acc_knowledge_checkout', names)
            self.assertIn('acc_knowledge_checkin', names)
            self.assertIn('acc_knowledge_review', names)
            created = dispatch({'id': 2, 'method': 'tools/call', 'params': {
                'name': 'acc_knowledge_note', 'arguments': {
                    'title': 'MCP learning', 'body': 'The vault is reachable.',
                    'type': 'finding', 'status': 'supported', 'worker': 'chatgpt'}}},
                url, 'knowledge-token')
            self.assertFalse(created['result']['isError'])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_path_escape_is_rejected(self):
        with self.assertRaises(ValueError):
            self.c.knowledge.transition({'path': '../outside.md', 'status': 'obsolete'})


if __name__ == '__main__':
    unittest.main()
