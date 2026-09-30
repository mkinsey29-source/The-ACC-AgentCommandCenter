# M09 — Hosted Platform API and Events

**Status:** Core v1 read/event contracts integrated in PR #30; hosted transport slice in progress on `temporary/m09-hosted-transport-v1`.

## Purpose

M09 is the hosted boundary between authoritative ACC Platform state and remote surfaces: ACC Web,
the ACC Plugin, and future cloud-connected Desktop. It consumes M08 and does not turn the existing
loopback `acc/server.py` into a public server.

## First slice

The first slice separates the application contract from the eventual hosting framework.

`acc/platform/` defines:

- `PlatformReadRepository`: account-scoped hosted read model;
- `AccountProjectView`: project summary;
- `PlatformEventSource`: durable account/project-scoped event replay source;
- `PlatformEvent` and `EventBatch`: cursor/reconnect contract;
- `PlatformApi`: framework-neutral authenticated read/event application boundary;
- `ApiResponse` and `ApiError`: deterministic status/error envelope.

Initial operations and required grants (all also require the `acc.web` entitlement):

| Operation | Permissions |
|---|---|
| list projects | `project.read` |
| project state (project + tasks + workers + attention) | `project.read` **and** `task.read` (it returns full task records) |
| one task | `task.read` |
| events after a durable cursor | `event.read` |

Workers and attention items have no separate permission in v1; they are covered by `project.read`.
If they later gain sensitive fields, give them their own permission before exposing those fields.

`PlatformReadRepository` must return **public projections**. M09 checks ownership
(`account_id`/`project_id`, plus task `id`) but otherwise passes records through unchanged. It does
not strip fields, so internal fields such as argv, prompts, credential references or local paths
must never be in the read model.

## Security rules

Every account-scoped operation parses a Bearer session and calls M08 with the route's explicit
`account_id`. The first Web-facing slice requires both the exact read permission and the
`acc.web` entitlement.

M08 errors map deterministically:

- `AuthenticationError` -> 401 `authentication_required`;
- `AuthorizationError` -> 403 `access_denied`;
- caller input validation (malformed bearer header, account/project/task ID, cursor or limit) -> 400.
  Route IDs are validated before any repository call;
- repository/event-source contract violation -> 500 without row/event details;
- any other unexpected exception -> opaque 500 `internal_error` via `PlatformApi.handle`. The HTTP
  adapter must call every operation through `handle` (or reproduce it) and log the chained
  exception server-side only.

Tokens are never echoed. A token for account A requesting account B always returns the same 403,
whether or not B or its projects exist, so it can't be used to enumerate accounts. Within one's own
account, an unknown project or task is 404.

Hosted storage adapters must apply `account_id` in the storage query itself. The API also checks
record account/project IDs as defense in depth.

## Event/reconnect contract

Transport is replaceable (polling, SSE, WebSocket), but replay semantics are stable:

- cursor is a nonnegative integer;
- reconnect requests events strictly after the last accepted cursor;
- events are strictly ordered by sequence;
- every event carries account/project ownership;
- cross-tenant/project events fail closed;
- an empty batch preserves the requested cursor and cannot claim `has_more`;
- a batch may not contain more events than the requested `limit` (1–500);
- event timestamps are finite and nonnegative;
- a missing cursor (`None`) means "from the beginning" (0); any other non-integer, boolean or
  negative cursor is a 400.

`seq` is monotonic **within one (account, project) stream**. It need not be contiguous: a source may
use an account-wide or global sequence, so gaps in a project stream are normal and are not a
missed-event signal.

### Reset/retention signal

The hosted-transport slice adds the required additive reset signal before retention/pruning:
`EventBatch.reset_required` and `oldest_available`.

A reset batch contains no events, cannot claim `has_more`, identifies the oldest retained sequence,
and supplies the cursor to resume from **after the client refetches project state**. This covers both
pruned-history gaps and a cursor that is no longer valid after restore/recovery. The HTTP ticket
endpoint returns `409 event_reset_required` rather than opening a stream from a stale cursor; an
already-open WebSocket can also emit `reset_required` and close with code 4009.

Other reconnect notes:
- repeated reconnects with the same cursor are idempotent;
- duplicates and replays (`seq <= cursor`) fail closed as 500;
- `events_after` for a project ID that doesn't exist in the account returns an empty batch, not a
  404. The adapter may add a `project` existence check if subscribing to phantom projects becomes a
  concern.

This gives the later WebSocket adapter a durable replay contract instead of a separate live-only
event model.

## Hosted transport slice

`acc/platform/transport.py` is a dependency-free ASGI application boundary. Any production ASGI
server can host it without coupling the M09 application contract to that server.

It provides:

- explicit HTTPS Origin allowlisting; no wildcard origins;
- CORS responses scoped to the accepted origin;
- HTTP routes under `/v1/accounts/{account_id}/...`;
- short-lived, random, one-time WebSocket tickets issued only after `event.read` authorization;
- bearer session tokens never placed in the WebSocket URL;
- reauthorization on every WebSocket poll so revocation or permission loss closes the stream;
- replay from the ticket's durable cursor;
- reset/retention handling;
- read-only WebSocket input (only `ping` is accepted).

The one-time ticket store retains the already-presented ACC session Authorization value only in
process memory for the short-lived stream. It is never serialized, logged, returned to the client,
or placed in the WebSocket URL.

## Still deliberately deferred

- no write/command endpoints;
- no durable hosted database adapter;
- no attempt to expose the local `Coordinator` directly;
- no changes to M08 or the frozen spine.

After independent review and executable verification of this transport slice, the next M09 work is
command/mutation endpoints with idempotent operation IDs and optimistic revisions, plus the durable
hosted repository/runtime needed to deploy the real Platform service.
