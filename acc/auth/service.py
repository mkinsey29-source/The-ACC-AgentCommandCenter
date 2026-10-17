"""M08 hosted session authentication, permission, and entitlement checks."""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections.abc import Callable, Iterable, Mapping

from ..contracts import capability_list, capability_name
from ..state_authority import policy_for
from .identity import IdentityVerificationError, IdentityVerifier, verifier_map
from .models import AuthContext, SessionRecord, VerifiedIdentity
from .repository import AuthRepository, AuthSnapshot


class AuthError(Exception):
    """Base class for safe-to-surface hosted auth failures.

    Deliberately not a ``ValueError``: M09 must map auth failures explicitly (401/403) and must not
    let a generic input-validation handler (400) swallow them, or vice versa.
    """


class AuthenticationError(AuthError):
    """No usable principal: missing/invalid/revoked/expired session, unverified identity, or an
    inactive user. M09 maps this to HTTP 401."""


class AuthorizationError(AuthError):
    """A valid principal lacks access to the requested account, permission, entitlement, or
    quota. M09 maps this to HTTP 403."""


_SESSION_INACTIVE = 'ACC session is not active.'
_ACCOUNT_UNAVAILABLE = 'ACC account is not available to this user.'


def _token_digest(token: str) -> str:
    if not isinstance(token, str) or not token.startswith('accs_') or len(token) > 512:
        raise AuthenticationError(_SESSION_INACTIVE)
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
        assertion: Mapping,
        account_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        """The only public login path: verify a provider assertion, then issue a session."""
        verifier = self.identity_verifiers.get(provider) if isinstance(provider, str) else None
        if verifier is None:
            raise AuthenticationError('Identity provider is not configured.')
        if not isinstance(assertion, Mapping):
            raise AuthenticationError('Identity assertion could not be verified.')
        try:
            identity = verifier.verify(assertion)
        except Exception as exc:  # Fail closed; never surface provider detail (may hold secrets).
            raise AuthenticationError('Identity assertion could not be verified.') from exc
        # Exact type: a subclass could carry provider credentials or extra claims into ACC.
        if type(identity) is not VerifiedIdentity or identity.provider != provider:
            raise AuthenticationError('Identity verifier returned an invalid provider assertion.')
        return self._issue_session(identity, account_id, ttl_seconds=ttl_seconds)

    def _issue_session(
        self,
        identity: VerifiedIdentity,
        account_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        """Issue a session for an identity a configured verifier has already verified.

        Private on purpose: there is no public API that turns a caller-constructed
        ``VerifiedIdentity`` into a session. Hosted code must log in through ``exchange_identity``.
        """
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if type(ttl) is not int or not 60 <= ttl <= self.max_ttl_seconds:
            raise ValueError('Requested session lifetime is outside the allowed range.')
        user_id = self.repository.resolve_identity(identity)
        if user_id is None:
            raise AuthenticationError('Identity is not linked to an ACC user.')
        self._active_user(user_id)
        # Every account-side refusal uses one message so a verified identity cannot probe which
        # account IDs exist, are suspended, or have members.
        try:
            account, membership, _ = self._account_access(account_id, user_id)
        except AuthorizationError:
            raise AuthorizationError(_ACCOUNT_UNAVAILABLE) from None

        now = int(self.clock())
        token = 'accs_' + secrets.token_urlsafe(32)
        record = SessionRecord(
            session_id=uuid.uuid4().hex,
            account_id=account.account_id,
            user_id=membership.user_id,
            issued_at=now,
            expires_at=now + ttl,
            identity_provider=identity.provider,
        )
        self.repository.save_session(_token_digest(token), record)
        return token

    def _active_user(self, user_id: str):
        user = self.repository.user(user_id)
        if user is None or user.user_id != user_id or user.status != 'active':
            # An inactive user is no longer a usable principal: 401, not 403.
            raise AuthenticationError('ACC user is not active.')
        return user

    def _account_access(self, account_id: str, user_id: str):
        """Load current account-side state, rejecting records keyed to another tenant/user."""
        account = self.repository.account(account_id)
        if account is None or account.account_id != account_id or account.status != 'active':
            raise AuthorizationError('ACC account is not active.')
        membership = self.repository.membership(account_id, user_id)
        if (membership is None or membership.account_id != account_id
                or membership.user_id != user_id):
            raise AuthorizationError('ACC account membership is no longer active.')
        entitlements = self.repository.entitlements(account_id)
        if entitlements is None or entitlements.account_id != account_id:
            raise AuthorizationError('ACC account has no entitlement snapshot.')
        return account, membership, entitlements

    def authenticate(self, token: str, *, account_id: str | None = None) -> AuthContext:
        """Resolve a session to its current principal and account state.

        With ``account_id=None`` this is identity-level only ("who am I"). Every account-scoped
        operation must pass the route's tenant ID, or use ``authorize``/``require_limit``, which
        require it.
        """
        snapshot = self.repository.auth_snapshot(_token_digest(token))
        # Unknown, revoked and expired sessions are deliberately indistinguishable.
        if snapshot is None:
            raise AuthenticationError(_SESSION_INACTIVE)
        if type(snapshot) is not AuthSnapshot:
            raise AuthenticationError(_SESSION_INACTIVE)
        record = snapshot.session
        if record.revoked or int(self.clock()) >= record.expires_at:
            raise AuthenticationError(_SESSION_INACTIVE)
        if account_id is not None and record.account_id != account_id:
            raise AuthorizationError('ACC session is not valid for this account.')
        user = snapshot.user
        if user is None or user.user_id != record.user_id or user.status != 'active':
            raise AuthenticationError('ACC user is not active.')
        account = snapshot.account
        if (account is None or account.account_id != record.account_id
                or account.status != 'active'):
            raise AuthorizationError('ACC account is not active.')
        membership = snapshot.membership
        if (membership is None or membership.account_id != record.account_id
                or membership.user_id != record.user_id):
            raise AuthorizationError('ACC account membership is no longer active.')
        entitlements = snapshot.entitlements
        if entitlements is None or entitlements.account_id != record.account_id:
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
        account_id: str,
        permissions: Iterable[str] = (),
        entitlements: Iterable[str] = (),
    ) -> AuthContext:
        """Account-scoped check. ``account_id`` is required so a token for one tenant can never be
        used for another tenant's route (confused deputy)."""
        required_permissions = capability_list(
            tuple(permissions), label='required permissions', limit=200)
        required_entitlements = capability_list(
            tuple(entitlements), label='required entitlements', limit=200)
        context = self.authenticate(token, account_id=account_id)

        granted = frozenset(context.membership.permissions)
        missing_permissions = [name for name in required_permissions if name not in granted]
        if missing_permissions:
            raise AuthorizationError('Permission denied: ' + ', '.join(missing_permissions) + '.')

        enabled = frozenset(context.entitlements.features)
        missing_entitlements = [name for name in required_entitlements if name not in enabled]
        if missing_entitlements:
            raise AuthorizationError('Entitlement required: ' + ', '.join(missing_entitlements) + '.')
        return context

    def require_limit(self, token: str, name: str, requested: int, *, account_id: str) -> AuthContext:
        """Check a prospective *total* against the account's current quota ceiling.

        This is a check, not a reservation: callers that consume quota must re-check and record
        usage atomically in their own transaction (M09/M20), or concurrent requests can overshoot.
        """
        if type(requested) is not int or requested < 0:
            raise ValueError('requested must be a nonnegative integer.')
        limit_name = capability_name(name, 'entitlement limit')
        context = self.authenticate(token, account_id=account_id)
        allowed = context.entitlements.limit(limit_name)
        if allowed is None:
            raise AuthorizationError('Entitlement limit is not configured: ' + limit_name + '.')
        if requested > allowed:
            raise AuthorizationError(
                f'Entitlement limit exceeded: {limit_name} allows {allowed}, requested {requested}.')
        return context

    def revoke(self, token: str) -> bool:
        return self.repository.revoke_session(_token_digest(token))
