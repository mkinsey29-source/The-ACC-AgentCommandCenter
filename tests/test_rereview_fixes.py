"""Regression tests for the second independent review (2026-10-13): SC7-01, SC8-01, SC9-01..05, OTF-01.

Every class names the stable finding ID it reproduces. They fail on cecf0a9 and pass now. The owner-only paths use
the agent credential and a paired device, since both are gated callers.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from acc import setup as acc_setup
from acc.bridge import dispatch, resolve_endpoint
from acc.core import Coordinator
from acc.gates import argv_display, classify, redact_url, visible
from acc.knowledge import OwnerApprovalRequired
from acc.pairing import Pairing
from acc.reach import Reach, check
from acc.server import Server, _write_endpoint, mutate
from test_gated_access import AGENT, OWNER, REMOTE, Fixture as GatedFixture

REPO = Path(__file__).resolve().parent.parent
FAKE = str(Path(__file__).with_name('fake_tailscale.py'))
PY = sys.executable


def wait_for(condition, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(.1)
    return False


class KnowledgeFixture(GatedFixture):
    """A knowledge-enabled project and a configured model worker that writes a result claiming verified knowledge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project, self.vault = self.root / 'project', self.root / 'vault'
        self.project.mkdir(); self.vault.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), 'checkout', '-q', '-b', 'feature/test'], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        self.marker = self.root / 'marker'
        stub = self.root / 'stub.py'
        stub.write_text(
            'import json, sys\n'
            'packet = json.load(open(sys.argv[1]))\n'
            'contract = packet["knowledge"]["result_contract"]\n'
            'open(' + repr(str(self.marker)) + ', "w").write("worker ran")\n'
            'json.dump({"task_id": contract["task_id"], "run_id": contract["run_id"], "revision": contract["revision"], "snapshot_id": None,\n'
            '  "summary": "done", "knowledge": {"checkout_synthesis": {"learned": "a", "application": "b", "conflicts": "c", "mistakes_to_avoid": "d"},\n'
            '  "learnings": [], "issues": [], "solutions": [], "loops": [], "decisions": [], "corrections": [], "unvalidated": [],\n'
            '  "evidence": ["ran"], "completed_knowledge": [{"title": "Self promoted", "body": "claim", "scope": "Claims", "type": "finding",\n'
            '  "status": "verified", "evidence": ["worker text"]}]}}, open(packet["result_file"], "w"))\n')
        config = self.root / 'agents.json'
        config.write_text(json.dumps({
            'agents': [{'id': 'stub', 'name': 'Stub worker', 'argv': [PY, str(stub), '{prompt_file}']}],
            'knowledge': {'enabled': True, 'vault': {'id': 'fixture', 'path': str(self.vault)}, 'embeddings': {'provider': 'none'}}}))
        self.c = Coordinator(self.project, self.root / 'state', config)
        self.reach = Reach('tailscale-serve', '127.0.0.1', [REMOTE], secure_cookie=True)
        self.server = Server(('127.0.0.1', 0), self.c, OWNER, reach=self.reach,
                             pairing=Pairing(self.root / 'state' / 'pairing.json'), agent_token=AGENT)
        self.port = self.server.server_port
        self.local = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def notes(self):
        found = {}
        for path in self.vault.rglob('*.md'):
            found[path.relative_to(self.vault).as_posix()] = path.read_text()
        return found

    def statuses(self):
        import re
        return {name: (re.search(r'(?m)^status:\s*"?([a-z-]+)', text) or [None, None])[1] for name, text in self.notes().items()}


