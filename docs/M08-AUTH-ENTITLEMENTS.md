# M08 — Accounts, Authentication, Permissions, and Entitlements

**Status:** Core v1 primitives implemented on `temporary/m08-auth-entitlements-v1`; production identity and durable storage adapters remain.

## Purpose

M08 is the hosted ACC account boundary consumed by the future M09 Platform API and M13 Plugin.
It does not replace the current loopback/local session-token server and does not alter the frozen
M01/M02/M03/M05 spine.

## Frozen rules consumed

M08 consumes, but does not modify:

- `acc/contracts.py` for the shared dotted capability vocabulary;
- `acc/state_authority.py` for account and credential authority.

The implementation refuses to start if those M02 rules no longer say:

- account state: `platform / read_replica`;
- credentials: `holder / never`.

Connector/provider credentials are intentionally not represented in M08 session records or identity
records. A provider-specific login verifier may inspect its own login assertion, but only a stable
verified external subject is returned to ACC.

## v1 model

`acc/auth/` now defines:

- `UserState`: platform user lifecycle;
- `AccountState`: tenant/account lifecycle;
- `Membership`: account role plus exact permission grants;
- `EntitlementSnapshot`: account feature flags and nonnegative quota limits;
- `VerifiedIdentity`: stable provider + subject identity with no provider credential fields;
- `SessionRecord`: one account-bound ACC session;
- `AuthContext`: current user/account/membership/entitlement view;
- `IdentityVerifier`: provider-specific verification boundary;
- `AuthRepository`: persistence seam;
- `InMemoryAuthRepository`: deterministic reference/test implementation;
- `AuthService`: login exchange, opaque-session issuance, authentication, authorization,
  entitlement-limit checks, and revocation.

## Session and tenant policy

ACC hosted session tokens are random opaque values prefixed `accs_`.

The plaintext token is returned to the caller once. The repository stores only its SHA-256 digest.
This is safe for high-entropy random bearer tokens and avoids a database containing directly usable
session credentials.

A session is bound to exactly one ACC account. M09 should pass the route/account tenant ID into
`authenticate(..., account_id=...)` or `authorize(..., account_id=...)`; a token issued for one
account is rejected for another.

The service checks current user, account, membership, and entitlement state on every request.
Permissions and entitlements are **not** frozen into the bearer token. Therefore suspension,
membership removal, permission removal, or entitlement changes take effect immediately without
waiting for the session token to expire.

Default session lifetime is one hour. The service supports bounded lifetimes from one minute through
the configured maximum, with an absolute implementation ceiling of seven days.

## Authentication versus authorization

M08 exposes separate safe error classes for M09:

- `AuthenticationError`: invalid, missing, revoked, or expired ACC session; unverified/unconfigured
  identity provider;
- `AuthorizationError`: the caller has a valid identity/session but lacks current account,
  permission, or entitlement access.

M09 can map these to HTTP 401 and HTTP 403 respectively without parsing error text.

## Identity-provider boundary

Public login should go through `AuthService.exchange_identity()`, which delegates assertion
verification to a configured `IdentityVerifier`. The verifier returns a `VerifiedIdentity`.
Provider-specific access/refresh credentials do not enter the ACC session model.

`create_session(VerifiedIdentity, ...)` remains the trusted internal seam for tests and future
server-side flows that already performed verification. M09 must not construct a
`VerifiedIdentity` directly from untrusted HTTP input.

No specific external identity provider is selected in this module. Provider-specific adapters can be
added without changing the account/session contract.

## Permissions

Permissions reuse the frozen dotted capability syntax and are exact grants. There is no wildcard
permission and account roles do not silently imply unlimited access. Provisioning code may choose
the permission set appropriate for owner/admin/member/viewer, but the authorization engine checks the
explicit grants it is given.

This keeps the security decision deterministic and avoids a hidden second permission vocabulary.

## Entitlements

Entitlements are account-level platform state:

- `features`: dotted feature identifiers such as `acc.web` or `workers.openai`;
- `limits`: dotted quota identifiers mapped to nonnegative integer ceilings.

M08 intentionally does not hard-code commercial plan names, prices, or purchase flows. Billing or
subscription systems can produce an `EntitlementSnapshot` without changing the auth engine.

## Verification

Focused M08 verification currently covers:

- opaque bearer-token generation and digest-only repository storage;
- expiry and revocation;
- current user/account/membership invalidation;
- exact permission checks;
- live entitlement changes and quota checks;
- tenant/account binding;
- identity binding conflicts;
- identity-verifier success/rejection;
- M02 account/credential authority invariants.

Focused result: **13 tests pass**, plus Python compilation.

## Remaining work before M08 is production-complete

1. Add a durable hosted `AuthRepository` adapter with transactional account/membership/session state.
2. Add at least one production identity-provider verifier and account-linking/provisioning flow.
3. Define session cleanup/rotation and security-event/audit persistence for the hosted service.
4. Wire the authoritative subscription/billing source into `EntitlementSnapshot` updates.
5. Add hosted integration tests through M09 (401/403 mapping, account isolation, reconnect/revocation).
6. Confirm the already-frozen credential-holder policy with Marvin before M08/M16 introduces any
   platform-held connector secret store.

Until those are done, M08 should be reported as **PARTIAL**, not production-complete.
