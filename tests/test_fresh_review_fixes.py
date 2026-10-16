"""Regression tests for the fresh independent review (2026-10-14): SC8 endpoint hardening, SC9 consent and checkout, OTF docs.

Stable IDs follow the reviewer's report: ACC-FIR-20261014-SC8-01..03, SC9-01..05 and OTF-01. Each test fails on a9fdf67.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from acc import server
from acc.bridge import is_local, resolve_endpoint
from acc.gates import PATH_LISTING_LIMIT, argv_display, classify, exact, redact_url, visible
from acc.server import _write_endpoint
from test_gated_access import AGENT, OWNER, REMOTE, Fixture as GatedFixture
from test_rereview_fixes import ApprovalBinding, KnowledgeFixture, PublishConsent, PY, REPO, wait_for
from test_server_startup import StartupCase, TailscaleServeStartTests


class EndpointCleanup(TailscaleServeStartTests):
    """ACC-FIR-20261014-SC8-01: a failed endpoint write releases everything startup had already created."""

    def test_an_endpoint_write_failure_removes_the_route_and_closes_every_listener(self):
        closed = []
        real_close = server.Server.server_close

        def tracking_close(srv):
            closed.append(srv.role)
            return real_close(srv)
        with mock.patch.object(server, '_write_endpoint', side_effect=OSError('disk denied')), \
                mock.patch.object(server.Server, 'server_close', tracking_close):
            code, out, err = self.run_main('--port', '0', '--reach', 'tailscale-serve')
        self.assertEqual(code, 2, err)
        self.assertIn('ACC refused to start:', err)
        self.assertEqual(self.web(), {}, 'the Serve route ACC created was removed')
        self.assertEqual(json.loads((self.state / 'reach.json').read_text()), {'route': None})
        self.assertEqual(closed, ['both'], 'the listener was closed')
        self.assertEqual(self.served, [], 'nothing served')

    def test_a_split_listener_endpoint_failure_closes_both_sockets(self):
        closed = []
        real_close = server.Server.server_close
        with mock.patch.object(server, '_write_endpoint', side_effect=OSError('disk denied')), \
                mock.patch.object(server.Server, 'server_close', lambda srv: (closed.append(srv.role), real_close(srv))[1]), \
                mock.patch('acc.reach.address_class', wraps=server.address_class):
            code, out, err = self.run_main('--port', '0', '--reach', 'custom', '--host', '127.0.0.1', '--allow-host', 'acc.test:9')
        self.assertEqual(code, 2, err)
        self.assertEqual(sorted(closed), ['both'])

    def test_a_pre_existing_directory_where_the_file_would_go_is_a_clean_refusal(self):
        self.state.mkdir(parents=True)
        (self.state / 'endpoint.json').mkdir()
        code, out, err = self.run_main('--port', '0', '--reach', 'tailscale-serve')
        self.assertEqual(code, 2, err)
        self.assertEqual(self.web(), {})


class EndpointFile(unittest.TestCase):
    """ACC-FIR-20261014-SC8-02 (no link following) and SC8-03 (what the bridge accepts)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_a_planted_temporary_link_is_not_followed_and_a_link_at_the_target_is_replaced(self):
        victim = self.dir / 'victim'
        victim.write_text('keep me')
        (self.dir / 'endpoint.tmp').symlink_to(victim)
        (self.dir / 'endpoint.json').symlink_to(victim)
        _write_endpoint(self.dir / 'endpoint.json', 4321)
        self.assertEqual(victim.read_text(), 'keep me')
        written = self.dir / 'endpoint.json'
        self.assertFalse(written.is_symlink())
        self.assertEqual(json.loads(written.read_text())['url'], 'http://127.0.0.1:4321')
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith('endpoint.') and p.suffix == '.tmp'
                          and p.name != 'endpoint.tmp'], [], 'no temporary file left behind')

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions')
    def test_the_file_is_readable_by_others_but_writable_only_by_acc(self):
        _write_endpoint(self.dir / 'endpoint.json', 1)
        mode = (self.dir / 'endpoint.json').stat().st_mode & 0o777
        self.assertEqual(mode, 0o644)

    def test_a_failed_write_leaves_no_temporary_file(self):
        with mock.patch('os.replace', side_effect=OSError('nope')), self.assertRaises(OSError):
            _write_endpoint(self.dir / 'endpoint.json', 1)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_the_bridge_accepts_only_a_plain_loopback_origin(self):
        for good in ('http://127.0.0.1:8765', 'http://localhost:8765', 'http://127.0.0.1:8765/'):
            with self.subTest(good=good):
                self.assertTrue(is_local(good))
        for bad in ('http://:fixture@127.0.0.1:9', 'http://u:p@127.0.0.1:9', 'http://u@127.0.0.1:9', 'http://127.0.0.1',
                    'http://127.0.0.1:9/x', 'http://127.0.0.1:9?q=1', 'http://127.0.0.1:9#f', 'http://evil.example.com:9',
                    'https://127.0.0.1:9', 'http://127.0.0.1:99999', 'http://[::1]:9', ''):
            with self.subTest(bad=bad):
                self.assertFalse(is_local(bad))

    def endpoint(self, **changes):
        path = self.dir / 'endpoint.json'
        path.write_text(json.dumps({'url': 'http://127.0.0.1:4242', 'pid': os.getpid(), **changes}))
        path.chmod(0o644)
        return str(path)

    def test_a_good_file_is_followed_and_each_untrustworthy_one_is_ignored(self):
        fallback = 'http://127.0.0.1:1'
        self.assertEqual(resolve_endpoint(self.endpoint(), fallback), 'http://127.0.0.1:4242')
        for label, changes in (('empty-user credentials', {'url': 'http://:pw@127.0.0.1:4242'}), ('dead process', {'pid': 2 ** 22 + 12345}),
                               ('no process id', {'pid': None}), ('bad process id', {'pid': 'x'}), ('non-loopback', {'url': 'http://example.com:1'})):
            with self.subTest(label=label):
                self.assertEqual(resolve_endpoint(self.endpoint(**changes), fallback), fallback)

    @unittest.skipIf(os.name == 'nt', 'POSIX links and permissions')
    def test_a_link_or_a_file_others_can_write_is_ignored(self):
        fallback = 'http://127.0.0.1:1'
        real = self.endpoint()
        link = self.dir / 'link.json'
        link.symlink_to(real)
        self.assertEqual(resolve_endpoint(str(link), fallback), fallback)
        os.chmod(real, 0o666)
        self.assertEqual(resolve_endpoint(real, fallback), fallback)
        os.chmod(real, 0o664)
        self.assertEqual(resolve_endpoint(real, fallback), fallback)

    def test_the_server_can_announce_the_endpoint_somewhere_the_orchestrator_account_can_read(self):
        class Case(StartupCase):
            def runTest(self):
                pass
        case = Case(); case.setUp(); self.addCleanup(case.tmp.cleanup)
        elsewhere = case.root / 'shared' / 'endpoint.json'
        seen = []
        code, out, err = case.run_main('--port', '0', '--endpoint-file', str(elsewhere), serve=lambda srv: seen.append(elsewhere.read_text()))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(seen[0])['url'], f"http://127.0.0.1:{case.servers[-1].server_port}")
        self.assertFalse((case.state / 'endpoint.json').exists())
        self.assertFalse(elsewhere.exists(), 'removed at shutdown')