class DefaultCheckin(KnowledgeFixture):
    """ACC-RR-20261013-SC9-01: an omitted check-in status is active, which only the owner may establish."""

    BODY = {'title': 'Implicit', 'summary': 'claim', 'worker': 'probe'}

    def test_an_omitted_status_waits_for_the_owner_for_both_callers(self):
        cookie = self.pair_device()
        for who, call in (('agent', lambda: self.agent('POST', '/api/knowledge/checkin', self.BODY)),
                          ('device', lambda: self.device(cookie, 'POST', '/api/knowledge/checkin', self.BODY))):
            with self.subTest(who=who):
                status, body = call()
                self.assertEqual((status, body.get('status')), (202, 'approval_required'), body)
        self.assertEqual(self.notes(), {}, 'nothing may be written before the owner answers')

    def test_a_routine_check_in_with_status_supported_is_written_and_is_not_established(self):
        status, body = self.agent('POST', '/api/knowledge/checkin', {**self.BODY, 'status': 'supported'})
        self.assertEqual(status, 200, body)
        self.assertEqual(set(self.statuses().values()), {'supported'})

    def test_the_write_boundary_refuses_a_gated_check_in_even_when_nothing_classified_it(self):
        for payload in (self.BODY, {**self.BODY, 'status': 'active'}, {**self.BODY, 'status': 'verified'},
                        {**self.BODY, 'status': 'supported', 'scopes': ['K'], 'evidence': ['e'],
                         'completed_knowledge': [{'title': 'x', 'body': 'b', 'scope': 'K', 'status': 'verified', 'evidence': ['e']}]}):
            with self.subTest(payload=payload), self.assertRaises(OwnerApprovalRequired):
                mutate(self.c, ['api', 'knowledge', 'checkin'], payload, 'gated')
        self.assertEqual(self.notes(), {}, 'refusal happens before any write')

    def test_every_other_established_write_is_refused_at_the_boundary_for_a_gated_caller(self):
        note = self.c.knowledge.create_note({'title': 'Old', 'body': 'b', 'status': 'hypothesis'})
        for parts, payload in ((['api', 'knowledge', 'note'], {'title': 'N', 'body': 'b', 'status': 'verified'}),
                               (['api', 'knowledge', 'note'], {'title': 'N', 'body': 'b', 'status': 'active'}),
                               (['api', 'knowledge', 'transition'], {'path': note['path'], 'status': 'verified'}),
                               (['api', 'knowledge', 'rebuttal'], {'original_path': note['path'], 'title': 't', 'explanation': 'e'})):
            with self.subTest(parts=parts, payload=payload), self.assertRaises(OwnerApprovalRequired):
                mutate(self.c, parts, payload, 'gated')
        self.assertEqual(list(self.notes()), [note['path']])

    def test_the_owner_approving_writes_it_as_asked(self):
        _, asked = self.agent('POST', '/api/knowledge/checkin', self.BODY)
        status, answered = self.owner('POST', f"/api/approvals/{asked['approval_id']}/approve", {})
        self.assertEqual((status, answered['status']), (200, 'executed'), answered)
        self.assertIn('active', set(self.statuses().values()))


