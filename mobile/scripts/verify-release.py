"""Verify the same APK used in the Android smoke test before personal test delivery."""
import hashlib
import json
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from apk_signers import require_same_signers

scratch = Path(os.environ['RUNNER_TEMP'])
apk = Path('mobile/builds/ACC-Android-build-9.apk')
old = (scratch / 'build3-signature.txt').read_text()
new = (scratch / 'build9-signature.txt').read_text()
certificates = require_same_signers(old, new)
assert 'Verified using v2 scheme (APK Signature Scheme v2): true' in new
badging = (scratch / 'build9-badging.txt').read_text()
assert "name='com.agentcommandcenter.acc' versionCode='9' versionName='0.9.0'" in badging
assert "sdkVersion:'24'" in badging and "targetSdkVersion:'36'" in badging
manifest = (scratch / 'build9-manifest.txt').read_text()
assert re.search(r'android:allowBackup[^\n]*0x0\b', manifest)
assert re.search(r'expo\.modules\.updates\.ENABLED[^\n]*\n[^\n]*android:value[^\n]*0x0\b', manifest)
metadata = {'source_sha': os.environ.get('ACC_APK_SOURCE_SHA', os.environ['GITHUB_SHA']), 'verification_sha': os.environ['GITHUB_SHA'], 'bytes': apk.stat().st_size, 'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'same_signer_as_build3': True, 'versionCode': 9, 'versionName': '0.9.0'}
metadata['certificate_sha256'] = sorted(certificates)
metadata['packaging_verified'] = True
reports = [p for p in Path('mobile').rglob('TEST-*.xml') if 'test-results/testDebugUnitTest' in p.as_posix()]
assert reports, 'Native unit test reports missing'
metadata['native_unit_tests'] = {key: sum(int(ET.parse(p).getroot().get(key, '0')) for p in reports) for key in ['tests', 'failures', 'errors', 'skipped']}
assert metadata['native_unit_tests']['tests'] >= 36
assert all(metadata['native_unit_tests'][key] == 0 for key in ['failures', 'errors', 'skipped'])
with zipfile.ZipFile(apk) as z:
    metadata['abis'] = sorted({p.split('/')[1] for p in z.namelist() if p.startswith('lib/') and p.endswith('.so')})
    assert metadata['abis'] == ['arm64-v8a', 'x86_64']
    libs = [p for p in z.namelist() if p.startswith('lib/') and p.endswith('.so')]
    for p in libs:
        data = z.read(p)
        assert data[:5] == b'\x7fELF\x02'
        offset = struct.unpack_from('<Q', data, 32)[0]
        size, count = struct.unpack_from('<HH', data, 54)
        for i in range(count):
            header = struct.unpack_from('<IIQQQQQQ', data, offset + i * size)
            assert header[0] != 1 or header[-1] >= 16384, p
    metadata['native_libraries'] = len(libs)
    bundle = z.read('assets/index.android.bundle')
    metadata['hermes_version'] = struct.unpack_from('<I', bundle, 8)[0]
    for marker in [b'GitHub access token', b'Open Claude Code in browser', b'Attach to next ChatGPT message']:
        assert marker in bundle, 'Build9 JavaScript feature marker missing'
    metadata['build9_feature_markers'] = True
    assert metadata['hermes_version'] == 99
    assert any(b'AccChatGPT' in z.read(p) for p in z.namelist() if p.endswith('.dex'))
Path('mobile/builds/build9-verification.json').write_text(json.dumps(metadata, indent=2) + '\n')
Path('mobile/builds/build9-release-notes.md').write_text(
    'Personal Android test APK. Adds official Claude Code browser setup plus GitHub repository/branch browsing, pinned UTF-8 file previews and explicit file attachments to the next ChatGPT message. Claude runs on its official browser page, not inside ACC; its sign-in and GitHub authorization stay with Claude.\n\n'
    'Install over the existing app; do not uninstall. Existing ChatGPT sign-in, Sentry and task data stay on device. Public GitHub repositories need no sign-in. Private access uses a fine-grained selected-repository token entered only in Connections, encrypted on Android. Request Contents: Read-only and an expiry. ACC cannot push, merge or run autonomous repository tools. Files are sent only when deliberately attached and Send is pressed; sent content remains in local chat history.\n\n'
    'Build8 direct ChatGPT replies were manually accepted. New GitHub private-account access and Claude cloud task completion remain owner acceptance checks. Native smoke uses only public fixtures, no account credentials.\n\n'
    f'Source: {metadata["source_sha"]}\n\nSHA-256: {metadata["sha256"]}\n\nBytes: {metadata["bytes"]}\n\n'
    'ARM64 tablet/phone plus x86_64 emulator, min24/target36. Same signer as Build3; OTA and backup disabled. No production/store publication or commercial subscription approval. Never share credentials or callback URLs in chat.\n')
print(json.dumps(metadata, indent=2))
