# M08 — Accounts, Authentication, Permissions, and Entitlements

**Status:** Core v1 primitives, independently security-reviewed and corrected in PR #29 (2026-09-30). Production identity and durable storage adapters remain.

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

A session is bound to exactly one ACC account. `authorize(..., account_id=...)` and
`require_limit(..., account_id=...)` **require** the route's tenant ID, so a token issued for one
account is rejected for another. `authenticate(token)` without `account_id` is identity-level only
("who am I") and must never be the basis of an account-scoped decision.

The service also rejects any user, account, membership or entitlement record whose own IDs do not
match the key it was requested with. A buggy durable repository therefore fails closed instead of
leaking another tenant's grants.

Unknown, revoked and expired sessions all return the same `AuthenticationError`. At login, a
nonexistent, inactive, unentitled or non-member account all return the same `AuthorizationError`,
so a verified identity cannot enumerate account IDs.

The service checks current user, account, membership, and entitlement state on every request.
Permissions and entitlements are **not** frozen into the bearer token. Therefore suspension,
membership removal, permission removal, or entitlement changes take effect immediately without
waiting for the session token to expire.

Default session lifetime is one hour. The service supports bounded lifetimes from one minute through
the configured maximum, with an absolute implementation ceiling of seven days.

## Authentication versus authorization

M08 exposes separate safe error classes for M09:

- `AuthenticationError` (HTTP 401): invalid, missing, revoked or expired ACC session; an
  unverified or unconfigured identity provider; an identity not linked to a user; a suspended or
  closed **user**;
- `AuthorizationError` (HTTP 403): a valid principal lacks access to the account (wrong tenant,
  suspended or closed account, removed membership, no entitlement snapshot), a permission, an
  entitlement, or a quota.

M09 maps these without parsing error text. `AuthError` is deliberately **not** a `ValueError`. An
invalid request parameter, such as an out-of-range session lifetime or a malformed permission name,
raises plain `ValueError` (HTTP 400).

## Identity-provider boundary

`AuthService.exchange_identity()` is the **only** public login path. It delegates assertion
verification to a configured `IdentityVerifier`, and any verifier exception fails closed as a generic
`AuthenticationError` without surfacing provider detail. The verifier must return exactly a
`VerifiedIdentity`, not a subclass that could carry extra claims or provider credentials, for the
same provider. Provider-specific access/refresh credentials never enter the ACC session model.

There is no public method that turns a caller-constructed `VerifiedIdentity` into a session
(`_issue_session` is private). M09 must not construct a `VerifiedIdentity` from untrusted HTTP
input. Identities resolve by `(provider, subject)` only; email is display metadata and is never used
for account linking.

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

`require_limit(token, name, requested, account_id=...)` compares a prospective **total** with the
ceiling. A missing limit is refused (fail closed). It is a check, not a reservation: code that
consumes quota must re-check and record usage atomically in its own transaction, or concurrent
requests can overshoot.

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
- SQLite restart persistence, digest-only storage, insert-only session creation, durable revocation,
  stable identity binding, and one-snapshot authentication.

Independent review (2026-09-30) found that the committed package did not import:
`@runtime_checkable` had been applied to the exception class. The earlier 13/13 result came from a
hand-built copy. After the fixes, `tests/test_auth_entitlements.py` has **26 tests, all passing** on
the real checkout, including 13 security regressions.

## Remaining work before M08 is production-complete

1. **Adapter implemented, production wiring pending.** `SQLiteAuthRepository` durably stores users,
   accounts, stable provider/subject links, memberships, entitlement snapshots, and session digests.
   Its `auth_snapshot` query reads session + user + account + membership + entitlements as one
   SQLite statement, and `save_session` is insert-only. `AuthService.authenticate` consumes exactly
   one snapshot per request. The staging composition can now run on it (`ACC_STAGING_DATABASE`;
   see `M09-STAGING-RUNTIME.md`). Startup fixtures go through `provision_once`, which applies a
   keyed fixture once per database and never overwrites or resurrects later state. Shared
   production deployment wiring, hosted integration against a deployed service, and the database
   operations policy remain open.
2. Add at least one production identity-provider verifier and account-linking/provisioning flow.
3. Define session cleanup/rotation, **revoke-all-sessions for a user or account** (incident response;
   today only per-token revocation exists, though status checks still block suspended principals), and
   security-event/audit persistence for the hosted service.
4. Wire the authoritative subscription/billing source into `EntitlementSnapshot` updates.
5. Add hosted integration tests through M09 (401/403 mapping, account isolation, reconnect/revocation).
6. Confirm the already-frozen credential-holder policy with Marvin before M08/M16 introduces any
   platform-held connector secret store.

Until those are done, M08 should be reported as **PARTIAL**, not production-complete.
