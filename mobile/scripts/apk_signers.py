"""Read certificate identities from legacy and SDK-range apksigner output."""
import re


def signer_fingerprints(output: str) -> frozenset[str]:
    values = re.findall(
        r'^(?:V[1-4](?:\.[0-9]+)? )?Signer[^\r\n]* certificate SHA-256 digest:[ \t]*([0-9a-f]{64})[ \t]*$',
        output, re.IGNORECASE | re.MULTILINE,
    )
    if not values:
        raise ValueError('No complete signer certificate SHA-256 fingerprint in verifier output')
    return frozenset(value.lower() for value in values)


def require_same_signers(old: str, new: str) -> frozenset[str]:
    expected = signer_fingerprints(old)
    if signer_fingerprints(new) != expected:
        raise ValueError('APK signing certificates differ from Build 3')
    return expected