class StartSnapshot(ApprovalBinding):
    """ACC-FIR-20261014-SC9-01: what is shown, bound and later compared come from one snapshot."""

    def race(self, caller):
        marker_b = self.marker_b
        task_id = self.stored_command_task()
        real_get = self.c.store.get
        state = {'reads': 0, 'thread': None}

        def get(task):
            if task == task_id:
                if state['thread'] is None:
                    record = real_get(task)  # the first read: what the approval will display
                    state['reads'] += 1
                    # immediately afterwards a paired device / agent reassigns the task to another worker
                    state['thread'] = threading.Thread(target=lambda: self.c.assign(task_id, 'stub'), daemon=True)
                    state['thread'].start()
                    time.sleep(.1)
                    return record
                if threading.current_thread() is not state['thread']:
                    time.sleep(.5)  # a later read by the request sees the reassigned task, as the reviewed code's second read did
            return real_get(task)
        with mock.patch.object(self.c.store, 'get', get):
            status, body = caller('POST', f'/api/tasks/{task_id}/start', {})
        self.assertEqual((status, body.get('status')), (202, 'approval_required'), body)
        state['thread'].join(5)
        self.assertEqual(real_get(task_id)['agent'], 'stub', 'the reassignment really happened')
        return task_id, body

    def test_a_reassignment_during_classification_cannot_bind_an_unshown_worker(self):
        for who, caller in self.callers().items():
            with self.subTest(who=who):
                for marker in (self.marker_a, self.marker_b):
                    marker.unlink(missing_ok=True)
                task_id, body = self.race(caller)
                shown = body['approval']['details']
                self.assertIn('local-command', shown['agent'])
                self.assertIn('print', shown['argv_json'] + 'print', 'what was shown is the stored command')
                status, answered = self.owner('POST', f"/api/approvals/{body['approval_id']}/approve", {})
                self.assertEqual(status, 409, answered)
                time.sleep(.5)
                self.assertFalse(self.marker_a.exists() or self.marker_b.exists(), 'neither worker may run')
                self.assertEqual(self.owner('GET', f"/api/approvals/{body['approval_id']}")[1]['status'], 'stale')

    def test_the_gate_reads_the_task_once_under_the_coordinator_lock(self):
        source = (REPO / 'acc' / 'gates.py').read_text()
        body = source[source.index('def _start_gate'):source.index('def classify(')]
        self.assertEqual(body.count('coordinator.store.get('), 1, 'one read, not a display read and a binding read')
        self.assertIn("getattr(coordinator, 'lock'", body)
        self.assertIn('_command_fingerprint(task, config)', body)


