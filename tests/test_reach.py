import unittest

from acc import reach
from acc.reach import Reach, ReachError, address_class, check, format_findings, normalize_host, resolve

TOKEN = 'x' * 43


class FakeRoute:
    def __init__(self, ready=True, message=''):
        self.status = {'ready': ready, 'message': message, 'hostname': 'box.tail1234.ts.net'}

    def probe(self):
        return self.status


class AddressTests(unittest.TestCase):
    def test_address_classes(self):
        cases = {
            '127.0.0.1': 'loopback', 'localhost': 'loopback', '::1': 'loopback', '[::1]': 'loopback',
            '192.168.1.20': 'private', '10.0.0.5': 'private', '172.16.9.1': 'private',
            '100.101.102.103': 'private', 'fd12:3456::1': 'private', '169.254.1.1': 'private',
            '0.0.0.0': 'wildcard', '::': 'wildcard',
            '8.8.8.8': 'public', '2001:4860:4860::8888': 'public', 'example.com': 'name',
        }
        for host, expected in cases.items():
            with self.subTest(host=host):
                self.assertEqual(address_class(host), expected)

    def test_host_values_are_validated_as_a_browser_sends_them(self):
        self.assertEqual(normalize_host(' Box.Tail1234.TS.net:8443 '), 'box.tail1234.ts.net:8443')
        self.assertEqual(normalize_host('192.168.1.20:8765'), '192.168.1.20:8765')
        self.assertEqual(normalize_host('[::1]:8765'), '[::1]:8765')
        for bad in ('', 'https://example.com', 'example.com/path', '*.example.com', 'a b', 'user@host',
                    'host:0', 'host:70000', None, 5):
            with self.subTest(bad=bad), self.assertRaises(ReachError):
                normalize_host(bad)


class ClassifyTests(unittest.TestCase):
    def test_local_remote_and_unknown_hosts(self):
        r = Reach('custom', '192.168.1.20', ['acc.example.com', '192.168.1.20:8765'])
        self.assertEqual(r.classify('127.0.0.1:8765', 8765), 'local')
        self.assertEqual(r.classify('LOCALHOST:8765', 8765), 'local')
        self.assertIsNone(r.classify('127.0.0.1:9999', 8765))
        self.assertEqual(r.classify('acc.example.com', 8765), 'remote')
        self.assertEqual(r.classify('192.168.1.20:8765', 8765), 'remote')
        self.assertIsNone(r.classify('evil.example.net', 8765))
        self.assertIsNone(r.classify('', 8765))

    def test_local_mode_never_accepts_a_remote_host(self):
        self.assertIsNone(Reach('local').classify('box.tail1234.ts.net:8443', 8765))

    def test_remote_hosts_can_be_replaced_when_a_route_starts(self):
        r = Reach('tailscale-serve', '127.0.0.1', secure_cookie=True)
        self.assertIsNone(r.classify('box.tail1234.ts.net:8443', 8765))
        r.set_remote_hosts(['box.tail1234.ts.net:8443'])
        self.assertEqual(r.classify('box.tail1234.ts.net:8443', 8765), 'remote')


class ResolveTests(unittest.TestCase):
    def kwargs(self, **extra):
        return {'port': 8765, 'token': TOKEN, **extra}

    def test_local_is_the_default_shape(self):
        r = resolve('local', **self.kwargs())
        self.assertEqual((r.mode, r.bind_host, r.remote, r.remote_hosts()), ('local', '127.0.0.1', False, frozenset()))

    def test_local_refuses_remote_options(self):
        for extra in ({'host': '192.168.1.20'}, {'allow_hosts': ['a.example.com']}, {'behind_https': True}):
            with self.subTest(extra=extra), self.assertRaises(ReachError):
                resolve('local', **self.kwargs(**extra))

    def test_every_remote_mode_refuses_a_missing_or_weak_token(self):
        for token in ('', None, 'short', 'x' * 31):
            for mode, extra in (('tailscale-serve', {'route': FakeRoute()}),
                                ('private', {'host': '192.168.1.20'}),
                                ('docker', {'allow_hosts': ['localhost:8765'], 'container': True}),
                                ('custom', {'host': '192.168.1.20', 'allow_hosts': ['a.example.com']})):
                with self.subTest(mode=mode, token=token), self.assertRaises(ReachError):
                    resolve(mode, port=8765, token=token, **extra)

    def test_tailscale_serve_stays_on_loopback_with_a_derived_host(self):
        r = resolve('tailscale-serve', **self.kwargs(route=FakeRoute()))
        self.assertEqual((r.bind_host, r.remote, r.secure_cookie, r.remote_hosts()), ('127.0.0.1', True, True, frozenset()))

    def test_tailscale_serve_refuses_when_tailscale_is_not_ready(self):
        with self.assertRaisesRegex(ReachError, 'sign in'):
            resolve('tailscale-serve', **self.kwargs(route=FakeRoute(False, 'Open Tailscale and sign in.')))
        with self.assertRaises(ReachError):
            resolve('tailscale-serve', **self.kwargs())

    def test_tailscale_serve_refuses_manual_hosts(self):
        with self.assertRaises(ReachError):
            resolve('tailscale-serve', **self.kwargs(route=FakeRoute(), host='192.168.1.20'))
        with self.assertRaises(ReachError):
            resolve('tailscale-serve', **self.kwargs(route=FakeRoute(), allow_hosts=['a.example.com']))

    def test_private_needs_one_explicit_private_address(self):
        r = resolve('private', **self.kwargs(host='192.168.1.20'))
        self.assertEqual((r.bind_host, r.remote_hosts()), ('192.168.1.20', frozenset({'192.168.1.20:8765'})))
        self.assertEqual(resolve('private', **self.kwargs(host='100.64.5.6')).bind_host, '100.64.5.6')
        for host in (None, '', '0.0.0.0', '::', '8.8.8.8', 'example.com', '127.0.0.1'):
            with self.subTest(host=host), self.assertRaises(ReachError):
                resolve('private', **self.kwargs(host=host))

    def test_docker_only_inside_a_container_and_with_a_published_host(self):
        r = resolve('docker', **self.kwargs(allow_hosts=['localhost:8765'], container=True))
        self.assertEqual((r.bind_host, r.remote_hosts()), ('0.0.0.0', frozenset({'localhost:8765'})))
        with self.assertRaisesRegex(ReachError, 'inside a container'):
            resolve('docker', **self.kwargs(allow_hosts=['localhost:8765'], container=False))
        with self.assertRaisesRegex(ReachError, 'allow-host'):
            resolve('docker', **self.kwargs(container=True))
        with self.assertRaises(ReachError):
            resolve('docker', **self.kwargs(allow_hosts=['localhost:8765'], container=True, host='8.8.8.8'))

    def test_custom_needs_a_host_and_accepted_hosts(self):
        with self.assertRaises(ReachError):
            resolve('custom', **self.kwargs(allow_hosts=['a.example.com']))
        with self.assertRaises(ReachError):
            resolve('custom', **self.kwargs(host='192.168.1.20'))
        r = resolve('custom', **self.kwargs(host='192.168.1.20', allow_hosts=['acc.example.com'], behind_https=True))
        self.assertEqual((r.bind_host, r.secure_cookie), ('192.168.1.20', True))

    def test_custom_refuses_a_public_address_without_acknowledgement(self):
        for host in ('8.8.8.8', 'example.com'):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ReachError, 'internet'):
                    resolve('custom', **self.kwargs(host=host, allow_hosts=['acc.example.com']))
                self.assertEqual(resolve('custom', **self.kwargs(
                    host=host, allow_hosts=['acc.example.com'], public_ack=True)).bind_host, host)

    def test_public_ack_is_only_for_custom(self):
        with self.assertRaises(ReachError):
            resolve('private', **self.kwargs(host='192.168.1.20', public_ack=True))

    def test_unknown_mode_and_too_many_hosts(self):
        with self.assertRaises(ReachError):
            resolve('lan', **self.kwargs())
        with self.assertRaises(ReachError):
            resolve('custom', **self.kwargs(host='192.168.1.20', allow_hosts=[f'h{i}.example.com' for i in range(9)]))


