"""M08 hosted session authentication, permission, and entitlement checks."""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections.abc import Callable, Iterable

from ..contracts import capability_list, capability_name
from ..state_authority import policy_for
from .identity import IdentityVerificationError, IdentityVerifier, verifier_map
from .models import AuthContext, SessionRecord, VerifiedIdentity
from .repository import AuthRepository


class AuthError(ValueError):
    """Base class for safe-to-surface hosted auth failures."""


class AuthenticationError(AuthError):
    """The caller has no usable ACC session/verified identity."""


class AuthorizationError(AuthError):
    """The caller is authenticated but lacks current account access."""


def _token_digest(token: str) -> str:
    if not isinstance(token, str) or not token.startswith('accs_') or len(token) > 512:
        raise AuthenticationError('Invalid ACC session token.')
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class AuthService:
    """Platform-side account session authority.

    The service accepts only a VerifiedIdentity produced by a provider-specific verifier. It does
    not ingest, store, or replicate provider credentials. Session bearer tokens are random opaque
    values; the repository receives only a SHA-256 digest.

    Permissions and entitlements are read from the repository on every authorization check rather
    than copied into the bearer token. Account/entitlement changes therefore take effect without
    waiting for token expiry, consistent with M02's platform-authoritative account state.
    """

    def __init__(
        self,
        repository: AuthRepository,
        *,
        clock: Callable[[], float] = time.time,
        default_ttl_seconds: int = 3600,
        max_ttl_seconds: int = 86400,
        identity_verifiers: tuple[IdentityVerifier, ...] = (),
    ) -> None:
        credential_policy = policy_for('credential')
        account_policy = policy_for('account')
        if credential_policy != {'kind': 'credential', 'authority': 'holder', 'sync': 'never'}:
            raise RuntimeError('M08 requires holder-authoritative, never-replicated credentials.')
        if account_policy['authority'] != 'platform' or account_policy['sync'] != 'read_replica':
            raise RuntimeError('M08 requires platform-authoritative account state.')
        if type(max_ttl_seconds) is not int or not 60 <= max_ttl_seconds <= 604800:
            raise ValueError('max_ttl_seconds must be between 60 seconds and 7 days.')
        if type(default_ttl_seconds) is not int or not 60 <= default_ttl_seconds <= max_ttl_seconds:
            raise ValueError('default_ttl_seconds is outside the supported range.')
        self.repository = repository
        self.clock = clock
        self.default_ttl_seconds = default_ttl_seconds
        self.max_ttl_seconds = max_ttl_seconds
        self.identity_verifiers = verifier_map(identity_verifiers)

    def exchange_identity(
        self,
        provider: str,
        assertion: dict,
        account_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        verifier = self.identity_verifiers.get(provider)
        if verifier is None:
            raise AuthenticationError('Identity provider is not configured.')
        try:
            identity = verifier.verify(assertion)
        except IdentityVerificationError as exc:
            raise AuthenticationError('Identity assertion could not be verified.') from exc
        if not isinstance(identity, VerifiedIdentity) or identity.provider != provider:
            raise AuthenticationError('Identity verifier returned an invalid provider assertion.')
        return self.create_session(identity, account_id, ttl_seconds=ttl_seconds)

    def create_session(
        self,
        identity: VerifiedIdentity,
        account_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        user_id = self.repository.resolve_identity(identity)
        if user_id is None:
            raise AuthenticationError('Identity is not linked to an ACC user.')
        user = self.repository.user(user_id)
        if user is None or user.status != 'active':
            raise AuthorizationError('ACC user is not active.')
        account = self.repository.account(account_id)
        if account is None:
            raise AuthorizationError('ACC account is unavailable.')
        if account.status != 'active':
            raise AuthorizationError('ACC account is not active.')
        membership = self.repository.membership(account_id, user_id)
        if membership is None:
            raise AuthorizationError('User is not a member of this ACC account.')
        entitlements = self.repository.entitlements(account_id)
        if entitlements is None:
            raise AuthorizationError('ACC account has no entitlement snapshot.')

        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if type(ttl) is not int or not 60 <= ttl <= self.max_ttl_seconds:
            raise AuthenticationError('Requested session lifetime is outside the allowed range.')

        now = int(self.clock())
        token = 'accs_' + secrets.token_urlsafe(32)
        record = SessionRecord(
            session_id=uuid.uuid4().hex,
            account_id=account.account_id,
            user_id=user_id,
            issued_at=now,
            expires_at=now + ttl,
            identity_provider=identity.provider,
        )
        self.repository.save_session(_token_digest(token), record)
        return token

    def authenticate(self, token: str, *, account_id: str | None = None) -> AuthContext:
        record = self.repository.session(_token_digest(token))
        if record is None or record.revoked:
            raise AuthenticationError('ACC session is not active.')
        if int(self.clock()) >= record.expires_at:
            raise AuthenticationError('ACC session has expired.')

        if account_id is not None and record.account_id != account_id:
            raise AuthorizationError('ACC session is not valid for this account.')

        user = self.repository.user(record.user_id)
        if user is None or user.status != 'active':
            raise AuthorizationError('ACC user is not active.')
        account = self.repository.account(record.account_id)
        if account is None or account.status != 'active':
            raise AuthorizationError('ACC account is not active.')
        membership = self.repository.membership(record.account_id, record.user_id)
        if membership is None:
            raise AuthorizationError('ACC account membership is no longer active.')
        entitlements = self.repository.entitlements(record.account_id)
        if entitlements is None:
            raise AuthorizationError('ACC account has no entitlement snapshot.')
        return AuthContext(
            session=record,
            account=account,
            membership=membership,
            entitlements=entitlements,
        )

    def authorize(
        self,
        token: str,
        *,
        permissions: Iterable[str] = (),
        entitlements: Iterable[str] = (),
        account_id: str | None = None,
    ) -> AuthContext:
        context = self.authenticate(token, account_id=account_id)
        required_permissions = capability_list(
            tuple(permissions), label='required permissions', limit=200)
        required_entitlements = capability_list(
            tuple(entitlements), label='required entitlements', limit=200)

        granted = frozenset(context.membership.permissions)
        missing_permissions = [name for name in required_permissions if name not in granted]
        if missing_permissions:
            raise AuthorizationError('Permission denied: ' + ', '.join(missing_permissions) + '.')

        enabled = frozenset(context.entitlements.features)
        missing_entitlements = [name for name in required_entitlements if name not in enabled]
        if missing_entitlements:
            raise AuthorizationError('Entitlement required: ' + ', '.join(missing_entitlements) + '.')
        return context

    def require_limit(self, token: str, name: str, requested: int) -> AuthContext:
        if type(requested) is not int or requested < 0:
            raise ValueError('requested must be a nonnegative integer.')
        context = self.authenticate(token)
        limit_name = capability_name(name, 'entitlement limit')
        allowed = context.entitlements.limit(limit_name)
        if allowed is None:
            raise AuthorizationError('Entitlement limit is not configured: ' + limit_name + '.')
        if requested > allowed:
            raise AuthorizationError(
                f'Entitlement limit exceeded: {limit_name} allows {allowed}, requested {requested}.')
        return context

    def revoke(self, token: str) -> bool:
        return self.repository.revoke_session(_token_digest(token))
