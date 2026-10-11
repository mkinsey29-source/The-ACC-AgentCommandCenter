"""The owner token is memory-only; a one-use launch code is how the owner's browser receives it."""
import contextlib
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from acc import bridge
from acc.core import Coordinator
from acc.launch import ATTEMPTS_PER_MINUTE, LAUNCH_TTL, MAX_OUTSTANDING, LaunchCodes, LaunchError
from acc.pairing import Pairing
from acc.reach import Reach
from acc.server import Server

OWNER, AGENT, REMOTE = 'O' * 43, 'A' * 43, 'box.tail1234.ts.net:8443'


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


class LaunchCodeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.codes = LaunchCodes(OWNER, clock=self.clock)

    def test_a_code_redeems_for_the_token_once(self):
        code = self.codes.mint()['code']
        self.assertEqual(self.codes.redeem(code), OWNER)
        with self.assertRaises(LaunchError) as ctx:
            self.codes.redeem(code)
        self.assertEqual(ctx.exception.status, 403)

    def test_wrong_and_junk_codes_are_refused_and_do_not_consume_a_good_one(self):
        code = self.codes.mint()['code']
        for bad in ('nope', '', None, 5, 'x' * 500, [code]):
            with self.subTest(bad=bad), self.assertRaises(LaunchError):
                self.codes.redeem(bad)
        self.assertEqual(self.codes.redeem(code), OWNER)

    def test_a_code_expires(self):
        code = self.codes.mint()['code']
        self.clock.now += LAUNCH_TTL + 1
        with self.assertRaises(LaunchError):
            self.codes.redeem(code)

    def test_only_the_newest_codes_stay_valid(self):
        minted = [self.codes.mint()['code'] for _ in range(MAX_OUTSTANDING + 1)]
        with self.assertRaises(LaunchError):
            self.codes.redeem(minted[0])
        for code in minted[1:]:
            self.assertEqual(self.codes.redeem(code), OWNER)

    def test_guessing_is_rate_limited_and_recovers(self):
        for _ in range(ATTEMPTS_PER_MINUTE):
            with self.assertRaises(LaunchError) as ctx:
                self.codes.redeem('guess')
            self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(LaunchError) as ctx:
            self.codes.redeem('guess')
        self.assertEqual(ctx.exception.status, 429)
        self.clock.now += 61
        self.assertEqual(self.codes.redeem(self.codes.mint()['code']), OWNER)

    def test_codes_are_not_kept_in_the_clear(self):
        code = self.codes.mint()['code']
        self.assertNotIn(code, json.dumps(self.codes._codes))


class HttpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Coordinator(self.root, self.root / 'state')
        self.reach = Reach('tailscale-serve', '127.0.0.1', [REMOTE], secure_cookie=True)
        self.server = Server(('127.0.0.1', 0), self.c, OWNER, reach=self.reach,
                             pairing=Pairing(self.root / 'state' / 'pairing.json'), agent_token=AGENT)
        self.port = self.server.server_port
        self.local = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.c.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def call(self, method, path, token=None, host=None, body=None, origin=None, raw=None):
        headers = {'Host': host or self.local}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if origin:
            headers['Origin'] = origin
        data = raw
        if body is not None:
            data = json.dumps(body)
        if data is not None:
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        try:
            return response.status, json.loads(payload)
        except ValueError:
            return response.status, payload


class LaunchHttpTests(HttpCase):
    def test_a_code_gives_the_browser_a_working_token_once(self):
        code = self.server.launch.mint()['code']
        status, body = self.call('POST', '/api/launch', body={'code': code})
        self.assertEqual((status, body), (200, {'token': OWNER}))
        self.assertEqual(self.call('GET', '/api/state', token=body['token'])[0], 200)
        self.assertEqual(self.call('POST', '/api/launch', body={'code': code})[0], 403)

    def test_the_exchange_is_for_local_hosts_only_and_a_refusal_keeps_the_code(self):
        code = self.server.launch.mint()['code']
        self.assertEqual(self.call('POST', '/api/launch', host=REMOTE, body={'code': code})[0], 403)
        self.assertEqual(self.call('POST', '/api/launch', host='evil.example.net', body={'code': code})[0], 403)
        self.assertEqual(self.call('POST', '/api/launch', body={'code': code}, origin='https://untrusted.example')[0], 403)
        self.assertEqual(self.call('POST', '/api/launch', body={'code': code})[0], 200)

    def test_bad_requests_get_no_token(self):
        for body in ({}, {'code': ''}, {'code': 5}, {'code': 'nope'}):
            with self.subTest(body=body):
                status, reply = self.call('POST', '/api/launch', body=body)
                self.assertEqual(status, 403)
                self.assertNotIn('token', reply)
        self.assertEqual(self.call('POST', '/api/launch', raw='not json')[0], 400)

    def test_only_the_owner_can_mint_a_new_window_link(self):
        self.assertEqual(self.call('POST', '/api/launch/new', body={})[0], 403)
        self.assertEqual(self.call('POST', '/api/launch/new', token=AGENT, body={})[0], 403)
        self.assertEqual(self.call('POST', '/api/launch/new', token=OWNER, host=REMOTE, body={})[0], 403)
        status, made = self.call('POST', '/api/launch/new', token=OWNER, body={})
        self.assertEqual(status, 200)
        self.assertTrue(made['url'].startswith(f'http://127.0.0.1:{self.port}/#launch='))
        code = made['url'].split('#launch=')[1]
        self.assertEqual(self.call('POST', '/api/launch', body={'code': code})[1], {'token': OWNER})

    def test_the_owner_token_never_appears_in_a_response_other_than_the_exchange(self):
        for path in ('/api/state', '/api/remote'):
            self.assertNotIn(OWNER, json.dumps(self.call('GET', path, token=OWNER)[1]))
        made = self.call('POST', '/api/launch/new', token=OWNER, body={})[1]
        self.assertNotIn(OWNER, json.dumps(made))


class BridgeMessageTests(unittest.TestCase):
    def test_a_missing_token_file_explains_what_to_use(self):
        err = io.StringIO()
        with mock.patch.object(sys, 'argv', ['acc.bridge', '--token-file', '/nonexistent/dir/token']), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            bridge.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('agent-token', err.getvalue())
        self.assertIn('no longer saved', err.getvalue())


if __name__ == '__main__':
    unittest.main()
