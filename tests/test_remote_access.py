import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from acc.core import Coordinator
from acc.pairing import Pairing
from acc.reach import Reach, ReachError
from acc.server import Handler, Server

TOKEN = 'T' * 43
REMOTE = 'box.tail1234.ts.net:8443'


class Fixture(unittest.TestCase):
    secure = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Coordinator(self.root, self.root / 'state')
        self.pairing = Pairing(self.root / 'state' / 'pairing.json')
        self.reach = Reach('tailscale-serve', '127.0.0.1', [REMOTE], secure_cookie=self.secure)
        self.server = Server(('127.0.0.1', 0), self.c, TOKEN, reach=self.reach, pairing=self.pairing)
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

    def call(self, method, path, host=None, token=None, cookie=None, body=None, origin=None):
        headers = {'Host': host or self.local}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if cookie:
            headers['Cookie'] = cookie
        if origin:
            headers['Origin'] = origin
        raw = None
        if body is not None:
            raw = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        return response.status, response, parsed

    def owner(self, method, path, body=None):
        return self.call(method, path, token=TOKEN, body=body)

    def pair_device(self, name='Pixel'):
        _, _, begun = self.owner('POST', '/api/pairing/begin', {})
        status, _, claimed = self.call('POST', '/api/pairing/claim', host=REMOTE,
                                       body={'secret': begun['token'], 'name': name})
        self.assertEqual(status, 200, claimed)
        status, _, approved = self.owner('POST', '/api/pairing/approve', {'request_id': claimed['request_id']})
        self.assertEqual(status, 200, approved)
        status, response, polled = self.call('POST', '/api/pairing/poll', host=REMOTE,
                                             body={'request_id': claimed['request_id'], 'claim': claimed['claim']})
        self.assertEqual((status, polled), (200, {'status': 'approved'}))
        cookie = response.getheader('Set-Cookie')
        return approved, cookie, cookie.split(';')[0]


