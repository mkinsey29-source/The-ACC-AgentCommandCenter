import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acc import pairing as pairing_module
from acc.pairing import DEVICE_TTL, MAX_DEVICES, PAIR_TTL, PENDING_TTL, Pairing, PairingError


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class PairingCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'state' / 'pairing.json'
        self.clock = Clock()
        self.p = Pairing(self.path, clock=self.clock)

    def pair(self, name='Pixel', use_code=False, p=None):
        p = p or self.p
        begun = p.begin()
        claimed = p.claim(begun['code' if use_code else 'token'], name)
        approved = p.approve(claimed['request_id'])
        polled = p.poll(claimed['request_id'], claimed['claim'])
        return begun, claimed, approved, polled


class FlowTests(PairingCase):
    def test_full_flow_with_the_link_token(self):
        begun, claimed, approved, polled = self.pair()
        self.assertEqual(polled['status'], 'approved')
        credential = polled['credential']
        self.assertEqual(self.p.authenticate(credential)['id'], approved['id'])
        self.assertEqual([d['name'] for d in self.p.devices()], ['Pixel'])

    def test_claim_with_the_eight_digit_code(self):
        _, _, _, polled = self.pair(use_code=True)
        self.assertEqual(polled['status'], 'approved')

    def test_both_sides_see_the_same_match_code_before_approval(self):
        begun = self.p.begin()
        claimed = self.p.claim(begun['token'], 'Pixel')
        self.assertEqual(len(claimed['code']), 6)
        pending = self.p.pending()
        self.assertEqual([(x['id'], x['name'], x['code']) for x in pending],
                         [(claimed['request_id'], 'Pixel', claimed['code'])])
        self.assertEqual(self.p.poll(claimed['request_id'], claimed['claim']), {'status': 'waiting'})
        self.assertIsNone(self.p.authenticate('anything'))

    def test_credential_is_handed_over_exactly_once(self):
        _, claimed, _, polled = self.pair()
        self.assertEqual(polled['status'], 'approved')
        with self.assertRaises(PairingError) as ctx:
            self.p.poll(claimed['request_id'], claimed['claim'])
        self.assertEqual(ctx.exception.status, 404)

    def test_denied_request_never_gets_a_credential(self):
        begun = self.p.begin()
        claimed = self.p.claim(begun['token'], 'Pixel')
        self.p.deny(claimed['request_id'])
        self.assertEqual(self.p.poll(claimed['request_id'], claimed['claim']), {'status': 'denied'})
        self.assertEqual(self.p.devices(), [])
        with self.assertRaises(PairingError):
            self.p.approve(claimed['request_id'])

    def test_the_wrong_claim_secret_cannot_poll(self):
        begun = self.p.begin()
        claimed = self.p.claim(begun['token'], 'Pixel')
        self.p.approve(claimed['request_id'])
        for bad in ('wrong', '', None, 5):
            with self.subTest(bad=bad), self.assertRaises(PairingError) as ctx:
                self.p.poll(claimed['request_id'], bad)
            self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(PairingError):
            self.p.poll('unknown', claimed['claim'])
        self.assertEqual(self.p.poll(claimed['request_id'], claimed['claim'])['status'], 'approved')

    def test_revoke_stops_the_device_at_once(self):
        _, _, approved, polled = self.pair()
        self.assertTrue(self.p.is_active(approved['id']))
        self.assertTrue(self.p.revoke(approved['id']))
        self.assertIsNone(self.p.authenticate(polled['credential']))
        self.assertFalse(self.p.is_active(approved['id']))
        self.assertFalse(self.p.revoke(approved['id']))


class OneTimeTests(PairingCase):
    def test_a_pairing_is_single_use(self):
        begun = self.p.begin()
        self.p.claim(begun['token'], 'one')
        with self.assertRaises(PairingError) as ctx:
            self.p.claim(begun['token'], 'two')
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(PairingError):
            self.p.claim(begun['code'], 'two')

    def test_a_wrong_secret_does_not_consume_the_pairing(self):
        begun = self.p.begin()
        for bad in ('wrong', '', None, 12345678):
            with self.subTest(bad=bad), self.assertRaises(PairingError):
                self.p.claim(bad, 'x')
        self.assertEqual(self.p.claim(begun['token'], 'ok')['code'].isdigit(), True)

    def test_a_pairing_expires_after_five_minutes(self):
        begun = self.p.begin()
        self.clock.now += PAIR_TTL + 1
        with self.assertRaises(PairingError):
            self.p.claim(begun['token'], 'late')

    def test_a_new_pairing_replaces_the_old_one(self):
        old = self.p.begin()
        new = self.p.begin()
        with self.assertRaises(PairingError):
            self.p.claim(old['token'], 'x')
        self.p.claim(new['token'], 'x')

    def test_nothing_to_claim_when_no_pairing_was_started(self):
        with self.assertRaises(PairingError):
            self.p.claim('anything', 'x')

    def test_a_waiting_request_expires(self):
        begun = self.p.begin()
        claimed = self.p.claim(begun['token'], 'Pixel')
        self.clock.now += PENDING_TTL + 1
        with self.assertRaises(PairingError) as ctx:
            self.p.approve(claimed['request_id'])
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.p.poll(claimed['request_id'], claimed['claim']), {'status': 'expired'})
        self.assertEqual(self.p.pending(), [])


