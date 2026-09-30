"""Repository boundary for M08 authentication/account state.

The in-memory implementation is deterministic test/reference storage. M09 may provide a durable
database adapter without changing the auth service contract.
"""
from __future__ import annotations

from typing import Protocol

from .models import (
    AccountState,
    EntitlementSnapshot,
    Membership,
    SessionRecord,
    UserState,
    VerifiedIdentity,
    _bounded_id,
)


class AuthRepository(Protocol):
    def resolve_identity(self, identity: VerifiedIdentity) -> str | None: ...
    def user(self, user_id: str) -> UserState | None: ...
    def account(self, account_id: str) -> AccountState | None: ...
    def membership(self, account_id: str, user_id: str) -> Membership | None: ...
    def entitlements(self, account_id: str) -> EntitlementSnapshot | None: ...
    def session(self, token_digest: str) -> SessionRecord | None: ...
    def save_session(self, token_digest: str, session: SessionRecord) -> None: ...
    def revoke_session(self, token_digest: str) -> bool: ...


class InMemoryAuthRepository:
    """Reference repository used by tests and local contract exercises.

    Session keys are token digests. Plaintext bearer tokens are never persisted here.
    Connector/provider credentials are outside this repository by design.
    """

    def __init__(self) -> None:
        self._identities: dict[tuple[str, str], str] = {}
        self._users: dict[str, UserState] = {}
        self._accounts: dict[str, AccountState] = {}
        self._memberships: dict[tuple[str, str], Membership] = {}
        self._entitlements: dict[str, EntitlementSnapshot] = {}
        self._sessions: dict[str, SessionRecord] = {}

    def put_user(self, user: UserState) -> None:
        self._users[user.user_id] = user

    def bind_identity(self, identity: VerifiedIdentity, user_id: str) -> None:
        user_id = _bounded_id(user_id, 'user_id')
        if user_id not in self._users:
            raise ValueError('ACC user must exist before an identity can be bound.')
        key = (identity.provider, identity.subject)
        current = self._identities.get(key)
        if current is not None and current != user_id:
            raise ValueError('Identity is already bound to another ACC user.')
        self._identities[key] = user_id

    def put_account(self, account: AccountState) -> None:
        self._accounts[account.account_id] = account

    def put_membership(self, membership: Membership) -> None:
        self._memberships[(membership.account_id, membership.user_id)] = membership

    def put_entitlements(self, entitlements: EntitlementSnapshot) -> None:
        self._entitlements[entitlements.account_id] = entitlements

    def remove_membership(self, account_id: str, user_id: str) -> bool:
        key = (_bounded_id(account_id, 'account_id'), _bounded_id(user_id, 'user_id'))
        return self._memberships.pop(key, None) is not None

    def resolve_identity(self, identity: VerifiedIdentity) -> str | None:
        return self._identities.get((identity.provider, identity.subject))

    def user(self, user_id: str) -> UserState | None:
        return self._users.get(user_id)

    def account(self, account_id: str) -> AccountState | None:
        return self._accounts.get(account_id)

    def membership(self, account_id: str, user_id: str) -> Membership | None:
        return self._memberships.get((account_id, user_id))

    def entitlements(self, account_id: str) -> EntitlementSnapshot | None:
        return self._entitlements.get(account_id)

    def session(self, token_digest: str) -> SessionRecord | None:
        return self._sessions.get(token_digest)

    def save_session(self, token_digest: str, session: SessionRecord) -> None:
        self._sessions[token_digest] = session

    def revoke_session(self, token_digest: str) -> bool:
        session = self._sessions.get(token_digest)
        if session is None or session.revoked:
            return False
        self._sessions[token_digest] = SessionRecord(
            session_id=session.session_id,
            account_id=session.account_id,
            user_id=session.user_id,
            issued_at=session.issued_at,
            expires_at=session.expires_at,
            identity_provider=session.identity_provider,
            revoked=True,
        )
        return True

    def session_digests(self) -> tuple[str, ...]:
        """Inspection helper for tests; values are irreversible SHA-256 digests."""
        return tuple(self._sessions)