class HostClassTests(Fixture):
    def test_the_shell_is_served_to_both_hosts_without_a_login(self):
        for host in (self.local, REMOTE):
            with self.subTest(host=host):
                status, _, body = self.call('GET', '/', host=host)
                self.assertEqual(status, 200)
                self.assertIn(b'ACC', body)

    def test_an_unknown_host_is_refused_everywhere(self):
        for host in ('evil.example.net', f'127.0.0.1:{self.port + 1}', 'box.tail1234.ts.net:9999'):
            with self.subTest(host=host):
                self.assertEqual(self.call('GET', '/', host=host)[0], 403)
                self.assertEqual(self.call('GET', '/api/state', host=host, token=TOKEN)[0], 403)

    def test_a_missing_host_header_is_refused(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.putrequest('GET', '/api/state', skip_host=True)
        conn.putheader('Authorization', 'Bearer ' + TOKEN)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_the_control_token_works_on_local_hosts_only(self):
        self.assertEqual(self.call('GET', '/api/state', token=TOKEN)[0], 200)
        status, _, body = self.call('GET', '/api/state', host=REMOTE, token=TOKEN)
        self.assertEqual(status, 401)
        self.assertIn('not paired', body['error'])
        self.assertEqual(self.call('POST', '/api/project/mode', host=REMOTE, token=TOKEN, body={'mode': 'online'})[0], 403)

    def test_a_local_request_without_the_token_is_unauthorized(self):
        self.assertEqual(self.call('GET', '/api/state')[0], 401)

    def test_foreign_origins_are_refused_on_both_hosts(self):
        self.assertEqual(self.call('GET', '/api/state', token=TOKEN, origin='https://untrusted.example')[0], 403)
        self.assertEqual(self.call('GET', '/', host=REMOTE, origin='https://untrusted.example')[0], 403)
        self.assertEqual(self.call('GET', '/', host=REMOTE, origin='https://' + REMOTE)[0], 200)
        self.assertEqual(self.call('GET', '/api/state', token=TOKEN, origin=f'http://localhost:{self.port}')[0], 200)

    def test_a_loopback_name_from_a_non_loopback_client_is_not_local(self):
        stub = mock.Mock(spec=Handler)
        stub.headers = {'Host': self.local}
        stub.server = self.server
        stub.client_address = ('192.168.1.50', 40000)
        self.assertIsNone(Handler.host_class(stub))
        stub.client_address = ('127.0.0.1', 40000)
        self.assertEqual(Handler.host_class(stub), 'local')


class PairingOverHttpTests(Fixture):
    def test_full_pairing_then_device_access(self):
        approved, cookie, pair = self.pair_device()
        self.assertTrue(cookie.startswith('acc_device='))
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/', 'Secure', 'Max-Age='):
            self.assertIn(flag, cookie)
        status, _, state = self.call('GET', '/api/state', host=REMOTE, cookie=pair)
        self.assertEqual(status, 200)
        self.assertIn('tasks', state)

    def test_the_credential_is_never_in_a_response_body(self):
        _, _, begun = self.owner('POST', '/api/pairing/begin', {})
        _, _, claimed = self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': begun['token']})
        self.owner('POST', '/api/pairing/approve', {'request_id': claimed['request_id']})
        _, response, polled = self.call('POST', '/api/pairing/poll', host=REMOTE,
                                        body={'request_id': claimed['request_id'], 'claim': claimed['claim']})
        credential = response.getheader('Set-Cookie').split(';')[0].split('=', 1)[1]
        self.assertNotIn(credential, json.dumps(polled))
        status, _, again = self.call('POST', '/api/pairing/poll', host=REMOTE,
                                     body={'request_id': claimed['request_id'], 'claim': claimed['claim']})
        self.assertEqual(status, 404)

    def test_begin_returns_a_link_on_the_remote_origin(self):
        status, _, begun = self.owner('POST', '/api/pairing/begin', {})
        self.assertEqual(status, 200)
        self.assertEqual(begun['origin'], 'https://' + REMOTE)
        self.assertEqual(begun['url'], f"https://{REMOTE}/#pair={begun['token']}")
        self.assertEqual(len(begun['code']), 8)

    def test_the_owner_sees_the_match_code_and_the_device_after_approval(self):
        _, _, begun = self.owner('POST', '/api/pairing/begin', {})
        _, _, claimed = self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': begun['code'], 'name': 'Pixel'})
        _, _, remote = self.owner('GET', '/api/remote')
        self.assertEqual([(p['name'], p['code']) for p in remote['pending']], [('Pixel', claimed['code'])])
        self.owner('POST', '/api/pairing/approve', {'request_id': claimed['request_id']})
        _, _, remote = self.owner('GET', '/api/remote')
        self.assertEqual((remote['pending'], [d['name'] for d in remote['devices']]), ([], ['Pixel']))
        self.assertEqual((remote['mode'], remote['enabled'], remote['origin']), ('tailscale-serve', True, 'https://' + REMOTE))

    def test_claim_and_poll_are_for_remote_hosts_only(self):
        _, _, begun = self.owner('POST', '/api/pairing/begin', {})
        self.assertEqual(self.call('POST', '/api/pairing/claim', body={'secret': begun['token']})[0], 403)
        self.assertEqual(self.call('POST', '/api/pairing/claim', host='evil.example.net', body={'secret': begun['token']})[0], 403)

    def test_a_wrong_secret_is_refused_and_a_used_one_is_gone(self):
        _, _, begun = self.owner('POST', '/api/pairing/begin', {})
        self.assertEqual(self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': 'nope'})[0], 403)
        self.assertEqual(self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': begun['token']})[0], 200)
        self.assertEqual(self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': begun['token']})[0], 403)

    def test_denied_request_gets_no_cookie(self):
        _, _, begun = self.owner('POST', '/api/pairing/begin', {})
        _, _, claimed = self.call('POST', '/api/pairing/claim', host=REMOTE, body={'secret': begun['token']})
        self.assertEqual(self.owner('POST', '/api/pairing/deny', {'request_id': claimed['request_id']})[0], 200)
        status, response, polled = self.call('POST', '/api/pairing/poll', host=REMOTE,
                                             body={'request_id': claimed['request_id'], 'claim': claimed['claim']})
        self.assertEqual((status, polled), (200, {'status': 'denied'}))
        self.assertIsNone(response.getheader('Set-Cookie'))

    def test_a_paired_device_cannot_manage_pairing_or_pair_others(self):
        _, _, pair = self.pair_device()
        for method, path, body in (('POST', '/api/pairing/begin', {}), ('POST', '/api/pairing/approve', {'request_id': 'x'}),
                                   ('POST', '/api/pairing/deny', {'request_id': 'x'}), ('POST', '/api/pairing/revoke', {'device_id': 'x'}),
                                   ('GET', '/api/remote', None)):
            with self.subTest(path=path):
                self.assertEqual(self.call(method, path, host=REMOTE, cookie=pair, body=body)[0], 403)

    def test_a_device_can_use_the_api_but_its_cookie_means_nothing_on_a_local_host(self):
        _, _, pair = self.pair_device()
        status, _, _ = self.call('POST', '/api/project/mode', host=REMOTE, cookie=pair, body={'mode': 'offline'})
        self.assertEqual(status, 200)
        self.assertEqual(self.owner('GET', '/api/state')[2]['project_mode'], 'offline')
        self.assertEqual(self.call('GET', '/api/state', cookie=pair)[0], 401)
        self.assertEqual(self.call('GET', '/api/state', host=REMOTE, cookie='acc_device=forged')[0], 401)

    def test_revoking_a_device_stops_it_and_ends_its_event_stream(self):
        approved, _, pair = self.pair_device()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=8)
        conn.request('GET', '/api/events?after=0', headers={'Host': REMOTE, 'Cookie': pair})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.fp.readline()  # the stream is live
        self.assertEqual(self.owner('POST', '/api/pairing/revoke', {'device_id': approved['id']})[2], {'revoked': True})
        deadline, ended = time.time() + 6, False
        while time.time() < deadline:
            try:
                if response.fp.read(1) == b'':
                    ended = True
                    break
            except (OSError, http.client.HTTPException):
                ended = True
                break
        conn.close()
        self.assertTrue(ended, 'the revoked device kept its stream')
        self.assertEqual(self.call('GET', '/api/state', host=REMOTE, cookie=pair)[0], 401)

    def test_the_owner_panel_data_is_available_to_the_owner_only(self):
        self.assertEqual(self.call('GET', '/api/remote')[0], 401)
        self.assertEqual(self.call('GET', '/api/remote', host=REMOTE, token=TOKEN)[0], 401)
        self.assertEqual(self.owner('GET', '/api/remote')[0], 200)


class PlainHttpCookieTests(Fixture):
    secure = False

    def test_the_cookie_is_not_marked_secure_without_an_https_front_door(self):
        _, cookie, _ = self.pair_device()
        self.assertNotIn('Secure', cookie)
        self.assertIn('HttpOnly', cookie)


class LocalOnlyServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Coordinator(self.root, self.root / 'state')
        self.server = Server(('127.0.0.1', 0), self.c, 'test-token')
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.c.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, host, body=None, token='test-token'):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': host, 'Authorization': 'Bearer ' + token}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b'null')
        conn.close()
        return response.status, data

    def test_local_behaviour_is_unchanged(self):
        self.assertEqual(self.request('GET', '/api/state', f'127.0.0.1:{self.port}')[0], 200)
        self.assertEqual(self.request('GET', '/api/state', f'localhost:{self.port}')[0], 200)
        self.assertEqual(self.request('GET', '/api/state', f'127.0.0.1:{self.port}', token='wrong')[0], 401)

    def test_no_remote_host_is_ever_accepted_in_local_mode(self):
        self.assertEqual(self.request('GET', '/api/state', REMOTE)[0], 403)

    def test_remote_endpoints_report_off_and_refuse_pairing(self):
        status, data = self.request('GET', '/api/remote', f'127.0.0.1:{self.port}')
        self.assertEqual((status, data['enabled'], data['devices'], data['pending']), (200, False, [], []))
        self.assertEqual(self.request('POST', '/api/pairing/begin', f'127.0.0.1:{self.port}', {})[0], 404)
        self.assertEqual(self.request('POST', '/api/pairing/claim', REMOTE, {'secret': 'x'})[0], 403)


class UnsafeConstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = Coordinator(Path(self.tmp.name), Path(self.tmp.name) / 'state')
        self.addCleanup(self.c.close)
        self.pairing = Pairing(Path(self.tmp.name) / 'state' / 'pairing.json')

    def test_a_non_loopback_bind_in_local_mode_is_refused(self):
        with self.assertRaises(ReachError):
            Server(('0.0.0.0', 0), self.c, 'test-token')

    def test_a_remote_mode_without_pairing_is_refused(self):
        with self.assertRaisesRegex(ReachError, 'pairing'):
            Server(('127.0.0.1', 0), self.c, TOKEN, reach=Reach('tailscale-serve', '127.0.0.1', [REMOTE]))

    def test_a_remote_mode_with_a_weak_token_is_refused(self):
        with self.assertRaisesRegex(ReachError, 'token'):
            Server(('127.0.0.1', 0), self.c, 'short', reach=Reach('tailscale-serve', '127.0.0.1', [REMOTE]),
                   pairing=self.pairing)

    def test_a_wildcard_bind_without_accepted_hosts_is_refused(self):
        with self.assertRaises(ReachError):
            Server(('0.0.0.0', 0), self.c, TOKEN, reach=Reach('custom', '0.0.0.0'), pairing=self.pairing)


if __name__ == '__main__':
    unittest.main()