class WorkerResultPromotion(KnowledgeFixture):
    """ACC-RR-20261013-SC9-02: a worker's own claim of verified knowledge is kept as supported until the owner promotes it."""

    def run_worker(self):
        status, task = self.agent('POST', '/api/tasks', {'title': 'Worker task', 'instruction': 'do it', 'agent': 'stub',
                                                         'knowledge_scopes': ['Claims']})
        self.assertEqual(status, 201, task)
        status, started = self.agent('POST', f"/api/tasks/{task['id']}/start", {})
        self.assertEqual(status, 200, started)
        self.assertTrue(wait_for(lambda: self.marker.exists()))
        self.assertTrue(wait_for(lambda: self.c.store.get(task['id'])['status'] not in ('launching', 'running', 'processing_result')))
        return task['id']

    def test_a_configured_worker_cannot_write_verified_knowledge_by_itself(self):
        task_id = self.run_worker()
        self.assertEqual(self.owner('GET', '/api/state')[1]['attention'], [], 'running a configured worker needs no approval')
        statuses = self.statuses()
        written = {name: status for name, status in statuses.items()
                   if 'acc/checkin' in self.notes()[name] or 'acc/completed-knowledge' in self.notes()[name]}
        self.assertEqual(len(written), 2, 'the check-in note and the completed-knowledge note were captured: ' + str(statuses))
        self.assertEqual(set(written.values()), {'supported'}, 'nothing the worker wrote is established')
        self.assertNotIn('verified', statuses.values())
        claimed = [text for text in self.notes().values() if 'Self promoted' in text and 'acc/completed-knowledge' in text]
        self.assertEqual(len(claimed), 1)
        self.assertIn('Promotion requested', claimed[0])
        self.assertIn('knowledge_promotion_requested', self.events())
        self.assertEqual(self.c.store.get(task_id)['knowledge']['last_knowledge_notes'][0]['status'], 'supported')

    def test_the_owner_can_then_promote_it(self):
        self.run_worker()
        path = next(name for name, text in self.notes().items() if 'Self promoted' in text and 'acc/completed-knowledge' in text)
        status, body = self.owner('POST', '/api/knowledge/transition', {'path': path, 'status': 'verified', 'evidence': ['owner checked']})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.statuses()[path], 'verified')

    def test_capture_downgrades_without_a_partial_established_write(self):
        checkout = self.c.knowledge.checkout({'title': 't', 'instruction': 'i', 'worker': 'w', 'task_id': 'x', 'run_id': 'r',
                                              'stage': 'task', 'scopes': ['Claims']})
        task = {'id': 'x', 'task_number': 1, 'title': 't', 'knowledge_scopes': ['Claims'], 'knowledge': {'last_checkout': checkout}}
        self.c.knowledge.capture_result(task, {'summary': 's', 'run_id': 'r', 'knowledge': {
            'checkout_synthesis': {'learned': 'a', 'application': 'b', 'conflicts': 'c', 'mistakes_to_avoid': 'd'},
            'learnings': [], 'issues': [], 'solutions': [], 'loops': [], 'decisions': [], 'corrections': [], 'unvalidated': [],
            'evidence': ['e'], 'completed_knowledge': [
                {'title': 'A', 'body': 'b', 'scope': 'Claims', 'status': 'verified', 'evidence': ['e']},
                {'title': 'B', 'body': 'b', 'scope': 'Claims', 'status': 'supported', 'evidence': ['e']}]}}, 'task', 'w')
        self.assertNotIn('verified', self.statuses().values())

    def test_every_route_that_captures_a_result_goes_through_the_untrusted_boundary(self):
        import re
        callers = {}
        for path in (REPO / 'acc').glob('*.py'):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r'knowledge\.(checkin|_checkin|capture_result)\(', line) or re.search(r'self\._checkin\(', line):
                    callers.setdefault(path.name, []).append(line.strip())
        self.assertEqual(sorted(callers), ['core.py', 'knowledge.py', 'server.py', 'workflow.py'], callers)
        self.assertTrue(all('capture_result' in line for line in callers['core.py'] + callers['workflow.py']), callers)
        self.assertTrue(any("authority='worker'" in line for line in callers['knowledge.py']), callers)
        self.assertTrue(all('authority' in line for line in callers['server.py']), callers)

    def events(self):
        return [e['kind'] for e in self.c.store.events(0, 2000)]