class LimitTests(PairingCase):
    def test_device_cap_blocks_approval_and_claims(self):
        for i in range(MAX_DEVICES):
            self.pair(name=f'device {i}')
        begun = self.p.begin()
        with self.assertRaisesRegex(PairingError, 'old device'):
            self.p.claim(begun['token'], 'ninth')
        self.assertEqual(len(self.p.devices()), MAX_DEVICES)

    def test_approval_rechecks_the_cap(self):
        begun = self.p.begin()
        claimed = self.p.claim(begun['token'], 'late one')
        for i in range(MAX_DEVICES):
            self.pair(name=f'device {i}')
        with self.assertRaisesRegex(PairingError, 'old device'):
            self.p.approve(claimed['request_id'])

    def test_claims_are_rate_limited_and_recover(self):
        for _ in range(60):
            with self.assertRaises(PairingError) as ctx:
                self.p.claim('guess', 'x')
            self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(PairingError) as ctx:
            self.p.claim('guess', 'x')
        self.assertEqual(ctx.exception.status, 429)
        self.clock.now += 61
        with self.assertRaises(PairingError) as ctx:
            self.p.claim('guess', 'x')
        self.assertEqual(ctx.exception.status, 403)


class StorageTests(PairingCase):
    def test_no_secret_is_stored_in_the_clear(self):
        begun, claimed, _, polled = self.pair()
        raw = self.path.read_text()
        for secret in (begun['token'], begun['code'], claimed['claim'], polled['credential']):
            self.assertNotIn(secret, raw)
        self.assertEqual(set(json.loads(raw)['devices'][0]), {'id', 'name', 'hash', 'created', 'last_seen', 'expires'})

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions')
    def test_the_file_is_private(self):
        self.pair()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_devices_survive_a_restart_and_expire(self):
        _, _, approved, polled = self.pair()
        again = Pairing(self.path, clock=self.clock)
        self.assertEqual(again.authenticate(polled['credential'])['id'], approved['id'])
        self.clock.now += DEVICE_TTL + 1
        self.assertIsNone(again.authenticate(polled['credential']))
        self.assertEqual(again.devices(), [])

    def test_expired_devices_are_dropped_when_a_new_one_is_approved(self):
        self.pair(name='old')
        self.clock.now += DEVICE_TTL + 1
        self.pair(name='new')
        self.assertEqual([d['name'] for d in json.loads(self.path.read_text())['devices']], ['new'])

    def test_a_failed_write_changes_nothing_and_can_be_retried(self):
        begun = self.p.begin()
        claimed = self.p.claim(begun['token'], 'Pixel')
        with mock.patch.object(self.p, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.p.approve(claimed['request_id'])
        self.assertEqual(self.p.devices(), [])
        self.assertEqual(self.p.poll(claimed['request_id'], claimed['claim']), {'status': 'waiting'})
        self.p.approve(claimed['request_id'])
        self.assertEqual(len(self.p.devices()), 1)

    def test_a_corrupt_file_is_refused_loudly(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{not json')
        with self.assertRaises(PairingError) as ctx:
            Pairing(self.path, clock=self.clock)
        self.assertEqual(ctx.exception.status, 500)
        self.path.write_text('{"devices": "no"}')
        with self.assertRaises(PairingError):
            Pairing(self.path, clock=self.clock)

    def test_last_seen_is_recorded_and_throttled(self):
        _, _, approved, polled = self.pair()
        self.p.authenticate(polled['credential'])
        first = self.p.devices()[0]['last_seen']
        self.assertEqual(first, self.clock.now)
        self.clock.now += 10
        self.p.authenticate(polled['credential'])
        self.assertEqual(self.p.devices()[0]['last_seen'], first)
        self.clock.now += 100
        self.p.authenticate(polled['credential'])
        self.assertEqual(self.p.devices()[0]['last_seen'], self.clock.now)

    def test_a_failed_last_seen_write_does_not_lock_out_the_device(self):
        _, _, _, polled = self.pair()
        with mock.patch.object(self.p, '_save', side_effect=OSError('read-only')):
            self.assertIsNotNone(self.p.authenticate(polled['credential']))


class AuthenticateTests(PairingCase):
    def test_junk_credentials_are_refused(self):
        self.pair()
        for bad in (None, '', 'x' * 101, 5, b'bytes', 'wrong'):
            with self.subTest(bad=bad):
                self.assertIsNone(self.p.authenticate(bad))

    def test_names_are_cleaned(self):
        for raw, expected in (('Pixel 9', 'Pixel 9'), ('<b>x</b>', 'bx/b'), ('a\x00b\x1fc', 'abc'),
                              ('', 'My phone'), (None, 'My phone'), ('n' * 100, 'n' * 60)):
            with self.subTest(raw=raw):
                begun = self.p.begin()
                claimed = self.p.claim(begun['token'], raw)
                self.assertEqual(self.p.pending()[0]['name'], expected)
                self.p.deny(claimed['request_id'])


if __name__ == '__main__':
    unittest.main()
