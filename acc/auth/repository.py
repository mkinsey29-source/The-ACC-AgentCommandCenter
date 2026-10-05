"""Repository boundary for M08 authentication/account state.

The in-memory implementation is deterministic test/reference storage. M09 may provide a durable
database adapter without changing the auth service contract.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
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


@dataclass(frozen=True)
class AuthSnapshot:
    """One consistent view of a session and the current authorization records."""
    session: SessionRecord
    user: UserState | None
    account: AccountState | None
    membership: Membership | None
    entitlements: EntitlementSnapshot | None


class AuthRepository(Protocol):
    def resolve_identity(self, identity: VerifiedIdentity) -> str | None: ...
    def user(self, user_id: str) -> UserState | None: ...
    def account(self, account_id: str) -> AccountState | None: ...
    def membership(self, account_id: str, user_id: str) -> Membership | None: ...
    def entitlements(self, account_id: str) -> EntitlementSnapshot | None: ...
    def session(self, token_digest: str) -> SessionRecord | None: ...
    def auth_snapshot(self, token_digest: str) -> AuthSnapshot | None:
        """Read session and current auth state from one consistent database snapshot."""
        ...
    def save_session(self, token_digest: str, session: SessionRecord) -> None:
        """Insert-only: must refuse to overwrite an existing digest."""
    def revoke_session(self, token_digest: str) -> bool:
        """Atomically mark one session revoked; return whether it was active before."""


class InMemoryAuthRepository:
    """Reference repository used by tests and local contract exercises.

    Session keys are token digests. Plaintext bearer tokens are never persisted here.
    Connector/provider credentials are outside this repository by design.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._identities: dict[tuple[str, str], str] = {}
        self._users: dict[str, UserState] = {}
        self._accounts: dict[str, AccountState] = {}
        self._memberships: dict[tuple[str, str], Membership] = {}
        self._entitlements: dict[str, EntitlementSnapshot] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._provisioned: set[str] = set()

    def put_user(self, user: UserState) -> None:
        with self._lock:
            self._users[user.user_id] = user

    def bind_identity(self, identity: VerifiedIdentity, user_id: str) -> None:
        user_id = _bounded_id(user_id, 'user_id')
        with self._lock:
            if user_id not in self._users:
                raise ValueError('ACC user must exist before an identity can be bound.')
            key = (identity.provider, identity.subject)
            current = self._identities.get(key)
            if current is not None and current != user_id:
                raise ValueError('Identity is already bound to another ACC user.')
            self._identities[key] = user_id

    def put_account(self, account: AccountState) -> None:
        with self._lock:
            self._accounts[account.account_id] = account

    def put_membership(self, membership: Membership) -> None:
        with self._lock:
            self._memberships[(membership.account_id, membership.user_id)] = membership

    def put_entitlements(self, entitlements: EntitlementSnapshot) -> None:
        with self._lock:
            self._entitlements[entitlements.account_id] = entitlements

    def remove_membership(self, account_id: str, user_id: str) -> bool:
        key = (_bounded_id(account_id, 'account_id'), _bounded_id(user_id, 'user_id'))
        with self._lock:
            return self._memberships.pop(key, None) is not None

    def resolve_identity(self, identity: VerifiedIdentity) -> str | None:
        with self._lock:
            return self._identities.get((identity.provider, identity.subject))

    def user(self, user_id: str) -> UserState | None:
        with self._lock:
            return self._users.get(user_id)

    def account(self, account_id: str) -> AccountState | None:
        with self._lock:
            return self._accounts.get(account_id)

    def membership(self, account_id: str, user_id: str) -> Membership | None:
        with self._lock:
            return self._memberships.get((account_id, user_id))

    def entitlements(self, account_id: str) -> EntitlementSnapshot | None:
        with self._lock:
            return self._entitlements.get(account_id)

    def session(self, token_digest: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.get(token_digest)

    def auth_snapshot(self, token_digest: str) -> AuthSnapshot | None:
        with self._lock:
            session = self._sessions.get(token_digest)
            if session is None:
                return None
            return AuthSnapshot(
                session=session,
                user=self._users.get(session.user_id),
                account=self._accounts.get(session.account_id),
                membership=self._memberships.get((session.account_id, session.user_id)),
                entitlements=self._entitlements.get(session.account_id),
            )

    def save_session(self, token_digest: str, session: SessionRecord) -> None:
        with self._lock:
            if token_digest in self._sessions:
                raise ValueError('Session digest already exists.')
            self._sessions[token_digest] = session

    def revoke_session(self, token_digest: str) -> bool:
        with self._lock:
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

    def provision_once(self, key: str, *, users=(), identities=(), accounts=(), memberships=(),
                       entitlements=()) -> bool:
        """Apply a named fixture once; later calls change nothing. See ``SQLiteAuthRepository``."""
        key = _bounded_id(key, 'provisioning key')
        with self._lock:
            if key in self._provisioned:
                return False
            for identity, user_id in identities:
                current = self._identities.get((identity.provider, identity.subject))
                if current is not None and current != user_id:
                    raise ValueError('Identity is already bound to another ACC user.')
            for user in users:
                self._users.setdefault(user.user_id, user)
            for account in accounts:
                self._accounts.setdefault(account.account_id, account)
            for identity, user_id in identities:
                if user_id not in self._users:
                    raise ValueError('ACC user must exist before an identity can be bound.')
                self._identities.setdefault((identity.provider, identity.subject), user_id)
            for membership in memberships:
                self._memberships.setdefault((membership.account_id, membership.user_id), membership)
            for snapshot in entitlements:
                self._entitlements.setdefault(snapshot.account_id, snapshot)
            self._provisioned.add(key)
            return True

    def session_digests(self) -> tuple[str, ...]:
        """Inspection helper for tests; values are irreversible SHA-256 digests."""
        with self._lock:
            return tuple(self._sessions)
