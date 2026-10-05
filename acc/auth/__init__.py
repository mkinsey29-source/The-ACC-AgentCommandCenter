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
from .identity import IdentityVerificationError, IdentityVerifier
from .repository import AuthRepository, AuthSnapshot, InMemoryAuthRepository
from .sqlite_repository import SQLiteAuthRepository
from .service import AuthenticationError, AuthorizationError, AuthError, AuthService

__all__ = [
    'ACCOUNT_ROLES',
    'AccountState',
    'AuthContext',
    'AuthenticationError',
    'AuthorizationError',
    'AuthError',
    'AuthRepository',
    'AuthSnapshot',
    'AuthService',
    'EntitlementSnapshot',
    'IdentityVerificationError',
    'IdentityVerifier',
    'InMemoryAuthRepository',
    'Membership',
    'SessionRecord',
    'SQLiteAuthRepository',
    'UserState',
    'VerifiedIdentity',
]
