"""Certificate identity must survive apksigner's SDK-range display format."""
import importlib.util
import re
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'acc_mobile_apk_signers', Path(__file__).resolve().parents[1] / 'mobile/scripts/apk_signers.py',
)
signers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signers)


class ApkSignerFormatTests(unittest.TestCase):
    fingerprint = '0123456789abcdef' * 4

    def line(self, name, value=None):
        return f'{name} certificate SHA-256 digest: {value or self.fingerprint}\n'

    def test_sdk_range_reproduces_legacy_parser_failure(self):
        output = self.line('Signer (minSdkVersion=24, maxSdkVersion=2147483647)')
        self.assertIsNone(re.search(r'Signer #1 certificate SHA-256 digest: ([a-f0-9]+)', output))
        self.assertEqual(signers.signer_fingerprints(output), {self.fingerprint})

    def test_legacy_and_sdk_range_are_same_identity(self):
        self.assertEqual(signers.require_same_signers(
            self.line('Signer #1'), self.line('Signer (minSdkVersion=24, maxSdkVersion=2147483647)'),
        ), {self.fingerprint})

    def test_dev_range_uppercase_and_duplicates(self):
        output = self.line('Signer (minSdkVersion=36 (dev release=true), maxSdkVersion=2147483647)', self.fingerprint.upper())
        self.assertEqual(signers.signer_fingerprints(output + output), {self.fingerprint})

    def test_changed_or_additional_signer_is_refused(self):
        old = self.line('Signer #1')
        for new in [self.line('Signer #1', 'a' * 64), old + self.line('Signer #2', 'a' * 64)]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                signers.require_same_signers(old, new)

    def test_missing_short_and_public_key_only_are_refused(self):
        for output in ['', self.line('Signer #1', 'abc'), f'Signer #1 public key SHA-256 digest: {self.fingerprint}\n']:
            with self.subTest(output=output), self.assertRaises(ValueError):
                signers.signer_fingerprints(output)

    def test_source_stamp_is_not_an_apk_signer(self):
        output = self.line('Source Stamp Signer', 'b' * 64) + self.line('Signer #1')
        self.assertEqual(signers.signer_fingerprints(output), {self.fingerprint})

    def test_actual_build_tools_scheme_label(self):
        self.assertEqual(signers.require_same_signers(
            self.line('Signer #1'), self.line('V2 Signer:'),
        ), {self.fingerprint})

    def test_scheme_label_changed_signer_is_refused(self):
        with self.assertRaises(ValueError):
            signers.require_same_signers(self.line('V2 Signer:'), self.line('V2 Signer:', 'a' * 64))