class ApprovalBinding(GatedFixture):
    """ACC-RR-20261013-SC9-03: an approval is for the exact task, worker and command that were shown."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.marker_a, self.marker_b = self.root / 'a-ran', self.root / 'b-ran'
        stub = self.root / 'stub.py'
        stub.write_text(f'open({str(self.marker_b)!r}, "w").write("B")\n')
        config = self.root / 'agents.json'
        config.write_text(json.dumps({'agents': [{'id': 'stub', 'name': 'Stub worker', 'argv': [PY, str(stub), '{prompt_file}']}]}))
        self.c = Coordinator(self.root, self.root / 'state', config)
        self.reach = Reach('tailscale-serve', '127.0.0.1', [REMOTE], secure_cookie=True)
        self.server = Server(('127.0.0.1', 0), self.c, OWNER, reach=self.reach,
                             pairing=Pairing(self.root / 'state' / 'pairing.json'), agent_token=AGENT)
        self.port = self.server.server_port
        self.local = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stored_command_task(self):
        argv = [PY, '-c', f'open({str(self.marker_a)!r}, "w").write("A")']
        status, task = self.owner('POST', '/api/tasks', {'title': 'Stored command', 'instruction': 'run', 'argv': argv})
        self.assertEqual(status, 201, task)
        return task['id']

    def request_start(self, caller, task_id):
        status, body = caller('POST', f'/api/tasks/{task_id}/start', {})
        self.assertEqual((status, body.get('status')), (202, 'approval_required'), body)
        return body['approval_id']

    def callers(self):
        cookie = self.pair_device()
        return {'agent': self.agent, 'device': lambda m, p, b=None: self.device(cookie, m, p, b)}

    def test_reassignment_after_the_request_makes_the_approval_stale(self):
        for who, caller in self.callers().items():
            with self.subTest(who=who):
                for marker in (self.marker_a, self.marker_b):
                    marker.unlink(missing_ok=True)
                task_id = self.stored_command_task()
                approval = self.request_start(caller, task_id)
                self.assertEqual(caller('POST', f'/api/tasks/{task_id}/assign', {'agent': 'stub'})[0], 200)
                status, body = self.owner('POST', f'/api/approvals/{approval}/approve', {})
                self.assertEqual(status, 409, body)
                time.sleep(.5)
                self.assertFalse(self.marker_a.exists() or self.marker_b.exists(), 'neither worker may have run')
                self.assertEqual(self.owner('GET', f'/api/approvals/{approval}')[1]['status'], 'stale')
                self.assertEqual(self.c.store.get(task_id)['status'], 'queued')

    def test_a_revision_change_makes_it_stale_and_a_new_request_is_a_new_approval(self):
        task_id = self.stored_command_task()
        first = self.request_start(self.agent, task_id)
        self.assertEqual(self.owner('POST', f'/api/tasks/{task_id}/instructions', {'instruction': 'a different requirement'})[0], 200)
        second = self.request_start(self.agent, task_id)
        self.assertNotEqual(first, second, 'a changed task is a new request, not the old text')
        self.assertEqual(self.owner('GET', f'/api/approvals/{first}')[1]['status'], 'superseded')
        self.assertEqual(self.owner('POST', f'/api/approvals/{first}/approve', {})[0], 409)
        self.assertFalse(self.marker_a.exists())
        status, body = self.owner('POST', f'/api/approvals/{second}/approve', {})
        self.assertEqual((status, body['status']), (200, 'executed'), body)
        self.assertTrue(wait_for(self.marker_a.exists))

    def test_altered_execution_data_makes_it_stale(self):
        task_id = self.stored_command_task()
        approval = self.request_start(self.agent, task_id)
        with self.c.lock:
            task = self.c.store.get(task_id)
            task['argv'] = [PY, '-c', f'open({str(self.marker_b)!r}, "w").write("changed")']
            self.c.store.save(task, 'test_alter', {'message': 'argv altered'})
        self.assertEqual(self.owner('POST', f'/api/approvals/{approval}/approve', {})[0], 409)
        time.sleep(.5)
        self.assertFalse(self.marker_a.exists() or self.marker_b.exists())

    def test_a_changed_worker_configuration_makes_it_stale(self):
        task_id = self.stored_command_task()
        approval = self.request_start(self.agent, task_id)
        self.c.agents['local-command'] = {**self.c.agents['local-command'], 'description': 'reconfigured'}
        self.assertEqual(self.owner('POST', f'/api/approvals/{approval}/approve', {})[0], 409)
        self.assertFalse(self.marker_a.exists())

    def test_an_unchanged_task_still_runs_once_and_cannot_be_replayed(self):
        task_id = self.stored_command_task()
        approval = self.request_start(self.agent, task_id)
        self.assertEqual(self.request_start(self.agent, task_id), approval, 'an identical retry returns the same request')
        status, body = self.owner('POST', f'/api/approvals/{approval}/approve', {})
        self.assertEqual((status, body['status']), (200, 'executed'), body)
        self.assertTrue(wait_for(self.marker_a.exists))
        self.assertEqual(self.owner('POST', f'/api/approvals/{approval}/approve', {})[0], 409)


class CommandDisplay(unittest.TestCase):
    """ACC-RR-20261013-SC9-04: distinct argument lists look distinct, and nothing invisible is hidden."""

    class Coordinator:
        agents = {}

    def shown(self, argv):
        gate = classify(self.Coordinator(), ['api', 'tasks'], {'title': 't', 'argv': argv})
        return gate.details['argv_json'], gate

    def test_argument_boundaries_are_visible(self):
        a, _ = self.shown(['python', '-c', 'print(1)', 'ignored'])
        b, _ = self.shown(['python', '-c', 'print(1) ignored'])
        self.assertNotEqual(a, b)
        self.assertEqual(json.loads(a), ['python', '-c', 'print(1)', 'ignored'])
        self.assertEqual(json.loads(b), ['python', '-c', 'print(1) ignored'])

    def test_quotes_newlines_and_spaces_are_distinguishable_and_lossless(self):
        samples = [['a b'], ['a', 'b'], ['a"b'], ['a\nb'], ['a\\nb'], ['a  b'], ['a b '], ['']]
        shown = [self.shown(argv)[0] for argv in samples]
        self.assertEqual(len(set(shown)), len(samples))
        for argv, text in zip(samples, shown):
            self.assertEqual(json.loads(text), argv)

    def test_bidi_and_other_invisible_characters_are_written_out(self):
        tricky = 'safe‮evil⁦​﻿ end\x1b[0m\x00'
        text, gate = self.shown(['run', tricky])
        for ch in text:
            self.assertNotIn(ch, ('‮', '⁦', '​', '﻿', ' ', '\x1b', '\x00'))
        for escaped in ('\\u202e', '\\u2066', '\\u200b', '\\ufeff', '\\u00a0', '\\u001b', '\\u0000'):
            self.assertIn(escaped, text.replace('\\\\', '\\'))
        self.assertEqual(gate.details['argv'], ['run', tricky], 'the raw content is still preserved for the record')
        self.assertTrue(all(ord(c) >= 0x20 and c != '\x7f' for c in gate.summary))

    def test_a_very_long_argument_is_shown_whole(self):
        text, gate = self.shown(['x' * 50000])
        self.assertEqual(json.loads(text), ['x' * 50000])
        self.assertLess(len(gate.summary), 400)

    def test_html_is_left_for_the_page_to_escape_and_the_page_escapes_it(self):
        text, _ = self.shown(['<img src=x onerror=alert(1)>'])
        self.assertIn('<img', text)
        app = (REPO / 'acc' / 'web' / 'app.js').read_text()
        self.assertIn('escapeHTML(lines.join', app)
        self.assertIn('d.argv_json', app)

    def test_visible_handles_astral_and_surrogate_input(self):
        self.assertEqual(visible('a\U000e0001b'), 'a\\udb40\\udc01b')
        self.assertEqual(argv_display(['a', 'b']), '["a", "b"]')


class PublishConsent(GatedFixture):
    """ACC-RR-20261013-SC9-05: consent names the preview's real repository, task branch, base and destination."""

    def put_preview(self, task_id, **changes):
        preview = {'id': 'prev-1', 'task_id': task_id, 'revision': 1, 'snapshot_id': 'snap', 'repository': 'octo/widgets',
                   'branch': 'acc/task-7-fix', 'base': 'release', 'remote': 'origin',
                   'push_url': 'https://user:s3cretvalue@github.com/octo/widgets.git', 'head': 'a' * 40, 'expected_tree': {},
                   'outgoing_commits': [{'sha': 'b' * 40, 'subject': 's'}], 'changed_paths': ['src/a.py'], 'created': time.time(), **changes}
        with self.c.store.connect() as db:
            self.c.conversation.put(db, 'publish_preview_' + task_id, preview)
        return preview

    def make_task(self):
        return self.owner('POST', '/api/tasks', {'title': 'Fix widgets', 'instruction': 'fix'})[1]['id']

    def test_the_approval_shows_the_previews_branch_repository_base_and_a_redacted_destination(self):
        task_id = self.make_task()
        self.c.github.settings = {**self.c.github.settings, 'source': 'main', 'base': 'main', 'remote': 'origin'}
        self.put_preview(task_id)
        status, body = self.agent('POST', f'/api/tasks/{task_id}/publish', {'preview_id': 'prev-1', 'request_id': 'req-1'})
        self.assertEqual(status, 202, body)
        details = body['approval']['details']
        self.assertEqual((json.loads(details['repository']), json.loads(details['branch']), json.loads(details['base'])),
                         ('octo/widgets', 'acc/task-7-fix', 'release'))
        self.assertEqual(json.loads(details['push_destination']), 'https://github.com/octo/widgets.git')
        self.assertNotIn('s3cretvalue', json.dumps(body))
        self.assertIn('acc/task-7-fix', body['approval']['summary'])
        self.assertNotEqual(json.loads(details['branch']), self.c.github.settings['source'], 'the configured source branch is not what is pushed')

    def test_a_replaced_preview_makes_the_approval_stale(self):
        task_id = self.make_task()
        self.put_preview(task_id)
        _, body = self.agent('POST', f'/api/tasks/{task_id}/publish', {'preview_id': 'prev-1', 'request_id': 'req-1'})
        self.put_preview(task_id, id='prev-2', push_url='https://github.com/evil/other.git', repository='evil/other')
        status, answered = self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})
        self.assertEqual(status, 409, answered)
        self.assertEqual(self.owner('GET', f"/api/approvals/{body['approval_id']}")[1]['status'], 'stale')

    def test_a_request_with_no_matching_preview_says_so_and_cannot_be_approved_into_a_push(self):
        task_id = self.make_task()
        status, body = self.agent('POST', f'/api/tasks/{task_id}/publish', {'preview_id': 'none', 'request_id': 'req-1'})
        self.assertEqual(status, 202, body)
        self.assertIn('No saved preview', body['approval']['details']['preview'])
        self.put_preview(task_id, id='none')
        self.assertEqual(self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})[0], 409)

    def test_redaction_removes_credentials_only(self):
        self.assertEqual(redact_url('https://a:b@github.com/o/r.git'), 'https://github.com/o/r.git')
        self.assertEqual(redact_url('https://github.com/o/r.git'), 'https://github.com/o/r.git')
        self.assertEqual(redact_url('git@github.com:o/r.git'), 'git@github.com:o/r.git')

    def test_target_change_rejection_in_publish_itself_is_unchanged(self):
        source = (REPO / 'acc' / 'github.py').read_text()
        self.assertIn("'push_url', 'expected_tree')):", source)
        self.assertIn('Publication target changed; inspect a new preview.', source)