class ValidateTests(unittest.TestCase):
    def test_local_must_bind_loopback(self):
        Reach('local').validate('t', '127.0.0.1', False)
        Reach('local').validate('t', 'localhost', False)
        for bad in ('0.0.0.0', '192.168.1.20', '8.8.8.8'):
            with self.subTest(bad=bad), self.assertRaises(ReachError):
                Reach('local').validate('t', bad, False)

    def test_remote_modes_need_token_and_pairing(self):
        r = Reach('private', '192.168.1.20', ['192.168.1.20:8765'])
        r.validate(TOKEN, '192.168.1.20', True)
        with self.assertRaisesRegex(ReachError, 'token'):
            r.validate('short', '192.168.1.20', True)
        with self.assertRaisesRegex(ReachError, 'pairing'):
            r.validate(TOKEN, '192.168.1.20', False)

    def test_serve_mode_refuses_a_non_loopback_bind(self):
        with self.assertRaises(ReachError):
            Reach('tailscale-serve', '127.0.0.1').validate(TOKEN, '192.168.1.20', True)

    def test_wildcard_bind_needs_accepted_hosts_outside_docker(self):
        with self.assertRaises(ReachError):
            Reach('custom', '0.0.0.0').validate(TOKEN, '0.0.0.0', True)
        Reach('custom', '0.0.0.0', ['acc.example.com']).validate(TOKEN, '0.0.0.0', True)


class CheckTests(unittest.TestCase):
    def test_local_report(self):
        findings, r = check('local', port=8765, token=None)
        self.assertIsNotNone(r)
        self.assertEqual([f.status for f in findings], ['ok', 'ok', 'ok'])
        self.assertIn('remote access', format_findings(findings))

    def test_refusal_is_a_failed_finding_with_the_reason(self):
        findings, r = check('private', port=8765, token=TOKEN, host='0.0.0.0')
        self.assertIsNone(r)
        self.assertEqual(findings[-1].status, 'fail')
        self.assertIn('private address', findings[-1].detail)
        self.assertIn('FAIL', format_findings(findings))

    def test_remote_report_lists_token_pairing_hosts_and_cookie(self):
        findings, r = check('private', port=8765, token=TOKEN, host='192.168.1.20')
        names = {f.setting: f for f in findings}
        self.assertEqual(set(names), {'mode', 'bind address', 'control token', 'device pairing', 'accepted hosts', 'cookie'})
        self.assertEqual(names['cookie'].status, 'warn')
        self.assertIn('192.168.1.20:8765', names['accepted hosts'].detail)

    def test_serve_report_marks_cookie_secure_and_hosts_pending(self):
        findings, r = check('tailscale-serve', port=8765, token=TOKEN, route=FakeRoute())
        names = {f.setting: f for f in findings}
        self.assertEqual(names['cookie'].status, 'ok')
        self.assertIn('Tailscale', names['accepted hosts'].detail)

    def test_docker_detection_can_be_forced(self):
        findings, r = check('docker', port=8765, token=TOKEN, allow_hosts=['localhost:8765'], container=True)
        self.assertIsNotNone(r)
        self.assertEqual(r.bind_host, '0.0.0.0')


if __name__ == '__main__':
    unittest.main()