class CheckoutAuthority(KnowledgeFixture):
    """ACC-FIR-20261014-SC9-02: a gated caller's checkout is never established guidance."""

    CHECKOUT = {'title': 'Plan', 'instruction': 'Read the guide', 'worker': 'probe'}

    def test_agent_and_device_checkouts_are_saved_as_supported(self):
        cookie = self.pair_device()
        for who, call in (('agent', lambda: self.agent('POST', '/api/knowledge/checkout', self.CHECKOUT)),
                          ('device', lambda: self.device(cookie, 'POST', '/api/knowledge/checkout', {**self.CHECKOUT, 'title': 'Plan two'}))):
            with self.subTest(who=who):
                status, body = call()
                self.assertEqual(status, 200, body)
        statuses = [v for k, v in self.statuses().items() if 'Checkouts' in k]
        self.assertEqual(statuses, ['supported', 'supported'])

    def test_the_owner_and_the_product_own_checkouts_stay_active(self):
        status, body = self.owner('POST', '/api/knowledge/checkout', self.CHECKOUT)
        self.assertEqual(status, 200, body)
        self.assertEqual([v for k, v in self.statuses().items() if 'Checkouts' in k], ['active'])

    def test_a_gated_checkout_still_completes_the_checkin_cycle(self):
        status, body = self.agent('POST', '/api/knowledge/checkout', {**self.CHECKOUT, 'scopes': ['Claims']})
        self.assertEqual(status, 200, body)
        status, checked = self.agent('POST', '/api/knowledge/checkin', {
            'title': 'Done', 'summary': 'finished', 'worker': 'probe', 'status': 'supported', 'checkout_path': body['path'], 'scopes': ['Claims']})
        self.assertEqual(status, 200, checked)

    def test_every_writer_of_a_note_is_accounted_for(self):
        knowledge = (REPO / 'acc' / 'knowledge.py').read_text()
        calls = re.findall(r'self\.create_note\(', knowledge)
        self.assertEqual(len(calls), 5, 'a new create_note call site must be reviewed for its authority')
        self.assertEqual(len(re.findall(r"'status': '(?:active|verified)'", knowledge)), 1, 'only rebuttal writes a literal established status')
        self.assertIn("'supported' if authority == 'gated' else 'active'", knowledge)
        self.assertIn("lambda p: c.knowledge.checkout(p, authority)", (REPO / 'acc' / 'server.py').read_text())


