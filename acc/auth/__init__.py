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
    VerifiedIdentity,
)
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
    'InMemoryAuthRepository',
    'Membership',
    'SessionRecord',
    'VerifiedIdentity',
]
