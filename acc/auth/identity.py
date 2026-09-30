"""Identity-provider verification boundary for hosted ACC login."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from ..contracts import provider_id
from .models import VerifiedIdentity


@runtime_checkable
class IdentityVerifier(Protocol):
    """Verifies one external identity assertion before ACC creates a session.

    Implementations own provider-specific token/callback verification. They return only a stable
    verified identity; provider access/refresh credentials remain with the verifier/provider flow
    and are never copied into ACC session records.
    """

    id: str

    def verify(self, assertion: Mapping[str, Any]) -> VerifiedIdentity: ...


def verifier_map(verifiers: list[IdentityVerifier] | tuple[IdentityVerifier, ...]) -> dict[str, IdentityVerifier]:
    result: dict[str, IdentityVerifier] = {}
    for verifier in verifiers:
        identity_provider = provider_id(getattr(verifier, 'id', None), 'identity verifier id')
        if identity_provider in result:
            raise ValueError('Duplicate identity verifier: ' + identity_provider)
        result[identity_provider] = verifier
    return result
