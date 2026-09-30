"""Data contracts for ACC hosted authentication and account authorization."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from ..contracts import capability_list, capability_name, provider_id


ACCOUNT_ROLES = frozenset(('owner', 'admin', 'member', 'viewer'))
ACCOUNT_STATUSES = frozenset(('active', 'suspended', 'closed'))
USER_STATUSES = frozenset(('active', 'suspended', 'closed'))


def _bounded_id(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(label + ' must be a string.')
    value = value.strip()
    if not value or len(value) > 200 or '\0' in value:
        raise ValueError(label + ' must contain 1-200 characters.')
    return value


@dataclass(frozen=True)
class VerifiedIdentity:
    """Identity already verified by an M08 identity-provider adapter.

    Provider access/refresh tokens are intentionally absent. A verified assertion contains only
    the stable external subject and optional display metadata required to resolve an ACC user.
    """
    provider: str
    subject: str
    email: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'provider', provider_id(self.provider, 'identity provider'))
        object.__setattr__(self, 'subject', _bounded_id(self.subject, 'identity subject'))
        if self.email is not None:
            if not isinstance(self.email, str) or not 3 <= len(self.email.strip()) <= 320:
                raise ValueError('email must contain 3-320 characters when supplied.')
            object.__setattr__(self, 'email', self.email.strip())


@dataclass(frozen=True)
class UserState:
    user_id: str
    status: str = 'active'
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, 'user_id', _bounded_id(self.user_id, 'user_id'))
        if self.status not in USER_STATUSES:
            raise ValueError('Unsupported user status.')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('user revision must be a nonnegative integer.')


@dataclass(frozen=True)
class AccountState:
    account_id: str
    status: str = 'active'
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        if self.status not in ACCOUNT_STATUSES:
            raise ValueError('Unsupported account status.')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('account revision must be a nonnegative integer.')


@dataclass(frozen=True)
class Membership:
    account_id: str
    user_id: str
    role: str = 'member'
    permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        object.__setattr__(self, 'user_id', _bounded_id(self.user_id, 'user_id'))
        if self.role not in ACCOUNT_ROLES:
            raise ValueError('Unsupported account role.')
        object.__setattr__(
            self, 'permissions',
            capability_list(self.permissions, label='membership permissions', limit=200),
        )


@dataclass(frozen=True)
class EntitlementSnapshot:
    """Current platform-authoritative feature and quota snapshot for one account."""
    account_id: str
    features: tuple[str, ...] = ()
    limits: Mapping[str, int] = field(default_factory=dict)
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        object.__setattr__(
            self, 'features',
            capability_list(self.features, label='entitlement features', limit=200),
        )
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('entitlement revision must be a nonnegative integer.')
        if not isinstance(self.limits, Mapping) or len(self.limits) > 100:
            raise ValueError('entitlement limits must be an object with at most 100 entries.')
        normalized: dict[str, int] = {}
        for key, value in self.limits.items():
            key = capability_name(key, 'entitlement limit')
            if type(value) is not int or value < 0:
                raise ValueError('entitlement limit values must be nonnegative integers.')
            normalized[key] = value
        object.__setattr__(self, 'limits', MappingProxyType(normalized))

    def has(self, feature: str) -> bool:
        return capability_name(feature, 'entitlement feature') in self.features

    def limit(self, name: str) -> int | None:
        return self.limits.get(capability_name(name, 'entitlement limit'))


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    account_id: str
    user_id: str
    issued_at: int
    expires_at: int
    identity_provider: str
    revoked: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, 'session_id', _bounded_id(self.session_id, 'session_id'))
        object.__setattr__(self, 'account_id', _bounded_id(self.account_id, 'account_id'))
        object.__setattr__(self, 'user_id', _bounded_id(self.user_id, 'user_id'))
        object.__setattr__(
            self, 'identity_provider',
            provider_id(self.identity_provider, 'identity provider'),
        )
        if type(self.issued_at) is not int or type(self.expires_at) is not int:
            raise ValueError('session timestamps must be integer epoch seconds.')
        if self.expires_at <= self.issued_at:
            raise ValueError('session expiry must be after issue time.')


@dataclass(frozen=True)
class AuthContext:
    session: SessionRecord
    account: AccountState
    membership: Membership
    entitlements: EntitlementSnapshot
