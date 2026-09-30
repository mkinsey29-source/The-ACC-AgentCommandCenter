"""Hosted ACC account authentication, permissions, and entitlement primitives.

M08 deliberately does not modify the loopback/local server. Hosted surfaces consume these
primitives through M09.
"""
from .models import (
    ACCOUNT_ROLES,
    AccountState,
    AuthContext,
    EntitlementSnapshot,
    Membership,
    SessionRecord,
    UserState,
    VerifiedIdentity,
)
from .identity import IdentityVerifier
from .repository import AuthRepository, InMemoryAuthRepository
from .service import AuthError, AuthService

__all__ = [
    'ACCOUNT_ROLES',
    'AccountState',
    'AuthContext',
    'AuthError',
    'AuthRepository',
    'AuthService',
    'EntitlementSnapshot',
    'IdentityVerifier',
    'InMemoryAuthRepository',
    'Membership',
    'SessionRecord',
    'UserState',
    'VerifiedIdentity',
]