class BridgeEndpoint(unittest.TestCase):
    """ACC-RR-20261013-SC8-01: the generated MCP configuration reaches the loopback listener, across restarts."""

    def start(self, state, project):
        c = Coordinator(project, state)
        reach = Reach('custom', '127.0.0.1', ['localhost:1'], secure_cookie=False)
        pairing = Pairing(state / 'pairing.json')
        public = Server(('127.0.0.1', 0), c, OWNER, reach=reach, pairing=pairing, agent_token=AGENT, role='public')
        owner = Server(('127.0.0.1', 0), c, OWNER, reach=reach, pairing=pairing, agent_token=AGENT, role='owner', shared=public)
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (public, owner)]
        for t in threads:
            t.start()
        return c, public, owner

    def stop(self, c, *servers):
        for s in servers:
            s.shutdown(); s.server_close()
        c.close()

    def bridge_call(self, process, request_id):
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                                        'params': {'name': 'acc_state', 'arguments': {}}}) + '\n')
        process.stdin.flush()
        return json.loads(process.stdout.readline())

    def test_the_generated_configuration_follows_the_loopback_listener_across_a_restart(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        project, host = root / 'project', root / 'host'
        project.mkdir()
        # setup chooses the *public* port, as a real run does; the generated fragment still has to work
        probe = Server(('127.0.0.1', 0), mock.MagicMock(), OWNER); public_port = probe.server_port; probe.server_close()
        settings = acc_setup.initialize(argparse.Namespace(yes=True, project=str(project), port=public_port), host)
        state = Path(settings['state_dir']); state.mkdir(parents=True, exist_ok=True)
        (state / 'agent-token').write_text(AGENT)
        fragment = acc_setup.read_json(host / 'hermes-mcp.json')['mcp_servers']['acc']
        self.assertIn('--endpoint-file', fragment['args'])
        self.assertEqual(fragment['args'][fragment['args'].index('--endpoint-file') + 1], str(state / 'endpoint.json'))

        c, public, owner = self.start(state, project)
        _write_endpoint(state / 'endpoint.json', owner.server_port)
        self.assertNotEqual(public.server_port, owner.server_port)
        self.assertNotIn(AGENT, (state / 'endpoint.json').read_text(), 'the endpoint file holds no credential')

        process = subprocess.Popen([fragment['command'], *fragment['args']], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.kill)
        first = self.bridge_call(process, 1)
        self.assertFalse(first['result'].get('isError'), first)
        self.assertIn('tasks', first['result']['content'][0]['text'])

        # the reviewed defect: a fixed URL on the public listener never reaches the agent
        defective = dispatch({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call', 'params': {'name': 'acc_state', 'arguments': {}}},
                             f'http://127.0.0.1:{public.server_port}', AGENT)
        self.assertTrue(defective['result'].get('isError'), 'the public listener must keep refusing the agent credential')

        # restart on different ports
        self.stop(c, public, owner)
        c, public2, owner2 = self.start(state, project)
        self.addCleanup(self.stop, c, public2, owner2)
        _write_endpoint(state / 'endpoint.json', owner2.server_port)
        again = self.bridge_call(process, 2)
        self.assertFalse(again['result'].get('isError'), again)
        process.stdin.close()

    def test_without_an_endpoint_file_the_bridge_falls_back_to_the_url_and_rejects_non_local_files(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'endpoint.json'
        self.assertEqual(resolve_endpoint(str(path), 'http://127.0.0.1:1'), 'http://127.0.0.1:1')
        for bad in ('not json', json.dumps({'url': 'http://evil.example.com'}), json.dumps({'url': 'https://127.0.0.1:2'}),
                    json.dumps({'url': 'http://u:p@127.0.0.1:2'}), json.dumps({'url': 5}), json.dumps([1])):
            with self.subTest(bad=bad):
                path.write_text(bad)
                self.assertEqual(resolve_endpoint(str(path), 'http://127.0.0.1:1'), 'http://127.0.0.1:1')
        path.write_text(json.dumps({'url': 'http://127.0.0.1:4242', 'pid': os.getpid()}))
        self.assertEqual(resolve_endpoint(str(path), 'http://127.0.0.1:1'), 'http://127.0.0.1:4242')

    def test_the_public_listener_still_refuses_the_agent_credential_and_the_endpoint_file_is_the_only_new_surface(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name); (root / 'p').mkdir()
        c, public, owner = self.start(root / 'state', root / 'p')
        self.addCleanup(self.stop, c, public, owner)
        import http.client
        for server, host, expected in ((public, f'127.0.0.1:{public.server_port}', 403), (public, f'localhost:{public.server_port}', 403),
                                       (owner, f'127.0.0.1:{owner.server_port}', 200)):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            conn.request('GET', '/api/state', headers={'Host': host, 'Authorization': 'Bearer ' + AGENT})
            status = conn.getresponse().status
            conn.close()
            self.assertIn(status, (expected, 401) if expected == 403 else (expected,), (host, status))

    def test_startup_writes_the_endpoint_file_and_removes_it_at_shutdown(self):
        from test_server_startup import StartupCase

        class Case(StartupCase):
            def runTest(self):
                pass
        case = Case(); case.setUp(); self.addCleanup(case.tmp.cleanup)
        code, out, err = case.run_main('--owner-port', '0')
        endpoint = case.state / 'endpoint.json'
        self.assertFalse(endpoint.exists(), 'removed when the server stops')
        seen = []
        code, out, err = case.run_main(serve=lambda srv: seen.append(json.loads((case.state / 'endpoint.json').read_text())))
        self.assertEqual(seen[0]['url'], f"http://127.0.0.1:{case.servers[-1].server_port}")
        self.assertNotIn(OWNER, json.dumps(seen))


class ReachCheckWording(unittest.TestCase):
    """ACC-RR-20261013-SC7-01: reach-check validates and reads; it does not promise startup succeeds."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'state.json'
        self.path.write_text(json.dumps({'fail_serve': True}))
        env = mock.patch.dict(os.environ, {'FAKE_TAILSCALE_STATE': str(self.path)}); env.start(); self.addCleanup(env.stop)
        from acc.tailscale import Tailscale
        self.ts = Tailscale([PY, FAKE])

    def test_a_denied_serve_permission_passes_the_read_only_check_but_the_report_says_what_it_did_not_test(self):
        findings, reach = check('tailscale-serve', port=8765, token='T' * 43, route=self.ts)
        self.assertIsNotNone(reach, 'a read-only check cannot see that Serve will be refused')
        text = ' '.join(f.detail for f in findings if f.setting == 'not checked')
        for phrase in ('permission to change Tailscale Serve', 'HTTPS certificate', 'binding the port', 'another tool'):
            self.assertIn(phrase, text)
        self.assertNotIn('would start', ' '.join(f.detail for f in findings))
        from acc.tailscale import TailscaleError
        with self.assertRaises(TailscaleError):
            self.ts.enable('http://127.0.0.1:8765')

    def test_the_check_never_changes_serve(self):
        calls = []
        real = self.ts.command
        self.ts.command = lambda *args: (calls.append(args), real(*args))[1]
        check('tailscale-serve', port=8765, token='T' * 43, route=self.ts)
        self.assertTrue(calls)
        self.assertTrue(all(args[:1] == ('status',) or args[:2] == ('serve', 'status') for args in calls), calls)

    def test_help_and_docs_carry_the_qualification(self):
        server = (REPO / 'acc' / 'server.py').read_text()
        helptext = ' '.join(subprocess.run([PY, '-m', 'acc.server', '--help'], capture_output=True, text=True, cwd=REPO).stdout.split())
        self.assertIn('so it cannot prove startup will succeed', helptext)
        self.assertNotIn('whether ACC would start', helptext + server)
        guide = (REPO / 'docs' / 'REMOTE-ACCESS.md').read_text()
        self.assertIn('does not prove that startup will succeed', guide)
        self.assertNotIn('whether\nACC would start', guide)
        from acc.tailscale import Tailscale
        self.assertIn('does not prove', Tailscale.plan.__doc__)


class ApiDocs(unittest.TestCase):
    """ACC-RR-20261013-OTF-01: the API reference lists every gated operation the code gates."""

    def test_api_md_lists_the_gates(self):
        api = (REPO / 'docs' / 'API.md').read_text()
        for needle in ('tasks/{id}/start', 'knowledge/checkin', 'command.start', 'knowledge.checkin', 'Approvals are bound'):
            self.assertIn(needle, api, needle)


if __name__ == '__main__':
    unittest.main()