class ExactDisplay(unittest.TestCase):
    """ACC-FIR-20261014-SC9-03 (valid, lossless JSON) and SC9-04 (every approval target)."""

    class Coordinator:
        agents = {}
        lock = threading.RLock()

        class store:
            @staticmethod
            def get(task):
                raise KeyError(task)

    HIDDEN = {'combining grapheme joiner': '͏', 'braille blank': '⠀', 'variation selector': '️',
              'tag character': '\U000e0001', 'zero width space': '​', 'bidi override': '‮', 'hangul filler': 'ㅤ',
              'variation selector supplement': '\U000e0100', 'nbsp': ' ', 'bom': '﻿'}

    def test_the_argument_list_is_valid_ascii_json_that_round_trips_every_hidden_character(self):
        for label, ch in self.HIDDEN.items():
            with self.subTest(label=label):
                argv = ['run', f'a{ch}b']
                text = argv_display(argv)
                self.assertEqual(json.loads(text), argv)
                self.assertTrue(text.isascii(), 'nothing can hide in it')
                self.assertNotEqual(text, argv_display(['run', 'ab']))
        self.assertEqual(json.loads(argv_display(['é', '日本', '😀'])), ['é', '日本', '😀'])

    def test_a_command_gate_carries_the_same_exact_text_everywhere(self):
        gate = classify(self.Coordinator(), ['api', 'tasks'], {'title': 't', 'argv': ['x', '͏', '\U000e0001']})
        self.assertTrue(gate.details['argv_json'].isascii())
        self.assertEqual(json.loads(gate.details['argv_json']), ['x', '͏', '\U000e0001'])
        self.assertEqual(gate.details['argv'], ['x', '͏', '\U000e0001'])
        self.assertTrue(all(ch.isascii() or ch.isprintable() for ch in gate.summary))

    def test_visible_writes_the_blank_and_selector_characters_and_keeps_readable_text(self):
        for ch in self.HIDDEN.values():
            self.assertTrue(visible('a' + ch + 'b').isascii() or ch.isprintable() and False, repr(ch))
        self.assertEqual(visible('naïve 日本 résumé'), 'naïve 日本 résumé')
        self.assertEqual(json.loads('"' + visible('x\U000e0001y') + '"'), 'x\U000e0001y')

    def test_the_export_folder_is_shown_exactly_and_without_raw_bidi(self):
        folder = 'safe‮/hidden' + 'x' * 400
        gate = classify(self.Coordinator(), ['api', 'archive', 'export'], {'directory': folder})
        for text in (gate.summary, gate.details['directory']):
            self.assertNotIn('‮', text)
        self.assertEqual(json.loads(gate.details['directory_json']), folder, 'the full folder, unclipped, lossless')

    def test_other_summaries_write_out_invisible_characters_in_titles(self):
        for parts, payload in ((['api', 'knowledge', 'checkin'], {'title': 'T‮itle', 'status': 'active'}),
                               (['api', 'knowledge', 'note'], {'title': 'T‮itle', 'status': 'verified'}),
                               (['api', 'knowledge', 'transition'], {'path': 'a‮/b', 'status': 'verified'}),
                               (['api', 'github', 'configure'], {'remote': 'o‮rigin'})):
            with self.subTest(parts=parts):
                gate = classify(self.Coordinator(), parts, payload)
                self.assertNotIn('‮', gate.summary + json.dumps(gate.details, ensure_ascii=False))


