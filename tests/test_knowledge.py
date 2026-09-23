import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from acc.bridge import dispatch
from acc.core import Coordinator
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