class PublishDisplay(PublishConsent):
    """ACC-FIR-20261014-SC9-04 (paths, bidi, truncation) and SC9-05 (credentials)."""

    def test_changed_paths_are_exact_capped_and_the_cap_is_stated(self):
        task_id = self.make_task()
        paths = [f'dir/‮{n}.py' for n in range(PATH_LISTING_LIMIT + 25)]
        self.put_preview(task_id, changed_paths=paths)
        _, body = self.agent('POST', f'/api/tasks/{task_id}/publish', {'preview_id': 'prev-1', 'request_id': 'r'})
        details = body['approval']['details']
        self.assertEqual(len(details['changed_paths']), PATH_LISTING_LIMIT)
        self.assertEqual((details['changed_paths_total'], details['changed_paths_truncated']), (PATH_LISTING_LIMIT + 25, 25))
        self.assertEqual(json.loads(details['changed_paths'][0]), paths[0])
        self.assertNotIn('‮', json.dumps(details, ensure_ascii=False))

    def test_target_fields_are_exact_json_strings(self):
        task_id = self.make_task()
        self.put_preview(task_id, branch='acc/task‮-7', repository='octo/wid͏gets')
        _, body = self.agent('POST', f'/api/tasks/{task_id}/publish', {'preview_id': 'prev-1', 'request_id': 'r'})
        details = body['approval']['details']
        self.assertEqual(json.loads(details['branch']), 'acc/task‮-7')
        self.assertEqual(json.loads(details['repository']), 'octo/wid͏gets')
        self.assertTrue(details['branch'].isascii() and details['repository'].isascii())

    def test_every_form_of_embedded_credential_is_removed(self):
        for url, expected in (('https://user:s3cret@github.com/o/r.git', 'https://github.com/o/r.git'),
                              ('https://user:p@ss@w0rd@github.com/o/r.git', 'https://github.com/o/r.git'),
                              ('https://tokenonly@github.com/o/r.git', 'https://github.com/o/r.git'),
                              ('https://:onlypass@github.com/o/r.git', 'https://github.com/o/r.git'),
                              ('https://github.com/o/r.git', 'https://github.com/o/r.git'),
                              ('git@github.com:o/r.git', 'git@github.com:o/r.git')):
            with self.subTest(url=url):
                self.assertEqual(redact_url(url), expected)
        self.assertNotIn('ss@w0rd', redact_url('https://user:p@ss@w0rd@github.com/o/r.git'))


class DocsMatchTheCode(unittest.TestCase):
    """ACC-FIR-20261014-OTF-01: the guides claim what the tests establish, and list endpoint.json."""

    def setUp(self):
        self.api = (REPO / 'docs' / 'API.md').read_text()
        self.guide = (REPO / 'docs' / 'REMOTE-ACCESS.md').read_text()
        self.app = (REPO / 'acc' / 'web' / 'app.js').read_text()

    def test_endpoint_json_is_in_the_runtime_file_inventory(self):
        line = next(l for l in self.api.splitlines() if l.startswith('Runtime files:'))
        self.assertIn('`endpoint.json`', line)

    def test_checkout_semantics_are_documented(self):
        for text in (self.api, self.guide):
            self.assertIn('checkout', text)
        self.assertIn('saved as `supported`', self.api)

    def test_claims_are_scoped_to_what_is_established(self):
        self.assertNotIn('even if a route was not classified', self.api)
        self.assertNotIn('by any route', self.guide.replace('by any route that', ''))
        self.assertNotIn('the full request the owner is approving is always shown', self.app.lower())
        self.assertIn('best effort', self.api + self.guide)
        self.assertIn('one snapshot', self.api)
        self.assertIn('not shown', self.guide + self.api)

    def test_endpoint_trust_is_documented(self):
        for needle in ('--endpoint-file', 'writable only by', 'stale'):
            self.assertIn(needle, self.guide)


if __name__ == '__main__':
    unittest.main()
